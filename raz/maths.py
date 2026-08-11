"""Step 4a — the book's HTML-built mathematics, converted to LaTeX.

readthesequences carries no MathML and no TeX. Every expression is assembled
out of ``<em>``, ``<sub>``, ``<sup>`` and a few styled spans, and laid out with
tables when it needs more than one line.

That markup carries the one distinction this conversion turns on: **``<em>``
marks a variable**. So the split between an italic symbol and an upright name
-- *P*(cancer), not *P*(*cancer*) -- comes from the source rather than from a
guess about which letters are symbols. Bare text is a name or prose and is set
upright; text inside ``<em>`` is a variable and is set in maths italic.

Nothing is guessed at. A tag, class or character with no rule here is counted
and printed as a visible marker rather than dropped, and ``check`` confirms
that every letter and digit of the source expression survived into the LaTeX
-- the same discipline raz.verify applies to the prose.

Run:  python -m raz.maths        # convert every expression and report
"""

from __future__ import annotations

import collections
import glob
import json
import re
import sys

from bs4 import BeautifulSoup, NavigableString, Tag

from .common import BUILD

# --- the symbol table ---------------------------------------------------------

#: Every non-ASCII character that occurs inside an expression, and its maths
#: equivalent. Closed set: anything outside it is reported, not guessed.
SYMBOL = {
    "¬": r"\lnot ", "×": r"\times ", "−": "-", "≠": r"\neq ",
    "≈": r"\approx ", "≤": r"\leq ", "≥": r"\geq ", "⇒": r"\Rightarrow ",
    "→": r"\to ", "←": r"\leftarrow ", "Σ": r"\sum ", "∆": r"\Delta ",
    "…": r"\dots ", "⋮": r"\vdots ", "⁄": "/", "“": r"\text{“}",
    "”": r"\text{”}", "’": r"\text{’}",
}

#: The skin's spacing characters. Maths mode ignores ordinary spaces, so the
#: ones the author put in deliberately have to be restated as maths spaces.
SPACE = {
    "\u2004": r"\;", "\u2009": r"\,", "\u200a": r"\,", "\xa0": r"\ ",
    "\ufeff": "", "\u200b": "",
}

#: Characters that mean in maths what they mean in the source.
PASS = set("0123456789()[]+-=<>/*!?:;,.'|")

#: In LaTeX these are commands, not characters.
ESCAPE = {"%": r"\%", "$": r"\$", "&": r"\&", "#": r"\#",
          "{": r"\{", "}": r"\}", "_": r"\_"}

#: Named operators. Set upright with their own spacing, which is what the
#: source is reaching for when it leaves them outside <em>.
OPERATORS = {"log": r"\log", "ln": r"\ln", "sin": r"\sin", "cos": r"\cos"}

#: A run of letters -- a name or a phrase, not a symbol. Underscores and inner
#: spaces are part of the run, so ``Squared_Modulus`` and ``total probability
#: of all the different ways`` each stay one upright word or phrase instead of
#: being shattered into letters.
WORD = re.compile(r" ?[A-Za-z][A-Za-z _]*[A-Za-z] ?| ?[A-Za-z] ?")

#: The two images used as oversized brackets.
BRACKETS = {"big_left_parens.svg": r"\left(", "big_right_parens.svg": r"\right)"}


def marker(what: str) -> str:
    r"""What an unreadable construct prints as: loud, and impossible to miss
    in a proof, because the alternative is a silently wrong equation."""
    return r"\mathbf{[?~%s]}" % re.sub(r"[^A-Za-z0-9 ]", "", what).replace(" ", "~")


# --- inline conversion --------------------------------------------------------


def symbols(text: str, rep) -> str:
    """Everything that is not a letter run. Letters are handled by text_run."""
    out = []
    for c in text:
        if c in SYMBOL:
            out.append(SYMBOL[c])
        elif c in SPACE:
            out.append(SPACE[c])
        elif c in ESCAPE:
            out.append(ESCAPE[c])
        elif c in PASS:
            out.append(c)
        elif c.isspace():
            out.append(" ")
        else:
            rep[f"unknown_char.{c}"] += 1
            out.append(marker("char"))
    return "".join(out)


#: How long a letter run has to be before it reads as a name rather than as
#: variables written side by side. Inside <em> the corpus holds 1,293
#: single-letter runs and exactly four longer ones: 'colors' three times, as a
#: summation index, and one 'Si' the source meant as S-sub-i.
NAME_LENGTH = 3


def text_run(text: str, rep, variable: bool = False) -> str:
    r"""One text node.

    Outside ``<em>`` every letter run is a name or prose and is set upright.
    Inside it, a short run is a variable and needs no \text -- maths mode sets
    letters italic already -- while a longer run is still a name.
    """
    out, last = [], 0
    for m in WORD.finditer(text):
        out.append(symbols(text[last:m.start()], rep))
        run, core = m.group(0), m.group(0).strip()
        if core in OPERATORS:
            out.append(OPERATORS[core])
        elif variable and len(core) < NAME_LENGTH:
            out.append(core)
        else:
            if variable:
                rep[f"name_inside_em.{core}"] += 1
            out.append(r"\text{%s}" % run.replace("_", r"\_"))
        last = m.end()
    out.append(symbols(text[last:], rep))
    return "".join(out)


#: Maths spacing left stranded at the edge of a subscript by the source's own
#: layout spaces -- ``log<sub> 2</sub>`` should not set as log with a padded 2.
EDGE = re.compile(r"^(?:\\[,;:!]|\\ |\s)+|(?:\\[,;:!]|\\ |\s)+$")


def tidy(tex: str) -> str:
    return EDGE.sub("", tex)


def expr(node, rep, variable: bool = False) -> str:
    return "".join(item(ch, rep, variable) for ch in node.children)


def item(ch, rep, variable: bool = False) -> str:
    if isinstance(ch, NavigableString):
        return text_run(str(ch), rep, variable)
    if not isinstance(ch, Tag):
        return ""

    cls = ch.get("class") or []
    name = ch.name

    if name in ("em", "i"):
        # A variable, and everything nested inside it -- a subscript on a
        # variable is a variable too.
        return expr(ch, rep, variable=True)
    if name == "sub":
        inner = tidy(expr(ch, rep, variable))
        return "_{%s}" % inner if inner else ""
    if name == "sup":
        inner = tidy(expr(ch, rep, variable))
        return "^{%s}" % inner if inner else ""
    if name == "br":
        return "\\\\\n"
    if name == "strong":
        return r"\mathbf{%s}" % expr(ch, rep, variable)

    if name == "img":
        src = (ch.get("src") or "").rsplit("/", 1)[-1]
        if src in BRACKETS:
            return BRACKETS[src]
        rep[f"unknown_image.{src}"] += 1
        return marker("image")

    if name == "span":
        if "fraction" in cls:
            return fraction(ch.get_text(), rep)
        if "bigsigma" in cls or "sigma" in cls:
            return bigsigma(ch, rep)
        if {"num", "denom", "frasl"} & set(cls):
            # Only reachable if extract's splice missed a triple.
            rep["loose_fraction_part"] += 1
            return expr(ch, rep, variable)
        if "equation" in cls or not cls:
            return expr(ch, rep, variable)
        rep[f"unknown_span.{'.'.join(cls)}"] += 1
        return expr(ch, rep, variable)

    rep[f"unknown_tag.{name}"] += 1
    return expr(ch, rep, variable)


#: Either slash: the skin's .fraction spans use an ASCII one, the hand-built
#: fractions in Zut Allais the true fraction slash U+2044.
FRACTION = re.compile("^\\s*([0-9][0-9,]*)\\s*[/\u2044]\\s*([0-9][0-9,]*)\\s*$")


def fraction(text: str, rep) -> str:
    r"""A diagonal fraction.

    The skin sets these with the OpenType ``frac`` feature -- raised numerator,
    fraction slash, lowered denominator -- which is what \sfrac produces. A
    stacked \frac would be wrong: these sit in running text, and 1/131,115,985
    stacked would be two lines tall in the middle of a sentence.
    """
    m = FRACTION.match(text)
    if not m:
        rep[f"unknown_fraction.{text.strip()[:24]}"] += 1
        return marker("fraction")
    num, den = (g.replace(",", "{,}") for g in m.groups())
    return r"\sfrac{%s}{%s}" % (num, den)


def bigsigma(node, rep) -> str:
    """A summation sign with its index below it."""
    sub = node.find("sub")
    index = expr(sub, rep).strip() if sub else ""
    return r"\sum_{%s}" % index if index else r"\sum"


# --- displays -----------------------------------------------------------------


def display(body: str) -> str:
    r"""A display, set as an ordinary centred paragraph.

    LaTeX's display machinery -- \[ \], align, gather -- fakes a preceding
    line with \makebox[.6\linewidth]{} whenever a display starts in vertical
    mode, so TeX's \predisplaysize logic has a line to measure. Where the
    display *is* its own paragraph, which is how every one in this book is
    built, that phantom prints as close to a blank line above the formula and
    nothing below it: measured in the book, 21.8pt above against 6.7pt below,
    where every other gap on the page is 5.8pt.

    The box forms of the same environments (aligned, gathered) carry none of
    that machinery, so the display can be placed as a plain centred box with
    glue this file controls -- see \razdisplay in render.py. They cannot break
    across a page, which is no constraint here: the tallest display in the
    book is seven rows.
    """
    return r"\razdisplay{$\displaystyle %s$}" % body


def cell_text(td) -> str:
    return td.get_text(" ", strip=True)


def table(node, rep) -> str:
    r"""A multi-line derivation.

    The layout is three columns -- left side, relation, right side -- which is
    what \align exists for. Two wrinkles:

      * a fraction is built by putting the numerator in one row and the
        denominator in the next, with the relation and the right-hand side
        spanning both; those two source rows are one line of maths
      * a leading empty column is used purely to indent, and carries nothing
    """
    rows = [tr.find_all("td", recursive=False) for tr in node.find_all("tr")]
    if "spaced_table" in (node.get("class") or []):
        return grid(rows, rep)

    lines, i = [], 0
    while i < len(rows):
        cells = [c for c in rows[i] if cell_text(c)]
        nums = [c for c in cells if "numerator" in classes(c)]
        merged = {}
        if nums and i + 1 < len(rows):
            dens = [c for c in rows[i + 1] if "denominator" in classes(c)]
            # A line can carry several fractions side by side -- Bayes's
            # theorem in odds form is three of them -- and they pair off left
            # to right with the denominators on the row below.
            if len(dens) == len(nums):
                for num, den in zip(nums, dens):
                    merged[id(num)] = build_fraction(num, den, rep)
                i += 1
            elif not dens and len(nums) == 1:
                rep["fraction_over_unmarked_rows"] += 1
                merged[id(nums[0])] = r"\frac{%s}{%s}" % (
                    tidy(expr(nums[0], rep)), spanned(rows[i + 1:], rep))
                i = len(rows)
            else:
                rep["fraction_rows_unpaired"] += 1
        lines.append(align_row(cells, rep, merged))
        i += 1
    body = " \\\\\n".join(l for l in lines if l.strip(" &"))
    return display("\\begin{aligned}\n%s\n\\end{aligned}" % body)


def classes(cell) -> list[str]:
    return cell.get("class") or []


#: A sentence's punctuation, sitting at the end of a denominator cell.
TAIL = re.compile(r"\s*([.,;])$")


def build_fraction(num, den, rep) -> str:
    r"""\frac, with the sentence's punctuation kept out of it.

    The source ends several denominators with the full stop that closes the
    sentence: a table-drawn fraction has nowhere else to put it. A real \frac
    does -- leaving it under the bar would read as part of the denominator.
    """
    body = tidy(expr(den, rep))
    m = TAIL.search(body)
    tail = ""
    if m:
        body, tail = body[: m.start()], m.group(1)
        rep["punctuation_lifted_out_of_denominator"] += 1
    return r"\frac{%s}{%s}%s" % (tidy(expr(num, rep)), body, tail)


def spanned(rows, rep) -> str:
    r"""The denominator when the source never marks one.

    One fraction in *An Intuitive Explanation of Bayes's Theorem* puts a
    two-line sum under the bar, drawn with oversized bracket images in cells
    that span both rows. Read in document order the closing bracket lands
    after the first line, so a cell that spans every remaining row is hoisted
    out to the side it sits on -- which is how it reads on the page.
    """
    if not rows:
        return ""
    depth = len(rows)
    lead, middle, trail = [], [], []
    seen_inline = False
    for c in rows[0]:
        if int(c.get("rowspan") or 1) >= depth:
            (trail if seen_inline else lead).append(c)
        else:
            seen_inline = True
            middle.append(c)
    for r in rows[1:]:
        middle.extend(r)
    parts = [tidy(expr(c, rep)) for c in lead + middle + trail]
    return " ".join(p for p in parts if p)


def align_row(cells, rep, merged: dict) -> str:
    """One line of an alignment.

    The first relation is the alignment point and takes the ``&``; a later one
    -- a line can read *a* = *b* × *c* -- stays where it stands. A line with no
    relation still sets, just unaligned, and is reported.
    """
    parts, aligned = [], False
    for c in cells:
        tex = merged.get(id(c)) or tidy(expr(c, rep))
        if not tex:
            continue
        if "equal_sign" in classes(c) and not aligned:
            aligned = True
            parts.append("&" + tex)
        else:
            parts.append(tex)
    if not aligned:
        rep["align_row_without_relation"] += 1
    return " ".join(parts)


def grid(rows, rep) -> str:
    """A plain grid of values -- a table of results, not a derivation."""
    width = max(len(r) for r in rows)
    body = " \\\\\n".join(
        " & ".join(tidy(expr(c, rep)) for c in r) + " &" * (width - len(r))
        for r in rows)
    return display("\\begin{array}{%s}\n%s\n\\end{array}" % ("l" * width, body))


def paragraph(node, rep) -> str:
    body = expr(node, rep).strip()
    if "\\\\" in body:
        # Several lines: the author broke a long sum by hand.
        return display("\\begin{gathered}\n%s\n\\end{gathered}" % body)
    return display(body)


# --- entry point --------------------------------------------------------------


def convert(node: dict, rep) -> str:
    """LaTeX for one 'math' or 'math_block' node."""
    soup = BeautifulSoup(node["html"], "lxml")
    root = soup.find(["span", "p", "table"])
    if root is None:
        rep["unreadable_node"] += 1
        return marker("expression")

    if node["t"] == "math":
        # item(), not expr(): the class that says what this is sits on the
        # root itself, and descending straight into its children would read a
        # top-level <span class="fraction"> as if it were plain text.
        return "$%s$" % tidy(item(root, rep))
    if root.name == "table":
        return table(root, rep)
    if root.name == "p":
        return paragraph(root, rep)
    return display(expr(root, rep).strip())


# --- verification -------------------------------------------------------------

#: \begin{align*} carries its environment name as ordinary letters, which
#: would read as content; it goes first.
ENVIRONMENT = re.compile(r"\\(?:begin|end)\{[^}]*\}(?:\{[^}]*\})?")
STRIP = re.compile(r"\\[A-Za-z]+|[{}$&\\~^_]")
#: Commands that stand for words rather than for symbols. Stripping these
#: like any other command would delete the very letters being checked for.
NAMED = re.compile(r"\\(log|ln|sin|cos)(?![A-Za-z])")


def check(source_text: str, tex: str) -> tuple[str, str]:
    """Letters and digits in, letters and digits out.

    Symbols become commands and cannot be compared this way, but a dropped
    word or a lost digit is the failure that would actually change what an
    equation says, and this catches it.
    """
    # ASCII only: a symbol like the summation sign is a command in the
    # LaTeX and a letter in the source, and is covered by SYMBOL being a
    # closed set rather than by this check.
    keep = lambda s: "".join(sorted(c for c in s if c.isalnum() and c.isascii()))
    tex = STRIP.sub(" ", NAMED.sub(r"\1", ENVIRONMENT.sub(" ", tex)))
    return keep(source_text), keep(tex)


def main() -> int:
    from .extract import walk

    rep = collections.Counter()
    bad, total = [], 0
    for f in sorted(glob.glob(str(BUILD / "chapters" / "*.json"))):
        doc = json.loads(open(f, encoding="utf-8").read())
        for n in walk(doc["blocks"]) + walk(doc["footnotes"]):
            if n.get("t") not in ("math", "math_block"):
                continue
            total += 1
            tex = convert(n, rep)
            want, got = check(n["text"], tex)
            if want != got:
                bad.append((doc["page"], n["text"][:70], tex[:70]))

    print(f"{total} expressions converted; {len(bad)} lost characters")
    for page, src, tex in bad[:15]:
        print(f"    {page}\n      source: {src}\n      latex : {tex}")
    unknown = {k: v for k, v in rep.items() if k.startswith("unknown")}
    if unknown:
        print("  constructs with no rule (printed as visible markers):")
        for k, v in sorted(unknown.items(), key=lambda kv: -kv[1]):
            print(f"    {v:5d}  {k}")
    other = {k: v for k, v in rep.items() if not k.startswith("unknown")}
    for k, v in sorted(other.items()):
        print(f"    {k}: {v}")
    return 1 if bad or unknown else 0


if __name__ == "__main__":
    sys.exit(main())
