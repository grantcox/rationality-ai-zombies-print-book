# Rationality: From AI to Zombies — printable book sources

Extraction pipeline that turns the [readthesequences.com](https://www.readthesequences.com)
edition into a normalised, typesettable document set, keeping the hyperlinks
that the EPUB/MOBI/LaTeX editions dropped.

## Why this source

Spot-checking one chapter across every available edition:

| source | inline links | footnote marker ↔ body | edition |
|---|---|---|---|
| **rendered HTML** (this pipeline) | yes, cleanly classed | normalised | 2018 six-volume |
| `?action=source` (PmWiki) | yes, with original `lesswrong.com` URLs | three different dialects | 2018 six-volume |
| `?action=markdown` | rewritten, and internal links indistinguishable from external | association broken | 2018 six-volume |
| EPUB / MOBI | **none** — flattened | yes | 2015 |
| [jrincayc LaTeX](https://github.com/jrincayc/rationality-ai-zombies) | **none** — flattened | yes | 2015 |

The rendered HTML wins on structure: every part of a page sits in a labelled
container, and the three kinds of link are distinguishable by class, which is
the distinction the whole project turns on.

    a.wikilink  →  internal cross-reference  →  'xref' node  →  📖 footnote
    a.urllink   →  external link             →  'link' node  →  → footnote
    sup / span.citation  →  existing citation →  'fn' node   →  numbered note

### The greaterwrong / lesswrong rewrite

readthesequences rewrites `lesswrong.com` to `www.greaterwrong.com` when it
renders; the wiki source keeps the original. In a 10-chapter sample of
`?action=source`, **30 of 30** LessWrong links were `lesswrong.com` and none
were greaterwrong, so the rewrite is a pure host substitution.

`common.normalize_url` inverts it, which also matches the URLs printed in the
physical 2018 edition. Verified by probing 40 random rewritten URLs: 40/40
returned 200. The legacy `/lw/<id>/<slug>/` form still resolves and is much
shorter than the modern `/posts/<hash>/<slug>` form greaterwrong redirects to.

The rendered HTML also injects `&shy;` soft hyphens into long URLs so they
wrap on screen; those are stripped, since they would corrupt a printed URL.

## Numbering

`Contents.html` carries the structure *and* the numbering: the wiki's
`%item value=N%` markers survive as `<li value="N">`, pinning the global
counters at each book boundary. The result —

    6 books · 26 sequences (lettered A–Z across the whole work) · 333 chapters

— is exactly what the LaTeX edition's counters produce independently
(6 `\part`, 26 `\chapter`, 333 `\mysection`), from a different source. So a
cross-reference can be rendered as `Book III, chapter 132, "The Wonder of
Evolution"` with confidence.

One page of the book is not in `Contents.html`: the author's preface, which
the site puts on its front page (`HomePage`) and lists nowhere. `raz/toc.py`
names it into the spine rather than reading it, which is why the pipeline
reports 379 pages against the Contents' 378. It is unnumbered, and belongs to
Book I's front matter along with `Biases: An Introduction`.

## Pipeline

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python -m raz.toc      # 1. spine        -> build/toc.json
.venv/bin/python -m raz.extract  # 2. documents    -> build/chapters/*.json
.venv/bin/python -m raz.verify   # 2b. check nothing was lost
.venv/bin/python -m raz.links    # 3. manifests    -> build/links_*.csv
.venv/bin/python -m raz.links --check   # ... and probe every URL
```

If `python3 -m venv` appears to hang, it is stuck in `ensurepip` while macOS
scans the freshly copied interpreter; give it a minute. Interrupting leaves a
broken `.venv`, so `rm -rf .venv` before retrying. With `uv` installed,
`uv venv && uv pip install -r requirements.txt` sidesteps the bootstrap.

Point `$RAZ_MIRROR` at the `www.readthesequences.com` mirror if it is not at
the default path in `raz/common.py`.

Everything under `build/` is derived and none of it is committed — `rm -rf
build` is always safe. Three things live at the repository root instead:

* `overrides.csv` — the only file edited by hand, and the only durable human
  input in the repository.
* `link_status.json` — the cached HTTP probe results. Not derived from the
  mirror but from the open web, and several minutes of requests to rebuild,
  so it must not sit in a folder people delete.
* Assets supplied for the print edition rather than taken from the mirror —
  `eliezer-yudkowsky.jpeg`, on the About the Author page, and `covers/`, the
  six pieces of jacket artwork. The root is on `\graphicspath` alongside
  `build/assets_pdf`, which holds the plates the mirror does carry.

The intermediate JSON is deliberate: footnote and cross-reference design will
be iterated on many times, and re-running extraction for each experiment —
or debugging typesetting and extraction at once — is the slow way round. The
JSON is also diffable, so extraction gets proof-read once.

### Current output

    379 pages · 317 citations · 677 external references · 1,790 cross-references
    citation markers reconcile with bodies on 379/379 pages
    2,421,845 characters of prose, 0 unaccounted for
    323/323 cross-reference targets resolve to a numbered place in the book
    491 distinct external URLs, all 105 needing a ruling now decided
    426 expressions converted to LaTeX maths, 0 lost characters
    the whole book typesets: 1,540pp in one volume, no undefined references
    six volumes: 215 / 339 / 297 / 475 / 221 / 237pp, 64 overfull, 0 undefined

Both link counts match the source DOM exactly, which is the check that the
walk is not quietly skipping a container.

Rendering:

```bash
.venv/bin/python -m raz.render --volumes --pdf             # 4. the six books
.venv/bin/python -m raz.render --volumes --only 3 --pdf    # ... just Book III
.venv/bin/python -m raz.render --all --pdf                 # one continuous volume
```

Three LaTeX passes per volume, not two: one to place the pages, one to write
the contents and resolve the `\ref` a repeated footnote is set from, one to
typeset a contents list that may have grown a page in the meantime.

#### Page size and bleed

The book is 6 × 9in trimmed, and the PDF pages are 6.125 × 9.25 — an eighth of
an inch of bleed at the head, the foot and the fore-edge, and none at the
spine, which is bound rather than cut. That is what the printer asks for, and
it is the standard interior-with-bleed spec.

```latex
\setstocksize{9.25in}{6.125in}
\settrimmedsize{9in}{6in}{*}
\settrims{0.125in}{0.125in}
```

`\settrims` takes the head and fore-edge amounts and gives whatever is left to
the foot and the spine; memoir mirrors the fore-edge onto the versos, so the
bleed is on the right of a recto and the left of a verso. Confirmed by
measuring: text starts 0.903in from the sheet edge on a recto and 0.815in on a
verso, which is the 0.9in gutter against the 0.125 + 0.7in fore-edge.

Every margin is measured from the trimmed 6 × 9 page rather than from the
sheet, so widening the sheet moves nothing and the page counts are unchanged.
Nothing in the interior actually runs off the edge — the bleed is empty paper,
there so the guillotine can wander an eighth of an inch without cutting into
the text block.

The PDFs carry no `TrimBox`, only a `MediaBox` at the full 6.125 × 9.25. If the
printer wants the trim marked in the file, that has to be added per page,
because its position mirrors between recto and verso.

### Covers

```bash
.venv/bin/python -m raz.covers --pdf                       # 5. six jackets
.venv/bin/python -m raz.covers --pdf --style serif         # ... in the text face
.venv/bin/python -m raz.covers --only 4 --pdf --png --guides
```

One sheet per book: back cover, spine and front cover side by side, with
`covers/coverN.jpg` running full bleed underneath. The artwork is hung off the
right edge and clipped at the left, so the part of it the front cover shows is
the part that was composed to be looked at, and the same file serves every
spine width.

The blurb on the back is not retyped. Every book's page on the mirror opens
with the paragraph Rob Bensinger wrote for it in "Biases: An Introduction",
and `raz/covers.py` takes it from there. The site signs the paragraph with the
chapter as well as the name; the chapter means nothing on a jacket, so only
the name is kept.

The front and spine are set in tracked upper-case Montserrat, the blurb in
EB Garamond as the interior is. `--style serif` sets the display lines in
Garamond too, which is what the design was chosen against.

The display face doubles its word space. Letterspacing pushes every pair of
letters apart without touching the space between words, so at the tracking
these lines want, "MERE REALITY" closes up into one word; the space has to
grow with the tracking to stay a space.

`--guides` draws the trim in cyan, the two folds in magenta and the safe area
in dashed yellow. It is for proofing on screen and must not be left on in
anything sent to the printer.

#### Where the geometry comes from

The printer quotes three numbers for Book I: exported PDF 13.819 × 9.861,
trim 13.207 × 9.249, bleed 0.306 all round. Everything else is derived.

The trim is 0.249in taller than the 9in page, which is the case boards
standing 0.1245in proud of the text block at head and tail. The same overhang
at the fore-edge makes each panel 6.1245in wide, and whatever is left of the
trim width once two panels come out of it is the spine:

| Book | Exported PDF | Spine |
|---|---|---|
| I | 13.819 | 0.957 |
| II | 14.069 | 1.207 |
| III | 13.944 | 1.082 |
| IV | 14.319 | 1.457 |
| V | 13.819 | 0.957 |
| VI | 13.819 | 0.957 |

Those spines are exact eighths of an inch apart, which is the pattern in the
widths the printer quoted for the other five volumes — so the model is the
printer's own, not a guess that happens to fit. `FINAL_W` is therefore the
only per-book input; if a volume's page count changes, the printer's new
exported width is the one number to update.

#### Legibility

The artwork is not ours to choose and the next one may be pale where this one
is dark, so the type carries its own ground rather than trusting the picture:
a wash over the whole sheet, gradients into the head and foot, and a soft
elliptical pool behind each block of type. Book VI is the test case — its
front cover has a bright golden core directly behind the title.

The gradients run the full width of the sheet rather than stopping at a fold.
Stopping one at a fold puts a hard vertical edge down the cover, which is the
one artefact of all this that the eye does pick out.

The spine is set as a single box the length of the spine's run with the three
items sprung apart inside it, rather than three separately anchored ones.
"How to Actually Change Your Mind" is long enough to reach the middle of the
spine and collide with a series mark pinned there; sprung, the gaps can only
close up, never cross. They still have to stay open, so the size is set from
how much lettering there is.

### Link health

Two different questions, and only the first is fully automatable:

1. **Is it alive?** `raz.links --check` probes every distinct URL.
2. **Does it land where the text implies?** A 200 does not settle this. A
   lapsed domain that has been re-registered answers 200, and a site that has
   dropped an old article usually redirects to its front page rather than
   returning 404. So the probe also records the *landing* URL and compares it
   with the cited one.

Verdicts: `ok` (386), `dead` (43), `blocked` (38 — 403/406, generally fine in
a browser), `changed-target` (18), `redirected-to-root` (6). A host that moves
but keeps its path (`theregister.co.uk` → `.com`, or a geo-redirect on Google
Books) counts as `ok`; only a move that also changes the path is flagged.

The landing check is what catches the worst class of failure. Nine links go
to `wiki.lesswrong.com`, which was retired. Seven of them now answer **200**
from `abcp.mywikis.com/challenge.php` — another site's bot challenge. A
status-only check calls all nine healthy. All nine have an exact successor at
`lesswrong.com/w/<slug>`.

### URLs the wiki broke

PmWiki's auto-linker gives up part way through some URLs, and the remainder is
left as plain text beside the link. Seven are affected, in two shapes:

* **link + text** — `http://www2` linked, `. psych.ubc.ca/…` beside it;
  `…/eerm.nsf/` linked, ` vwAN/EE-0280B-04.pdf/$file/…` beside it.
* **link + text + link** — an archived URL contains a second `http://`, so both
  halves get linked with the timestamp bare between them:

      [http://web.archive.org/web/] " 20060424082937/" [http://www.nvon.nl/…]

  Left alone this yields a phantom reference to the un-archived page, which the
  book never cites. `splice_split_urls` rejoins the three pieces and marks the
  trailing half absorbed so it gets no footnote of its own.

The extractor records a `suggested_url` and the `orphan_tail` — the exact text
that belongs to the URL — but never rewrites a link silently. The orphan
matters for `remove`: dropping the link alone would strand `S0140525X00003435.`
in the middle of a bibliography entry.

Because the tail is what distinguishes a broken URL from a normal one (nearly
every link ends in `/`, so the href cannot tell `…selling_nonapples/` + " to
build flying machines" from `…eerm.nsf/` + " vwAN/EE-0280B"), detection keys off
whether the following text looks like URL material. That keeps it at 7 hits
with no false positives across 2.4M characters.

### Reviewing links: overrides.csv

The probe says a link is broken; it cannot say what to do about it. That is a
judgement about the text, so it lives in `overrides.csv` at the repository
root — the single place where the book deviates from its source, reviewable
in one sitting and in a diff.

| action | effect | when |
|---|---|---|
| `keep` | print as-is | recording "I checked this" — a 403 a browser opens fine |
| `replace` | print the footnote against a new URL | the page moved |
| `archive` | print a Wayback snapshot | the page is gone but was captured |
| `unlink` | drop the footnote, keep the sentence | the anchor is a phrase that reads fine as prose |
| `remove` | drop the footnote *and* the anchor text | the anchor text **is** the URL — the Bibliography case |

`unlink` and `remove` differ only in whether the anchor text survives. Getting
it wrong either strands a bare dead URL in the text or eats a clause of a
sentence, so `build/links_review.csv` suggests the right one per row based on
whether the anchor text is the URL.

An optional `page` column scopes a decision to one chapter; blank applies it
everywhere. Unknown actions, a `replace` with no replacement, and duplicate
rules are all rejected with a line number.

`overrides.csv` is the only file anyone edits by hand, and the only durable
human input in the repository. `build/links_review.csv` is a **report**: it is
regenerated on every run and must not be edited, because the next run discards
it.

The workflow is copy-and-paste. The report's first five columns are exactly
`overrides.csv`'s — `url, action, replacement, page, note` — so accepting a
decision means copying a row's first five cells into `overrides.csv`. The
remaining columns are underscore-prefixed evidence for the suggested action:

| column | what it tells you |
|---|---|
| `_if_dropped` | whether `remove` or `unlink` is the safe choice here |
| `_link_text` | the words the link is attached to |
| `_verdict` / `_status` / `_landed_on` | what the probe found |
| `_orphan_text` | text the wiki left stranded beside a broken URL |
| `_count` / `_cited_in` | how often, and where |

Rows already ruled on drop off the report. `raz.links` checks every rule on
each run and reports two things it cannot fix itself: a decision whose URL is
no longer a link in the book (stale — delete it), and **a `remove` whose link
text is prose**, which would delete words from a sentence. That check lives
with the rules rather than with any one entry path, so it fires however a rule
got into the file.

## Document model

Each `build/chapters/NNN-Page-Name.json` carries its spine entry (book,
sequence, chapter number, title) plus:

* `blocks` — `p`, `h`, `quote`, `byline`, `list`, `dl`, `table`, `pre`, `hr`,
  `ornament`, `math_block`
* inline — `text`, `em`, `strong`, `smallcaps`, `sub`, `sup`, `code`, `year`,
  `link`, `xref`, `fn`, `img`, `math`, `br`
* `footnotes` — `{n, blocks}`; bodies are blocks because some notes run to
  several paragraphs, contain block quotes, and cite further notes of their own
* `links` — the external URLs and cross-reference targets on the page

Anything the extractor does not recognise is preserved and counted in
`build/extract_report.json`; nothing is dropped silently. `raz.verify` is the
backstop — it compares the extracted text against the source page character by
character and currently reports zero loss.

## Things the stylesheet says and the markup does not

Three constructs exist only in CSS. Each is read back out of the document
rather than hardcoded, and each is counted in the run report.

| construct | how it is found | how it prints |
|---|---|---|
| the `+:` / `-:` category marks on the *Magical Categories* training data | `p.dataset_plus` / `p.dataset_minus`, whose marks are `::before` content | a label hanging in the left margin, wrapped lines aligned under the text |
| the `Q:` / `A:` labels in the two mock interviews | `p.question`, and for the answer the same adjacent-sibling relation the selector `.question + p::before` uses — the answer carries no class at all | the same gutter label; the question bold, as the skin sets it |
| the one display heading, "Bayes's Theorem:" | an inline `font-size` at 350%; no other block in the book is above 144% | centred and bold, at two-thirds that size — 350% of an 11pt body is display type scaled for a browser window, and on a 6×9 measure it stranded the plate's caption overleaf |

The display heading is grey with a black outline on screen. That is a screen
effect which muddies at print resolution, so it is set solid.

An `<hr>` prints as a short centred rule. memoir's `\pfbreak` hooks the output
routine and shows its mark only when the break does not land on a page
boundary, which is why these were coming out as bare white space.

## Mathematics

There is no MathML and no TeX in the source. Every expression is assembled out
of `<em>`, `<sub>`, `<sup>` and a few styled spans, and laid out with tables
when it needs more than one line. `raz/maths.py` converts all 426 of them.

The conversion turns on one thing the markup already records: **`<em>` marks a
variable**. So the italic/upright split — *P*(cancer), not *P*(*cancer*) —
comes from the source rather than from a guess about which letters are
symbols. Inside `<em>`, a short run is a variable and a longer one is still a
name: the corpus holds 1,293 single-letter runs there and exactly four longer
ones (`colors` three times, as a summation index, and one `Si`).

| source | becomes |
|---|---|
| `span.equation` | inline `$…$` |
| `span.fraction`, and `num`+`frasl`+`denom` built by hand | `\sfrac` — a diagonal fraction, as the skin's OpenType `frac` feature draws it |
| `p.equation` | `\[…\]`, or `gather*` where the author broke the line |
| `table.equation` | `align*`, aligned on the relation column |
| `td.numerator` / `td.denominator` on consecutive rows | one `\frac` per pair, left to right |
| `span.bigsigma`, `span.sigma` | `\sum` with its index |
| the two oversized bracket images | `\left(` … `\right)` |

Set in Garamond-Math, EB Garamond's companion; the stock maths font would put
Computer Modern next to a Renaissance text face.

Two places where the printed form deliberately departs from the source, both
counted in the run report:

* **Punctuation is lifted out of a denominator** (3). A table-drawn fraction
  has nowhere to put the full stop that ends the sentence, so the source puts
  it under the bar. A real `\frac` does have somewhere.
* **One fraction over unmarked rows** (1). A two-line sum under a bar, drawn
  with bracket images in cells spanning both rows and no `denominator` class
  anywhere; a cell that spans every remaining row is hoisted to the side it
  sits on, which is how it reads on the page.

Nothing is guessed at: a tag, class or character with no rule is counted and
printed as a visible marker. `python -m raz.maths` reconverts everything and
checks that every letter and digit of each source expression survived —
currently **426 expressions, 0 lost characters**.

## Known follow-ups

* ~~HTML-built equations~~ **done** — see *Mathematics* below.
* **Link review is complete**: 105 decisions in `overrides.csv` — 61 `replace`,
  37 `keep`, 4 `remove`, 3 `unlink`, 1 `archive`. Nothing outstanding.
* **Design decisions** settled so far: one numbered footnote series per
  chapter; external links footnoted as `→ http://example.org/path`;
  cross-references footnoted as `see Book III, chapter 132, "…"`; anchor
  phrases marked with a dotted underline; chapter number and title centred;
  notes set flush left; paragraphs separated by half a line and never indented,
  in quotations as well as in the body. A chapter that cites the same address
  or the same other chapter twice gets one note, marked twice.

  A link also defers to a citation footnote in the same paragraph that points
  at the same address. The author often links a phrase and cites the source a
  few words later; two marks side by side pointing at one place read as two
  places, so the phrase keeps its underline and the citation's mark serves
  both. Five occurrences across the six volumes, four of them in Book II's
  introduction — the paragraph and the citation are the same edit, which is
  why they cluster.

  Every vertical gap is that same half line. Three separate things were
  inflating it, each on different pages: `\flushbottom` stretching short
  pages, the `center` *environment*'s `\topsep`, and LaTeX's display
  machinery, which fakes a preceding line with `\makebox[.6\linewidth]{}`
  whenever a display starts in vertical mode. Measured before the fix: 21.8pt
  above a display against 6.7pt below, where every other gap was 5.8pt. Now
  `\raggedbottom`, `\centering`, and the box forms of the maths environments
  (`aligned`, `gathered`) — see *Mathematics*.

  A display then goes into the vertical list as a plain centred box with
  `\nointerlineskip` on both sides, because TeX's interline glue is computed
  from the height of the box *below* it: a tall formula pushes itself away
  from the line above while sitting normally against the line below, so the
  asymmetry varies with the formula. With that dependency gone,
  `\razdisplayabove` and `\razdisplaybelow` set the gap outright. They are
  not equal (0.56 and 0.63 of a line) because the glue is measured from the
  box edges and the eye reads the ink: they are calibrated so the *white*
  above and below comes out the same, 9.4pt against a 9.1pt paragraph gap.
  Measured by counting blank pixel rows in the rendered page — `pdftotext`
  reports font-metric boxes, which disagree with what is actually on paper by
  a couple of points and mislead badly around fractions.

  Addresses are printed whole, scheme included, because 270 of the 492 are
  `http://` and the book cannot promise a reader that they are all `https://`.
  A link whose anchor text *is* its address prints inline instead, with no
  note: the footnote would only repeat the line above it.
* ~~Volume split~~ **done.** Six volumes matching Books I–VI: 1,848pp in
  total, against 1,573 as one continuous book. The 277 extra pages are what
  making each volume stand on its own costs — six title pages and tables of
  contents, 26 part openers with their blank versos, and the glossary and
  bibliography carried in every volume, because a reader holding Book IV
  cannot look a term up in Book I.

  Each volume runs: title page, contents, the book's own introduction where
  it has one, its parts, then the glossary, the bibliography, and a page
  about the author with his photograph. Book I opens with the author's
  preface ahead of its introduction — the site's own front page, which the
  Contents does not list; see *Numbering*. Front matter is numbered in roman
  and the body restarts at 1 — memoir's `\frontmatter`/`\mainmatter`, and the
  reason the contents can be typeset before the pages it lists are numbered.
  The glossary and bibliography are set off in the contents by the same gap
  that separates the parts: they belong to no part, and without it they read
  as the tail of the last one.

  A part opens on a right-hand page and its first chapter opens on the next
  right-hand page, so a part is announced by a spread rather than by a line
  of type at the foot of a verso. That is two `\cleartorecto`, not one: the
  second skips the blank verso the first leaves behind. Later chapters in the
  part start wherever they fall. The glossary, the bibliography and the
  contents open recto too.

  The part letters run A–Z across the whole work rather than restarting each
  volume, so Book II opens at Part E. That is what the cross-reference
  footnotes cite, and chapter numbers are global for the same reason — Book
  II's contents begins at 46.

  The wiki's own book and sequence pages carry no text, only an ornament, so
  the title pages and part openers are generated from the spine and nothing
  is lost by replacing them.

  The six introductions are Rob Bensinger's, not Yudkowsky's, and the wiki
  marks the credit inconsistently — centred on two of them, an ordinary first
  paragraph on the other four. All six are now set as a subheading under the
  chapter title, close under it and with the full gap below the pair, so the
  two lines read as one heading. The rule is a first paragraph whose whole
  text is a short "by …" line, which across the 379 pages picks out those six
  and nothing else.

  The preface closes with a signature the wiki gives no markup to, and is the
  only signed piece in the book; a trailing "—Name, Month Year" paragraph is
  retyped as a `byline` block, which is already flush right and italic.

## Licence

The book is Eliezer Yudkowsky's, released under CC BY-NC-SA 3.0. This
repository contains extraction tooling only; no book text is committed.
