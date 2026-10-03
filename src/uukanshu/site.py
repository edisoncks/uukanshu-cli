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
from urllib.parse import quote, urljoin, urlsplit

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


def canonical_chapter_url(href: str, base: str) -> str | None:
    """Resolve href against base and return a canonical chapter URL.

    One resolve -> strip query/fragment -> shape-check step shared by the
    prev/next nav (link) and card parsing, so tracking params never fork
    URLs and validation cannot drift. Non-chapter hrefs (TOC index,
    lastchapter.php stubs) return None. See SCRAPING.md.
    """
    abs_url = absolutize(href, base)
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
    return canonical_chapter_url(href_raw, url)


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


# --------------------------------------------------------------- catalogue

# Fixed category catalogue: /class_<id>_<page>.html, ids 1..10. Stored in
# Simplified like every built-in chrome string; the UI converts with ui().
CATEGORIES = (
    (1, "玄幻奇幻"),
    (2, "武侠仙侠"),
    (3, "现代都市"),
    (4, "历史军事"),
    (5, "科幻小说"),
    (6, "游戏竞技"),
    (7, "恐怖灵异"),
    (8, "言情小说"),
    (9, "动漫同人"),
    (10, "其他类型"),
)


def recent_url(page: int) -> str:
    """Recently-updated list page (30 cards each)."""
    return f"{BASE}/top/lastupdate_{page}.html"


def category_url(cid: int, page: int) -> str:
    """Category list page (30 cards each)."""
    return f"{BASE}/class_{cid}_{page}.html"


def search_url(keyword: str, page: int) -> str:
    """Search-results page URL for page > 1 (page 1 is the POST target)."""
    return f"{BASE}/search/{quote(keyword, safe='')}_{page}.html"


class Card(NamedTuple):
    """One book card from a recent/category/search list page."""
    bid: int
    title: str
    author: str
    words: str
    reads: str
    latest_title: str
    latest_url: str | None
    intro: str


class BookMeta(NamedTuple):
    """Book-detail header from /book/<id>/ or a single-result search page."""
    title: str
    author: str
    words: str
    category: str
    status: str
    intro: str
    latest_title: str
    latest_url: str | None
    updated_at: str


# Card layout: bookbox > p10 > bookinfo > h4.bookname + div.author*3 +
# div.cat + div.update. Class tokens (not full class strings) so extra
# classes / attribute order cannot break a parse; see SCRAPING.md.
_BOOKBOX_RE = re.compile(
    r'<div\b[^>]*class\s*=\s*["\'][^"\']*\bbookbox\b[^"\']*["\'][^>]*>', re.I)
_BOOKNAME_RE = re.compile(
    r'<h4\b[^>]*class\s*=\s*["\'][^"\']*\bbookname\b[^"\']*["\'][^>]*>(.*?)</h4>',
    re.S | re.I)
_AUTHOR_DIV_RE = re.compile(
    r'<div\b[^>]*class\s*=\s*["\'][^"\']*\bauthor\b[^"\']*["\'][^>]*>(.*?)</div>',
    re.S | re.I)
_CAT_DIV_RE = re.compile(
    r'<div\b[^>]*class\s*=\s*["\'][^"\']*\bcat\b[^"\']*["\'][^>]*>(.*?)</div>',
    re.S | re.I)
_UPDATE_DIV_RE = re.compile(
    r'<div\b[^>]*class\s*=\s*["\'][^"\']*\bupdate\b[^"\']*["\'][^>]*>(.*?)</div>',
    re.S | re.I)
_BOOK_HREF_RE = re.compile(r"/book/(\d+)(?:/|$)", re.I)
_PAGESTATS_RE = re.compile(
    r'<em\b[^>]*id\s*=\s*["\']pagestats["\'][^>]*>\s*(\d+)\s*/\s*(\d+)\s*</em>',
    re.I)
_SEARCH_TOTAL_RE = re.compile(r"共有\s*(?:<b\b[^>]*>)?\s*(\d+)", re.I)
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_META_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']')
_BOOKTITLE_RE = re.compile(
    r'<h1\b[^>]*class\s*=\s*["\'][^"\']*\bbooktitle\b[^"\']*["\'][^>]*>(.*?)</h1>',
    re.S | re.I)
_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.S | re.I)
_BOOKTAG_RE = re.compile(
    r'<p\b[^>]*class\s*=\s*["\'][^"\']*\bbooktag\b[^"\']*["\'][^>]*>(.*?)</p>',
    re.S | re.I)
_BOOKINTRO_RE = re.compile(
    r'<p\b[^>]*class\s*=\s*["\'][^"\']*\bbookintro\b[^"\']*["\'][^>]*>(.*?)</p>',
    re.S | re.I)
_BOOKTIME_RE = re.compile(
    r'<p\b[^>]*class\s*=\s*["\'][^"\']*\bbooktime\b[^"\']*["\'][^>]*>(.*?)</p>',
    re.S | re.I)
_SPAN_RE = re.compile(r"<span\b([^>]*)>(.*?)</span>", re.S | re.I)
_CLASS_ATTR_RE = re.compile(r'class\s*=\s*["\']([^"\']*)["\']', re.I)
_STATUSES = ("連載", "完結", "连载", "完结")


def _text(inner: str) -> str:
    """Tags out, entities in, whitespace collapsed - card/meta one-liners."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", inner))).strip()


def _book_id_from_href(href: str) -> int | None:
    """Book id from a card/book href; other hosts and non-book paths -> None."""
    try:
        p = urlsplit(href)
    except ValueError:
        return None
    if p.scheme and p.scheme.lower() not in ("http", "https"):
        return None
    if p.netloc and not _HOST.fullmatch(p.netloc.lower()):
        return None
    m = _BOOK_HREF_RE.search(p.path)
    return int(m.group(1)) if m else None


def _first_anchor_with_class(page: str, token: str):
    """(href, inner) of the first anchor whose opening tag carries the token."""
    token_re = re.compile(rf'class\s*=\s*["\'][^"\']*\b{token}\b', re.I)
    for m in _ANCHOR_RE.finditer(page):
        opening = m.group(0).split(">", 1)[0]
        if token_re.search(opening):
            return m.group(1), m.group(2)
    return None


def _spans(block: str) -> list[tuple[list[str], str]]:
    """[(class tokens, text)] for every span in the block."""
    out = []
    for m in _SPAN_RE.finditer(block):
        cls = _CLASS_ATTR_RE.search(m.group(1))
        out.append((cls.group(1).lower().split() if cls else [], _text(m.group(2))))
    return out


def _meta_map(page: str) -> dict[str, str]:
    """Meta tags keyed by property/name, attribute order tolerant."""
    out: dict[str, str] = {}
    for tag in _META_TAG_RE.findall(page):
        attrs = {k.lower(): v for k, v in _META_ATTR_RE.findall(tag)}
        key = attrs.get("property") or attrs.get("name")
        if key and "content" in attrs:
            out.setdefault(key.lower(), attrs["content"])
    return out


def parse_page_stats(page: str) -> tuple[int, int] | None:
    """(current, total) pager page numbers, or None when the pager is absent."""
    m = _PAGESTATS_RE.search(page)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_cards(page: str) -> list[Card]:
    """Book cards from a recent/category/search results page.

    Each bookbox block parses independently: missing fields degrade to empty
    strings and cards without a usable book link are dropped. Pagination
    markup after the last block never matches the field regexes. See
    SCRAPING.md.
    """
    out: list[Card] = []
    for seg in _BOOKBOX_RE.split(page)[1:]:
        m = _BOOKNAME_RE.search(seg)
        if not m:
            continue
        anchors = _iter_anchors(m.group(1))
        if not anchors:
            continue
        href, inner = anchors[0]
        bid = _book_id_from_href(href)
        title = _text(inner)
        if bid is None or not title:
            continue
        author = words = reads = ""
        for am in _AUTHOR_DIV_RE.finditer(seg):
            t = _text(am.group(1))
            if not author and t.startswith("作者"):
                author = t[len("作者"):].lstrip("：: ").strip()
            elif not words and ("字數" in t or "字数" in t):
                words = t
            elif not reads and ("閱讀" in t or "阅读" in t):
                reads = t
        latest_title, latest_url = "", None
        cm = _CAT_DIV_RE.search(seg)
        if cm:
            cat_anchors = _iter_anchors(cm.group(1))
            if cat_anchors:
                latest_title = _text(cat_anchors[0][1])
                latest_url = canonical_chapter_url(
                    cat_anchors[0][0], f"{BASE}/book/{bid}/")
        intro = ""
        um = _UPDATE_DIV_RE.search(seg)
        if um:
            intro = _text(um.group(1))
            for prefix in ("簡介", "简介"):
                if intro.startswith(prefix):
                    intro = intro[len(prefix):].lstrip("：: ").strip()
                    break
        out.append(Card(bid, title, author, words, reads,
                        latest_title, latest_url, intro))
    return out


def parse_search_page(page: str) -> tuple[int | None, list[Card]]:
    """(total, cards) for a search-results page.

    An exact-title hit redirects to the full book page (og:type=novel):
    it is surfaced as a single card so the browse UI has one shape. total
    is None when the page carries no count. See SCRAPING.md.
    """
    meta = _meta_map(page)
    if meta.get("og:type", "").lower() == "novel" or _BOOKTITLE_RE.search(page):
        bid = meta.get("og:book_id", "").strip()
        if not bid.isdigit():
            bid = ""
            raw = meta.get("og:novel:read_url", "")
            got = _book_id_from_href(raw) if raw else None
            if got is not None:
                bid = str(got)
        if not bid.isdigit():
            return None, []
        book_url = f"{BASE}/book/{int(bid)}/"
        bm = parse_book_meta(page, book_url)
        return 1, [Card(int(bid), bm.title, bm.author, bm.words, "",
                        bm.latest_title, bm.latest_url, bm.intro)]
    m = _SEARCH_TOTAL_RE.search(page)
    total = int(m.group(1)) if m else None
    return total, parse_cards(page)


def parse_book_meta(page: str, url: str) -> BookMeta:
    """Book-detail header from /book/<id>/ (or a single-result search page).

    p.bookintro embeds an img tag; _text strips it so no tag ever leaks
    into the UI. Author/category/status come from p.booktag spans, with
    the same label fallbacks as the Android app. See SCRAPING.md.
    """
    tm = _BOOKTITLE_RE.search(page) or _H1_RE.search(page)
    title = _text(tm.group(1)) if tm else ""
    tag_block = _BOOKTAG_RE.search(page)
    tag_html = tag_block.group(1) if tag_block else ""
    author = ""
    red = _first_anchor_with_class(page, "red")
    if red:
        author = _text(red[1])
    if not author and tag_html:
        am = re.search(r"作者[：:]\s*(\S+)", _text(tag_html))
        if am:
            author = am.group(1)
    spans = _spans(tag_html)
    words = next((t for _cls, t in spans if "字" in t), "")
    category = next((t for cls, t in spans
                     if "blue" in cls and "字" not in t), "")
    if not category:
        category = next((t for _cls, t in spans
                         if "字" not in t and t not in _STATUSES), "")
    status = next((t for _cls, t in spans if t in _STATUSES), "")
    intro = ""
    im = _BOOKINTRO_RE.search(page)
    if im:
        intro = _text(im.group(1))
    updated_at = ""
    tm2 = _BOOKTIME_RE.search(page)
    if tm2:
        updated_at = _text(tm2.group(1))
        for prefix in ("更新時間", "更新时间"):
            if updated_at.startswith(prefix):
                updated_at = updated_at[len(prefix):].lstrip("：: ").strip()
                break
    latest_title, latest_url = "", None
    chapter = _first_anchor_with_class(page, "bookchapter")
    if chapter:
        latest_title = _text(chapter[1])
        latest_url = canonical_chapter_url(chapter[0], url)
    return BookMeta(title, author, words, category, status, intro,
                    latest_title, latest_url, updated_at)



