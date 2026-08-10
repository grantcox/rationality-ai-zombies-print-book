"""Step 3 — build the link manifests.

Two products, both meant to be read and hand-corrected once before the book
is typeset:

  build/links_external.csv   one row per distinct external URL -- the text
                             that will appear in a '->' footnote, plus every
                             chapter that cites it.
  build/links_internal.csv   one row per distinct cross-reference target,
                             resolved to the book and chapter number that a
                             '[book] III, chapter 132' footnote needs.

External URLs are already un-rewritten from greaterwrong.com back to
lesswrong.com by common.normalize_url; that is a pure host substitution, so
the path carries over untouched.

overrides.csv at the repository root records what to do about links that are
broken or that go somewhere other than the text implies. It is the only file
edited by hand; build/links_review.csv is a regenerated report listing what
still needs a ruling, and its first five columns are overrides.csv's, so
accepting a decision is a copy-and-paste.

Run:  python -m raz.links            build the manifests
      python -m raz.links --check    also probe every URL and record status
"""

from __future__ import annotations

import collections
import concurrent.futures
import csv
import glob
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

from . import overrides
from .common import BUILD, REPO, display_url

#: Cached HTTP probe results. This lives at the repository root, not under
#: build/, because it is not derived from the mirror -- it is a record of what
#: the open web looked like when the links were checked. Rebuilding it means
#: several minutes of requests against 492 hosts, so `rm -rf build` must not
#: take it with it.
STATUS_CACHE = REPO / "link_status.json"


def load_docs() -> list[dict]:
    return [
        json.loads(open(f, encoding="utf-8").read())
        for f in sorted(glob.glob(str(BUILD / "chapters" / "*.json")))
    ]


def walk(node):
    if isinstance(node, dict):
        if "t" in node:
            yield node
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def anchor_text(node) -> str:
    parts = [n["v"] for n in walk(node.get("c", [])) if n.get("t") == "text"]
    return " ".join("".join(parts).split())


def label(entry: dict) -> str:
    """How a cross-reference to this page reads in a footnote."""
    if entry["kind"] == "chapter":
        return (f"Book {entry['book_roman']}, chapter {entry['number']}, "
                f"“{entry['title']}”")
    if entry["kind"] == "sequence":
        return (f"Book {entry['book_roman']}, sequence "
                f"{entry['sequence_letter']}, “{entry['title']}”")
    if entry["kind"] == "book":
        return f"Book {entry['book_roman']}, “{entry['title']}”"
    if entry["kind"] == "interlude":
        return f"Book {entry['book_roman']}, “{entry['title']}”"
    return f"“{entry['title']}”"


def build():
    docs = load_docs()
    spine = {e["page"]: e for e in
             json.loads((BUILD / "toc.json").read_text(encoding="utf-8"))["entries"]}

    ext: dict[str, dict] = {}
    internal: dict[str, dict] = {}

    for doc in docs:
        where = doc["page"]
        for n in walk([doc["blocks"], doc["footnotes"]]):
            if n.get("t") == "link":
                if n.get("absorbed_by"):
                    # The tail of a URL the wiki split in two; it is not a
                    # reference in its own right and gets no footnote.
                    continue
                rec = ext.setdefault(
                    n["url"],
                    {"url": n["url"], "display": display_url(n["url"]),
                     "host": urllib.parse.urlparse(n["url"]).netloc,
                     "count": 0, "pages": [], "anchors": [],
                     "suggested": "", "orphan": ""},
                )
                rec["count"] += 1
                rec["pages"].append(where)
                if n.get("suggested_url"):
                    rec["suggested"] = n["suggested_url"]
                    rec["orphan"] = n.get("orphan_tail", "")
                a = anchor_text(n)
                if a and a not in rec["anchors"]:
                    rec["anchors"].append(a)
            elif n.get("t") == "xref":
                rec = internal.setdefault(
                    n["page"],
                    {"target": n["page"], "count": 0, "pages": [], "anchors": []},
                )
                rec["count"] += 1
                rec["pages"].append(where)
                a = anchor_text(n)
                if a and a not in rec["anchors"]:
                    rec["anchors"].append(a)

    for rec in internal.values():
        entry = spine.get(rec["target"])
        rec["resolved"] = entry is not None
        rec["kind"] = entry["kind"] if entry else ""
        rec["book"] = entry.get("book_roman", "") if entry else ""
        rec["number"] = entry.get("number", "") if entry else ""
        rec["title"] = entry["title"] if entry else ""
        rec["footnote"] = label(entry) if entry else ""

    status = {}
    if STATUS_CACHE.exists():
        status = json.loads(STATUS_CACHE.read_text(encoding="utf-8"))

    rules = overrides.load()

    ext_rows = sorted(ext.values(), key=lambda r: (-r["count"], r["url"]))
    for r in ext_rows:
        probe = status.get(r["url"]) or {}
        r["status"] = probe.get("status", "")
        r["final"] = probe.get("final", "")
        # Recomputed here rather than read from the cache, so the heuristic
        # can be tuned without re-probing 492 URLs.
        r["verdict"] = (
            landing_verdict(r["url"], r["status"], r["final"], probe.get("size", 0))
            if probe else ""
        )
        r["anchor_is_url"] = any(anchor_is_url(a, r) for a in r["anchors"])
        d = overrides.decide(rules, r["url"])
        r["action"] = d["action"]
        r["final_url"] = d["url"]
        r["reviewed"] = d["reviewed"]
        r["note"] = d["note"]

    with open(BUILD / "links_external.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "display", "host", "count", "status", "verdict",
                    "landed_on", "action", "final_url", "suggested_url",
                    "orphan_text", "anchor_text", "cited_in"])
        for r in ext_rows:
            w.writerow([r["url"], r["display"], r["host"], r["count"],
                        r["status"], r["verdict"], r["final"], r["action"],
                        r["final_url"], r["suggested"], r["orphan"],
                        " | ".join(r["anchors"][:3]),
                        " ".join(sorted(set(r["pages"])))])

    needs = [r for r in ext_rows
             if not r["reviewed"] and r["verdict"] not in ("ok", "")]
    # This file is a report, regenerated on every run and never edited: the
    # first five columns are exactly overrides.csv's, so accepting a decision
    # is a straight copy of the row's first five cells into that file. The
    # underscored columns are the evidence behind the suggested action, and
    # '_if_dropped' leads them because it decides 'remove' vs 'unlink' --
    # picking wrong deletes words from a sentence.
    header = ["url", "action", "replacement", "page", "note",
              "_if_dropped", "_link_text", "_verdict", "_status",
              "_landed_on", "_orphan_text", "_count", "_cited_in"]
    with open(REVIEW, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in sorted(needs, key=lambda r: (r["verdict"], -r["count"])):
            w.writerow([
                r["url"], overrides.suggest(r), "", "", "",
                "remove (link text is the URL)" if r["anchor_is_url"]
                else "unlink (link text is prose — remove would delete it)",
                " | ".join(r["anchors"][:2]),
                r["verdict"], r["status"], r["final"], r["orphan"],
                r["count"], " ".join(sorted(set(r["pages"])))])

    int_rows = sorted(internal.values(), key=lambda r: (-r["count"], r["target"]))
    with open(BUILD / "links_internal.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["target", "resolved", "kind", "book", "chapter_number",
                    "title", "footnote_text", "count", "anchor_text", "cited_in"])
        for r in int_rows:
            w.writerow([r["target"], r["resolved"], r["kind"], r["book"],
                        r["number"], r["title"], r["footnote"], r["count"],
                        " | ".join(r["anchors"][:3]),
                        " ".join(sorted(set(r["pages"])))])

    hosts = collections.Counter(r["host"] for r in ext_rows)
    lw = sum(r["count"] for r in ext_rows if "lesswrong.com" in r["host"])
    total_ext = sum(r["count"] for r in ext_rows)
    unresolved = [r for r in int_rows if not r["resolved"]]

    print(f"build/links_external.csv: {len(ext_rows)} distinct URLs, "
          f"{total_ext} citations ({lw} to LessWrong, {total_ext - lw} elsewhere)")
    print("  top hosts: " + ", ".join(f"{h} ({c})" for h, c in hosts.most_common(6)))
    if status:
        verdicts = collections.Counter(r["verdict"] for r in ext_rows)
        print("  landing check: " + ", ".join(
            f"{k or 'unprobed'}: {v}" for k, v in verdicts.most_common()))
    else:
        print(f"  no probe results at {STATUS_CACHE.name} — "
              f"run `python -m raz.links --check` to judge link health")

    # Reported whether or not the links have been probed: these are facts
    # about the decisions themselves, and going quiet without probe data
    # would hide an unsafe rule.
    decided = sum(1 for r in ext_rows if r["reviewed"])
    outstanding = [r for r in ext_rows
                   if not r["reviewed"] and r["verdict"] not in ("ok", "")]
    print(f"  decisions recorded in overrides.csv: {decided}; "
          f"still to rule on: {len(outstanding)} "
          f"(see build/links_review.csv)")
    # A decision whose URL is no longer a link in the book -- usually a
    # ruling made before an extraction fix removed the link.
    live = {r["url"] for r in ext_rows}
    stale = sorted({url for (url, _page) in rules if url not in live})
    if stale:
        print(f"  {len(stale)} decision(s) no longer match any link in the "
              f"book and can be deleted from overrides.csv:")
        for url in stale:
            print(f"    {url}")

    problems = overrides.validate_against_links(rules, ext_rows)
    if problems:
        print(f"  ! {len(problems)} unsafe decision(s) in overrides.csv:")
        for p in problems:
            print(f"    ✗ {p}")

    truncated = [r for r in ext_rows if r["suggested"]]
    if truncated:
        print(f"  {len(truncated)} URL(s) truncated by the wiki's auto-linker; "
              f"a reconstruction is suggested in the CSV for review:")
        for r in truncated:
            print(f"    {r['url']}  ->  {r['suggested']}")
    print(f"build/links_internal.csv: {len(int_rows)} distinct targets, "
          f"{sum(r['count'] for r in int_rows)} cross-references")
    print(f"  resolved to a numbered place in the book: "
          f"{len(int_rows) - len(unresolved)}/{len(int_rows)}")
    for r in unresolved:
        print(f"    unresolved: {r['target']} ({r['count']}x, "
              f"e.g. “{(r['anchors'] or [''])[0]}”)")
    return ext_rows


DEAD = ("404", "410", "000", "err", "500", "502", "503")


REVIEW = BUILD / "links_review.csv"


def anchor_is_url(anchor: str, row: dict) -> bool:
    """Is the visible link text just the URL itself?

    This decides 'remove' vs 'unlink' -- whether dropping a dead link should
    take its anchor text with it. The comparison has to strip the soft hyphens
    the site skin injects into displayed URLs, or a bare-URL anchor reads as
    prose and the wrong action gets suggested.
    """
    a = anchor.replace("­", "").replace("​", "").strip().rstrip("./")
    return a in (row["url"].rstrip("/"), row["display"].rstrip("/"))


def host(netloc: str) -> str:
    """Bare host for comparison. removeprefix, not lstrip -- lstrip('www.')
    would eat the leading 'w' of a host like wjh-www.harvard.edu."""
    return netloc.lower().removeprefix("www.")


def landing_verdict(url: str, code: str, final: str, size: int) -> str:
    """Judge whether a 200 actually landed on the thing the text points at.

    A live status is not the same as a live page. A lapsed domain that has
    been re-registered answers 200, and a site that has dropped an old article
    usually redirects to its front page rather than returning 404. Both look
    healthy in a status-only check, so the landing URL is compared with the
    one the book cites.
    """
    if code in DEAD:
        return "dead"
    if code.startswith(("4", "5")):
        return "blocked"  # 403/406/429 -- usually fine in a browser
    if not final:
        return "ok"
    want, got = urllib.parse.urlparse(url), urllib.parse.urlparse(final)
    want_path = want.path.rstrip("/")
    got_path = got.path.rstrip("/")
    if want_path and not got_path:
        return "redirected-to-root"  # the article is gone; front page served
    if host(want.netloc) != host(got.netloc):
        # A site that moved domain but kept its URLs (theregister.co.uk ->
        # .com) still serves the cited article, as does a geo-redirect
        # (books.google.com -> .com.au). Only a host change that also changes
        # the path is worth a human look.
        if want_path and want_path == got_path:
            return "ok"
        return "changed-target"
    if size and size < 512:
        return "suspiciously-small"
    return "ok"


def check(rows):
    """Probe every distinct URL once, recording where it actually lands."""
    def probe(url):
        try:
            out = subprocess.run(
                ["curl", "-sS", "-o", "/dev/null", "-m", "20",
                 "-w", "%{http_code}\t%{url_effective}\t%{size_download}",
                 "-L", "--max-redirs", "5", "-A", "Mozilla/5.0", url],
                capture_output=True, text=True, timeout=40,
            )
            code, _, rest = out.stdout.strip().partition("\t")
            final, _, size = rest.partition("\t")
            return url, {"status": code or "000", "final": final,
                         "size": int(size) if size.isdigit() else 0}
        except Exception:
            return url, {"status": "err", "final": "", "size": 0}

    print(f"\nprobing {len(rows)} URLs …")
    status = {}
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        for i, (url, rec) in enumerate(ex.map(probe, [r["url"] for r in rows]), 1):
            rec["verdict"] = landing_verdict(url, rec["status"], rec["final"], rec["size"])
            status[url] = rec
            if i % 100 == 0:
                print(f"  {i}/{len(rows)}")
    STATUS_CACHE.write_text(json.dumps(status, indent=1), encoding="utf-8")

    print("  status:  " + ", ".join(
        f"{k}: {v}" for k, v in
        collections.Counter(r["status"] for r in status.values()).most_common()))
    print("  landing: " + ", ".join(
        f"{k}: {v}" for k, v in
        collections.Counter(r["verdict"] for r in status.values()).most_common()))
    return status


if __name__ == "__main__":
    rows = build()
    if "--check" in sys.argv:
        check(rows)
        build()  # rewrite the CSV with the status column filled in
