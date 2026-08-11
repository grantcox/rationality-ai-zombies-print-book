"""Step 4 — render extracted chapters to LaTeX.

Reads build/chapters/*.json, applies the decisions in overrides.csv, and emits
a LaTeX document. Design as settled so far:

  * one numbered footnote series per chapter, shared by citations and links
  * an external link becomes  → lesswrong.com/lw/vs/selling_nonapples
  * a cross-reference becomes see Book III, chapter 132, “The Wonder of Evolution”
  * the anchor phrase carries a dotted underline
  * the ❦ fleuron is dropped: it separates a chapter title from its body, and
    a page break already does that. Pass --ornaments to keep it.

Set in EB Garamond, which ships with TeX Live and carries both the arrow and
the fleuron; the website sets the same text in Garamond Premier.

Run:  python -m raz.render --chapters Reversed-Stupidity-Is-Not-Intelligence
      python -m raz.render --all --variant A
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import overrides
from .maths import convert as maths_tex
from .common import BUILD, REPO

TEX = BUILD / "tex"
ASSETS_PDF = BUILD / "assets_pdf"

#: How the two kinds of link are marked in the running text. The footnote is
#: written either way; only the mark on the anchor differs. 1,790
#: cross-references is a lot of underlining, so the density is worth seeing
#: before committing to it.
VARIANTS = {
    "A": {"underline_link": True, "underline_xref": True, "xref_note": True,
          "blurb": "both kinds marked, both footnoted"},
    "B": {"underline_link": True, "underline_xref": False, "xref_note": True,
          "blurb": "only external links marked; cross-references footnoted but unmarked"},
    "C": {"underline_link": True, "underline_xref": False, "xref_note": False,
          "blurb": "only external links; cross-references left as plain prose"},
}

ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
    "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def esc(text: str) -> str:
    return "".join(ESCAPES.get(c, c) for c in text)


#: A bare URL sitting in ordinary text. 71 of these occur, nearly all inside
#: citation footnotes where the author wrote the address out in full.
BARE_URL = re.compile(r"https?://\S+")


class UrlBank:
    r"""Every address in the book, defined once at the top level.

    ``\url`` cannot appear inside another command's argument. It works by
    changing catcodes, which it can only do while reading its own argument --
    inside ``\footnote{...}`` the argument has already been tokenised, so an
    address containing ``%`` comments out the rest of the line and swallows
    the closing brace. Nearly every address here is inside a footnote.

    ``\urldef`` is url.sty's answer: it reads the address at the top level,
    where the package can do its work, and binds it to a macro that is then
    safe to use anywhere. So each distinct address gets one definition in the
    preamble and every citation of it is a single token.
    """

    def __init__(self):
        self.macros: dict[str, str] = {}

    @staticmethod
    def clean(url: str) -> str:
        # The skin's soft hyphens would print as hyphens a reader could not
        # tell from part of the address.
        return url.replace("­", "").replace("​", "")

    def ref(self, url: str) -> str:
        url = self.clean(url)
        if url not in self.macros:
            n = len(self.macros)
            # Macro names may only contain letters, so the index is spelled
            # in them: 0 -> a, 27 -> bb.
            name = ""
            while True:
                name = chr(ord("a") + n % 26) + name
                n = n // 26 - 1
                if n < 0:
                    break
            self.macros[url] = "\\razurl" + name
        return self.macros[url]

    def preamble(self) -> str:
        return "\n".join(r"\urldef{%s}\url{%s}" % (macro, url)
                         for url, macro in self.macros.items())


def text_to_tex(text: str, bank: UrlBank) -> str:
    r"""Escape running text, handing any bare URL to \url.

    An unbroken 90-character address in a footnote leaves TeX no break point
    and it stretches the line's word spaces instead; \url lets it break at
    punctuation.
    """
    out, last = [], 0
    for m in BARE_URL.finditer(text):
        url = m.group(0).rstrip(".,;:)")
        out.append(esc(text[last:m.start()]))
        out.append(bank.ref(url))
        out.append(esc(m.group(0)[len(url):]))
        last = m.end()
    out.append(esc(text[last:]))
    return "".join(out)


def plain_text(node) -> str:
    """The visible text of a node, unescaped."""
    if isinstance(node, dict):
        if node.get("t") == "text":
            return node["v"]
        return "".join(plain_text(v) for v in node.values()
                       if isinstance(v, (dict, list)))
    if isinstance(node, list):
        return "".join(plain_text(v) for v in node)
    return ""


# --- decisions ---------------------------------------------------------------


def apply_decisions(value, rules, report):
    """Attach each link's ruling to its node, and act on 'remove'.

    'remove' drops the anchor text with the footnote, and must take the
    orphan tail with it -- the run of plain text the wiki's auto-linker left
    stranded beside a broken URL. Left behind it prints as debris mid-sentence.
    """
    if isinstance(value, dict):
        return {k: apply_decisions(v, rules, report) if isinstance(v, (list, dict))
                else v for k, v in value.items()}
    if not isinstance(value, list):
        return value
    if not all(isinstance(x, dict) and "t" in x for x in value):
        return [apply_decisions(v, rules, report) for v in value]

    out = []
    for i, n in enumerate(value):
        if n.get("t") == "link" and not n.get("absorbed_by"):
            d = overrides.decide(rules, n["url"])
            n["_action"], n["_url"] = d["action"], d["url"]
            report[d["action"]] += 1
            if d["action"] == "remove":
                tail = n.get("orphan_tail")
                nxt = value[i + 1] if i + 1 < len(value) else None
                if tail and isinstance(nxt, dict) and nxt.get("t") == "text":
                    if nxt["v"].startswith(tail):
                        nxt["v"] = nxt["v"][len(tail):]
                        report["orphan_stripped"] += 1
                    else:
                        report["orphan_mismatch"] += 1
                continue  # drop the anchor text with the link
        out.append(apply_decisions(n, rules, report))
    return out


# --- inline ------------------------------------------------------------------

WRAP = {
    "em": r"\emph{%s}", "strong": r"\textbf{%s}", "smallcaps": r"\textsc{%s}",
    "sub": r"\textsubscript{%s}", "sup": r"\textsuperscript{%s}",
    "code": r"\texttt{%s}", "small": r"{\small %s}", "big": r"{\large %s}",
    "del": r"\sout{%s}", "boxed": r"\fbox{%s}", "year": "%s", "ordinal": "%s",
}


#: How much of the source's own size a display heading is set at. 350% of an
#: 11pt body is 38.5pt, which on a 6x9 measure is display type sized for a
#: browser window rather than a page: it filled the line and pushed the plate
#: at the end of the chapter far enough down to strand its caption overleaf.
#: Two-thirds still reads unmistakably as display type.
BANNER_SCALE = 2 / 3

#: The volume number as the title page spells it. "Book Two", not "Book II":
#: the roman numeral is how a cross-reference cites a volume mid-sentence, and
#: on a title page the word reads as part of the sentence it belongs to.
BOOK_WORDS = ["", "One", "Two", "Three", "Four", "Five", "Six"]

#: Printed at the back of every volume. Supplied by the publisher rather than
#: taken from the mirror, which has no such page.
ABOUT_THE_AUTHOR = (
    "Eliezer Yudkowsky is a decision theorist and computer scientist at the "
    "Machine Intelligence Research Institute in Berkeley, California who is "
    "known for his work in technological forecasting. His publications include "
    "the Cambridge Handbook of Artificial Intelligence chapter \u201cThe Ethics of "
    "Artificial Intelligence,\u201d co-authored with Nick Bostrom. Yudkowsky\u2019s "
    "writings have helped spark a number of ongoing academic and public debates "
    "about the long-term impact of AI, and he has written a number of popular "
    "introductions to topics in cognitive science and formal epistemology, such "
    "as Rationality: From AI to Zombies and \u201cHarry Potter and the Methods of "
    "Rationality.\u201d"
)

#: A run of two or more spaces inside a fixed-width display. One space is an
#: ordinary word space; several are the author lining up a column.
COLUMN_GAP = re.compile("[ \u00a0]{2,}")


def keep_columns(tex: str) -> str:
    r"""Hold a multi-space run at its exact width.

    TeX collapses runs of input spaces, which would close up the gap the site
    uses to right-align the numbers in Morality as Fixed Computation. A tie
    is an unbreakable space of the current font's fixed width, so n of them in
    a typewriter face reproduce n columns. Single spaces are left ordinary, or
    the long data lines could not wrap at all.
    """
    return COLUMN_GAP.sub(lambda m: "~" * len(m.group(0)), tex)


class Renderer:
    def __init__(self, doc, variant, opts, report):
        self.doc = doc
        self.v = VARIANTS[variant]
        self.opts = opts
        self.report = report
        self.notes = {f["n"]: f for f in doc["footnotes"]}
        self.used_notes: set[int] = set()
        self.mono = False
        self.shared: dict[tuple, str] = {}
        self.bank = opts["bank"]

    # -- footnote text -------------------------------------------------------

    def link_note(self, node) -> str:
        # \url takes the raw URL: it sets its own catcodes, so the text must
        # not be pre-escaped. The arrow is the literal glyph from EB Garamond
        # rather than a maths-font \rightarrow, so it matches the text.
        from .common import printed_url
        return "→\\ " + self.bank.ref(printed_url(node["_url"]))

    def xref_note(self, node) -> str:
        entry = self.opts["spine"].get(node["page"])
        if entry is None:
            self.report["xref_unresolved"] += 1
            return "see " + esc(node["page"])
        from .links import label
        return "see " + esc(label(entry))

    def note_mark(self, key: tuple, body) -> str:
        r"""One number per distinct note, however often the chapter cites it.

        Two links to the same address, or two cross-references to the same
        chapter, are the same note. Printing it twice under two numbers tells
        a reader they are different things, and the second one earns its space
        on the page by repeating the first.

        The repeat is a \ref to a \label inside the original rather than a
        number worked out here, so it stays right whatever LaTeX assigns --
        the footnote counter is not ours to predict.
        """
        if key in self.shared:
            self.report["footnote_shared"] += 1
            return r"\repeatnote{%s}" % self.shared[key]
        tag = "fn:%d:%d" % (self.doc["order"], len(self.shared) + 1)
        self.shared[key] = tag
        return r"\footnote{\label{%s}%s}" % (tag, body() if callable(body) else body)

    def marked(self, body: str, underline: bool) -> str:
        return r"\dotuline{%s}" % body if underline else body

    def anchor_is_address(self, node) -> bool:
        from .common import display_url
        from .links import anchor_is_url
        return anchor_is_url(plain_text(node),
                             {"url": node["url"], "display": display_url(node["url"])})

    # -- inline walk ---------------------------------------------------------

    def inline(self, nodes) -> str:
        return "".join(self.one(n) for n in nodes)

    def one(self, n) -> str:
        t = n.get("t")
        if t == "text":
            tex = text_to_tex(n["v"], self.bank)
            return keep_columns(tex) if self.mono else tex
        if t == "br":
            # The comment eats the newline: without it the source line break
            # becomes a space token at the head of the next line, indenting it
            # by one character. Invisible in prose, glaring in a column.
            return "\\\\%\n"

        # The wiki wraps a citation marker in <sup>, but \footnote raises its
        # own mark. Emitting \textsuperscript{\footnote{...}} typesets the note
        # body inside a superscript box and loses it, so the wrapper is dropped
        # when it holds nothing but markers.
        if t == "sup":
            kids = n.get("c", [])
            if kids and all(k.get("t") == "fn" or
                            (k.get("t") == "text" and not k["v"].strip())
                            for k in kids):
                return self.inline(kids)

        if t in WRAP:
            return WRAP[t] % self.inline(n.get("c", []))

        if t == "link":
            body = self.inline(n.get("c", []))
            if n.get("absorbed_by"):
                return body  # the tail of a split URL; no note of its own
            if n.get("_action") in ("unlink",):
                return body

            # When the link text is the address itself -- bibliography-style
            # locators -- there is no phrase to point at, and a footnote would
            # only repeat what is already on the line. Underlining it also
            # boxes it against line breaking, which strands a 60-character URL
            # and stretches the whole paragraph's word spaces.
            if self.anchor_is_address(n):
                self.report["link_printed_inline"] += 1
                return self.bank.ref(plain_text(n))

            return self.marked(body, self.v["underline_link"]) + \
                self.note_mark(("link", n["_url"]), lambda: self.link_note(n))

        if t == "xref":
            body = self.inline(n.get("c", []))
            if not self.v["xref_note"]:
                return body
            return self.marked(body, self.v["underline_xref"]) + \
                self.note_mark(("xref", n["page"]), lambda: self.xref_note(n))

        if t == "fn":
            note = self.notes.get(n["n"])
            if note is None:
                self.report["fn_missing"] += 1
                return ""
            self.used_notes.add(n["n"])
            return self.note_mark(
                ("fn", n["n"]),
                lambda: self.blocks(note["blocks"], inside_note=True).strip())

        if t == "img":
            return self.image(n)
        if t == "math":
            self.report["math_inline"] += 1
            return maths_tex(n, self.report)
        return self.inline(n.get("c", []))

    def image(self, n) -> str:
        # Only the SVGs become PDFs; a photograph keeps its own name and
        # extension, which lualatex places directly. Looking only for a .pdf
        # reported every photograph in the book as missing.
        src = Path(n["src"])
        for name in (src.stem + ".pdf", src.name):
            if (ASSETS_PDF / name).exists():
                return r"\includegraphics[max width=\linewidth]{%s}" % name
        self.report["image_missing"] += 1
        return r"\textbf{[missing image: %s]}" % esc(n["src"])

    # -- blocks --------------------------------------------------------------

    def blocks(self, blocks, inside_note=False, flush_first=False) -> str:
        """flush_first suppresses the indent on the first paragraph produced.

        An opening paragraph has nothing above it to be distinguished from, so
        indenting it is noise -- the convention applies both to a chapter's
        first paragraph of prose and to the first line of a display quote.
        """
        out = []
        pending = flush_first
        for b in blocks:
            flush = pending and b.get("t") == "p"
            if flush:
                pending = False
            out.append(self.block(b, inside_note, flush))
        return "\n\n".join(x for x in out if x)

    def block(self, b, inside_note=False, flush=False) -> str:
        t = b.get("t")
        if t == "ornament":
            if not self.opts["ornaments"]:
                return ""
            return r"\begin{center}❦\end{center}"
        if t == "p":
            body = self.inline(b.get("c", []))
            if not body.strip():
                return ""
            if b.get("dataset"):
                return self.dataset_line(b["dataset"], body)
            if b.get("qa"):
                return self.qa_line(b["qa"], body)
            if b.get("align") == "center":
                # \centering, not the center environment: center is built on a
                # list and adds \topsep above and below, which on a centred
                # line between two paragraphs reads as a much bigger gap than
                # the half-line everything else gets.
                return r"{\centering %s\par}" % body
            if b.get("indent"):
                return r"\begin{quote}%s\end{quote}" % body
            return (r"\noindent " + body) if flush else body
        if t == "monospaced":
            was, self.mono = self.mono, True
            try:
                inner = self.blocks(b.get("c", []), inside_note)
            finally:
                self.mono = was
            return "\\begin{fixedwidth}\n%s\n\\end{fixedwidth}" % inner
        if t == "byline":
            return r"\begin{flushright}\emph{%s}\end{flushright}" % \
                self.inline(b.get("c", []))
        if t == "h":
            cmd = {2: r"\section*", 3: r"\subsection*", 4: r"\subsubsection*"}
            return "%s{%s}" % (cmd.get(b.get("level", 3), r"\subsection*"),
                               self.inline(b.get("c", [])))
        if t == "quote":
            # Inside a fixed-width display the site uses a blockquote purely to
            # inset the block. Setting it as a real quotation would restore the
            # per-paragraph indent the display exists to avoid.
            if self.mono:
                return "\\begin{adjustwidth}{2em}{0pt}\n%s\n\\end{adjustwidth}" % \
                    self.blocks(b.get("c", []), inside_note)
            return "\\begin{quotation}\n%s\n\\end{quotation}" % \
                self.blocks(b.get("c", []), inside_note, flush_first=True)
        if t == "list":
            env = "enumerate" if b.get("ordered") else "itemize"
            items = "\n".join(r"\item %s" % self.blocks(i, inside_note)
                              for i in b.get("items", []))
            return "\\begin{%s}\n%s\n\\end{%s}" % (env, items, env)
        if t == "dl":
            items = "\n".join(
                r"\item[%s] %s" % (self.inline(i.get("dt", [])),
                                   self.blocks(i.get("dd", []), inside_note))
                for i in b.get("items", []))
            return "\\begin{description}\n%s\n\\end{description}" % items
        if t == "table":
            return self.table(b)
        if t == "hr":
            return r"\sectionbreak"
        if t == "pre":
            return "\\begin{verbatim}\n%s\n\\end{verbatim}" % b.get("v", "")
        if t == "banner":
            self.report["banner"] += 1
            return self.banner(b, inside_note)
        if t == "math_block":
            self.report["math_block"] += 1
            return maths_tex(b, self.report)
        self.report[f"block_unhandled_{t}"] += 1
        return ""

    #: What the site's CSS puts in front of each training-data line. Set in
    #: ASCII: the page uses U+2212 MINUS, which the typewriter face does not
    #: carry, and a hyphen is unambiguous in a fixed-width listing.
    DATASET_MARKS = {"plus": "+:", "minus": "-:", "none": ":"}

    def gutter_line(self, mark: str, body: str) -> str:
        """A paragraph with a label hanging in the left margin.

        The label is content, not decoration -- it says which category a data
        line belongs to, or whether a paragraph is the question or the answer
        -- so wrapped lines align under the text rather than under the label,
        keeping the block readable as one unit.
        """
        return (r"\par\hangindent=2em\hangafter=1\noindent"
                r"\makebox[2em][l]{%s}%s" % (mark, body.lstrip()))

    def dataset_line(self, role, body) -> str:
        self.report[f"dataset_{role}"] += 1
        # The wiki markup leaves one space after the opening tag. In a
        # fixed-width face that prints as a column, pushing the first line one
        # character right of the wrapped ones it should line up with.
        return self.gutter_line(esc(self.DATASET_MARKS.get(role, ":")), body)

    def banner(self, b, inside_note) -> str:
        r"""A display heading the wiki had no markup for.

        Sized from the source's own percentage rather than a chosen \Huge, so
        the one place the author wanted display type gets the size asked for.
        The screen version is grey with a black outline; that is a effect that
        muddies at print resolution, so it is set solid.
        """
        pt = 11.0 * b.get("scale", 200) / 100.0 * BANNER_SCALE
        return ("{\\parskip=0pt\\centering\\fontsize{%.1f}{%.1f}\\selectfont"
                "\\bfseries\n%s\n\\par}" % (pt, pt * 1.1,
                                            self.blocks(b.get("c", []), inside_note)))

    def qa_line(self, role, body) -> str:
        """A question or its answer, labelled the way the skin labels them.

        The question is set bold, as the stylesheet sets it; the answer is
        not. Both labels are bold, so the exchange reads as an exchange.
        """
        self.report[f"qa_{role}"] += 1
        # Stripped before the bold wrapper goes on, or gutter_line's lstrip
        # would be looking at a backslash and the question would sit a space
        # further right than its answer.
        body = body.lstrip()
        if role == "Q":
            body = r"\textbf{%s}" % body
        return self.gutter_line(r"\textbf{%s:}" % role, body)

    def table(self, b) -> str:
        rows = b.get("rows", [])
        if not rows:
            return ""
        cols = max(len(r) for r in rows)
        body = " \\\\\n".join(
            " & ".join(self.blocks(c).replace("\n\n", " ") for c in r)
            + " &" * (cols - len(r)) for r in rows)
        return "\\begin{center}\\begin{tabular}{%s}\n%s\n\\end{tabular}\\end{center}" % (
            "l" * cols, body)


# --- documents ---------------------------------------------------------------

PREAMBLE = r"""\documentclass[11pt,twoside,openright]{memoir}
\setstocksize{9in}{6in}
\settrimmedsize{9in}{6in}{*}
\setlrmarginsandblock{0.9in}{0.7in}{*}
\setulmarginsandblock{0.8in}{0.9in}{*}
\checkandfixthelayout

\usepackage{fontspec}
\setmainfont{EBGaramond}[
  Extension = .otf,
  UprightFont = *-Regular, ItalicFont = *-Italic,
  BoldFont = *-Bold, BoldItalicFont = *-BoldItalic,
  Ligatures = TeX ]
\usepackage{microtype}
\usepackage{xcolor}
\usepackage[normalem]{ulem}
\usepackage{graphicx}
\usepackage[export]{adjustbox}
\usepackage{url}
\urlstyle{same}   % URLs set in the text face, not typewriter
\usepackage{amsmath}
\usepackage{unicode-math}
% Garamond-Math is EB Garamond's companion: without it the equations would
% be set in Computer Modern, a different century from the text around them.
\setmathfont{Garamond-Math.otf}
\usepackage{xfrac}   % \sfrac: the diagonal fraction the skin sets with OpenType

% Running head carries the chapter title on the outer edge; the page number
% sits centred in the foot. A chapter's opening page shows the folio only --
% the title is right there below it.
\makepagestyle{raz}
\makeevenhead{raz}{\small\itshape\leftmark}{}{}
\makeoddhead{raz}{}{}{\small\itshape\rightmark}
\makeevenfoot{raz}{}{\thepage}{}
\makeoddfoot{raz}{}{\thepage}{}
\makepagestyle{razopen}
\makeevenfoot{razopen}{}{\thepage}{}
\makeoddfoot{razopen}{}{\thepage}{}
\pagestyle{raz}

% The chapter opener: number above title, both centred. \parskip is zeroed
% inside, or the \par that ends each line would add a paragraph gap to the
% spacing set here.
\newcommand{\chapnum}[1]{%
  {\parskip=0pt\centering
   \textcolor[gray]{0.55}{\large\textperiodcentered\;#1\;\textperiodcentered}\par}
  \vspace{0.4\baselineskip}}
\newcommand{\chaptitle}[1]{%
  {\parskip=0pt\centering\Large\bfseries #1\par}
  \vspace{1.1\baselineskip}}

% A volume's title page. The book's own title at twice the body size, the
% work's title, the volume's place in it and the author at one and a half.
% No folio: a title page that numbered itself would be the only page in the
% book announcing which page it is. Set as centred paragraphs rather than
% single lines -- "How to Actually Change Your Mind" at 22pt is wider than a
% 6in page's measure and has to be allowed to break.
\newcommand{\razhalftitle}[2]{%
  \thispagestyle{empty}%
  {\parskip=0pt\centering
   \null\vspace*{0.20\textheight}
   {\fontsize{22}{26.4}\selectfont #1\par}
   \vspace{2.2\baselineskip}
   {\fontsize{16.5}{19.8}\selectfont Rationality: From AI to Zombies\par
    Book #2\par}
   \vspace{2.2\baselineskip}
   {\fontsize{16.5}{19.8}\selectfont Eliezer Yudkowsky\par}
   \par}}

% A part opener. The letter runs A..Z across the whole work rather than
% restarting each volume, which is what the cross-reference footnotes cite and
% what the printed edition does -- Book II opens at Part E.
\newcommand{\razpart}[2]{%
  \thispagestyle{empty}%
  {\parskip=0pt\centering
   \null\vspace*{0.26\textheight}
   \textcolor[gray]{0.55}{\large Part~#1}\par
   \vspace{0.9\baselineskip}
   {\fontsize{19.8}{23.8}\selectfont\bfseries #2\par}
   \par}}

% The last page of every volume. Given the folio-only style of a chapter
% opening: it is a page of the book, not a jacket flap.
\newcommand{\razabout}[1]{%
  \thispagestyle{razopen}%
  \chaptitle{About the Author}%
  \noindent #1\par}

% Contents. The heading is set exactly as a chapter title is, so the volume's
% first page of type does not introduce a second style of heading. \cftparskip
% governs the space between entries -- the body's half-line \parskip would
% otherwise apply to every line of a sixty-entry list.
\renewcommand*{\printtoctitle}[1]{\centering\Large\bfseries #1}
\renewcommand*{\aftertoctitle}{\par\vspace{1.1\baselineskip}}
% The running head over the contents, in the same case as every other head in
% the book; memoir's default sets it in capitals.
\renewcommand*{\tocmark}{\markboth{\contentsname}{\contentsname}}
\settocdepth{chapter}
\setlength{\cftparskip}{0pt}
\setlength{\cftbeforechapterskip}{0.15\baselineskip}
\setlength{\cftbeforepartskip}{1.2\baselineskip}
% Chapters are numbered across the whole work, so the widest number is 333.
\setlength{\cftchapternumwidth}{2.4em}
\renewcommand*{\cftchapterfont}{\normalfont}
\renewcommand*{\cftchapterpagefont}{\normalfont}
\renewcommand*{\cftchapterdotsep}{\cftdotsep}
\renewcommand*{\cftchapterleader}{\normalfont\cftdotfill{\cftchapterdotsep}}
\renewcommand*{\cftpartfont}{\bfseries}
\renewcommand*{\cftpartpagefont}{\bfseries}

% A display of data or pseudocode: fixed-width, ragged right (justifying a
% listing invents word spaces that are not in the data).
\newenvironment{fixedwidth}
  {\par\addvspace{0.6\baselineskip}\begingroup
   \ttfamily\small\raggedright\parskip=0pt\setlength{\parindent}{0pt}}
  {\par\endgroup\addvspace{0.6\baselineskip}}

% A displayed quotation, inset on both sides, its paragraphs separated the
% same way the body's are. The stock environment indents every paragraph after
% the first by 1.5em, which would be the only indentation left in the book.
\renewenvironment{quotation}
  {\list{}{\setlength{\listparindent}{0pt}%
           \setlength{\itemindent}{0pt}%
           \setlength{\rightmargin}{\leftmargin}%
           \setlength{\parsep}{\parskip}}%
   \item\relax}
  {\endlist}

% What an <hr> becomes: a short centred rule. memoir's \pfbreak hooks the
% output routine and shows its mark only when the break does not land on a
% page boundary, which is why most of these were printing as bare space.
\newcommand{\sectionbreak}{%
  \par\addvspace{0.8\baselineskip}%
  {\parskip=0pt\centering\rule{0.18\textwidth}{0.4pt}\par}%
  \addvspace{0.8\baselineskip}}

% A display goes into the vertical list as a plain centred box, with glue set
% here and TeX's interline glue suppressed on both sides. Interline glue is
% computed from the height of the box *below* it, so a tall formula pushes
% itself away from the line above while sitting normally against the line
% below -- which is the asymmetry, and it varies with the formula.
% \nointerlineskip removes that dependency; the two lengths then control the
% gap outright. \parskip is cancelled below because the following paragraph
% adds it back.
\newlength{\razdisplayabove}
\newlength{\razdisplaybelow}
\setlength{\razdisplayabove}{0.56\baselineskip}
\setlength{\razdisplaybelow}{0.63\baselineskip}
\newcommand{\razdisplay}[1]{%
  \par\addvspace{\razdisplayabove}\nointerlineskip
  \hbox to \linewidth{\hfil #1\hfil}%
  \nointerlineskip\vskip\razdisplaybelow\vskip-\parskip}

% Displayed maths sits in the same half-line gap as everything else. The
% stock skips are elastic and differ above and below, so a run of formulae
% ends up unevenly spaced; the surrounding \parskip already provides the air.
% Set at \begin{document}, because selecting a font size resets them.
\AtBeginDocument{%
  \setlength{\abovedisplayskip}{0pt}%
  \setlength{\belowdisplayskip}{0pt}%
  \setlength{\abovedisplayshortskip}{0pt}%
  \setlength{\belowdisplayshortskip}{0pt}}

% A note cited twice in one chapter is one note. The repeat is set from a
% reference to the first, so it carries whatever number LaTeX assigns.
\newcommand{\repeatnote}[1]{\textsuperscript{\ref{#1}}}

% Paragraphs are separated by space rather than by a first-line indent. Doing
% both is belt and braces; the half-line gap already marks the break.
\graphicspath{{../assets_pdf/}}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.5\baselineskip plus 1pt minus 1pt}
% Half a line means half a line. \flushbottom would meet the bottom margin
% by stretching every gap on a short page, which on a page of six
% paragraphs reads as double spacing.
\raggedbottom
% A dotted-underlined phrase is an unbreakable box; a line that ends on one
% can miss the margin by a fraction of a point with nowhere to give.
\setlength{\emergencystretch}{1em}

% Footnotes set flush to the margin: mark, thin space, text, and continuation
% lines under the mark rather than tabbed past it.
\footmarkstyle{\textsuperscript{#1}\,}
\setlength{\footmarkwidth}{0em}
\setlength{\footmarksep}{0em}
\setlength{\footparindent}{0em}
\renewcommand{\footnoterule}{\kern-3pt\hrule width 2in\kern 2.6pt}
\setlength{\skip\footins}{2\baselineskip}

% Footnotes run 1..n within each chapter and are shared by citations,
% external links and cross-references; the note's own wording says which it
% is. The counter is reset per chapter by \setcounter, not \counterwithin,
% because the sections are unnumbered and would number the notes "0.0.1".
\tightlists

%%URLDEFS%%

\begin{document}
\frenchspacing
"""


def chapter_tex(doc, variant, opts, report, recto=False, toc=False) -> str:
    r"""One chapter, opening on a fresh page.

    ``recto`` forces a right-hand page, for the pieces the reader looks up
    rather than reads through -- the back matter, and the first chapter after
    a part opener. ``toc`` records the entry, which has to happen after the
    page break so the number written is the chapter's own.
    """
    r = Renderer(doc, variant, opts, report)
    body = r.blocks(doc["blocks"], flush_first=True)
    orphaned = set(r.notes) - r.used_notes
    if orphaned:
        report["fn_body_unused"] += len(orphaned)
    title = esc(doc["title"])
    n = doc.get("number")
    head = [r"\cleartorecto" if recto else r"\clearpage",
            r"\thispagestyle{razopen}", r"\setcounter{footnote}{0}"]
    if toc:
        head.append(r"\addcontentsline{toc}{chapter}{\protect\numberline{%s}%s}"
                    % (n or "", title))
    if n:
        head.append(r"\chapnum{%d}" % n)
    head.append(r"\chaptitle{%s}" % title)
    head.append(r"\markboth{%s}{%s}" % (title, title))
    return "\n".join(head) + "\n\n" + body


def part_tex(entry) -> str:
    r"""A part opener on a recto, with the verso after it left blank.

    Two \cleartorecto, not one: the second skips the blank verso so that the
    part's first chapter opens on a right-hand page too. Chapters after that
    fall wherever they fall.
    """
    letter, title = entry["sequence_letter"], esc(entry["title"])
    return "\n".join([
        r"\cleartorecto",
        r"\razpart{%s}{%s}" % (letter, title),
        r"\addcontentsline{toc}{part}{Part %s: %s}" % (letter, title),
        r"\cleartorecto",
    ])


def volume_tex(book, entries, docs, variant, opts, report) -> str:
    r"""One of the six volumes, front matter to colophon.

    The order is the one the work itself has: title page, contents, the book's
    own introduction where it has one, then its parts. The glossary and the
    bibliography are the whole work's, not a volume's, so every volume carries
    a copy -- a reader holding Book IV cannot look a term up in Book I.

    Front matter is numbered in roman and the body restarts at 1, which is
    memoir's \frontmatter/\mainmatter and the reason the contents can be
    typeset before the pages it lists are numbered.
    """
    out = [r"\frontmatter",
           r"\razhalftitle{%s}{%s}" % (esc(book["title"]),
                                       BOOK_WORDS[book["number"]]),
           r"\cleartorecto",
           # Starred: the unstarred form lists the contents in the contents.
           r"\tableofcontents*",
           r"\mainmatter"]

    first_of_part = False
    for e in entries:
        if e["kind"] == "book":
            continue  # the title page has already said this
        if e["kind"] == "sequence":
            out.append(part_tex(e))
            first_of_part = True
            continue
        out.append(chapter_tex(docs[e["page"]], variant, opts, report,
                               recto=first_of_part, toc=True))
        first_of_part = False

    for page in ("Glossary", "Bibliography"):
        out.append(chapter_tex(docs[page], variant, opts, report,
                               recto=True, toc=True))
    out.append(r"\cleartorecto" + "\n" + r"\razabout{%s}" % esc(ABOUT_THE_AUTHOR))
    return "\n\n".join(out)


def document(body: str, bank: UrlBank) -> str:
    # The address definitions have to be written after the body, since
    # rendering is what discovers them, but read before -- \urldef only works
    # at the top level.
    return (PREAMBLE.replace("%%URLDEFS%%", bank.preamble())
            + body + "\n\\end{document}\n")


def latex(name: str, passes: int) -> None:
    for _ in range(passes):
        rc = subprocess.run(
            ["lualatex", "-interaction=nonstopmode", "-halt-on-error",
             f"{name}.tex"], cwd=TEX, capture_output=True, text=True)
    if rc.returncode != 0:
        print(f"  lualatex failed on {name}:")
        for l in [l for l in rc.stdout.splitlines() if l.startswith("!")][:6]:
            print("   ", l)
        sys.exit(1)
    print(f"  build/tex/{name}.pdf")


def convert_assets(report) -> None:
    """SVG sources become PDF, which LaTeX can place directly.

    The svg package shells out to Inkscape, which is not installed; rsvg-convert
    is, and doing the conversion here keeps it a reproducible build step rather
    than something lualatex needs --shell-escape for.
    """
    src = BUILD / "assets"
    if not src.exists():
        return
    ASSETS_PDF.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.iterdir()):
        out = ASSETS_PDF / (p.stem + ".pdf")
        if out.exists():
            continue
        if p.suffix.lower() == ".svg":
            if shutil.which("rsvg-convert") is None:
                report["svg_no_converter"] += 1
                continue
            rc = subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(out), str(p)],
                                capture_output=True)
            report["svg_converted" if rc.returncode == 0 else "svg_failed"] += 1
        else:
            shutil.copyfile(p, ASSETS_PDF / p.name)
            report["raster_copied"] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapters", help="comma-separated page names")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--volumes", action="store_true",
                    help="one document per book, with front and back matter")
    ap.add_argument("--only", type=int, help="with --volumes, just this book")
    ap.add_argument("--variant", default="A", choices=sorted(VARIANTS))
    ap.add_argument("--ornaments", action="store_true",
                    help="keep the ❦ fleuron (dropped by default)")
    ap.add_argument("--pdf", action="store_true", help="run lualatex")
    args = ap.parse_args()

    spine_entries = json.loads((BUILD / "toc.json").read_text())["entries"]
    spine = {e["page"]: e for e in spine_entries}
    rules = overrides.load()
    report = collections.Counter()
    convert_assets(report)

    wanted = ([e["page"] for e in spine_entries]
              if (args.all or args.volumes)
              else (args.chapters or "").split(","))
    docs = {}
    for page in [w for w in wanted if w]:
        matches = sorted((BUILD / "chapters").glob(f"*-{page}.json"))
        if not matches:
            sys.exit(f"no extracted chapter for {page!r}")
        doc = json.loads(matches[0].read_text())
        # Once per document, not once per rendering: 'remove' edits the text
        # around a dropped link, and the back matter is rendered six times.
        doc["blocks"] = apply_decisions(doc["blocks"], rules, report)
        doc["footnotes"] = apply_decisions(doc["footnotes"], rules, report)
        docs[page] = doc

    TEX.mkdir(parents=True, exist_ok=True)
    common = {"spine": spine, "ornaments": args.ornaments}

    if args.volumes:
        books = json.loads((BUILD / "toc.json").read_text())["books"]
        # "Biases: An Introduction" precedes Book I in the spine and carries no
        # book of its own; it is that volume's introduction.
        owner = {"frontmatter": 1}
        for book in books:
            if args.only and book["number"] != args.only:
                continue
            entries = [e for e in spine_entries
                       if owner.get(e["kind"], e.get("book")) == book["number"]]
            vreport = collections.Counter()
            opts = {**common, "bank": UrlBank()}
            name = "book-%d" % book["number"]
            (TEX / f"{name}.tex").write_text(
                document(volume_tex(book, entries, docs, args.variant,
                                    opts, vreport), opts["bank"]),
                encoding="utf-8")
            chapters = sum(1 for e in entries if e["kind"] == "chapter")
            print(f"build/tex/{name}.tex — Book {book['roman']}: {book['title']} "
                  f"({chapters} chapters, "
                  f"{sum(1 for e in entries if e['kind'] == 'sequence')} parts)")
            for k, v in sorted(vreport.items()):
                print(f"    {k}: {v}")
            if args.pdf:
                # Three passes: one to place the pages, one to write the
                # contents and resolve the \ref a repeated footnote is set
                # from, one to typeset a contents list that may have grown.
                latex(name, 3)
        return

    opts = {**common, "bank": UrlBank()}
    parts = [chapter_tex(docs[p], args.variant, opts, report)
             for p in wanted if p in docs]
    name = f"sample-{args.variant}"
    (TEX / f"{name}.tex").write_text(document("\n\n".join(parts), opts["bank"]),
                                     encoding="utf-8")
    print(f"build/tex/{name}.tex — {len(parts)} chapter(s), variant {args.variant} "
          f"({VARIANTS[args.variant]['blurb']})")
    for k, v in sorted(report.items()):
        print(f"    {k}: {v}")

    if args.pdf:
        latex(name, 2)


if __name__ == "__main__":
    main()
