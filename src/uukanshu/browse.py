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
from textual.screen import Screen
from textual.widgets import (
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
        """Load once on first activation (tab switches are lazy)."""
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
        self.app.open_book(int(str(event.option.id)))


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
        Binding("slash", "search", "search"),
        Binding("r", "refresh", "refresh"),
        Binding("z", "toggle_simplified", "simplified"),
    ]

    TABS = ("tab-recent", "tab-category", "tab-search")
    TAB_NAMES = ("最近更新", "分类", "搜索")

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
            self.query_one("#search-input", Input).value = (
                self.app.browse_ui.get("query", ""))
            self.query_one("#pane-search", BookListPane).ensure_loaded()
            self.query_one("#search-input", Input).focus()

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
