"""Browse screens: the catalogue home over the reader pane.

The app (Reader) is the single Textual App; these screens are pushed on top
of the reader pane and popped when a chapter opens. Screens only touch the
app through its public surface (ui/display/open_book/open_chapter/catalog/
browse_cache/browse_ui), so they stay testable with a fake catalog. See
docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Footer,
    Input,
    OptionList,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option
from rich.text import Text

from .site import CATEGORIES, Card, CardPage
from .shelf import relative_time, resolve_chapter


class PageCache:
    """Small LRU of catalogue pages, owned by the app.

    Kept across browse sessions so returning from the reader renders
    instantly instead of refetching; the refresh binding bypasses it.
    """

    def __init__(self, limit: int = 12):
        self._limit = limit
        self._pages: OrderedDict[str, CardPage] = OrderedDict()

    def get(self, key: str) -> CardPage | None:
        page = self._pages.get(key)
        if page is not None:
            self._pages.move_to_end(key)
        return page

    def put(self, key: str, page: CardPage) -> None:
        self._pages[key] = page
        self._pages.move_to_end(key)
        while len(self._pages) > self._limit:
            self._pages.popitem(last=False)

    def discard(self, key: str) -> None:
        self._pages.pop(key, None)


class BookListPane(Vertical):
    """One paginated card list: status line, options, highlight preview."""

    BINDINGS = [
        Binding("n", "page(1)", "next page"),
        Binding("p", "page(-1)", "prev page"),
        Binding("right", "page(1)", show=False),
        Binding("left", "page(-1)", show=False),
        Binding("d", "scroll_page(1)", "down"),
        Binding("u", "scroll_page(-1)", "up"),
        Binding("r", "refresh", "refresh"),
    ]

    def __init__(self, mode: str, cid: int = 1, keyword: str = "", **kwargs):
        super().__init__(**kwargs)
        self.mode = mode          # "recent" | "category" | "search"
        self.cid = cid
        self.keyword = keyword
        self.page = 1
        self.pages = 1
        self.total: int | None = None
        self.cards: list[Card] = []
        self._error: str | None = None
        self._loading = False
        self._loaded_once = False

    # -- state

    def cache_key(self) -> str:
        if self.mode == "recent":
            return f"recent:{self.page}"
        if self.mode == "category":
            return f"cat:{self.cid}:{self.page}"
        return f"search:{self.keyword}:{self.page}"

    def ensure_loaded(self) -> "BookListPane":
        """Load once on first activation (tab switches are lazy; the
        search pane stays idle until its first keyword is submitted)."""
        if not self._loaded_once:
            self.load()
        return self

    def focus_list(self) -> "BookListPane":
        self.query_one(".book-list", OptionList).focus()
        return self

    def compose(self) -> ComposeResult:
        yield Static("", classes="list-status")
        yield OptionList(classes="book-list")
        yield Static("", classes="preview")

    # -- rendering

    def _status(self) -> str:
        app = self.app
        if self._error is not None:
            return (app.ui("错误：") + self._error + "  ·  r "
                    + app.ui("重试"))
        if self._loading:
            return app.ui("载入中…") + " loading…"
        if self.mode == "search" and not self.keyword.strip():
            return app.ui("搜索") + " · " + app.ui("输入书名，按 Enter 搜索")
        label = {
            "recent": app.ui("最近更新"),
            "category": (app.ui("分类") + " · "
                         + app.ui(dict(CATEGORIES).get(self.cid, ""))),
            "search": app.ui("搜索") + f"「{app.display(self.keyword)}」",
        }[self.mode]
        extra = ""
        if self.mode == "search" and self.total is not None:
            extra = " · " + app.ui("共") + f" {self.total} " + app.ui("条")
        return (label + " · " + app.ui("第") + f" {self.page}/{self.pages} "
                + app.ui("页") + f" · {len(self.cards)} " + app.ui("本")
                + extra)

    def _option_text(self, c: Card) -> Text:
        app = self.app
        t = Text()
        t.append(app.display(c.title), style="bold")
        bits = [x for x in (app.display(c.author), app.display(c.words)) if x]
        if bits:
            t.append("\n  " + " · ".join(bits), style="dim")
        if c.latest_title:
            t.append("\n  " + app.ui("更新到：") + app.display(c.latest_title),
                     style="cyan")
        if c.intro:
            t.append("\n  " + app.display(c.intro)[:100], style="dim")
        return t

    def _preview(self, c: Card) -> None:
        app = self.app
        text = Text()
        text.append(app.display(c.title), style="bold")
        if c.intro:
            text.append("\n" + app.display(c.intro))
        if c.reads:
            text.append("\n" + app.display(c.reads), style="dim")
        self.query_one(".preview", Static).update(text)

    def render_display(self) -> None:
        """Re-render texts after a Simplified/Traditional toggle."""
        self._refresh()

    def _refresh(self) -> None:
        self.query_one(".list-status", Static).update(self._status())
        ol = self.query_one(".book-list", OptionList)
        highlighted = ol.highlighted
        ol.clear_options()
        ol.add_options(Option(self._option_text(c), id=str(c.bid))
                       for c in self.cards)
        if self.cards:
            idx = min(highlighted if highlighted is not None else 0,
                      len(self.cards) - 1)
            ol.highlighted = idx
            self._preview(self.cards[idx])
        else:
            self.query_one(".preview", Static).update("")

    # -- loading

    def load(self, force: bool = False) -> None:
        # The search tab is idle until a keyword is submitted: an empty
        # POST draws the site's generic "hot books" page labelled as
        # search results. See docs/ARCHITECTURE.md.
        if self.mode == "search" and not self.keyword.strip():
            self._refresh()
            return
        self._loaded_once = True
        cache = getattr(self.app, "browse_cache", None)
        if cache is not None and not force:
            hit = cache.get(self.cache_key())
            if hit is not None:
                self._apply(hit)
                return
        self._error = None
        self._loading = True
        self._refresh()
        self.fetch_page()

    @work(exclusive=True, group="browse-list")
    async def fetch_page(self) -> None:
        """One user action = one fetch; errors render in-pane, never raise
        into the TUI (same rule as the reader)."""
        try:
            page = await asyncio.to_thread(self._fetch)
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            self._loading = False
            self._refresh()
            return
        cache = getattr(self.app, "browse_cache", None)
        if cache is not None:
            cache.put(self.cache_key(), page)
        self._apply(page)

    def _fetch(self) -> CardPage:
        cat = self.app.catalog
        if self.mode == "recent":
            return cat.recent_page(self.page)
        if self.mode == "category":
            return cat.category_page(self.cid, self.page)
        return cat.search_page(self.keyword, self.page)

    def _apply(self, page: CardPage) -> None:
        self.cards = page.cards
        self.page = page.page
        self.pages = max(1, page.pages)
        self.total = page.total
        self._loading = False
        self._error = None
        self._refresh()

    # -- actions

    def action_scroll_page(self, sign: int) -> None:
        """Half-page list movement, matching the reader's d/u semantics."""
        ol = self.query_one(".book-list", OptionList)
        if sign > 0:
            ol.action_page_down()
        else:
            ol.action_page_up()

    def action_page(self, sign: int) -> None:
        target = min(max(self.page + sign, 1), self.pages)
        if target == self.page:
            self.notify(self.app.ui("没有更多了") + " / no more pages",
                        severity="warning")
            return
        self.page = target
        self.load()

    def action_refresh(self) -> None:
        cache = getattr(self.app, "browse_cache", None)
        if cache is not None:
            cache.discard(self.cache_key())
        self.load(force=True)

    def on_option_list_option_highlighted(self, event) -> None:
        bid = int(str(event.option.id))
        card = next((c for c in self.cards if c.bid == bid), None)
        if card is not None:
            self._preview(card)

    def on_option_list_option_selected(self, event) -> None:
        bid = int(str(event.option.id))
        card = next((c for c in self.cards if c.bid == bid), None)
        self.app.open_book(bid, card)


class BrowseScreen(Screen):
    """Catalogue home: recent / category / search tabs.

    Pushed over the reader pane; Esc returns to reading. Tab headers are
    clickable, 1-3 jump between them. See docs/ARCHITECTURE.md.
    """

    CSS = """
    #browse-title { height: auto; padding: 1 2 0 2; text-style: bold; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0 1; }
    BookListPane { height: 1fr; }
    .list-status { height: auto; color: $text-muted; }
    .book-list { height: 1fr; }
    .preview { height: auto; max-height: 6; border-top: solid $primary;
               padding: 0 1; }
    #category-box { height: 1fr; }
    #category-list { width: 18; height: 1fr; }
    #search-input { width: 1fr; margin: 0 0 1 0; }
    """

    BINDINGS = [
        Binding("escape", "back", "back"),
        Binding("1", "tab(0)", show=False),
        Binding("2", "tab(1)", show=False),
        Binding("3", "tab(2)", show=False),
        Binding("4", "tab(3)", show=False),
        Binding("slash", "search", "search"),
        Binding("r", "refresh", "refresh"),
        Binding("z", "toggle_simplified", "simplified"),
    ]

    TABS = ("tab-recent", "tab-category", "tab-search", "tab-shelf")
    TAB_NAMES = ("最近更新", "分类", "搜索", "书架")

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._ui("书城") + " / Browse", id="browse-title")
            with TabbedContent(initial=self.TABS[0]):
                with TabPane(self._tab_label(0), id="tab-recent"):
                    yield BookListPane("recent", id="pane-recent")
                with TabPane(self._tab_label(1), id="tab-category"):
                    with Horizontal(id="category-box"):
                        yield OptionList(
                            *(Option(self._ui(name), id=str(cid))
                              for cid, name in CATEGORIES),
                            id="category-list")
                        yield BookListPane("category", id="pane-category")
                with TabPane(self._tab_label(2), id="tab-search"):
                    with Vertical():
                        yield Input(placeholder=self._ui("书名搜索…"),
                                    id="search-input")
                        yield BookListPane("search", id="pane-search")
                with TabPane(self._tab_label(3), id="tab-shelf"):
                    yield ShelfPane(id="pane-shelf")
        yield Footer()

    # -- app surface helpers

    def _ui(self, s: str) -> str:
        return self.app.ui(s)

    def _tab_label(self, i: int) -> str:
        return self._ui(self.TAB_NAMES[i])

    def _activate(self, i: int) -> None:
        self.query_one(TabbedContent).active = self.TABS[i]

    def _active_pane(self):
        active = self.query_one(TabbedContent).active
        if active == "tab-category":
            return self.query_one("#pane-category", BookListPane)
        if active == "tab-search":
            return self.query_one("#pane-search", BookListPane)
        if active == "tab-shelf":
            return self.query_one("#pane-shelf", ShelfPane)
        return self.query_one("#pane-recent", BookListPane)

    # -- tab lifecycle

    def on_tabbed_content_tab_activated(self, event) -> None:
        pane_id = event.pane.id if event.pane is not None else ""
        if pane_id in self.TABS:
            self.app.browse_ui["tab"] = self.TABS.index(pane_id)
        if pane_id == "tab-recent":
            self.query_one("#pane-recent", BookListPane).ensure_loaded()
            self.query_one("#pane-recent", BookListPane).focus_list()
        elif pane_id == "tab-category":
            pane = self.query_one("#pane-category", BookListPane)
            pane.cid = self.app.browse_ui.get("category", 1)
            pane.ensure_loaded().focus_list()
            self._sync_category_highlight()
        elif pane_id == "tab-search":
            query = self.app.browse_ui.get("query", "")
            self.query_one("#search-input", Input).value = query
            pane = self.query_one("#pane-search", BookListPane)
            pane.keyword = query
            pane.ensure_loaded()
            self.query_one("#search-input", Input).focus()
        elif pane_id == "tab-shelf":
            self.query_one("#pane-shelf", ShelfPane).ensure_loaded().focus_list()

    def _sync_category_highlight(self) -> None:
        cid = str(self.app.browse_ui.get("category", 1))
        ol = self.query_one("#category-list", OptionList)
        for i in range(ol.option_count):
            if str(ol.get_option_at_index(i).id) == cid:
                ol.highlighted = i
                return

    # -- actions

    def action_tab(self, i: int) -> None:
        self._activate(i)

    def action_back(self) -> None:
        if (getattr(self.app, "_raw", None) is not None
                or getattr(self.app, "_load_error", None) is not None):
            self.app.pop_screen()
        else:
            self.notify(self._ui("按 q 退出") + " / press q to quit")

    def action_search(self) -> None:
        self._activate(2)

    def action_refresh(self) -> None:
        self._active_pane().action_refresh()

    def action_toggle_simplified(self) -> None:
        self.app.simplified = not self.app.simplified
        self.refresh_display()

    def refresh_display(self) -> None:
        """Re-render chrome + loaded panes in the new display mode."""
        self.query_one("#browse-title", Static).update(
            self._ui("书城") + " / Browse")
        tc = self.query_one(TabbedContent)
        for i, tab_id in enumerate(self.TABS):
            tc.get_tab(tab_id).update(self._tab_label(i))
        self.query_one("#search-input", Input).placeholder = self._ui(
            "书名搜索…")
        ol = self.query_one("#category-list", OptionList)
        highlighted = ol.highlighted
        ol.clear_options()
        ol.add_options(Option(self._ui(name), id=str(cid))
                       for cid, name in CATEGORIES)
        if highlighted is not None:
            ol.highlighted = min(highlighted, ol.option_count - 1)
        for pane in self.query(BookListPane):
            pane.render_display()
        for pane in self.query(ShelfPane):
            pane.render_display()

    # -- events

    def on_input_submitted(self, event) -> None:
        q = event.value.strip()
        if not q:
            return
        self.app.browse_ui["query"] = q
        pane = self.query_one("#pane-search", BookListPane)
        pane.keyword = q
        pane.page = 1
        pane.cards = []
        pane.load(force=True)
        pane.focus_list()

    def on_option_list_option_selected(self, event) -> None:
        # The category sidebar only; card lists are handled by the pane.
        if event.option_list.id != "category-list":
            return
        cid = int(str(event.option.id))
        self.app.browse_ui["category"] = cid
        pane = self.query_one("#pane-category", BookListPane)
        pane.cid = cid
        pane.page = 1
        pane.cards = []
        pane.load(force=True)
        pane.focus_list()


class DetailScreen(Screen):
    """Book detail: meta, actions, full chapter list.

    Pushed from a catalogue card or shelf row. Enter on the chapter list
    reads that chapter; o reads the bookmarked one (or chapter 1); s toggles
    the shelf; Esc returns. See docs/ARCHITECTURE.md.
    """

    CSS = """
    #detail-title { height: auto; padding: 1 2 0 2; text-style: bold; }
    #detail-meta { height: auto; padding: 0 2; color: $text-muted; }
    #detail-intro { height: auto; max-height: 8; padding: 0 2 1 2; }
    #detail-actions { height: auto; padding: 0 2 1 2; }
    #detail-actions Button { margin: 0 1 0 0; min-width: 12; }
    #chapter-list { height: 1fr; margin: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "back", "back"),
        Binding("o", "read", "read"),
        Binding("s", "toggle_shelf", "shelf"),
        Binding("r", "reload", "refresh"),
        Binding("z", "toggle_simplified", "simplified"),
    ]

    def __init__(self, book_id: str | int, card: Card | None = None):
        super().__init__()
        self.book_id = str(book_id)
        self.card = card
        self.meta = None
        self.chapters: list = []
        self._error: str | None = None
        self._loading = True

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="detail-title")
            yield Static("", id="detail-meta")
            yield Static("", id="detail-intro")
            with Horizontal(id="detail-actions"):
                yield Button("", id="btn-read", variant="primary")
                yield Button("", id="btn-shelf")
                yield Button("", id="btn-back")
            yield OptionList(id="chapter-list")
        yield Footer()

    def on_mount(self) -> None:
        self._render_header()
        self.fetch_detail()

    # -- loading

    @work(exclusive=True, group="book-detail")
    async def fetch_detail(self) -> None:
        try:
            detail = await asyncio.to_thread(
                self.app.catalog.book_detail, self.book_id)
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            self._loading = False
            self._render_header()
            return
        self.meta = detail.meta
        self.chapters = detail.chapters
        self._loading = False
        self._error = None
        if detail.chapters:
            # The reader's l key can then reuse this TOC instead of refetching.
            self.app.seed_toc(self.book_id, detail.chapters)
        self._render_header()
        self._fill_chapters()
        self.query_one("#chapter-list", OptionList).focus()

    # -- rendering

    def _display(self, s: str) -> str:
        return self.app.display(s)

    def _ui(self, s: str) -> str:
        return self.app.ui(s)

    def _read_label(self) -> str:
        if self.app.shelf.get(self.book_id) is not None:
            return self._ui("继续阅读")
        return self._ui("开始阅读")

    def _shelf_label(self) -> str:
        if self.app.shelf.get(self.book_id) is not None:
            return self._ui("移出书架")
        return self._ui("加入书架")

    def _render_header(self) -> None:
        meta = self.meta
        card = self.card
        if meta:
            title = meta.title
        elif card:
            title = card.title
        else:
            title = self.book_id
        self.query_one("#detail-title", Static).update(self._display(title))
        bits = []
        if meta:
            if meta.author:
                bits.append(self._ui("作者") + "：" + self._display(meta.author))
            for value in (meta.status, meta.category, meta.words):
                if value:
                    bits.append(self._display(value))
            if meta.updated_at:
                bits.append(self._ui("更新") + "：" + meta.updated_at)
        elif card:
            if card.author:
                bits.append(self._ui("作者") + "：" + self._display(card.author))
            if card.words:
                bits.append(self._display(card.words))
        if self._loading:
            bits.append(self._ui("载入中…"))
        if self._error is not None:
            bits.append(self._ui("错误：") + self._error + "  ·  r "
                        + self._ui("重试"))
        self.query_one("#detail-meta", Static).update(" · ".join(bits))
        intro = ""
        if meta and meta.intro:
            intro = self._display(meta.intro)
        elif card and card.intro:
            intro = self._display(card.intro)
        self.query_one("#detail-intro", Static).update(intro)
        self.query_one("#btn-read", Button).label = self._read_label()
        self.query_one("#btn-shelf", Button).label = self._shelf_label()
        self.query_one("#btn-back", Button).label = self._ui("返回")

    def _fill_chapters(self) -> None:
        ol = self.query_one("#chapter-list", OptionList)
        ol.clear_options()
        progress = self.app.shelf.get(self.book_id)
        mark_id = progress.chapter_id if progress else 0
        ol.add_options(
            Option(f"{'▸' if ch.cid == mark_id else ' '} {ch.pos:>5}  "
                   + self._display(ch.title), id=str(ch.cid))
            for ch in self.chapters)
        if self.chapters:
            ol.highlighted = 0

    def refresh_display(self) -> None:
        """Re-render after a Simplified/Traditional toggle."""
        self._render_header()
        self._fill_chapters()

    # -- actions

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_reload(self) -> None:
        self._loading = True
        self._error = None
        self._render_header()
        self.fetch_detail()

    def action_read(self) -> None:
        url = resolve_chapter(self.chapters, self.app.shelf.get(self.book_id))
        if url is None:
            self.notify(self._ui("没有找到章节") + " / no chapters",
                        severity="warning")
            return
        self.app.open_chapter(url)

    def action_toggle_shelf(self) -> None:
        shelf = self.app.shelf
        if shelf.get(self.book_id) is not None:
            shelf.remove(self.book_id)
            self.notify(self._ui("已移出书架") + " / removed")
        else:
            meta = self.meta
            card = self.card
            title = meta.title if meta else (card.title if card else "")
            author = meta.author if meta else (card.author if card else "")
            shelf.record(self.book_id, title=title, author=author)
            self.notify(self._ui("已加入书架") + " / added")
        self._render_header()

    def action_toggle_simplified(self) -> None:
        self.app.simplified = not self.app.simplified
        self.refresh_display()

    # -- events

    def on_button_pressed(self, event) -> None:
        if event.button.id == "btn-read":
            self.action_read()
        elif event.button.id == "btn-shelf":
            self.action_toggle_shelf()
        elif event.button.id == "btn-back":
            self.action_back()

    def on_option_list_option_selected(self, event) -> None:
        cid = int(str(event.option.id))
        chapter = next((c for c in self.chapters if c.cid == cid), None)
        if chapter is not None:
            self.app.open_chapter(chapter.url)



class ShelfPane(Vertical):
    """Local bookshelf: one row per book, newest read first."""

    BINDINGS = [
        Binding("r", "refresh", "refresh"),
        Binding("d", "remove", "remove"),
        Binding("delete", "remove", show=False),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._rows: list = []
        self._loaded = False

    def compose(self) -> ComposeResult:
        yield Static("", classes="list-status")
        yield OptionList(classes="book-list")
        yield Static("", classes="preview")

    def ensure_loaded(self) -> "ShelfPane":
        if not self._loaded:
            self.load()
        return self

    def focus_list(self) -> "ShelfPane":
        self.query_one(".book-list", OptionList).focus()
        return self

    def load(self) -> None:
        self._loaded = True
        self._rows = self.app.shelf.all()
        self._refresh()

    def action_refresh(self) -> None:
        self.load()

    def render_display(self) -> None:
        self._refresh()

    # -- rendering

    def _status(self) -> str:
        app = self.app
        if not self._rows:
            return app.ui("书架") + " · " + app.ui("读到哪本就会自动记在这里")
        return app.ui("书架") + f" · {len(self._rows)} " + app.ui("本")

    def _option_text(self, p) -> Text:
        app = self.app
        t = Text()
        t.append(app.display(p.title or p.book_id), style="bold")
        bits = []
        if p.chapter_title:
            bits.append(app.display(p.chapter_title))
        bits.append(app.ui(relative_time(p.updated_at)))
        t.append("\n  " + " · ".join(bits), style="dim")
        return t

    def _preview(self, p) -> None:
        app = self.app
        text = Text()
        if p.author:
            text.append(app.ui("作者") + "：" + app.display(p.author))
        if p.chapter_url:
            if text:
                text.append("\n")
            text.append(p.chapter_url, style="dim")
        self.query_one(".preview", Static).update(text)

    def _refresh(self) -> None:
        self.query_one(".list-status", Static).update(self._status())
        ol = self.query_one(".book-list", OptionList)
        highlighted = ol.highlighted
        ol.clear_options()
        ol.add_options(Option(self._option_text(p), id=p.book_id)
                       for p in self._rows)
        if self._rows:
            idx = min(highlighted if highlighted is not None else 0,
                      len(self._rows) - 1)
            ol.highlighted = idx
            self._preview(self._rows[idx])
        else:
            self.query_one(".preview", Static).update("")

    def _highlighted(self):
        ol = self.query_one(".book-list", OptionList)
        if ol.highlighted is None or not self._rows:
            return None
        return self._rows[min(ol.highlighted, len(self._rows) - 1)]

    # -- actions

    def action_remove(self) -> None:
        p = self._highlighted()
        if p is None:
            return
        bid = p.book_id
        name = self.app.display(p.title or p.book_id)
        self.app.push_screen(
            ConfirmScreen(self.app.ui("移出书架") + "：" + name + "？"),
            lambda ok: self._remove_confirmed(ok, bid))

    def _remove_confirmed(self, ok: bool, bid: str) -> None:
        if ok:
            self.app.shelf.remove(bid)
            self.load()

    def on_option_list_option_selected(self, event) -> None:
        bid = str(event.option.id)
        row = next((p for p in self._rows if p.book_id == bid), None)
        if row is None:
            return
        card = Card(int(bid) if bid.isdigit() else 0, row.title,
                    row.author, "", "", "", None, "")
        self.app.open_book(bid, card)


class ConfirmScreen(ModalScreen):
    """Small yes/no modal (shelf removal). y confirms, Esc cancels."""

    CSS = """
    ConfirmScreen { align: center middle; }
    #confirm-box { width: auto; max-width: 70; height: auto;
                   border: round $primary; background: $surface;
                   padding: 1 2; }
    #confirm-actions { height: auto; margin-top: 1; }
    #confirm-actions Button { margin: 0 1 0 0; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("n", "cancel", show=False),
        Binding("y", "confirm", show=False),
    ]

    def __init__(self, message: str, confirm_label: str = "移除"):
        super().__init__()
        self.message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self.message)
            with Horizontal(id="confirm-actions"):
                yield Button(self.app.ui(self.confirm_label),
                             id="confirm-yes", variant="error")
                yield Button(self.app.ui("取消"), id="confirm-no")

    def on_button_pressed(self, event) -> None:
        if event.button.id == "confirm-yes":
            self.dismiss(True)
        elif event.button.id == "confirm-no":
            self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

