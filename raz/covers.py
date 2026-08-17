"""Step 5 — the printed cover for each volume.

One PDF per book, laid out back cover | spine | front cover across a single
sheet, with the artwork running full bleed underneath the lot. The back cover
carries the book's own blurb, taken from the mirror rather than retyped: every
book page on readthesequences.com opens with Rob Bensinger's paragraph from
"Biases: An Introduction".

The spine gets wider as the volume gets thicker, so each book's sheet is a
different width; see GEOMETRY below for where the numbers come from.

Run:  python -m raz.covers --pdf
      python -m raz.covers --pdf --style serif --guides
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup

from .common import BUILD, REPO, mirror_path
from .render import esc

COVERS = BUILD / "covers"
ART = REPO / "covers"

# --- geometry ----------------------------------------------------------------
#
# The printer quotes, for Book I: exported PDF 13.819 x 9.861, trim 13.207 x
# 9.249, bleed 0.306 all round. Everything else follows from those three.
#
# The trim is 0.249in taller than the 9in page, which is the case boards
# standing 0.1245in proud of the text block at head and tail. The same
# overhang at the fore-edge makes each panel 6.1245in wide, and what is left
# of the trim width once two panels are taken out is the spine. That gives
# 0.957in for Book I and steps of an eighth of an inch from there, which is
# exactly the pattern in the widths the printer quoted for the other five.

BLEED = 0.306
TRIM_H = 9.249
PANEL_W = 6.1245

#: Exported PDF width per book, as quoted. The only per-book input.
FINAL_W = {1: 13.819, 2: 14.069, 3: 13.944, 4: 14.319, 5: 13.819, 6: 13.819}

#: Clear of the trim by this much before setting anything. Board edges are
#: wrapped and folded, so the outer edges want more room than a paperback's.
SAFE = 0.55

#: And clear of the spine by this much: the hinge is creased, and type in it
#: disappears into the groove.
HINGE = 0.42

#: The mirror page that carries each book's blurb.
BLURB_PAGE = {
    1: "Book-I-Map-And-Territory",
    2: "Book-II-How-To-Actually-Change-Your-Mind",
    3: "Book-III-The-Machine-In-The-Ghost",
    4: "Book-IV-Mere-Reality",
    5: "Book-V-Mere-Goodness",
    6: "Book-VI-Becoming-Stronger",
}

SUBTITLE = "Rationality: From AI to Zombies"
AUTHOR = "Eliezer Yudkowsky"
SPINE_AUTHOR = "Yudkowsky"


# --- the blurb ---------------------------------------------------------------

def inline(node) -> str:
    """A blockquote's inline markup, as LaTeX. Only emphasis survives."""
    if isinstance(node, str):
        return esc(node)
    inner = "".join(inline(c) for c in node.children)
    if node.name in ("em", "i"):
        return r"\emph{%s}" % inner
    return inner


def blurb(book: int) -> tuple[str, str]:
    """The back cover text and its credit, from the book's own page.

    The site signs the paragraph "—Rob Bensinger, Biases: An Introduction".
    The chapter it came from means nothing on a jacket, so only the name is
    kept.
    """
    html = mirror_path(BLURB_PAGE[book]).read_text(encoding="utf-8", errors="replace")
    quote = BeautifulSoup(html, "lxml").find(id="wikitext").find("blockquote")
    if quote is None:
        sys.exit(f"no blockquote on {BLURB_PAGE[book]}")
    body, credit = [], ""
    for p in quote.find_all("p", recursive=False):
        if "blockquote_byline" in (p.get("class") or []):
            credit = p.get_text(" ", strip=True).split(",")[0].strip()
        else:
            body.append(inline(p))
    return "\n\n".join(body), esc(credit)


# --- the sheet ---------------------------------------------------------------

PREAMBLE = r"""\documentclass[11pt]{article}
\usepackage[paperwidth=%%W%%in,paperheight=%%H%%in,margin=0pt]{geometry}
\usepackage{fontspec}
\usepackage{graphicx}
\usepackage{tikz}
\usetikzlibrary{fadings}
\pagestyle{empty}
\frenchspacing   % as the interior is set
\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}
\setlength{\topskip}{0pt}
\special{papersize=%%W%%in,%%H%%in}

% The interior's face, for the blurb, and a geometric sans for the display
% lines. Montserrat has a wide, even upper case, which is what all-caps
% setting at this size depends on.
%
% WordSpace doubles the gap between words. The display lines are letterspaced,
% and letterspacing pushes every pair of letters apart without touching the
% word space, so at the tracking these lines want, Montserrat's narrow 0.26em
% space leaves "MERE REALITY" reading as one word. 4.0 is too much: it opens
% the four gaps in the subtitle far enough to break the line into pieces.
\setmainfont{EBGaramond}[
  Extension = .otf,
  UprightFont = *-Regular, ItalicFont = *-Italic,
  BoldFont = *-Bold, BoldItalicFont = *-BoldItalic,
  Ligatures = TeX ]
\newfontfamily\displayface{Montserrat}[
  Extension = .otf,
  UprightFont = *-%%SANSWEIGHT%%, ItalicFont = *-Italic,
  WordSpace = 1.7,
  Ligatures = TeX ]
\newfontfamily\displaytext{Montserrat}[
  Extension = .otf,
  UprightFont = *-Regular, ItalicFont = *-Italic,
  WordSpace = 1.7,
  Ligatures = TeX ]

% Opaque where the gradient starts, invisible where it ends. transparent!0 is
% no transparency at all, which reads backwards until you have said it once.
\tikzfading[name=downwards, top color=transparent!0, bottom color=transparent!100]
\tikzfading[name=upwards, top color=transparent!100, bottom color=transparent!0]
\tikzfading[name=blob, inner color=transparent!0, outer color=transparent!100]

\begin{document}%
\begin{tikzpicture}[x=1in, y=1in, inner sep=0pt, outer sep=0pt]
\useasboundingbox (0,0) rectangle (%%W%%,%%H%%);
\clip (0,0) rectangle (%%W%%,%%H%%);
"""


def sheet(book: int, image: Path, style: str, guides: bool) -> str:
    w = FINAL_W[book]
    h = TRIM_H + 2 * BLEED
    spine = w - 2 * BLEED - 2 * PANEL_W

    # Trim edges, then the two folds, left to right.
    x0, x1 = BLEED, w - BLEED
    y0, y1 = BLEED, h - BLEED
    fold_a = x0 + PANEL_W
    fold_b = fold_a + spine
    back_c, spine_c, front_c = x0 + PANEL_W / 2, fold_a + spine / 2, fold_b + PANEL_W / 2

    title, quote, credit = TITLES[book], *blurb(book)
    caps = style == "sans"

    def display(text, size, leading, weight=r"\displayface", track=4.0,
                block=False):
        r"""A line of the display face, upper case and tracked out if sans.

        ``block`` closes the paragraph inside the group, which is where it has
        to happen: the leading of a paragraph's lines is whatever
        ``\baselineskip`` holds when the ``\par`` runs, so a group that ends
        first hands its lines back to the 11pt body leading.
        """
        end = r"\par" if block else ""
        if not caps:
            return r"{\fontsize{%g}{%g}\selectfont %s%s}" % (size, leading, text, end)
        return (r"{%s\addfontfeature{LetterSpace=%g}"
                r"\fontsize{%g}{%g}\selectfont\MakeUppercase{%s}%s}"
                % (weight, track, size, leading, text, end))

    p = [PREAMBLE.replace("%%W%%", "%.4f" % w).replace("%%H%%", "%.4f" % h)
         .replace("%%SANSWEIGHT%%", "SemiBold" if caps else "Medium")]
    a = p.append

    # The artwork, sized to the sheet's height and hung off the right edge, so
    # that the part of it the front cover shows is the part that was composed
    # to be looked at. Everything past the left trim is clipped away.
    a(r"\node[anchor=south east] at (%.4f,0) {\includegraphics[height=%.4fin]{%s}};"
      % (w, h, image.as_posix()))

    # Scrims. The artwork is not ours to choose and the next one may be pale
    # where this one is dark, so the type carries its own ground: a wash over
    # the whole sheet, gradients into the head and foot, and a soft pool
    # behind each block of type.
    #
    # The gradients run the full width of the sheet rather than the width of
    # the front panel. Stopping one at a fold puts a hard vertical edge down
    # the cover, which is the one artefact of all this that the eye does pick
    # out.
    a(r"\fill[black, opacity=0.20] (0,0) rectangle (%.4f,%.4f);" % (w, h))
    a(r"\fill[black, opacity=0.50, path fading=downwards] "
      r"(0,%.4f) rectangle (%.4f,%.4f);" % (h - 4.6, w, h))
    a(r"\fill[black, opacity=0.42, path fading=upwards] "
      r"(0,0) rectangle (%.4f,%.4f);" % (w, 2.9))
    a(r"\fill[black, opacity=0.34, path fading=blob] "
      r"(%.4f,%.4f) ellipse (%.4f and %.4f);" % (front_c, y1 - 1.7, 3.7, 2.3))
    a(r"\fill[black, opacity=0.45, path fading=blob] "
      r"(%.4f,%.4f) ellipse (%.4f and %.4f);"
      % (back_c, y1 - 3.0, PANEL_W / 2 + 0.5, 3.0))
    a(r"\fill[black, opacity=0.30, path fading=blob] "
      r"(%.4f,%.4f) ellipse (%.4f and %.4f);" % (spine_c, h / 2, spine * 0.9, 5.6))

    # --- front cover ---------------------------------------------------------
    # Title, series and volume as one node, so the three sit as a unit however
    # many lines the title takes. Top-anchored: the titles then start at the
    # same height across all six, which matters more on a shelf than the
    # subtitle's own line landing in the same place.
    #
    # "flush center", not "center". TikZ's plain centre is centred *and*
    # justified -- it leaves the interword glue free to stretch and shrink --
    # and on a one-line display it will squeeze the whole of WordSpace back
    # out again. flush center is LaTeX's \centering, which holds the space at
    # its natural width and ends the lines ragged, as display type wants.
    measure = PANEL_W - 2 * SAFE
    a(r"\node[anchor=north, text=white, text width=%.4fin, align=flush center] "
      r"at (%.4f,%.4f) {%s\vspace{0.24in}%s%s};"
      % (measure, front_c, y1 - 0.82,
         display(esc(title), 25 if caps else 29, 32 if caps else 34,
                 track=5.0, block=True),
         display(esc(SUBTITLE), 15.5, 21, weight=r"\displaytext", block=True),
         display("Book %s" % ROMAN[book], 15.5, 21, weight=r"\displaytext",
                 block=True)))
    a(r"\node[anchor=south, text=white, text width=%.4fin, align=flush center] "
      r"at (%.4f,%.4f) {%s};"
      % (measure, front_c, y0 + 0.85,
         display(esc(AUTHOR), 22, 26, track=5.0, block=True)))

    # --- spine ---------------------------------------------------------------
    # Rotated a quarter turn clockwise, so the lettering reads downwards when
    # the book is lying face up.
    #
    # One box the length of the spine's run with the three items sprung apart
    # inside it, rather than three separately anchored ones: "How to Actually
    # Change Your Mind" is long enough to reach the middle of the spine and
    # collide with a series mark pinned there. Sprung, the gaps can only close
    # up, never cross. They still have to stay open, so the size is set from
    # how much lettering there is -- em is measured off a proof, and only the
    # ratio between the two faces matters.
    mark = "R:AZ %s" % ROMAN[book]
    run = TRIM_H - 2 * 0.62
    em = 0.78 if caps else 0.46
    spine_pt = max(9.5, min(13.5, 72 * run * 0.80
                            / (em * len(title + mark + SPINE_AUTHOR))))
    a(r"\node[rotate=-90, anchor=center, text=white] at (%.4f,%.4f) "
      r"{\hbox to %.4fin{%s\hfil %s\hfil %s}};"
      % (spine_c, h / 2, run,
         display(esc(title), spine_pt, spine_pt * 1.2, track=4.0),
         display(mark, spine_pt, spine_pt * 1.2, track=4.0),
         display(esc(SPINE_AUTHOR), spine_pt, spine_pt * 1.2, track=4.0)))

    # --- back cover ----------------------------------------------------------
    # Justified, and a good deal smaller than the front: it is read at arm's
    # length rather than across a room.
    a(r"\node[anchor=north west, text=white, text width=%.4fin, align=justify] "
      r"at (%.4f,%.4f) {\fontsize{12}{16.3}\selectfont %s\par"
      r"\vspace{1.1\baselineskip}{\raggedleft\itshape %s\par}};"
      % (PANEL_W - SAFE - HINGE, x0 + SAFE, y1 - 2.05, quote, credit))

    if guides:
        a(r"\draw[cyan, line width=0.4pt] (%.4f,%.4f) rectangle (%.4f,%.4f);"
          % (x0, y0, x1, y1))
        for x in (fold_a, fold_b):
            a(r"\draw[magenta, line width=0.4pt] (%.4f,%.4f) -- (%.4f,%.4f);"
              % (x, y0, x, y1))
        a(r"\draw[yellow, dashed, line width=0.4pt] (%.4f,%.4f) rectangle (%.4f,%.4f);"
          % (x0 + SAFE, y0 + SAFE, x1 - SAFE, y1 - SAFE))

    a(r"\end{tikzpicture}%")
    a(r"\end{document}")
    return "\n".join(p) + "\n"


TITLES = {
    1: "Map and Territory",
    2: "How to Actually Change Your Mind",
    3: "The Machine in the Ghost",
    4: "Mere Reality",
    5: "Mere Goodness",
    6: "Becoming Stronger",
}
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI"}


def latex(name: str) -> bool:
    rc = subprocess.run(
        ["lualatex", "-interaction=nonstopmode", "-halt-on-error", f"{name}.tex"],
        cwd=COVERS, capture_output=True, text=True)
    if rc.returncode != 0:
        print(f"  lualatex failed on {name}:")
        for l in [l for l in rc.stdout.splitlines() if l.startswith("!")][:8]:
            print("   ", l)
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, help="just this book")
    ap.add_argument("--style", default="sans", choices=("sans", "serif"),
                    help="sans: front and spine in tracked upper-case Montserrat")
    ap.add_argument("--guides", action="store_true",
                    help="draw trim, fold and safe-area lines for proofing")
    ap.add_argument("--pdf", action="store_true", help="run lualatex")
    ap.add_argument("--png", action="store_true", help="also write a preview")
    args = ap.parse_args()

    COVERS.mkdir(parents=True, exist_ok=True)
    for book in sorted(FINAL_W):
        if args.only and book != args.only:
            continue
        image = ART / f"cover{book}.jpg"
        if not image.exists():
            sys.exit(f"no artwork at {image}")
        name = f"cover-book-{book}-{args.style}"
        (COVERS / f"{name}.tex").write_text(
            sheet(book, image, args.style, args.guides), encoding="utf-8")
        spine = FINAL_W[book] - 2 * BLEED - 2 * PANEL_W
        print(f"build/covers/{name}.tex — Book {ROMAN[book]}: {TITLES[book]} "
              f"({FINAL_W[book]:.3f} x {TRIM_H + 2 * BLEED:.3f}in, "
              f"spine {spine:.3f}in)")
        if args.pdf and latex(name):
            print(f"  build/covers/{name}.pdf")
            if args.png:
                subprocess.run(["pdftoppm", "-r", "72", "-png", "-singlefile",
                                f"{name}.pdf", name], cwd=COVERS)


if __name__ == "__main__":
    main()
