"""Step 1 — build the book spine from the mirror's Table of Contents.

``Contents.html`` is the only place that carries the work's structure *and*
its numbering. The nesting gives books > sequences > chapters, and the wiki's
``%item value=N%`` markers survive into the rendered HTML as ``<li value="N">``,
pinning the global counters at each book boundary.

The result is 6 books, 26 sequences (lettered A..Z across the whole work) and
333 numbered chapters, which is exactly what the LaTeX edition's counters
produce independently -- see README.

Run:  python -m raz.toc
"""

from __future__ import annotations

import json
import sys

from bs4 import BeautifulSoup

from .common import BUILD, MIRROR, alpha, canonical_page, roman


def _entry(li):
    """The page name and title for a list item, ignoring nested lists."""
    a = li.find("a", recursive=False)
    if a is None:  # PmWiki sometimes emits a leading text node
        for cand in li.find_all("a", recursive=True):
            if cand.find_parent(["ul", "ol"]) is li.find_parent(["ul", "ol"]) or True:
                a = cand
                break
    return canonical_page(a["href"]), a.get_text(" ", strip=True)


def _child_lists(li):
    """Direct child ``<ul>``/``<ol>`` of a list item, in document order."""
    return [c for c in li.find_all(["ul", "ol"], recursive=False)]


def _direct_items(lst):
    return lst.find_all("li", recursive=False)


class Counter:
    """A global counter that the TOC's explicit ``value`` markers can pin."""

    def __init__(self, name):
        self.name = name
        self.n = 0
        self.pins = 0

    def next(self, li):
        want = li.get("value")
        self.n += 1
        if want is not None:
            want = int(want)
            if want != self.n:
                print(
                    f"  ! {self.name} counter drifted: TOC pins {want}, "
                    f"counted {self.n} -- trusting the TOC",
                    file=sys.stderr,
                )
                self.n = want
            self.pins += 1
        return self.n


def build():
    html = (MIRROR / "Contents.html").read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    root = soup.select("div.big_toc")[-1].find("ul", recursive=False)

    seq_no = Counter("sequence")
    ch_no = Counter("chapter")
    entries: list[dict] = []
    books: list[dict] = []

    def add(kind, page, title, **extra):
        entries.append(
            {"order": len(entries), "kind": kind, "page": page, "title": title, **extra}
        )
        return entries[-1]

    for li in _direct_items(root):
        page, title = _entry(li)
        lists = _child_lists(li)
        is_book = any(
            "upper-alpha" in (l.get("style") or "") for l in lists if l.name == "ol"
        )

        if not is_book:
            # Biases: An Introduction (front), Bibliography, Glossary (back).
            kind = "frontmatter" if not books else "backmatter"
            add(kind, page, title)
            continue

        bno = len(books) + 1
        book_title = title.split(":", 1)[1].strip() if ":" in title else title
        book = {
            "number": bno,
            "roman": roman(bno),
            "page": page,
            "title": book_title,
            "full_title": title,
        }
        books.append(book)
        bref = {"book": bno, "book_roman": book["roman"], "book_title": book_title}
        add("book", page, title, **bref)

        for lst in lists:
            if lst.name == "ul":
                # Unnumbered interludes ("The Simple Truth", the per-book
                # introductions, "A Technical Explanation of ...").
                for ili in _direct_items(lst):
                    ipage, ititle = _entry(ili)
                    add("interlude", ipage, ititle, **bref)
                continue

            for sli in _direct_items(lst):
                spage, stitle = _entry(sli)
                s = seq_no.next(sli)
                sref = {
                    **bref,
                    "sequence": s,
                    "sequence_letter": alpha(s),
                    "sequence_title": stitle,
                }
                add("sequence", spage, stitle, **sref)
                for inner in _child_lists(sli):
                    for cli in _direct_items(inner):
                        cpage, ctitle = _entry(cli)
                        add(
                            "chapter",
                            cpage,
                            ctitle,
                            number=ch_no.next(cli),
                            **sref,
                        )

    spine = {
        "books": books,
        "counts": {
            "books": len(books),
            "sequences": seq_no.n,
            "chapters": ch_no.n,
            "interludes": sum(1 for e in entries if e["kind"] == "interlude"),
            "entries": len(entries),
        },
        "entries": entries,
    }

    BUILD.mkdir(exist_ok=True)
    out = BUILD / "toc.json"
    out.write_text(json.dumps(spine, indent=1, ensure_ascii=False), encoding="utf-8")

    c = spine["counts"]
    print(f"{out.relative_to(BUILD.parent)}: {c['books']} books, "
          f"{c['sequences']} sequences, {c['chapters']} chapters, "
          f"{c['interludes']} interludes ({c['entries']} pages)")
    print(f"  numbering pinned by TOC at {seq_no.pins} sequence / "
          f"{ch_no.pins} chapter boundaries")
    return spine


if __name__ == "__main__":
    build()
