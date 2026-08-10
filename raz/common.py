"""Shared configuration and helpers for the extraction pipeline."""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build"

#: Root of the readthesequences.com mirror. Override with $RAZ_MIRROR.
MIRROR = Path(
    os.environ.get(
        "RAZ_MIRROR",
        "/Users/gcox/code/rationality-ai-zombies-print-book"
        "/www.readthesequences.com",
    )
)

SITE = "https://www.readthesequences.com"

# Containers that are site navigation rather than book content. Everything
# inside these is dropped before the body is walked.
NAV_CLASSES = (
    "nav_menu",
    "article-talk-selector",
    "bottom_nav",
    "original_lesswrong_link",
)


def mirror_path(page: str) -> Path:
    """Resolve a wiki page name to a file in the mirror.

    The crawl stored most pages twice, under a hyphenated name
    (``Reversed-Stupidity-Is-Not-Intelligence.html``) and a squished one
    (``ReversedStupidityIsNotIntelligence.html``). The hyphenated file is
    canonical: it is what the live site serves, and only it carries the
    modern ``sup > a.footnote`` citation markup.
    """
    page = page.strip().replace(" ", "-")
    candidates = [page, page.replace("-", "")]
    for name in candidates:
        p = MIRROR / f"{name}.html"
        if p.exists():
            return p
    raise FileNotFoundError(f"no mirror file for page {page!r} (tried {candidates})")


_PAGE_INDEX: dict[str, str] | None = None


def page_index() -> dict[str, str]:
    """Map every spelling of a page name to its hyphenated canonical form.

    The crawl saved most pages twice -- ``An-Alien-God.html`` and
    ``AnAlienGod.html`` -- and the wiki links to both spellings
    interchangeably. Squishing the hyphens out of every hyphenated filename
    gives an exact inverse map, so both spellings resolve to one key.
    """
    global _PAGE_INDEX
    if _PAGE_INDEX is None:
        idx = {}
        for p in MIRROR.glob("*.html"):
            name = p.stem
            idx.setdefault(name, name)
            if "-" in name:
                idx[name.replace("-", "")] = name  # canonical wins over squished
        _PAGE_INDEX = idx
    return _PAGE_INDEX


def canonical_page(href: str) -> str:
    """Turn a mirror href or wiki target into a canonical bare page name."""
    href = href.split("#", 1)[0].split("?", 1)[0]
    href = href.rsplit("/", 1)[-1]
    if href.endswith(".html"):
        href = href[: -len(".html")]
    href = href.strip().replace(" ", "-")
    return page_index().get(href, href)


# --- URL normalisation -------------------------------------------------------

# The site skin injects soft hyphens into long URLs so they wrap; they must not
# survive into a printed URL.
_SOFT = re.compile(r"[­​]")

_GREATERWRONG = re.compile(r"^https?://(?:www\.)?greaterwrong\.com(?=/|$)", re.I)
_LESSWRONG = re.compile(r"^https?://(?:www\.)?lesswrong\.com(?=/|$)", re.I)


def normalize_url(url: str) -> str:
    """Clean a rendered href and restore the preferred LessWrong host.

    readthesequences rewrites ``lesswrong.com`` to ``www.greaterwrong.com`` at
    render time; the wiki source keeps the original. Because the rewrite is a
    pure host substitution the path can be carried straight back over, which
    also matches the URLs printed in the physical 2018 edition.
    """
    url = _SOFT.sub("", url.strip())
    url = url.replace("&amp;", "&")
    url = _GREATERWRONG.sub("https://www.lesswrong.com", url)
    url = _LESSWRONG.sub("https://www.lesswrong.com", url)
    return url


def printed_url(url: str) -> str:
    """The address as it appears in a footnote: complete enough to type in.

    The scheme is whichever the link actually uses. 270 of the 492 addresses
    in the book are http://, and printing https:// for those would be printing
    an address that does not resolve.
    """
    return url.rstrip("/")


def display_url(url: str) -> str:
    """The shortened form the site itself shows as link text.

    Not what gets printed -- see printed_url. This exists so that a link whose
    anchor text is the address can be recognised as such whether the page
    wrote it out in full or dropped the scheme.
    """
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    return url.rstrip("/")


# --- numbering ---------------------------------------------------------------

ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]


def roman(n: int) -> str:
    return ROMAN[n]


def alpha(n: int) -> str:
    """1 -> A, 26 -> Z. Sequences are lettered A..Z across the whole work."""
    return chr(ord("A") + n - 1)


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
