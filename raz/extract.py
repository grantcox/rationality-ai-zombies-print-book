"""Step 2 — turn each mirror page into a normalised chapter document.

The rendered HTML is the structural source of truth: the wiki source is not
normalised (two different footnote dialects are in use across the corpus),
whereas the rendered markup puts every part of a page in a labelled container
and draws a clean line between the three kinds of link:

    a.wikilink  -> internal cross-reference   -> 'xref' node
    a.urllink   -> external link              -> 'link' node
    sup/span.citation -> existing citation    -> 'fn' node

Anything this extractor does not recognise is preserved as a 'raw' node and
counted in build/extract_report.json, so nothing is ever dropped silently.

Run:  python -m raz.extract
"""

from __future__ import annotations

import base64
import collections
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from .common import BUILD, MIRROR, NAV_CLASSES, canonical_page, normalize_url

CHAPTERS = BUILD / "chapters"
ASSETS = BUILD / "assets"

# Containers that are navigation or duplicated furniture rather than prose.
DROP_SELECTORS = (
    *(f".{c}" for c in NAV_CLASSES),
    "div.toc",  # per-book / per-sequence inline contents listings
)

SIMPLE_INLINE = {
    "em": "em",
    "i": "em",
    "strong": "strong",
    "b": "strong",
    "sub": "sub",
    "sup": "sup",
    "code": "code",
    "small": "small",
    "big": "big",
    "del": "del",
}

# Inline spans whose class carries typographic meaning worth keeping.
SPAN_ROLES = {
    "smallcaps": "smallcaps",
    "year": "year",
    "ordinal": "ordinal",
    "monospaced": "code",
    "boxed": "boxed",
}

# HTML-built mathematics. Flagged rather than guessed at: converting these to
# real LaTeX maths is a separate pass, and silently flattening them would lose
# the content.
MATH_CLASSES = {"equation", "fraction", "bigsigma", "num", "frasl", "denom"}

#: The category mark on each line of the Magical Categories training data.
#: The site supplies it from CSS ``::before`` rather than from the markup, so
#: it is invisible to a text extractor -- and it is not decoration: the whole
#: passage is about which way a classifier splits the data. The role is
#: recorded here and the glyph chosen at render time.
DATASET_ROLES = {
    "dataset": "none",
    "dataset_plus": "plus",
    "dataset_minus": "minus",
}

def qa_role(node: Tag, cls: list[str]) -> str | None:
    """Is this paragraph a question or its answer?

    Two pages set out a mock interview. The skin marks the question with a
    class and then reaches the answer with an adjacent-sibling selector --
    ``.question + p::before`` -- so the answer carries no class at all and
    its label exists only in the stylesheet. This reads the same relation
    from the document: the paragraph immediately after a question is its
    answer, whatever else it is.
    """
    if "question" in cls:
        return "Q"
    prev = node.find_previous_sibling()
    if prev is not None and prev.name == "p" and "question" in (prev.get("class") or []):
        return "A"
    return None


#: An inline style that scales a block's text well past body size is a display
#: heading -- the wiki has no markup for one. The corpus holds a single such
#: block, "Bayes's Theorem:" at 350%, and no other div is above 144%.
BANNER_SIZE = re.compile(r"font-size:\s*(\d+)%")
BANNER_MIN = 200


report = collections.Counter()
unknown_samples: dict[str, str] = {}

#: A CSS rule body: a brace pair containing at least one "property: value".
#: Narrow on purpose -- it decides what gets deleted from the page.
CSS_RULE = re.compile(r"\{[^{}]*[a-z-]+\s*:\s*[^{};]+;?[^{}]*\}", re.I)


def looks_like_css(text: str) -> bool:
    return bool(CSS_RULE.search(text))


def note(key: str, sample: Tag | None = None):
    report[key] += 1
    if sample is not None and key not in unknown_samples:
        unknown_samples[key] = str(sample)[:300]


# --- assets ------------------------------------------------------------------


def save_asset(src: str) -> str:
    """Copy a referenced image into build/assets and return its new name."""
    if src.startswith("data:"):
        header, _, payload = src.partition(",")
        ext = ".svg" if "svg" in header else ".png"
        data = base64.b64decode(payload) if ";base64" in header else payload.encode()
        name = f"inline-{hashlib.sha1(data).hexdigest()[:12]}{ext}"
        (ASSETS / name).write_bytes(data)
        note("asset.inline")
        return name
    if src.startswith(("http://", "https://")):
        note("asset.remote")
        return src  # license badges on the About page; left as URLs
    p = MIRROR / src
    if not p.exists():
        note("asset.missing", None)
        return src
    shutil.copyfile(p, ASSETS / p.name)
    note("asset.local")
    return p.name


# --- footnotes ---------------------------------------------------------------

FN_ID = re.compile(r"#footnote(\d+)$")
CITE_ID = re.compile(r"#citation(\d+)$")
LEADING_NUM = re.compile(r"^\s*(\d+)\.\s*")


def _footnote_number(p: Tag) -> tuple[int | None, str]:
    """Recover a footnote's number and say which dialect supplied it."""
    if p.get("id", "").startswith("footnote"):
        return int(p["id"][len("footnote"):]), "p-id"
    anchor = p.find("a", id=re.compile(r"^footnote\d+$"))
    if anchor is not None:
        return int(anchor["id"][len("footnote"):]), "anchor-id"
    back = p.select_one("span.back_to_citation_link a[href]")
    if back is not None:
        m = CITE_ID.search(back["href"])
        if m:
            return int(m.group(1)), "back-link"
    m = LEADING_NUM.match(p.get_text())
    if m:
        return int(m.group(1)), "literal"
    return None, "none"


def take_footnotes(body: Tag) -> list[dict]:
    """Pull citation bodies out of the page, handling all three dialects.

    Modern pages carry ``<p id="footnoteN"><span class="footnote">…``; older
    ones ``<p><a id="footnoteN"></a><span class="footnote">…``; and a third
    set (the per-book introductions) carry a plain ``<p>N. text …`` with the
    number as literal text and no wrapper span at all.

    A ``div.footnotes`` that yields no bodies is *not* a footnote block -- the
    Bibliography page reuses the class for its entries -- so it is left in
    place as ordinary content rather than being discarded.

    Bodies are extracted as blocks, because some notes run to several
    paragraphs and can contain block quotes and further citations of their own.
    """
    out = []
    for div in body.select("div.footnotes"):
        taken, consumed = [], []
        for p in div.find_all("p", recursive=False):
            n, dialect = _footnote_number(p)
            if n is None:
                continue
            for back in p.select("span.back_to_citation_link"):
                back.decompose()
            # The whole paragraph is the note. span.footnote does not always
            # wrap all of it -- on some pages the note continues past the
            # closing tag, carrying citations of its own.
            content = content_blocks(p)
            if dialect == "literal":
                _strip_leading_number(content)
            note(f"footnote.dialect.{dialect}")
            taken.append({"n": n, "blocks": content})
            consumed.append(p)
        if not taken:
            note("footnotes.div_not_notes")  # Bibliography &c -- keep as content
            continue
        out.extend(taken)
        for p in consumed:
            p.decompose()
        # A footnote block can also hold prose that is not a note (a closing
        # "recommended reading" paragraph, say). Keep whatever is left rather
        # than discarding it with the wrapper.
        if div.get_text(strip=True) or div.find("img"):
            note("footnotes.residual_content", div)
            div.unwrap()
        else:
            div.decompose()
    out.sort(key=lambda f: f["n"])
    return out


def _strip_leading_number(content: list[dict]):
    """Remove the literal 'N. ' prefix used by the third dialect."""
    for b in content:
        for node in b.get("c", []):
            if node.get("t") == "text":
                node["v"] = LEADING_NUM.sub("", node["v"], count=1)
                return
            return


# --- damaged auto-links ------------------------------------------------------

# On the Bibliography page PmWiki's auto-linker stopped at the first dot of a
# few bare URLs, linking ``http://www2`` and leaving ``. psych.ubc.ca/...`` as
# plain text beside it. The remainder is still on the page, so the true URL can
# be reconstructed -- but only as a suggestion for the manifest's review pass,
# never applied silently.
AUTOLINK_TAIL = re.compile(r"^(\.?)\s?(\S+)")

# Where the linker gives up: a host with no dot yet (http://www2), or a path
# cut at a separator (…/abstract_, …/eerm.nsf/, …20060424082937/http:).
CUT = ("_", "/", ":", "-", "%")


def truncated_autolink(a: Tag) -> tuple[str, str] | None:
    """Detect a URL the wiki's auto-linker cut in half.

    Returns (reconstructed_url, orphan_text) or None. The orphan is the run of
    plain text sitting beside the link that is really part of the URL; it has
    to travel with the link, or dropping a dead link leaves debris like
    ``S0140525X00003435.`` marooned in the middle of a sentence.
    """
    href = a.get("href", "")
    if not href.startswith(("http://", "https://")):
        return None
    netloc = href.split("//", 1)[-1].split("/", 1)[0]
    if "." in netloc and not href.endswith(CUT):
        return None  # a complete-looking URL; the linker did not stop early
    nxt = a.next_sibling
    if not isinstance(nxt, NavigableString):
        return None
    m = AUTOLINK_TAIL.match(str(nxt))
    if not m:
        return None
    dot, tail = m.group(1), m.group(2).rstrip(".,;")
    if not tail or not re.fullmatch(r"[\w./~%?=&#:+$-]+", tail):
        return None  # ordinary prose resumed; nothing to splice
    if not looks_like_url_tail(tail):
        return None
    return normalize_url(f"{href}{dot}{tail}"), m.group(0)


def looks_like_url_tail(tail: str) -> bool:
    """Is this text the rest of a URL, or just the next word of the sentence?

    Nearly every link in the book ends in '/', so the href cannot tell the two
    apart -- 'lw/vs/selling_nonapples/' followed by ' to build flying machines'
    looks structurally identical to 'eerm.nsf/' followed by ' vwAN/EE-0280B'.
    Only the tail itself distinguishes them.
    """
    if "/" in tail:
        return True
    if re.search(r"\.[a-z]{2,4}(?:$|/)", tail, re.I):
        return True  # a file extension or domain, e.g. Hastorf1954.pdf
    # An opaque identifier: alphanumeric and carrying digits, e.g.
    # S0140525X00003435. Plain English words are rejected here.
    return tail.isalnum() and any(c.isdigit() for c in tail)


# --- inline walk -------------------------------------------------------------


def finish(nodes: list[dict]) -> list[dict]:
    """Everything a finished run of inline content needs.

    Both paths that build one -- inlines() for a container, and blocks()'s
    buffer for loose content between block children -- go through here.
    Splitting them was a bug: the hand-built fractions in Zut Allais sit
    directly in list items, which take the second path, so the splice below
    never saw them.
    """
    return splice_fractions(splice_split_urls(merge_text(nodes)))


def inlines(node: Tag) -> list[dict]:
    out: list[dict] = []
    for ch in node.children:
        out.extend(inline(ch))
    return finish(out)


def splice_fractions(nodes: list[dict]) -> list[dict]:
    """Rejoin a fraction the page built out of three separate spans.

    Six fractions in Zut Allais are set by hand rather than with the skin's
    .fraction class -- a raised numerator, a fraction slash and a lowered
    denominator, as three adjacent spans. Read separately they are three
    unrelated fragments; joined, they are the same diagonal fraction the rest
    of the book uses, and get the same treatment.
    """
    out, i = [], 0
    while i < len(nodes):
        three = nodes[i:i + 3]
        marks = [_math_class(n) for n in three]
        if marks == ["num", "frasl", "denom"]:
            # The page's own fraction slash is kept rather than an ASCII one:
            # it is a character of the source, and substituting it would show
            # up in raz.verify as text that went missing -- correctly.
            num, slash, den = (n["text"] for n in three)
            out.append({
                "t": "math",
                "html": f'<span class="fraction">{num}{slash}{den}</span>',
                "text": f"{num}{slash}{den}",
            })
            note("math.fraction.spliced")
            i += 3
            continue
        out.append(nodes[i])
        i += 1
    return out


def _math_class(node: dict) -> str | None:
    if node.get("t") != "math":
        return None
    m = re.search(r'class=["\']([a-z]+)', node.get("html", ""))
    return m.group(1) if m else None


def splice_split_urls(nodes: list[dict]) -> list[dict]:
    """Rejoin a URL the auto-linker broke into link + text + link.

    An archived URL contains a second ``http://`` inside it, and PmWiki links
    each half separately with the timestamp left as bare text between them:

        [http://web.archive.org/web/] " 20060424082937/" [http://www.nvon.nl/…]

    Left alone that yields two references, the second of which -- the bare,
    un-archived page -- was never cited by the book at all. The pieces are
    rejoined into one link, and the trailing half is marked so it does not
    appear in the manifest as a reference in its own right.
    """
    for i, n in enumerate(nodes[:-2]):
        if n.get("t") != "link" or n.get("suspect") != "truncated":
            continue
        gap, nxt = nodes[i + 1], nodes[i + 2]
        if gap.get("t") != "text" or nxt.get("t") != "link":
            continue
        tail = gap["v"].strip()
        if not tail.endswith(("/", ":")):
            continue  # the URL does not obviously continue into the next link
        n["suggested_url"] = normalize_url(f"{n['url']}{tail}{nxt['url']}")
        n["absorbs"] = nxt["url"]
        nxt["absorbed_by"] = n["url"]
        note("link.split_across_links")
    return nodes


def merge_text(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        if n["t"] == "text" and out and out[-1]["t"] == "text":
            out[-1]["v"] += n["v"]
        else:
            out.append(n)
    return [n for n in out if n["t"] != "text" or n["v"]]


#: Inside a fixed-width display, whitespace is content rather than layout: the
#: site right-aligns a second column with runs of &nbsp;, and collapsing those
#: to one space destroys the column. Set only while walking such a container.
_preserve_ws = False

#: A line break in the HTML *source* is still just formatting, even inside a
#: preserving container -- it is the runs of spaces within a line that matter.
SOURCE_BREAK = re.compile(r"[ \t]*\n[ \t\n]*")


def inline(node) -> list[dict]:
    if isinstance(node, NavigableString):
        text = str(node)
        text = (SOURCE_BREAK.sub(" ", text) if _preserve_ws
                else re.sub(r"\s+", " ", text))
        return [{"t": "text", "v": text}]
    if not isinstance(node, Tag):
        return []

    cls = node.get("class") or []
    name = node.name

    if name == "br":
        return [{"t": "br"}]

    if name == "a":
        href = node.get("href", "")
        # A link to #footnoteN is a citation marker whichever wrapper the page
        # uses -- sup, span.citation, or (on a few pages) no wrapper at all.
        m = FN_ID.search(href)
        if m:
            return [{"t": "fn", "n": int(m.group(1))}]
        if not node.get_text(strip=True) and not node.find("img"):
            return []  # bare anchor target, e.g. <a id="citation1"></a>
        if "urllink" in cls:
            link = {"t": "link", "url": normalize_url(href), "c": inlines(node)}
            found = truncated_autolink(node)
            if found:
                note("link.truncated_autolink", node)
                link["suspect"] = "truncated"
                link["suggested_url"], link["orphan_tail"] = found
            return [link]
        if "wikilink" in cls:
            return [{"t": "xref", "page": canonical_page(href), "c": inlines(node)}]
        note("link.unclassed", node)
        return inlines(node)

    if name == "img":
        return [{
            "t": "img",
            "src": save_asset(node.get("src", "")),
            "alt": node.get("alt", "") or "",
        }]

    if name == "span":
        if "citation" in cls:  # older marker wrapper; the <a> inside carries it
            return inlines(node)
        if "back_to_citation_link" in cls:
            return []
        if MATH_CLASSES & set(cls):
            note(f"math.span.{cls[0]}")
            return [{"t": "math", "html": str(node), "text": node.get_text(" ", strip=True)}]
        for c in cls:
            if c in SPAN_ROLES:
                return [{"t": SPAN_ROLES[c], "c": inlines(node)}]
        if node.get("style"):
            note(f"span.style[{node['style'][:24]}]")
        return inlines(node)

    if name in SIMPLE_INLINE:
        return [{"t": SIMPLE_INLINE[name], "c": inlines(node)}]

    note(f"inline.unknown.{name}", node)
    return inlines(node)


# --- block walk --------------------------------------------------------------

ORNAMENT = re.compile(r"^[❦✦✳❖*\s]+$")

BLOCK_TAGS = {"p", "blockquote", "ul", "ol", "dl", "table", "hr", "pre",
              "div", "h1", "h2", "h3", "h4"}


def content_blocks(tag: Tag) -> list[dict]:
    """Blocks for a container that may hold either inline runs or real blocks.

    Footnote bodies are usually one run of inline content, but a few carry
    whole paragraphs and block quotes -- sometimes nested inside the inline
    wrapper span rather than beside it, so the search is not depth-limited.
    """
    if tag.find(list(BLOCK_TAGS)) is not None:
        return blocks(tag)
    inline_content = inlines(tag)
    return [{"t": "p", "c": inline_content}] if inline_content else []


def blocks(node: Tag) -> list[dict]:
    """Walk children, gathering runs of loose inline content into paragraphs.

    Containers mix block children with bare inline content (a footnote whose
    text surrounds a block quote, say). Buffering the inline run keeps it as
    one paragraph instead of shattering it into one per text node.
    """
    out: list[dict] = []
    buf: list[dict] = []

    def flush():
        if buf:
            merged = finish(buf)
            if any(n["t"] != "text" or n["v"].strip() for n in merged):
                out.append({"t": "p", "c": merged})
            buf.clear()

    for ch in node.children:
        if isinstance(ch, Tag) and ch.name in BLOCK_TAGS:
            flush()
            out.extend(block(ch))
        elif isinstance(ch, Tag) and ch.find(list(BLOCK_TAGS)) is not None:
            # An inline wrapper (span.footnote &c) that itself contains
            # blocks: descend rather than flattening the blocks into a run.
            flush()
            out.extend(blocks(ch))
        else:
            buf.extend(inline(ch))
    flush()
    return out


def block(node) -> list[dict]:
    if isinstance(node, NavigableString):
        return [{"t": "p", "c": [{"t": "text", "v": str(node).strip()}]}] if str(node).strip() else []
    if not isinstance(node, Tag):
        return []

    cls = node.get("class") or []
    name = node.name

    if name == "h1":
        return []  # page title; the spine already has it
    if name in ("h2", "h3", "h4"):
        return [{"t": "h", "level": int(name[1]), "c": inlines(node)}]

    if name == "p":
        text = node.get_text(strip=True)
        if not text and not node.find("img"):
            return []
        if ORNAMENT.match(text):
            return [{"t": "ornament"}]
        b = {"t": "p", "c": inlines(node)}
        if "blockquote_byline" in cls:
            b["t"] = "byline"
        elif "indent" in cls:
            b["indent"] = True
        elif MATH_CLASSES & set(cls):
            note("math.p.equation")
            return [{"t": "math_block", "html": str(node), "text": node.get_text(" ", strip=True)}]
        for c in cls:
            if c in DATASET_ROLES:
                b["dataset"] = DATASET_ROLES[c]
                note(f"dataset.{b['dataset']}")
        role = qa_role(node, cls)
        if role:
            b["qa"] = role
            note(f"qa.{role}")
        style = node.get("style") or ""
        if "center" in style:
            b["align"] = "center"
        return [b]

    if name == "blockquote":
        return [{"t": "quote", "c": blocks(node)}]

    if name in ("ul", "ol"):
        items = [blocks(li) for li in node.find_all("li", recursive=False)]
        return [{"t": "list", "ordered": name == "ol", "items": items}]

    if name == "dl":
        items = []
        for dt in node.find_all("dt", recursive=False):
            dd = dt.find_next_sibling("dd")
            items.append({"dt": inlines(dt), "dd": blocks(dd) if dd else []})
        return [{"t": "dl", "items": items}]

    if name == "table":
        if MATH_CLASSES & set(cls):
            note("math.table.equation")
            return [{"t": "math_block", "html": str(node), "text": node.get_text(" ", strip=True)}]
        rows = [
            [blocks(td) for td in tr.find_all(["td", "th"], recursive=False)]
            for tr in node.find_all("tr")
        ]
        return [{"t": "table", "rows": rows}]

    if name == "hr":
        return [{"t": "hr"}]
    if name == "pre":
        return [{"t": "pre", "v": node.get_text()}]

    if name == "div":
        # Three chapters set a display of data or pseudocode in a fixed-width
        # face. The class is on the container, so the grouping has to survive
        # as a block of its own; flattening it loses the fact that the lines
        # belong together.
        m = BANNER_SIZE.search(node.get("style") or "")
        if m and int(m.group(1)) >= BANNER_MIN:
            note("banner")
            return [{"t": "banner", "scale": int(m.group(1)), "c": blocks(node)}]
        if "monospaced" in cls:
            note("monospaced_block")
            global _preserve_ws
            was, _preserve_ws = _preserve_ws, True
            try:
                return [{"t": "monospaced", "c": blocks(node)}]
            finally:
                _preserve_ws = was
        return blocks(node)  # wrapper, img, indent, nohyphens …

    if name in SIMPLE_INLINE or name in ("a", "span", "img", "br"):
        return [{"t": "p", "c": inline(node)}]

    note(f"block.unknown.{name}", node)
    return blocks(node)


# --- per page ----------------------------------------------------------------


def extract(entry: dict) -> dict:
    from .common import mirror_path

    soup = BeautifulSoup(
        mirror_path(entry["page"]).read_text(encoding="utf-8", errors="replace"), "lxml"
    )
    body = soup.find(id="wikitext")
    if body is None:
        raise ValueError(f"no #wikitext in {entry['page']}")

    for sel in DROP_SELECTORS:
        for n in body.select(sel):
            n.decompose()

    # On two pages the Feb 2020 crawl caught PmWiki leaking a page's (:css:)
    # block as literal text ahead of the title; the live site has since fixed
    # both. Only stylesheet text is dropped, and only from ahead of the <h1>:
    # the six book title pages legitimately carry "Book III" up there.
    h1 = body.find("h1")
    if h1 is not None:
        for el in list(body.children):
            if el is h1:
                break
            if not isinstance(el, Tag):
                continue
            if looks_like_css(el.get_text(" ", strip=True)):
                note("dropped_leaked_css")
                el.decompose()

    # Consumes and removes only those footnote blocks it can actually read.
    footnotes = take_footnotes(body)

    doc = dict(entry)
    doc["blocks"] = blocks(body)
    doc["footnotes"] = footnotes

    ext, xref = [], []
    for n in walk(doc["blocks"]) + walk(footnotes):
        if n.get("t") == "link":
            ext.append(n["url"])
        elif n.get("t") == "xref":
            xref.append(n["page"])
    doc["links"] = {"external": ext, "internal": xref}
    return doc


def walk(node) -> list[dict]:
    """Every node dict anywhere in the document tree, depth-first.

    Blocks nest through lists of blocks (list items, table cells, definition
    bodies) as well as through 'c', so this walks values generically rather
    than naming the containers.
    """
    found: list[dict] = []
    if isinstance(node, dict):
        if "t" in node:
            found.append(node)
        for v in node.values():
            if isinstance(v, (dict, list)):
                found.extend(walk(v))
    elif isinstance(node, list):
        for v in node:
            found.extend(walk(v))
    return found


def main():
    spine = json.loads((BUILD / "toc.json").read_text(encoding="utf-8"))
    CHAPTERS.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)

    totals = collections.Counter()
    orphans = []
    for entry in spine["entries"]:
        doc = extract(entry)
        out = CHAPTERS / f"{entry['order']:03d}-{entry['page']}.json"
        out.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
        totals["pages"] += 1
        totals["footnotes"] += len(doc["footnotes"])
        totals["external"] += len(doc["links"]["external"])
        totals["internal"] += len(doc["links"]["internal"])

        # Every citation marker must have a body and vice versa, or the note
        # will print as a dangling number.
        marks = {n["n"] for n in walk(doc["blocks"]) + walk(doc["footnotes"])
                 if n.get("t") == "fn"}
        bodies = {f["n"] for f in doc["footnotes"]}
        if marks != bodies:
            orphans.append({
                "page": entry["page"],
                "markers_without_body": sorted(marks - bodies),
                "bodies_without_marker": sorted(bodies - marks),
            })

    (BUILD / "extract_report.json").write_text(
        json.dumps(
            {"totals": dict(totals), "notes": dict(report.most_common()),
             "footnote_orphans": orphans, "samples": unknown_samples},
            indent=1, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"  citation markers reconcile with bodies on "
          f"{totals['pages'] - len(orphans)}/{totals['pages']} pages")
    for o in orphans:
        print(f"    {o['page']}: markers without body "
              f"{o['markers_without_body']}, bodies without marker "
              f"{o['bodies_without_marker']}")

    print(f"build/chapters/: {totals['pages']} pages, "
          f"{totals['footnotes']} citations, "
          f"{totals['external']} external links, "
          f"{totals['internal']} cross-references")
    unknown = {k: v for k, v in report.items() if "unknown" in k}
    if unknown:
        print("  unrecognised constructs (preserved as raw, see extract_report.json):")
        for k, v in sorted(unknown.items(), key=lambda kv: -kv[1]):
            print(f"    {v:5d}  {k}")
    math = {k: v for k, v in report.items() if k.startswith("math.")}
    if math:
        print(f"  HTML-built maths flagged for a later pass: {sum(math.values())}")


if __name__ == "__main__":
    main()
