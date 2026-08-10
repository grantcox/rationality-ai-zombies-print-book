"""Hand-made decisions about individual links.

The probe can tell you a link is broken. It cannot tell you what to do about
it -- that is a judgement about the text, and it belongs in a file a human
edits, not in the extractor. `overrides.csv` in the repository root is that
file; it is the only place where the book deviates from its source, so the
whole set of deviations can be read in one sitting and reviewed in a diff.

Columns:

    url         the URL exactly as it appears in build/links_external.csv
    action      keep | replace | archive | unlink | remove
    replacement the new URL, for 'replace'; optional for 'archive'
    page        optional -- restrict this decision to one chapter, by page
                name. Blank applies it everywhere the URL occurs.
    note        why. Free text; it ends up in the review record, not the book.

The actions, and when each is right:

  keep       Print it as it stands. Use to record "I checked this, the probe
             is wrong" -- a 403 that a browser opens fine, say. Without a row,
             an unreviewed link is also kept, so this is about saying so.

  replace    The page moved. Print the footnote against the new URL.

  archive    The page is gone but was captured. Print the Wayback snapshot.
             Leave `replacement` blank to use the generic
             web.archive.org/web/<url> form, which resolves to the newest
             snapshot, or paste a dated snapshot to pin one.

  unlink     Drop the footnote; keep the sentence exactly as written. Right
             when the anchor is a phrase that reads perfectly as prose --
             "the least convenient path" needs no footnote to make sense.

  remove     Drop the footnote and the anchor text with it. Right only when
             the anchor text *is* the URL, so leaving it would print a dead
             address as if it were prose. This is the Bibliography case: the
             entry keeps its author, title and journal, and loses the
             trailing locator.

`unlink` and `remove` differ in exactly one way -- whether the anchor text
survives -- and picking the wrong one either strands a bare dead URL in the
text or eats a clause of a sentence. build/links_review.csv suggests the
right one per row, based on whether the anchor text is the URL.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .common import REPO

OVERRIDES = REPO / "overrides.csv"

ACTIONS = {"keep", "replace", "archive", "unlink", "remove"}
FIELDS = ["url", "action", "replacement", "page", "note"]

WAYBACK = "https://web.archive.org/web/{url}"


class OverrideError(Exception):
    pass


def load(path: Path | None = None) -> dict[tuple[str, str], dict]:
    """Read overrides.csv into a {(url, page): decision} map.

    A row with no page is stored under page ''. Look-ups should try the
    specific page first and fall back to '' -- see `decide`.
    """
    path = path or OVERRIDES
    if not path.exists():
        return {}
    out: dict[tuple[str, str], dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for lineno, row in enumerate(csv.DictReader(f), 2):
            if not row.get("url", "").strip() or row["url"].lstrip().startswith("#"):
                continue
            url = row["url"].strip()
            action = (row.get("action") or "").strip().lower()
            page = (row.get("page") or "").strip()
            replacement = (row.get("replacement") or "").strip()

            if action not in ACTIONS:
                raise OverrideError(
                    f"{path.name}:{lineno}: action {action!r} is not one of "
                    f"{', '.join(sorted(ACTIONS))}"
                )
            if action == "replace" and not replacement:
                raise OverrideError(
                    f"{path.name}:{lineno}: action 'replace' needs a replacement URL"
                )
            if action in ("unlink", "remove", "keep") and replacement:
                raise OverrideError(
                    f"{path.name}:{lineno}: action {action!r} takes no replacement URL"
                )
            if (url, page) in out:
                raise OverrideError(
                    f"{path.name}:{lineno}: duplicate rule for {url!r}"
                    + (f" on page {page!r}" if page else "")
                )
            out[(url, page)] = {
                "action": action,
                "replacement": replacement,
                "note": (row.get("note") or "").strip(),
                "line": lineno,
            }
    return out


def decide(rules: dict, url: str, page: str = "") -> dict:
    """The decision for a URL on a page: page-specific rule wins over global."""
    rule = rules.get((url, page)) or rules.get((url, ""))
    if rule is None:
        return {"action": "keep", "url": url, "note": "", "reviewed": False}
    action = rule["action"]
    new_url = url
    if action == "replace":
        new_url = rule["replacement"]
    elif action == "archive":
        new_url = rule["replacement"] or WAYBACK.format(url=url)
    return {
        "action": action,
        "url": new_url,
        "note": rule["note"],
        "reviewed": True,
    }


def suggest(row: dict) -> str:
    """The action to propose for a row of the review worklist."""
    verdict = row.get("verdict", "")
    if verdict in ("blocked", "ok"):
        return "keep"
    anchor_is_url = row.get("anchor_is_url")
    return "remove" if anchor_is_url else "unlink"


def validate_against_links(rules: dict, rows: list[dict]) -> list[str]:
    """Problems only visible once the link data is to hand.

    load() can check a row's shape but not its consequences. The one that
    matters is 'remove' on a link whose text is prose: it drops the anchor
    along with the footnote, deleting words from a sentence. Checking it here
    means it is caught however the rule got into the file.
    """
    by_url = {r["url"]: r for r in rows}
    problems = []
    for (url, page), rule in sorted(rules.items()):
        row = by_url.get(url)
        if row is None:
            continue  # no longer a link in the book; reported as stale
        if rule["action"] == "remove" and not row.get("anchor_is_url"):
            anchor = (row.get("anchors") or [""])[0]
            where = f" on {page}" if page else ""
            problems.append(
                f"{url}{where}: 'remove' would delete the link text from the "
                f"sentence — it reads {anchor[:60]!r}, not a URL. "
                f"Use 'unlink' to drop the footnote and keep the words."
            )
    return problems
