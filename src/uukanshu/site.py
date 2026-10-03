"""uukanshu - site access: plain-HTTPS fetching and HTML parsing.

Extracted from the single-module layout so the reader/browse UI and the CLI
share one implementation of the site contract. The package __init__
re-exports these names, so existing callers and tests are unaffected.
Site details and quirks: docs/SCRAPING.md.
"""

import gzip
import html
import http.client
import re
import ssl
import time
import urllib.error
import urllib.request
import zlib
from typing import NamedTuple
from urllib.parse import urljoin, urlsplit

BASE = "https://uukanshu.cc"

# Cap on a single response body; chapter/TOC pages are a few hundred KB at
# most, so a larger response means a broken or hostile server.
_MAX_BYTES = 10 * 1024 * 1024

# ---------------------------------------------------------------- fetching

# uukanshu.cc serves plain HTML to ordinary HTTPS clients; a browser-like
# header set is all it takes (no Cloudflare challenge, no headless browser).
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

# Frozen binaries (PyInstaller) don't see the system CA store reliably;
# prefer certifi's bundled roots, fall back to the system default.
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()


def _retryable(exc: BaseException) -> bool:
    """Hard 4xx answers won't change on retry; 408/429/5xx and transport
    errors might. Deterministic client URL errors (InvalidURL from spaces
    etc.) can never heal — fail fast. See SCRAPING.md."""
    if isinstance(exc, (http.client.InvalidURL, ValueError)):
        return False
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 429) or exc.code >= 500
    return True


def fetch(url: str) -> str:
    """Fetch a uukanshu page over plain HTTPS and return its HTML."""
    page = None
    last_exc = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30,
                                        context=_SSL_CONTEXT) as r:
                # Only gzip/identity are supported: no Accept-Encoding is
                # requested, but some CDNs gzip regardless. Anything else
                # (br, zstd) would decode to silent mojibake — fail loudly
                # instead. Read headers inside the with — after __exit__
                # the response is closed.
                encoding = r.headers.get("Content-Encoding", "").lower()
                if encoding == "gzip":
                    # Streamed with an incremental cap: gzip.decompress() on
                    # 10MB of compressed zeros would OOM (~10GB) before any
                    # length check runs. Truncated bodies raise EOFError here
                    # (retried below like before).
                    chunks = []
                    total = 0
                    with gzip.GzipFile(fileobj=r) as gz:
                        while True:
                            chunk = gz.read(64 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > _MAX_BYTES:
                                raise RuntimeError(f"response from {url} exceeds "
                                                   f"{_MAX_BYTES // (1024 * 1024)} MB")
                            chunks.append(chunk)
                    body = b"".join(chunks)
                elif encoding in ("", "identity"):
                    body = r.read(_MAX_BYTES + 1)
                else:
                    raise RuntimeError(
                        f"unsupported Content-Encoding {encoding!r} "
                        f"from {url}")
                if len(body) > _MAX_BYTES:
                    raise RuntimeError(f"response from {url} exceeds "
                                       f"{_MAX_BYTES // (1024 * 1024)} MB")
            page = body.decode("utf-8", errors="replace")
            break
        # URLError/HTTPError/TimeoutError are all OSError subclasses;
        # http.client's IncompleteRead/BadStatusLine are not — catch both.
        # gzip raises EOFError on a truncated body and zlib.error on a
        # corrupt payload (neither OSError nor HTTPException) — must be
        # caught or a flaky middlebox gives a raw traceback instead of a
        # clean error. urllib.request.Request/urlparse raise ValueError on
        # deterministic bad URLs (e.g. an unmatched '[' -> "Invalid IPv6
        # URL") before any socket is opened; uncaught it would escape as a
        # raw traceback past main()'s RuntimeError/OSError net, so wrap it
        # here (_retryable marks it non-retryable). Unsupported-encoding/
        # size RuntimeErrors must not retry.
        except (OSError, http.client.HTTPException, EOFError, zlib.error,
                ValueError) as exc:
            last_exc = exc
            if attempt == 2 or not _retryable(exc):
                break
            time.sleep(1.5 * (attempt + 1))
    if page is None:
        raise RuntimeError(f"failed to fetch {url}: {last_exc}")
    # Sniff for the Cloudflare interstitial in <title> only — these are
    # English strings that must never trip on a Chinese novel body.
    m = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
    title = html.unescape(m.group(1)) if m else ""
    if ("Attention Required" in title or "Just a moment" in title
            or "you have been blocked" in title):
        raise RuntimeError(
            "blocked by Cloudflare — try again later or from a "
            "different network")
    return page



def absolutize(href: str, url: str) -> str:
    """Resolve an href found on `url` against it. Hand-rolling this with
    `BASE + href` breaks for directory-relative hrefs, protocol-relative
    "//host/..." refs, and anything that merely starts with "http"."""
    if href.startswith(("http://", "https://")):
        return href
    return urljoin(url, href)


# Single anchor source so chapter_list/link/breadcrumb can't drift.
# See SCRAPING.md. Whitespace around `=` tolerated (legal HTML).
_ANCHOR_RE = re.compile(
    r'<a\s[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.S | re.I)


def _iter_anchors(page: str) -> list[tuple[str, str]]:
    """Raw (href, inner_html) pairs in document order."""
    return [(m.group(1), m.group(2)) for m in _ANCHOR_RE.finditer(page)]


_CHAPTER_PATH = re.compile(r"/book/(\d+)/(\d+)\.html", re.I)
_HOST = re.compile(r"(?:www\.)?uukanshu\.cc", re.I)


class Chapter(NamedTuple):
    """One TOC row; tuple-compatible. See ARCHITECTURE.md module map."""
    pos: int
    cid: int
    title: str
    url: str


def link(page: str, url: str, label: str):
    """Return the nav anchor's href as an absolute chapter URL, or None.

    Only chapter-shaped hrefs are accepted (consistent with chapter_list,
    which also lists only /book/<id>/<id>.html pages). At the book's ends
    the site points prev/next at the TOC index or a lastchapter.php stub
    that would fail to parse — the caller treats None as 'no chapter in
    that direction' and shows the end-of-book notification.
    """
    # `label` is a regex fragment ("上一章", "目[录錄]", ...); href may or
    # may not be the anchor's first attribute. Resolve BEFORE validating:
    # directory-relative hrefs ("456.html") only become chapter-shaped
    # after urljoin, and rejecting them upfront yields a false end-of-book.
    # Inner tags (<span>) and case variations are tolerated; query/fragment
    # are stripped before the chapter-shape check and the canonical URL
    # without query is returned (consistent with chapter_list which
    # returns BASE+path). Anchors scanned via _iter_anchors so all parsers
    # share one source. See SCRAPING.md nav section.
    label_re = re.compile(
        rf"(?:\s*<[^>]+>\s*)*{label}(?:\s*<[^>]+>\s*)*\s*", re.I)
    href_raw = None
    for _href, _inner in _iter_anchors(page):
        if label_re.fullmatch(_inner):
            href_raw = _href
            break
    if href_raw is None:
        return None
    abs_url = absolutize(href_raw, url)
    try:
        p = urlsplit(abs_url)
    except ValueError:
        return None
    # Single canonical check (host case-insensitive, query/fragment
    # dropped); relative refs were resolved via urljoin above. See
    # SCRAPING.md nav section.
    if p.scheme.lower() not in ("http", "https"):
        return None
    if not _HOST.fullmatch(p.netloc.lower()):
        return None
    m = _CHAPTER_PATH.fullmatch(p.path)
    if not m:
        return None
    return f"{BASE}/book/{int(m.group(1))}/{int(m.group(2))}.html"


def chapter_list(toc_page: str, book_id: str | None = None) -> list[Chapter]:
    """Return [Chapter(pos, cid, title, url)] for a book TOC page.

    The TOC page leads with a 'latest updates' block whose chapters also
    appear in the full ordered list below. Keeping the LAST occurrence of
    each (book, chapter) pair positions chapters in reading order.

    book_id, when given, drops chapter links that point at a different
    book (recommendation blocks etc.); None accepts every book. Chapter
    hrefs may be site-relative or absolute; query/fragment stripped and
    URLs canonicalized to BASE + /book/<int>/<int>.html. Dedup key is
    (int(book), int(chap)) so zero-padded variants don't duplicate.
    See SCRAPING.md.
    """
    matches: list[tuple[int, int, str]] = []  # (book, chap, inner)
    for href_raw, inner in _iter_anchors(toc_page):
        try:
            p = urlsplit(href_raw)
        except ValueError:
            continue
        if p.scheme and p.scheme.lower() not in ("http", "https"):
            continue
        if p.netloc and not _HOST.fullmatch(p.netloc.lower()):
            continue
        m = _CHAPTER_PATH.fullmatch(p.path)
        if not m:
            continue
        matches.append((int(m.group(1)), int(m.group(2)), inner.strip()))
    # Compare book ids numerically so "--book 00123" matches "/book/123/"
    # links; a non-numeric --book id matches nothing (clean empty downstream).
    if book_id is None:
        wanted = None
    else:
        try:
            wanted = int(str(book_id).strip())
        except ValueError:
            wanted = -1
    last_idx = {}
    for i, m in enumerate(matches):
        if wanted is not None and m[0] != wanted:
            continue
        last_idx[(m[0], m[1])] = i
    out, seen = [], set()
    for i, m in enumerate(matches):
        if wanted is not None and m[0] != wanted:
            continue
        key = (m[0], m[1])
        if key in seen or last_idx[key] != i:
            continue
        seen.add(key)
        # Title may contain inner tags (<b>); strip them. See SCRAPING.md.
        title = html.unescape(re.sub(r"<[^>]+>", "", m[2]).strip())
        out.append(Chapter(len(out) + 1, m[1], title,
                           f"{BASE}/book/{m[0]}/{m[1]}.html"))
    return out


def chapter_id(url: str) -> int | None:
    """The numeric chapter page id in a chapter URL, else None."""
    m = re.search(r"/book/\d+/(\d+)\.html", url or "", re.I)
    return int(m.group(1)) if m else None


def extract_chapter(page: str, url: str):
    """Pull book name, chapter title, clean text, and nav links from a page."""
    t = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S | re.I)
    title = (html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip()
             if t else url)

    # Breadcrumb anchors via _iter_anchors; inner tags stripped like
    # chapter titles. See SCRAPING.md.
    _bc_re = re.compile(r"(?:https?://[^\"']*)?/book/\d+/", re.I)
    bc: list[str] = []
    for _href, _inner in _iter_anchors(page):
        if _bc_re.fullmatch(_href):
            bc.append(html.unescape(re.sub(r"<[^>]+>", "", _inner)).strip())
    # Prefer the breadcrumb anchor for THIS book's id; the last match in
    # document order is only a fallback, so a footer/recommendation block
    # linking another book's index can't rename the title bar.
    book = ""
    book_id = re.search(r"/book/(\d+)/", url)
    if book_id:
        _own_re = re.compile(
            rf"(?:https?://[^\"']*)?/book/{book_id.group(1)}/", re.I)
        bc_own = [html.unescape(re.sub(r"<[^>]+>", "", _inner)).strip()
                  for _href, _inner in _iter_anchors(page)
                  if _own_re.fullmatch(_href)]
        bc_own = [b for b in bc_own if b]
        if bc_own:
            book = bc_own[0]
    bc = [b for b in bc if b]
    if not book and bc:
        book = bc[-1]

    m = re.search(r'<div\b[^>]*class\s*=\s*["\'][^"\']*\breadcotent\b[^"\']*["\'][^>]*>(.*)', page, re.S | re.I)
    if not m:
        raise RuntimeError("could not find chapter content on the page "
                           "(is this a chapter URL?)")
    body = m.group(1)
    # Cut at the nav-row container: from the "上一章 / 章节目录 / 下一章"
    # box to the end of the page it's all UI/footer noise — the keyboard
    # tip, the copyright blurb, the "Copyright ... TOP↑" footer, and the
    # GTM iframe/noscript leftovers — never chapter text. Case-insensitive
    # like the readcotent search above; see SCRAPING.md.
    # GTM iframe/noscript leftovers — never chapter text. Token match on
    # class (any attr order, extra classes) like the readcotent search
    # above; see SCRAPING.md.
    body = re.split(r'<div\b[^>]*class\s*=\s*["\'][^"\']*\bmulu-box\b[^"\']*["\']', body, maxsplit=1, flags=re.I)[0]
    body = re.sub(r"<(script|style|noscript|iframe)[^>]*>.*?</\1\s*>", "", body, flags=re.S | re.I)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", "", body)
    body = body.replace("&emsp;", "")
    lines = [line.strip()
             for line in html.unescape(body).splitlines() if line.strip()]
    text = "\n\n".join(lines)
    # Belt-and-braces for pages without the mulu-box container: also cut at
    # the literal nav row, tolerating the "章节/章節" prefix (simplified /
    # traditional) that prefixes the link label. Whitespace between tokens
    # is optional (stripped anchors may abut); require a line break
    # before 上一章 so an in-body mention ("有人說上一章 ... 很好笑")
    # doesn't truncate the chapter, and cut at the LAST standalone nav
    # row rather than the first mention. No trailing guard: footer may abut
    # the nav row after tag stripping. See SCRAPING.md.
    _nav_pat = re.compile(r"\n上一章\s*(?:章节|章節)?\s*目[录錄]\s*下一章")
    _nav_matches = list(_nav_pat.finditer(text))
    if _nav_matches:
        text = text[:_nav_matches[-1].start()].rstrip()

    return (book, title, text,
            link(page, url, "上一章"), link(page, url, "目[录錄]"),
            link(page, url, "下一章"))


