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
  n / →       next chapter          p / ← previous chapter
  l           chapter list — opens instantly with a spinner while the list
              is fetched; cached per book. Esc or q closes it, Enter jumps
  q           quit
  d/u            half-page down/up (smooth glide)   ↑↓ / PgUp / PgDn / Home / End
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

__version__ = "0.4.2"

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
from urllib.parse import urlsplit, urlunsplit

# Site contract re-exported so existing callers/tests keep working
# (`uukanshu.fetch`, `uukanshu.Chapter`, `uukanshu.chapter_list`, ...).
from .browse import BrowseScreen, PageCache
from .site import (
    BASE,
    HEADERS,
    Catalog,
    Chapter,
    _ANCHOR_RE,
    _CHAPTER_PATH,
    _HOST,
    _MAX_BYTES,
    _SSL_CONTEXT,
    _iter_anchors,
    _retryable,
    absolutize,
    chapter_id,
    chapter_list,
    extract_chapter,
    fetch,
    link,
)
from .shelf import Shelf

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Footer, Header, LoadingIndicator, OptionList, Static
from textual.widgets.option_list import Option
from rich.text import Text


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
    nums = []
    for p in parts:
        p = p.strip()
        # str.isdigit() is True for Unicode "digits" that int() rejects
        # (superscripts like "²", fullwidth "１"); require ASCII digits
        # so a malformed tag yields None instead of raising.
        if not (p.isascii() and p.isdigit()):
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


def _load_cached_latest():
    """(latest, checked_at) from cache, or (None, None). Corrupt cache
    is ignored — the check simply refetches."""
    try:
        with open(_update_cache_path(), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None, None
        latest = data.get("latest")
        checked_at = data.get("checked_at")
        # bool is an int subclass — exclude it explicitly.
        if not isinstance(latest, str) or type(checked_at) not in (int, float):
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
            raw = r.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            return None
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

    def __init__(self, current_url: str, ui=None):
        """ui is Reader.ui bound method reflecting `simplified`; see ARCHITECTURE.md."""
        super().__init__()
        self.chapters = []
        self._loaded = False
        self._error = None
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
        if self._error is not None:
            self._render_error(self._error)
        elif self._loaded:
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
        self._loaded = True
        self._error = None
        if self.is_mounted:
            self._fill(chapters)

    def _fill(self, chapters: list[Chapter]) -> None:
        self.query_one("#tocspin", LoadingIndicator).display = False
        ol = self.query_one(OptionList)
        ol.clear_options()
        ol.add_options(
            Option(f"{ch.pos:>5}  {ch.title}", id=str(ch.pos))
            for ch in chapters)
        current_id = chapter_id(self.current_url)
        if current_id is not None:
            # Match by chapter id, not raw URL: the current URL may differ
            # from the TOC entry in scheme, www prefix, or a redirect.
            pos = next((ch.pos for ch in chapters if ch.cid == current_id),
                       None)
            if pos is not None:
                ol.highlighted = pos - 1
                ol.scroll_to_highlight(top=True)
        ol.focus()

    def show_error(self, msg: str) -> None:
        self._error = msg
        if self.is_mounted:
            self._render_error(msg)

    def _render_error(self, msg: str) -> None:
        # Renders only; callers own the mounted check. Text, not markup:
        # msg can embed an untrusted URL or exception text whose brackets
        # would break Rich markup parsing.
        self.query_one("#tocspin", LoadingIndicator).display = False
        self.query_one("#tochead", Static).update(
            Text("error: ", style="bold red") + Text(msg)
            + Text(" — Esc/q to close"))

    def on_option_list_option_selected(self, event) -> None:
        pos = int(str(event.option_id))
        url = next((ch.url for ch in self.chapters if ch.pos == pos), None)
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
        Binding("right", "next", show=False),
        Binding("left", "prev", show=False),
        Binding("l", "list", "chapters"),
        Binding("b", "browse", "browse"),
        Binding("z", "toggle_simplified", "simplified"),
        Binding("t", "cycle_theme", "theme", key_display="t/T"),
        Binding("T", "cycle_theme_reverse", show=False),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, url: str | None, cc, simplified: bool, pad: int,
                 theme: str = "night", chapters=None,
                 update_check: bool = True, *, catalog=None, shelf=None):
        super().__init__()
        for t in READER_THEMES:
            self.register_theme(t)
        self.theme = theme
        self.url = url
        self.pad = pad
        self._update_check_enabled = update_check
        # Catalogue access + local bookshelf are injectable so UI tests run
        # without network or real user data. See docs/ARCHITECTURE.md.
        self.catalog = catalog if catalog is not None else Catalog()
        self.shelf = shelf if shelf is not None else Shelf()
        self.browse_cache = PageCache()
        self.browse_ui = {"tab": 0, "category": 1, "query": ""}
        self.simplified = simplified  # display mode, independent of cc
        self._t2s = cc    # t2s converter (reused from CLI when -z; built lazily otherwise)
        self._t2s_failed = False  # t2s load failed; don't retry every keystroke
        self._s2t = None  # lazily built: chrome localization for Traditional mode
        self._s2t_failed = False  # s2t load failed; don't retry every keystroke
        self._raw = None  # raw (book, title, text) of the last fetched chapter
        self._load_error = None  # raw "Type: msg" of last failed load, re-rendered via ui()
        self.next_url = self.prev_url = None
        m = re.search(r"/book/(\d+)/", url or "")
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
        if self.url:
            self.load_chapter(self.url)
        else:
            self.push_screen(BrowseScreen())
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
        Traditional while the reader is in Traditional mode. Convert
        failures fall back to Simplified — never raise into the TUI."""
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
        if not self._s2t:
            return s
        try:
            return self._s2t.convert(s)
        except Exception:
            return s

    def display(self, s: str) -> str:
        """Content string in the current display mode (raw when Traditional).
        Convert failures fall back to raw — never raise into the TUI."""
        if not self.simplified:
            return s
        conv = self._conv_t2s()
        if conv is None:
            return s
        try:
            return conv.convert(s)
        except Exception:
            return s

    def _render(self, book, title, text):
        """Render the given (raw) chapter content in the current mode.
        Convert failures fall back to raw — never raise into the TUI."""
        if self.simplified:
            conv = self._conv_t2s()
            if conv is not None:
                try:
                    book, title, text = (conv.convert(book),
                                        conv.convert(title),
                                        conv.convert(text))
                except Exception:
                    pass
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
        self._load_error = None
        try:
            page = await asyncio.to_thread(fetch, url)
            book, title, text, prev_url, _toc, next_url = extract_chapter(page, url)
        except Exception as exc:
            # fetch/extract raise RuntimeError for user-triggerable cases
            # (network, block pages, non-chapter URLs); anything else is a
            # parser bug from changed site markup. Show it in the pane —
            # with @work(exit_on_error=True) re-raising would tear down
            # the whole TUI over one bad page. Keep url/_raw at the last
            # good chapter so a later `l` highlights what is displayed,
            # not the failed target; record raw error for `z` re-render.
            # See ARCHITECTURE.md Reader state.
            self._load_error = f"{type(exc).__name__}: {exc}"
            doc.update(Text(self.ui("错误："), style="bold red") + Text(self._load_error))
            return
        m = re.search(r"/book/(\d+)/", url)
        if m:
            self.book_id = m.group(1)
        self.url = url
        self.prev_url, self.next_url = prev_url, next_url
        self._raw = (book, title, text)
        self._load_error = None
        self._render(book, title, text)
        self.query_one(VerticalScroll).scroll_home(immediate=True)

    # -- navigation actions

    @property
    def modal(self) -> bool:
        return isinstance(self.screen, (TocScreen, BrowseScreen))

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

    # -- catalogue actions

    def action_browse(self) -> None:
        if self.modal:
            return
        self.push_screen(BrowseScreen())

    @work(exclusive=True, group="open-book")
    async def open_book(self, book_id) -> None:
        """Open a book from the catalogue at its first chapter."""
        try:
            detail = await asyncio.to_thread(self.catalog.book_detail, book_id)
        except Exception as exc:
            self.notify(self.ui("打开失败：")
                        + f"{type(exc).__name__}: {exc}", severity="error")
            return
        if not detail.chapters:
            self.notify(self.ui("没有找到章节") + " / no chapters",
                        severity="error")
            return
        self.seed_toc(book_id, detail.chapters)
        self.open_chapter(detail.chapters[0].url)

    def seed_toc(self, book_id, chapters) -> None:
        """Seed the reader TOC cache (a detail fetch already parsed it)."""
        try:
            self._cache_book = str(int(book_id))
        except (TypeError, ValueError):
            self._cache_book = str(book_id)
        self.chapters_cache = chapters

    def open_chapter(self, url: str) -> None:
        """Load a chapter and return to the reader pane (pop browse stack)."""
        self.load_chapter(url)
        self._close_browse()

    @work(exclusive=True, group="browse-close")
    async def _close_browse(self) -> None:
        while len(self.screen_stack) > 1:
            await self.pop_screen()

    def action_toggle_simplified(self) -> None:
        # Error pane re-renders in the new mode instead of resurrecting
        # the stale chapter; see ARCHITECTURE.md Reader state.
        if self._load_error is None and self._raw is None:
            self.notify(self.ui("尚无内容") + " / nothing loaded yet",
                        severity="warning")
            return
        self.simplified = not self.simplified
        if self._load_error is not None:
            self.query_one("#doc", Static).update(
                Text(self.ui("错误："), style="bold red") + Text(self._load_error))
        else:
            self._render(*self._raw)
        if (self.chapters_cache and self._cache_book == self.book_id
                and isinstance(self.screen, TocScreen)):
            # Preserve browsing position: populate() re-highlights the
            # current chapter, which would discard where the user was.
            ol = self.screen.query_one(OptionList)
            old = ol.highlighted
            self.screen.populate(self._toc_converted(self.chapters_cache))
            if old is not None and ol.option_count:
                ol.highlighted = min(max(old, 0), ol.option_count - 1)
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

    def _toc_converted(self, chapters: list[Chapter]) -> list[Chapter]:
        """Chapter list with titles converted to the current mode. The
        cache itself stays raw — OpenCC round-trips aren't lossless.
        Per-title fallback: one bad title keeps its raw text."""
        if not self.simplified:
            return chapters
        conv = self._conv_t2s()
        if conv is None:
            return chapters
        out = []
        for ch in chapters:
            try:
                out.append(Chapter(ch.pos, ch.cid, conv.convert(ch.title), ch.url))
            except Exception:
                out.append(ch)
        return out

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
        # url/book_id/_raw are set only on successful load_chapter, so a
        # failed jump keeps highlighting the displayed chapter.
        if url:
            self.load_chapter(url)


# ---------------------------------------------------------------- CLI

def _book_target(url, book):
    """Shared URL-vs---book validation; see ARCHITECTURE.md CLI resolution."""
    book_url = book_url_from_arg(url)
    book_arg = book.strip() if book and book.strip() else None
    if url and (url or "").strip() and book_arg:
        sys.exit(f"error: got both a URL ({url!r}) and --book {book!r} — "
                 f"give one or the other")
    return book_url, book_arg


def book_url_from_arg(url: str):
    """Return the book index URL if `url` is one (e.g. .../book/123/ or
    .../book/123/index.html), else None. Chapter URLs never match."""
    # Strip pasted whitespace; accept http and www variants and normalize
    # to the canonical https BASE form. Without this, pasted URLs with
    # spaces or http:// were misclassified as chapter URLs.
    # Drop query/fragment (tracking params, #tuijian) before matching so a
    # pasted book URL with ?utm_* or #frag still opens the book instead of
    # failing as a chapter. Require '/' before index.html so
    # .../book/123index.html is not mistaken for book 123.
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    # Host is case-insensitive (DNS); path stays case-sensitive.
    # Collapse redundant slashes so .../123// opens the book.
    path = re.sub(r"/{2,}", "/", parts.path)
    clean = urlunsplit((parts.scheme, parts.netloc.lower(), path, "", ""))
    m = re.fullmatch(r"https?://(?:www\.)?uukanshu\.cc/book/(\d+)(?:/(?:index\.html)?)?", clean)
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
    # Strip pasted whitespace once; the chapter-URL branch below must
    # return the stripped form (see ARCHITECTURE.md CLI resolution).
    url = args.url.strip() if args.url else args.url
    if url and not url.startswith(("http://", "https://")):
        sys.exit(f"error: url must start with http:// or https:// — "
                 f"got {args.url!r}")
    book_url, book_arg = _book_target(args.url, args.book)
    chapter_n = 1 if args.chapter is None else args.chapter
    if book_url:
        book_id = re.search(r"/book/(\d+)/", book_url).group(1)
        chapters = chapter_list(fetch(book_url), book_id)
        if not chapters:
            sys.exit(f"error: no chapters found at {book_url}.")
        _check_chapter(chapter_n, len(chapters))
        return chapters[chapter_n - 1].url, chapters
    if url:
        if args.chapter is not None:
            # A chapter URL already names its chapter; silently ignoring
            # --chapter would open a chapter the user didn't ask for.
            sys.exit(f"error: --chapter {args.chapter} is ignored for a "
                     f"chapter URL — drop --chapter or start from the "
                     f"book URL / --book <id>")
        return url, None
    if not (args.book and args.book.strip()):
        sys.exit("error: give a chapter URL or --book <id> (see --help).")
    book = args.book.strip()
    page = fetch(f"{BASE}/book/{book}/")
    chapters = chapter_list(page, book)
    if not chapters:
        sys.exit("error: no chapters found on the book page.")
    _check_chapter(chapter_n, len(chapters))
    return chapters[chapter_n - 1].url, chapters


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
        except (AttributeError, OSError, ValueError):
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
    raw = os.environ.get("UUKANSHU_THEME", "night").strip()
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
        book_url, book_arg = _book_target(args.url, args.book)
        if args.chapter is not None:
            sys.exit(f"error: --chapter {args.chapter} is ignored with --list — "
                     f"drop --chapter or drop --list")
        if args.plain:
            sys.exit("error: --print is ignored with --list — "
                     "drop --print or drop --list")
        if not book_url and not book_arg:
            sys.exit("error: --list needs a book URL or --book <id>.")
        if book_url:
            toc_url, book_id = (book_url,
                                re.search(r"/book/(\d+)/", book_url).group(1))
        else:
            toc_url, book_id = f"{BASE}/book/{book_arg}/", book_arg
        chapters = chapter_list(fetch(toc_url), book_id)
        if not chapters:
            sys.exit(f"error: no chapters found at {toc_url}.")
        for ch in chapters:
            t = cc.convert(ch.title) if cc else ch.title
            print(f"{ch.pos:>5}  {t}")
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
