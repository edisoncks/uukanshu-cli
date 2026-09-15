"""uukanshu — read novels from uukanshu.cc in your terminal.

Fetches chapters over plain HTTPS using only the Python standard library,
strips the page down to just the chapter text, optionally converts
Traditional -> Simplified Chinese (OpenCC), and shows it in a Textual
reading pane: CJK-aware reflowing padding, live resize, and in-app
chapter navigation.

WHAT YOU NEED
  * Python 3.10+. Fetching itself uses only the standard library; OpenCC,
    textual, and certifi are installed with the package (and are bundled
    into the standalone release binaries).


KEYS (shown in the footer bar too)
  n           next chapter          p   previous chapter
  l           chapter list — opens instantly with a spinner while the list
              is fetched; cached per book. Esc or q closes it, Enter jumps
  q           quit
  d/u            half-page down/up (smooth glide)   arrows / PgUp / PgDn / Home / End
  z           toggle Simplified / Traditional — instantly re-renders the
              chapter text, header title, chapter list, and UI messages
              without refetching
  t/T         color theme — cycle night → sepia → paper →
              catppuccin-frappe → catppuccin-macchiato → catppuccin-mocha →
              tokyo-night → matrix (T goes in reverse)

  Built-in messages follow the content's mode; -z starts in Simplified.

EXAMPLES
  # browse a book's chapter list, then pick one by number
  uukanshu --book <ID> --list
  uukanshu --book <ID> --chapter 6

  # start a book from its address (opens chapter 1)
  uukanshu https://uukanshu.cc/book/<ID>/

  # read a specific chapter straight from its URL
  uukanshu https://uukanshu.cc/book/<ID>/<CHAPTER>.html

  # Simplified Chinese, custom text padding (default: 2 cols / 1 blank line)
  uukanshu --book <ID> -z
  uukanshu https://uukanshu.cc/book/<ID>/<CHAPTER>.html --pad 4
  export UUKANSHU_SIMPLIFIED=1     # -z by default
  export UUKANSHU_PAD=6            # roomier margins by default

  # color themes: night (default) | sepia | paper | catppuccin-frappe |
  # catppuccin-macchiato | catppuccin-mocha | tokyo-night | matrix,
  # or cycle with t
  uukanshu --book <ID> --theme sepia
  export UUKANSHU_THEME=sepia      # theme by default

  # dump clean text to stdout instead of launching the reader
  uukanshu --book <ID> --chapter 6 -z --print > chapter6.txt

TIPS
  * Textual reflows the text as you resize the terminal — padding stays
    correct on all four sides. No fixed-width wrapping, so the old less
    hacks are gone entirely.
  * Fetching is plain HTTPS with browser-like headers; transient network
    failures are retried automatically. If you still see errors, check
    your connection (or whether the site is up).
  * Book IDs are the number in /book/<ID>/ URLs. Find them by browsing the
    library (https://uukanshu.cc/class_1_1.html etc.) or searching by title:
    https://uukanshu.cc/modules/article/search.php?q=<title>
"""

__version__ = "0.3.2"

import argparse
import asyncio
import gzip
import html
import http.client
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Footer, Header, LoadingIndicator, OptionList, Static
from textual.widgets.option_list import Option
from rich.text import Text

BASE = "https://uukanshu.cc"

# Cap on a single response body; chapter/TOC pages are a few hundred KB at
# most, so a larger response means a broken or hostile server.
_MAX_BYTES = 10 * 1024 * 1024

# ----------------------------------------------------------------- themes

# Reading-oriented color themes; `night` is the default (most terminals
# run dark, and Textual offers no light/dark detection to auto-pick).
READER_THEMES = [
    Theme(
        name="night",
        dark=True,
        primary="#7d9ac1", secondary="#9a8ec1", accent="#c19a7d",
        foreground="#c9cfd8", background="#12161d",
        surface="#181d26", panel="#1a2029",
        warning="#d8b26a", error="#c96f6f", success="#79b28a",
    ),
    Theme(
        name="sepia",
        dark=False,
        primary="#8a5a2b", secondary="#6b6136", accent="#a0672f",
        foreground="#3b2f22", background="#f4ecd8",
        surface="#ede1c4", panel="#e7d9b8",
        warning="#a3722a", error="#a03d3d", success="#5d7d46",
    ),
    Theme(
        name="paper",
        dark=False,
        primary="#3a6ea5", secondary="#5a7d5a", accent="#8a6d3b",
        foreground="#22262b", background="#fbfbf9",
        surface="#f1f1ee", panel="#e9e9e4",
        warning="#a3722a", error="#a03d3d", success="#4a7d4a",
    ),
    Theme(
        name="catppuccin-frappe",
        dark=True,
        primary="#ca9ee6", secondary="#8caaee", accent="#ef9f76",
        foreground="#c6d0f5", background="#303446",
        surface="#414559", panel="#292c3c",
        warning="#e5c890", error="#e78284", success="#a6d189",
    ),
    Theme(
        name="catppuccin-macchiato",
        dark=True,
        primary="#c6a0f6", secondary="#8aadf4", accent="#f5a97f",
        foreground="#cad3f5", background="#24273a",
        surface="#363a4f", panel="#1e2030",
        warning="#eed49f", error="#ed8796", success="#a6da95",
    ),
    Theme(
        name="catppuccin-mocha",
        dark=True,
        primary="#cba6f7", secondary="#89b4fa", accent="#fab387",
        foreground="#cdd6f4", background="#1e1e2e",
        surface="#313244", panel="#181825",
        warning="#f9e2af", error="#f38ba8", success="#a6e3a1",
    ),
    Theme(
        name="tokyo-night",
        dark=True,
        primary="#7aa2f7", secondary="#bb9af7", accent="#ff9e64",
        foreground="#c0caf5", background="#1a1b26",
        surface="#292e42", panel="#16161e",
        warning="#e0af68", error="#f7768e", success="#9ece6a",
    ),
    Theme(
        name="matrix",
        dark=True,
        primary="#33ff66", secondary="#1f9d4d", accent="#9d1f6e",
        foreground="#33ff66", background="#000000",
        surface="#04140a", panel="#071a0e",
        warning="#33ffcc", error="#ff3366", success="#33ff66",
    ),
]

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
    errors might."""
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
                    body = gzip.decompress(r.read(_MAX_BYTES + 1))
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
        # gzip.decompress on a truncated body raises EOFError (neither
        # OSError nor HTTPException) — must be caught or a flaky
        # middlebox gives a raw traceback instead of a clean error.
        # Unsupported-encoding/size RuntimeErrors must not retry.
        except (OSError, http.client.HTTPException, EOFError) as exc:
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


# ---------------------------------------------------------- update check

# Stable contract: GitHub API JSON `tag_name`, not HTML scraping (layout
# changes would silently break a page parser). Unauthenticated rate limit
# is 60 req/hr/IP; the 12h file cache below keeps steady-state use at
# ~2 req/day, so normal use never gets near the limit.
GITHUB_API_LATEST = \
    "https://api.github.com/repos/edisoncks/uukanshu-cli/releases/latest"
_UPDATE_TTL = 12 * 3600


def parse_version(s: str | None):
    """"X.Y.Z" (optional leading v, surrounding whitespace) -> int tuple,
    else None. Malformed tags are ignored, never crash the check."""
    if not s:
        return None
    s = s.strip()
    if s[:1] in ("v", "V"):
        s = s[1:]
    parts = s.split(".")
    if not parts:
        return None
    nums = []
    for p in parts:
        p = p.strip()
        if not p.isdigit():
            return None
        nums.append(int(p))
    return tuple(nums) if nums else None


def is_newer(latest: str | None, current: str) -> bool:
    """True when `latest` parses and compares greater than `current`.
    Unparseable either side -> False (fail silent, never nag wrongly)."""
    lv, cv = parse_version(latest), parse_version(current)
    if lv is None or cv is None:
        return False
    n = max(len(lv), len(cv))
    lv += (0,) * (n - len(lv))
    cv += (0,) * (n - len(cv))
    return lv > cv


def _update_cache_path() -> str:
    """Platform cache file for the latest-version check."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "uukanshu", "update.json")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Caches",
                            "uukanshu", "update.json")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache")
    return os.path.join(base, "uukanshu", "update.json")


def _load_cached_latest(now: float | None = None):
    """(latest, checked_at) from cache, or (None, None). Corrupt cache
    is ignored — the check simply refetches."""
    try:
        with open(_update_cache_path(), encoding="utf-8") as f:
            data = json.load(f)
        latest = data.get("latest")
        checked_at = data.get("checked_at")
        if not isinstance(latest, str) or not isinstance(
                checked_at, (int, float)):
            return None, None
        return latest, checked_at
    except (OSError, ValueError):
        return None, None


def _save_cached_latest(latest: str, now: float | None = None) -> None:
    """Best-effort cache write; failures are silent by design."""
    try:
        path = _update_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"latest": latest,
                       "checked_at": now if now is not None else time.time()},
                      f)
    except OSError:
        pass


def latest_release_version(timeout: float = 5) -> str | None:
    """Query the GitHub public API for the latest release tag ("X.Y.Z",
    no leading v), or None on any failure (offline, rate-limit, bad JSON).
    Never raises — the update reminder must not break reading."""
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=timeout,
                                        context=_SSL_CONTEXT) as r:
            raw = r.read(64 * 1024)
        try:
            tag = json.loads(raw.decode("utf-8", errors="replace")).get(
                "tag_name")
        except (ValueError, AttributeError):
            return None
        if not isinstance(tag, str):
            return None
        ver = tag.strip()
        if ver[:1] in ("v", "V"):
            ver = ver[1:]
        return ver if parse_version(ver) is not None else None
    except Exception:
        return None


def _update_check_disabled_by_env() -> bool:
    """Opt-out via UUKANSHU_NO_UPDATE_CHECK=1/true/yes/on (any case)."""
    return os.environ.get("UUKANSHU_NO_UPDATE_CHECK", "").strip().lower() in (
        "1", "true", "yes", "on")


def absolutize(href: str, url: str) -> str:
    """Resolve an href found on `url` against it. Hand-rolling this with
    `BASE + href` breaks for directory-relative hrefs, protocol-relative
    "//host/..." refs, and anything that merely starts with "http"."""
    if href.startswith(("http://", "https://")):
        return href
    return urljoin(url, href)


_CHAPTER_HREF = re.compile(rf"(?:{re.escape(BASE)})?/book/\d+/\d+\.html")


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
    m = re.search(rf'<a\s[^>]*?href=["\']([^"\']+)["\'][^>]*>\s*{label}\s*</a>', page)
    if not m:
        return None
    abs_url = absolutize(m.group(1), url)
    if not _CHAPTER_HREF.fullmatch(abs_url):
        return None
    return abs_url


def chapter_list(toc_page: str, book_id: str | None = None):
    """Return [(position, chapter_page_id, title, url)] for a book TOC page.

    The TOC page leads with a 'latest updates' block whose chapters also
    appear in the full ordered list below. Keeping the LAST occurrence of
    each (book, chapter) pair positions chapters in reading order.

    book_id, when given, drops chapter links that point at a different
    book (recommendation blocks etc.); None accepts every book. Chapter
    hrefs may be site-relative or absolute.
    """
    matches = list(re.finditer(
        r'href=["\'](?:' + re.escape(BASE) + r')?(/book/(\d+)/(\d+)\.html)["\']'
        r'[^>]*>\s*([^<]+?)\s*</a>',
        toc_page, re.I))
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
        if wanted is not None and int(m.group(2)) != wanted:
            continue
        last_idx[(m.group(2), m.group(3))] = i
    out, seen = [], set()
    for i, m in enumerate(matches):
        if wanted is not None and int(m.group(2)) != wanted:
            continue
        key = (m.group(2), m.group(3))
        if key in seen or last_idx[key] != i:
            continue
        seen.add(key)
        out.append((len(out) + 1, int(m.group(3)),
                    html.unescape(m.group(4).strip()),
                    BASE + m.group(1)))
    return out


def chapter_id(url: str) -> int | None:
    """The numeric chapter page id in a chapter URL, else None."""
    m = re.search(r"/book/\d+/(\d+)\.html", url or "")
    return int(m.group(1)) if m else None


def extract_chapter(page: str, url: str):
    """Pull book name, chapter title, clean text, and nav links from a page."""
    t = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S | re.I)
    title = (html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip()
             if t else url)

    bc = re.findall(r'<a href=["\'](?:https?://[^"\']*)?/book/\d+/["\'][^>]*>([^<]+)</a>',
                    page)
    # Prefer the breadcrumb anchor for THIS book's id; the last match in
    # document order is only a fallback, so a footer/recommendation block
    # linking another book's index can't rename the title bar.
    book = ""
    book_id = re.search(r"/book/(\d+)/", url)
    if book_id:
        bc_own = re.findall(
            rf'<a href=["\'](?:https?://[^"\']*)?/book/{book_id.group(1)}/["\']'
            rf'[^>]*>([^<]+)</a>', page)
        if bc_own:
            book = html.unescape(bc_own[0]).strip()
    if not book and bc:
        book = html.unescape(bc[-1]).strip()

    m = re.search(r'<div\s+class=["\']readcotent[^"\']*["\'][^>]*>(.*)', page, re.S | re.I)
    if not m:
        raise RuntimeError("could not find chapter content on the page "
                           "(is this a chapter URL?)")
    body = m.group(1)
    # Cut at the nav-row container: from the "上一章 / 章节目录 / 下一章"
    # box to the end of the page it's all UI/footer noise — the keyboard
    # tip, the copyright blurb, the "Copyright ... TOP↑" footer, and the
    # GTM iframe/noscript leftovers — never chapter text.
    body = re.split(r'<div\s+class=["\']mulu-box["\']', body, maxsplit=1)[0]
    body = re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.S | re.I)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", "", body)
    body = body.replace("&emsp;", "")
    lines = [line.strip()
             for line in html.unescape(body).splitlines() if line.strip()]
    text = "\n\n".join(lines)
    # Belt-and-braces for pages without the mulu-box container: also cut at
    # the literal nav row, tolerating the "章节/章節" prefix (simplified /
    # traditional) that prefixes the link label. Require a line break
    # before 上一章 so an in-body mention ("有人說上一章 ... 很好笑")
    # doesn't truncate the chapter, and cut at the LAST standalone nav
    # row rather than the first mention.
    _nav_pat = re.compile(r"\n上一章\s+(?:章节|章節)?目[录錄]\s+下一章(?=\s|$)")
    _nav_matches = list(_nav_pat.finditer(text))
    if _nav_matches:
        text = text[:_nav_matches[-1].start()].rstrip()

    return (book, title, text,
            link(page, url, "上一章"), link(page, url, "目[录錄]"),
            link(page, url, "下一章"))


# OpenCC's t2s handles the 著/着 particle split correctly by itself: it
# keeps 著 in real words (著名, 著作, 土著, 見微知著, 執著...) and converts
# phrase-level contexts. A hand-rolled whitelist post-pass was tried and
# removed: it silently corrupted words OpenCC got right (土著 -> 土着,
# 見微知著 -> 见微知着) while failing its own purpose elsewhere (the
# whitelist entry 著书 blocked 看著 -> 看着 conversion). Do not re-add one.


# ---------------------------------------------------------------- reader UI

class TocOptionList(OptionList):
    """Chapter picker list that opens scrolled to the current chapter.

    OptionList.on_show() scrolls minimally so the highlighted option just
    becomes visible (bottom of the viewport for a deep chapter); here we
    pin the current chapter at the top instead, so browsing starts from
    where you are.
    """

    def on_show(self) -> None:
        self.scroll_to_highlight(top=True)


class TocScreen(ModalScreen):
    """Modal chapter picker: arrows to browse, Enter to jump, Esc to close."""

    CSS = """
    TocScreen { align: center middle; }
    #tocbox { width: 80%; height: 90%; border: round $primary;
              background: $surface; padding: 1 2; }
    #tochead { height: auto; margin-bottom: 1; text-style: bold; }
    LoadingIndicator { height: auto; }
    OptionList { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape", "close", "close", priority=True),
        Binding("q", "close", "close"),
        Binding("d", "half(1)", "down", priority=True),
        Binding("u", "half(-1)", "up", priority=True),
    ]

    def __init__(self, current_url, ui=None):
        super().__init__()
        self.chapters = []
        self.current_url = current_url
        self.ui = ui or (lambda s: s)

    def compose(self) -> ComposeResult:
        with Vertical(id="tocbox"):
            yield Static(self.ui("章节目录 / Chapters — ↑↓/d/u · Enter jump · Esc close"),
                         id="tochead")
            yield LoadingIndicator(id="tocspin")
            yield TocOptionList()

    def on_mount(self) -> None:
        self.query_one(OptionList).can_focus = True
        if self.chapters:
            self._fill(self.chapters)

    def action_half(self, sign: int) -> None:
        """Half-page step that moves the selection with the view, so d/u,
        arrow keys, and Enter all agree on which chapter is selected."""
        ol = self.query_one(OptionList)
        if not ol.option_count:
            return
        step = max(1, ol.container_size.height // 2) * sign
        current = ol.highlighted if ol.highlighted is not None else 0
        # watch_highlighted scrolls the selection into view automatically.
        ol.highlighted = min(max(current + step, 0), ol.option_count - 1)

    def populate(self, chapters) -> None:
        """Safe to call before OR after the modal has mounted."""
        self.chapters = chapters
        if self.is_mounted:
            self._fill(chapters)

    def _fill(self, chapters) -> None:
        self.query_one("#tocspin", LoadingIndicator).display = False
        ol = self.query_one(OptionList)
        ol.clear_options()
        ol.add_options(
            Option(f"{pos:>5}  {title}", id=str(pos))
            for pos, _id, title, _url in chapters)
        current_id = chapter_id(self.current_url)
        if current_id is not None:
            # Match by chapter id, not raw URL: the current URL may differ
            # from the TOC entry in scheme, www prefix, or a redirect.
            pos = next((p for p, i, _t, _u in chapters if i == current_id),
                       None)
            if pos is not None:
                ol.highlighted = pos - 1
                ol.scroll_to_highlight(top=True)
        ol.focus()

    def show_error(self, msg: str) -> None:
        if not self.is_mounted:
            return
        self.query_one("#tocspin", LoadingIndicator).display = False
        # Text, not markup: msg can embed an untrusted URL or exception
        # text whose brackets would break Rich markup parsing.
        self.query_one("#tochead", Static).update(
            Text("error: ", style="bold red") + Text(msg)
            + Text(" — Esc/q to close"))

    def on_option_list_option_selected(self, event) -> None:
        pos = int(str(event.option_id))
        url = next((u for p, _i, _t, u in self.chapters if p == pos), None)
        self.dismiss(url)

    def action_close(self) -> None:
        self.dismiss(None)


class Reader(App):
    TITLE = "uukanshu"
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    #page { height: 1fr; }
    #doc { width: 1fr; }
    """

    BINDINGS = [
        Binding("d", "half(1)", "down"),
        Binding("u", "half(-1)", "up"),
        Binding("n", "next", "next"),
        Binding("p", "prev", "prev"),
        Binding("l", "list", "chapters"),
        Binding("z", "toggle_simplified", "simplified"),
        Binding("t", "cycle_theme", "theme", key_display="t/T"),
        Binding("T", "cycle_theme_reverse", show=False),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, url: str, cc, simplified: bool, pad: int,
                 theme: str = "night", chapters=None,
                 update_check: bool = True):
        super().__init__()
        for t in READER_THEMES:
            self.register_theme(t)
        self.theme = theme
        self.url = url
        self.pad = pad
        self._update_check_enabled = update_check
        self.simplified = simplified  # display mode, independent of cc
        self._t2s = cc    # t2s converter (reused from CLI when -z; built lazily otherwise)
        self._t2s_failed = False  # t2s load failed; don't retry every keystroke
        self._s2t = None  # lazily built: chrome localization for Traditional mode
        self._s2t_failed = False  # s2t load failed; don't retry every keystroke
        self._raw = None  # raw (book, title, text) of the last fetched chapter
        self.next_url = self.prev_url = None
        m = re.search(r"/book/(\d+)/", url)
        self.book_id = m.group(1) if m else None
        self.chapters_cache = chapters  # TOC may already be parsed by the CLI
        self._cache_book = self.book_id if chapters is not None else None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield VerticalScroll(Static("", id="doc"), id="page")
        yield Footer()

    def on_mount(self) -> None:
        doc = self.query_one("#doc", Static)
        doc.styles.padding = (self.pad, self.pad)
        self.load_chapter(self.url)
        self.check_update()

    @work(exclusive=True, group="update")
    async def check_update(self) -> None:
        """Background new-version reminder; newer-only notify, same shape
        as the theme-change notification. Never blocks chapter loading
        and never raises into the TUI."""
        try:
            if not getattr(self, "_update_check_enabled", True):
                return
            if _update_check_disabled_by_env():
                return
            latest, checked_at = _load_cached_latest()
            now = time.time()
            if latest and isinstance(checked_at, (int, float)) \
                    and now - checked_at < _UPDATE_TTL:
                if is_newer(latest, __version__):
                    self.notify(self.ui("有新版本") +
                                f" v{latest} / new version available")
                return
            latest = await asyncio.to_thread(latest_release_version)
            if not latest:
                return
            _save_cached_latest(latest, now)
            if is_newer(latest, __version__):
                self.notify(self.ui("有新版本") +
                            f" v{latest} / new version available")
        except Exception:
            pass

    def _conv_t2s(self):
        """Lazily built Traditional -> Simplified converter, or None."""
        if self._t2s is None and not self._t2s_failed:
            try:
                import opencc
                self._t2s = opencc.OpenCC("t2s")
            except Exception:
                # Missing/broken OpenCC dict must not crash the reader
                # on first `z` press; callers fall back to raw text.
                self._t2s_failed = True
        return self._t2s

    def ui(self, s: str) -> str:
        """Built-in chrome strings are written Simplified; show them
        Traditional while the reader is in Traditional mode."""
        if self.simplified:
            return s
        if self._s2t is None and not self._s2t_failed:
            try:
                import opencc
                self._s2t = opencc.OpenCC("s2t")
            except Exception:
                # Leave chrome Simplified rather than crash the reader;
                # recorded so a missing dict doesn't retry on every call.
                self._s2t_failed = True
        return self._s2t.convert(s) if self._s2t else s

    def _render(self, book, title, text):
        """Render the given (raw) chapter content in the current mode."""
        if self.simplified:
            conv = self._conv_t2s()
            if conv is not None:
                book, title, text = conv.convert(book), conv.convert(title), conv.convert(text)
        self.title = f"{book} — {title}" if book else title
        self.query_one("#doc", Static).update(Text.assemble(
            (book + "\n", "bold"),
            (title + "\n\n", "bold cyan"),
            (text + "\n", ""),
        ))

    def action_half(self, sign: int) -> None:
        page = self.query_one("#page", VerticalScroll)
        step = max(1, page.container_size.height // 2) * sign
        page.scroll_relative(0, step, speed=40, easing="out_quart")

    # -- content loading

    @work(exclusive=True, group="nav")
    async def load_chapter(self, url: str) -> None:
        doc = self.query_one("#doc", Static)
        doc.update(self.ui("载入中… loading…"))
        try:
            page = await asyncio.to_thread(fetch, url)
            book, title, text, prev_url, _toc, next_url = extract_chapter(page, url)
        except Exception as exc:
            # fetch/extract raise RuntimeError for user-triggerable cases
            # (network, block pages, non-chapter URLs); anything else is a
            # parser bug from changed site markup. Show it in the pane —
            # with @work(exit_on_error=True) re-raising would tear down
            # the whole TUI over one bad page.
            doc.update(Text(self.ui("错误："), style="bold red") + Text(f"{type(exc).__name__}: {exc}"))
            return
        m = re.search(r"/book/(\d+)/", url)
        if m:
            self.book_id = m.group(1)
        self.url = url
        self.prev_url, self.next_url = prev_url, next_url
        self._raw = (book, title, text)
        self._render(book, title, text)
        self.query_one(VerticalScroll).scroll_home(immediate=True)

    # -- navigation actions

    @property
    def modal(self) -> bool:
        return isinstance(self.screen, TocScreen)

    def action_next(self) -> None:
        if self.modal:
            return
        if self.next_url:
            self.load_chapter(self.next_url)
        else:
            self.notify(self.ui("已是最新一章") + " / end of book", severity="warning")

    def action_prev(self) -> None:
        if self.modal:
            return
        if self.prev_url:
            self.load_chapter(self.prev_url)
        else:
            self.notify(self.ui("已是第一章") + " / start of book", severity="warning")

    def action_toggle_simplified(self) -> None:
        if self._raw is None:
            self.notify(self.ui("尚无内容") + " / nothing loaded yet",
                        severity="warning")
            return
        self.simplified = not self.simplified
        self._render(*self._raw)
        if (self.chapters_cache and self._cache_book == self.book_id
                and isinstance(self.screen, TocScreen)):
            self.screen.populate(self._toc_converted(self.chapters_cache))
            self.screen.query_one("#tochead", Static).update(
                self.ui("章节目录 / Chapters — ↑↓/d/u · Enter jump · Esc close"))

    def _cycle_theme(self, step: int) -> None:
        names = [t.name for t in READER_THEMES]
        self.theme = names[(names.index(self.theme) + step) % len(names)]
        self.notify(self.ui("主题") + f" / theme: {self.theme}")

    def action_cycle_theme(self) -> None:
        self._cycle_theme(1)

    def action_cycle_theme_reverse(self) -> None:
        self._cycle_theme(-1)

    def _toc_converted(self, chapters):
        """Chapter list with titles converted to the current mode. The
        cache itself stays raw — OpenCC round-trips aren't lossless."""
        if not self.simplified:
            return chapters
        conv = self._conv_t2s()
        if conv is None:
            return chapters
        return [(p, i, conv.convert(t), u) for (p, i, t, u) in chapters]

    def action_list(self) -> None:
        if self.modal:
            return
        if not self.book_id:
            self.notify(self.ui("无法确定书籍ID") + " / unknown book id",
                        severity="error")
            return
        screen = TocScreen(self.url, ui=self.ui)
        self.push_screen(screen, self.on_toc_choice)
        if self.chapters_cache and self._cache_book == self.book_id:
            screen.populate(self._toc_converted(self.chapters_cache))
        else:
            self.fetch_toc(screen)

    @work(exclusive=True, group="toc")
    async def fetch_toc(self, screen: TocScreen) -> None:
        """Fill an already-visible TOC modal once its book page has loaded."""
        book_id = self.book_id
        try:
            page = await asyncio.to_thread(fetch, f"{BASE}/book/{book_id}/")
            chapters = chapter_list(page, book_id)  # cached raw; converted at populate time
        except Exception as exc:
            if self.screen is screen:
                screen.show_error(f"{type(exc).__name__}: {exc}")
            return
        self.chapters_cache, self._cache_book = chapters, book_id
        if self.screen is screen:
            screen.populate(self._toc_converted(chapters))

    def on_toc_choice(self, url) -> None:
        if url:
            self.url = url
            self.load_chapter(url)


# ---------------------------------------------------------------- CLI

def book_url_from_arg(url: str):
    """Return the book index URL if `url` is one (e.g. .../book/123/ or
    .../book/123/index.html), else None. Chapter URLs never match."""
    # Strip pasted whitespace; accept http and www variants and normalize
    # to the canonical https BASE form. Without this, pasted URLs with
    # spaces or http:// were misclassified as chapter URLs.
    url = (url or "").strip()
    m = re.fullmatch(r"https?://(?:www\.)?uukanshu\.cc/book/(\d+)/?(?:index\.html)?", url)
    return f"{BASE}/book/{int(m.group(1))}/" if m else None


def _check_chapter(n: int, total: int) -> None:
    """--chapter must name a real TOC position; silently clamping to the
    nearest end would open a chapter the user didn't ask for."""
    if not 1 <= n <= total:
        sys.exit(f"error: --chapter {n} is out of range — this book has "
                 f"{total} chapters (try --list)")


def resolve_start_url(args):
    """Return (chapter_url, chapters) for the requested start point.

    chapters is the parsed TOC when one was fetched to resolve the start
    URL (book URL / --book), else None. Returning it lets the reader seed
    its cache instead of refetching the same page on the first 'l' press.
    """
    if args.url and not args.url.strip().startswith(("http://", "https://")):
        sys.exit(f"error: url must start with http:// or https:// — "
                 f"got {args.url!r}")
    book_url = book_url_from_arg(args.url)
    if book_url:
        book_id = re.search(r"/book/(\d+)/", book_url).group(1)
        chapters = chapter_list(fetch(book_url), book_id)
        if not chapters:
            sys.exit(f"error: no chapters found at {book_url}.")
        _check_chapter(args.chapter or 1, len(chapters))
        return chapters[(args.chapter or 1) - 1][3], chapters
    if args.url:
        if args.chapter is not None:
            # A chapter URL already names its chapter; silently ignoring
            # --chapter would open a chapter the user didn't ask for.
            sys.exit(f"error: --chapter {args.chapter} is ignored for a "
                     f"chapter URL — drop --chapter or start from the "
                     f"book URL / --book <id>")
        return args.url, None
    if not args.book:
        sys.exit("error: give a chapter URL or --book <id> (see --help).")
    page = fetch(f"{BASE}/book/{args.book}/")
    chapters = chapter_list(page, args.book)
    if not chapters:
        sys.exit("error: no chapters found on the book page.")
    _check_chapter(args.chapter or 1, len(chapters))
    return chapters[(args.chapter or 1) - 1][3], chapters


def _force_utf8_stdio():
    """Windows consoles default to a legacy codepage (e.g. cp1252) that
    cannot encode the help text (→, CJK) or novel content; force UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is None:
                continue
            enc = getattr(stream, "encoding", None)
            # encoding is None when redirected (pipes); that still needs
            # forcing — the old `stream.encoding.lower()` raised
            # AttributeError on None and silently left a non-UTF-8 pipe.
            if enc is None or enc.lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    """Integer env var with a clean error — argparse never sees bad
    defaults, so garbage would otherwise traceback at parser build."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        sys.exit(f"error: {name} must be a number — got {raw!r}")
    if minimum is not None and value < minimum:
        sys.exit(f"error: {name} must be >= {minimum} — got {raw!r}")
    return value


def _env_theme() -> str:
    """UUKANSHU_THEME, validated — argparse checks flag values against
    `choices` but never validates an env-injected default."""
    raw = os.environ.get("UUKANSHU_THEME", "night")
    if raw not in {t.name for t in READER_THEMES}:
        sys.exit("error: UUKANSHU_THEME must be one of: "
                 + ", ".join(t.name for t in READER_THEMES)
                 + f" (got {raw!r})")
    return raw


def _nonneg_int(value: str) -> int:
    """argparse type for --pad: reject garbage and negatives cleanly."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number")
    if n < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return n


def main():
    _force_utf8_stdio()
    try:
        run()
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        # Piping --list/--print to `head` closes stdout early; exit
        # quietly like standard Unix tools instead of printing
        # "error: [Errno 32] Broken pipe" with exit 1.
        try:
            sys.stderr.close()
        except Exception:
            pass
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
    except (RuntimeError, OSError, UnicodeError) as exc:
        sys.exit(f"error: {exc}")


def run():
    ap = argparse.ArgumentParser(
        prog="uukanshu",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__.strip(),
    )
    ap.add_argument("--version", action="version",
                    version=f"uukanshu {__version__}")
    ap.add_argument("url", nargs="?", help="chapter or book URL to start at")
    ap.add_argument("--book", "-b", help="book ID, from /book/<ID>/ URLs")
    ap.add_argument("--chapter", "-c", type=int, default=None, metavar="N",
                    help="chapter number from the book TOC (default: 1; "
                         "ignored/error with a chapter URL, see below)")
    ap.add_argument("--list", "-l", action="store_true",
                    help="list chapters of --book (plain text) and exit")
    ap.add_argument("--simplified", "-z", action="store_true",
                    default=os.environ.get("UUKANSHU_SIMPLIFIED") == "1",
                    help="convert Traditional -> Simplified Chinese "
                         "(env UUKANSHU_SIMPLIFIED=1 to default on)")
    ap.add_argument("--pad", type=_nonneg_int,
                    default=_env_int("UUKANSHU_PAD", 2, minimum=0),
                    metavar="N",
                    help="padding around text in the reader: N blank rows top/"
                         "bottom, N cols left/right (default 2; env "
                         "UUKANSHU_PAD=N)")
    ap.add_argument("--theme", "-t", choices=[t.name for t in READER_THEMES],
                    default=_env_theme(),
                    metavar="NAME",
                    help="reader color theme (default: night; env "
                         "UUKANSHU_THEME=NAME); cycle in-app with t")
    ap.add_argument("--print", "-p", action="store_true", dest="plain",
                    help="print plain text to stdout, no reader UI")
    ap.add_argument("--no-update-check", action="store_true",
                    help="disable the new-version reminder "
                         "(env UUKANSHU_NO_UPDATE_CHECK=1 to default off)")
    args = ap.parse_args()

    cc = None
    if args.simplified:
        try:
            import opencc
            cc = opencc.OpenCC("t2s")
        except Exception as exc:
            sys.exit(f"error: simplified conversion unavailable ({exc}) — "
                       "is OpenCC installed with its dictionaries?")

    if args.list:
        book_url = book_url_from_arg(args.url)
        if not book_url and not args.book:
            sys.exit("error: --list needs a book URL or --book <id>.")
        if book_url:
            toc_url, book_id = (book_url,
                                re.search(r"/book/(\d+)/", book_url).group(1))
        else:
            toc_url, book_id = f"{BASE}/book/{args.book}/", args.book
        chapters = chapter_list(fetch(toc_url), book_id)
        if not chapters:
            sys.exit(f"error: no chapters found at {toc_url}.")
        for pos, _id, title, _url in chapters:
            t = cc.convert(title) if cc else title
            print(f"{pos:>5}  {t}")
        return

    url, chapters = resolve_start_url(args)

    if args.plain:
        page = fetch(url)
        book, title, text, *_ = extract_chapter(page, url)
        if cc:
            book, title, text = cc.convert(book), cc.convert(title), cc.convert(text)
        print(f"{book}\n{title}\n\n{text}\n")
        return

    Reader(url, cc, args.simplified, args.pad, args.theme,
           chapters=chapters,
           update_check=not args.no_update_check).run()


if __name__ == "__main__":
    main()
