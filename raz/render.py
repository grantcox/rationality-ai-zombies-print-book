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


def text_to_tex(text: str) -> str:
    r"""Escape running text, handing any bare URL to \url.

    An unbroken 90-character address in a footnote leaves TeX no break point
    and it stretches the line's word spaces instead; \url lets it break at
    punctuation. The skin's soft hyphens are stripped first, or they would
    print as hyphens a reader could mistake for part of the address.
    """
    out, last = [], 0
    for m in BARE_URL.finditer(text):
        url = m.group(0).rstrip(".,;:)")
        out.append(esc(text[last:m.start()]))
        out.append(r"\url{%s}" % url.replace("­", "").replace("​", ""))
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


class Renderer:
    def __init__(self, doc, variant, opts, report):
        self.doc = doc
        self.v = VARIANTS[variant]
        self.opts = opts
        self.report = report
        self.notes = {f["n"]: f for f in doc["footnotes"]}
        self.used_notes: set[int] = set()

    # -- footnote text -------------------------------------------------------

    def link_note(self, node) -> str:
        # \url takes the raw URL: it sets its own catcodes, so the text must
        # not be pre-escaped. The arrow is the literal glyph from EB Garamond
        # rather than a maths-font \rightarrow, so it matches the text.
        from .common import display_url
        return "→\\ \\url{%s}" % display_url(node["_url"])

    def xref_note(self, node) -> str:
        entry = self.opts["spine"].get(node["page"])
        if entry is None:
            self.report["xref_unresolved"] += 1
            return "see " + esc(node["page"])
        from .links import label
        return "see " + esc(label(entry))

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
            return text_to_tex(n["v"])
        if t == "br":
            return "\\\\\n"

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
                return r"\url{%s}" % plain_text(n).replace("­", "").replace("​", "")

            return self.marked(body, self.v["underline_link"]) + \
                r"\footnote{%s}" % self.link_note(n)

        if t == "xref":
            body = self.inline(n.get("c", []))
            if not self.v["xref_note"]:
                return body
            return self.marked(body, self.v["underline_xref"]) + \
                r"\footnote{%s}" % self.xref_note(n)

        if t == "fn":
            note = self.notes.get(n["n"])
            if note is None:
                self.report["fn_missing"] += 1
                return ""
            self.used_notes.add(n["n"])
            body = self.blocks(note["blocks"], inside_note=True).strip()
            return r"\footnote{%s}" % body

        if t == "img":
            return self.image(n)
        if t == "math":
            self.report["math_inline"] += 1
            return r"\textbf{[maths:~%s]}" % esc(n.get("text", ""))
        return self.inline(n.get("c", []))

    def image(self, n) -> str:
        name = Path(n["src"]).stem + ".pdf"
        if not (ASSETS_PDF / name).exists():
            self.report["image_missing"] += 1
            return r"\textbf{[missing image: %s]}" % esc(n["src"])
        return r"\includegraphics[max width=\linewidth]{%s}" % name

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
            if b.get("align") == "center":
                return r"\begin{center}%s\end{center}" % body
            if b.get("indent"):
                return r"\begin{quote}%s\end{quote}" % body
            return (r"\noindent " + body) if flush else body
        if t == "byline":
            return r"\begin{flushright}\emph{%s}\end{flushright}" % \
                self.inline(b.get("c", []))
        if t == "h":
            cmd = {2: r"\section*", 3: r"\subsection*", 4: r"\subsubsection*"}
            return "%s{%s}" % (cmd.get(b.get("level", 3), r"\subsection*"),
                               self.inline(b.get("c", [])))
        if t == "quote":
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
            return r"\pfbreak"
        if t == "pre":
            return "\\begin{verbatim}\n%s\n\\end{verbatim}" % b.get("v", "")
        if t == "math_block":
            self.report["math_block"] += 1
            return r"\begin{center}\textbf{[maths block:~%s]}\end{center}" % \
                esc(b.get("text", ""))
        self.report[f"block_unhandled_{t}"] += 1
        return ""

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

% The chapter number, set above the title as its own line.
\newcommand{\chapnum}[1]{%
  {\textcolor[gray]{0.55}{\large\textperiodcentered\;#1\;\textperiodcentered}}\par
  \vspace{0.4\baselineskip}}

\graphicspath{{../assets_pdf/}}
\setlength{\parindent}{1.2em}
\renewcommand{\footnoterule}{\kern-3pt\hrule width 2in\kern 2.6pt}
\setlength{\skip\footins}{2\baselineskip}

% Footnotes run 1..n within each chapter and are shared by citations,
% external links and cross-references; the note's own wording says which it
% is. The counter is reset per chapter by \setcounter, not \counterwithin,
% because the sections are unnumbered and would number the notes "0.0.1".
\tightlists

\begin{document}
\frenchspacing
"""


def chapter_tex(doc, variant, opts, report) -> str:
    r = Renderer(doc, variant, opts, report)
    body = r.blocks(doc["blocks"], flush_first=True)
    orphaned = set(r.notes) - r.used_notes
    if orphaned:
        report["fn_body_unused"] += len(orphaned)
    head = [r"\thispagestyle{razopen}", r"\setcounter{footnote}{0}"]
    n = doc.get("number")
    if n:
        head.append(r"\chapnum{%d}" % n)
    head.append(r"\section*{%s}" % esc(doc["title"]))
    head.append(r"\markboth{%s}{%s}" % (esc(doc["title"]), esc(doc["title"])))
    return "\n".join(head) + "\n\n" + body


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

    wanted = ([e["page"] for e in spine_entries] if args.all
              else (args.chapters or "").split(","))
    docs = []
    for page in [w for w in wanted if w]:
        matches = sorted((BUILD / "chapters").glob(f"*-{page}.json"))
        if not matches:
            sys.exit(f"no extracted chapter for {page!r}")
        doc = json.loads(matches[0].read_text())
        doc["blocks"] = apply_decisions(doc["blocks"], rules, report)
        doc["footnotes"] = apply_decisions(doc["footnotes"], rules, report)
        docs.append(doc)

    opts = {"spine": spine, "ornaments": args.ornaments}
    parts = [chapter_tex(d, args.variant, opts, report) for d in docs]
    tex = PREAMBLE + "\n\n\\clearpage\n\n".join(parts) + "\n\\end{document}\n"

    TEX.mkdir(parents=True, exist_ok=True)
    name = f"sample-{args.variant}"
    (TEX / f"{name}.tex").write_text(tex, encoding="utf-8")
    print(f"build/tex/{name}.tex — {len(docs)} chapter(s), variant {args.variant} "
          f"({VARIANTS[args.variant]['blurb']})")
    for k, v in sorted(report.items()):
        print(f"    {k}: {v}")

    if args.pdf:
        for _ in range(2):
            rc = subprocess.run(
                ["lualatex", "-interaction=nonstopmode", "-halt-on-error",
                 f"{name}.tex"], cwd=TEX, capture_output=True, text=True)
        if rc.returncode != 0:
            tail = [l for l in rc.stdout.splitlines() if l.startswith("!")][:6]
            print("  lualatex failed:")
            for l in tail:
                print("   ", l)
            sys.exit(1)
        print(f"  build/tex/{name}.pdf")


if __name__ == "__main__":
    main()
