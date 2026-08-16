"""Extraction check — does every word on the page survive into the document?

Compares the word multiset of each extracted chapter against the word
multiset of the source page, ignoring the things the extractor deliberately
turns into structure rather than text:

  * citation marker digits, which become 'fn' nodes
  * the section ornament, which becomes an 'ornament' node
  * page-local <style> rules, which are presentation, not prose

Anything else that goes missing is a bug. Run this after any change to
extract.py.

Run:  python -m raz.verify
"""

from __future__ import annotations

import collections
import glob
import json
import re
import sys

from bs4 import BeautifulSoup

from .common import BUILD, mirror_path
from .extract import DROP_SELECTORS, ORNAMENT_CHARS, looks_like_css

ORNAMENTS = set(ORNAMENT_CHARS)
# "back to citation" arrows; navigation within the page, dropped on purpose.
ARROWS = {"↩", "↩︎"}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def text_of(node) -> str:
    if isinstance(node, dict):
        if node.get("t") == "text":
            return node["v"]
        if node.get("t") in ("math", "math_block"):
            return node.get("text", "")
        if node.get("t") == "pre":
            return node.get("v", "")
        return " ".join(text_of(v) for v in node.values() if isinstance(v, (dict, list)))
    if isinstance(node, list):
        return " ".join(text_of(v) for v in node)
    return ""


def ignorable(word: str, markers: set[str]) -> bool:
    return (
        word in ORNAMENTS
        or word in ARROWS
        or word in markers
        or word.isdigit()
    )


def main() -> int:
    rows = []
    for f in sorted(glob.glob(str(BUILD / "chapters" / "*.json"))):
        doc = json.loads(open(f, encoding="utf-8").read())
        soup = BeautifulSoup(
            mirror_path(doc["page"]).read_text(encoding="utf-8", errors="replace"),
            "lxml",
        )
        body = soup.find(id="wikitext")
        # extract's own list, not a copy of it: a container dropped there and
        # not here is reported as prose that went missing.
        for sel in list(DROP_SELECTORS) + ["h1", "style"]:
            for n in body.select(sel):
                n.decompose()
        # Mirrors extract's rule, so stylesheet text the crawl leaked into two
        # pages is not counted as prose that went missing.
        for el in list(body.children):
            if hasattr(el, "get_text") and looks_like_css(el.get_text(" ", strip=True)):
                el.decompose()

        markers = {str(fn["n"]) for fn in doc["footnotes"]}

        # Compared as characters, not words: the extractor joins runs that
        # get_text(" ") separates (``<span>false</span>.`` -> ``false.``),
        # so word-level diffs report spacing changes as if they were losses.
        dom_words = [
            w for w in norm(body.get_text(" ")).split() if not ignorable(w, markers)
        ]
        got_words = norm(
            text_of(doc["blocks"]) + " " + text_of(doc["footnotes"])
        ).split()
        dom = collections.Counter("".join(dom_words))
        got = collections.Counter("".join(got_words))
        missing = dom - got
        hint = [
            w for w in dom_words
            if w not in got_words and any(c in missing for c in w)
        ][:10]
        rows.append((sum(missing.values()), sum(dom.values()), doc["page"], hint))

    rows.sort(reverse=True)
    lost = sum(r[0] for r in rows)
    total = sum(r[1] for r in rows)
    print(f"{total:,} characters of prose in source; {lost:,} unaccounted for "
          f"({100 * lost / total:.4f}%)")
    bad = [r for r in rows if r[0]]
    if not bad:
        print("  every word of prose is present in the extracted documents")
        return 0
    print(f"  {len(bad)} page(s) with missing text:")
    for n, _, page, missing in bad[:20]:
        print(f"    {n:5d}  {page}   {list(missing)[:10]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
