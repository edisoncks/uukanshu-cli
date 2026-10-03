"""Browse-screen pilot tests with a fake catalogue (no network)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uukanshu as u
from uukanshu import browse, site
from uukanshu import shelf as sh
from textual.widgets import Input, OptionList


def card(bid, title="Book", **kw):
    fields = dict(bid=bid, title=title, author="Author", words="字數：1",
                  reads="", latest_title="Latest", latest_url=None,
                  intro="Intro")
    fields.update(kw)
    return site.Card(**fields)


def meta(title="Book"):
    return site.BookMeta(title, "Author", "1字", "玄幻奇幻", "連載", "Intro",
                         "Latest", None, "2026-01-01")


class FakeCatalog:
    def __init__(self):
        self.calls = []
        self.recent = {}
        self.categories = {}
        self.searches = {}
        self.details = {}

    def recent_page(self, page=1):
        self.calls.append(("recent", page))
        return self.recent.get(page, site.CardPage([], page, page, None))

    def category_page(self, cid, page=1):
        self.calls.append(("category", cid, page))
        return self.categories.get((cid, page),
                                   site.CardPage([], page, page, None))

    def search_page(self, keyword, page=1):
        self.calls.append(("search", keyword, page))
        return self.searches.get((keyword, page),
                                 site.CardPage([], page, page, 0))

    def book_detail(self, book_id):
        self.calls.append(("detail", str(book_id)))
        return self.details.get(str(book_id), site.BookDetail(meta(), []))


def make_app(tmp_path, catalog, url=None):
    return u.Reader(url, None, False, 2, update_check=False,
                    catalog=catalog,
                    shelf=sh.Shelf(path=str(tmp_path / "shelf.json")))


async def wait_until(pilot, cond, tries=80):
    for _ in range(tries):
        await pilot.pause(0.02)
        if cond():
            return True
    return False


def _browse_screen(app):
    return isinstance(app.screen, browse.BrowseScreen)


def test_bare_start_opens_browse(tmp_path):
    cat = FakeCatalog()
    cat.recent[1] = site.CardPage([card(1, "First"), card(2, "Second")],
                                  1, 5, None)
    app = make_app(tmp_path, cat)

    async def go():
        async with app.run_test(size=(100, 32)) as pilot:
            assert await wait_until(pilot, lambda: _browse_screen(app))
            pane = app.screen.query_one("#pane-recent", browse.BookListPane)
            assert await wait_until(pilot, lambda: len(pane.cards) == 2)
            ol = pane.query_one(".book-list", OptionList)
            assert ol.option_count == 2
            assert ("recent", 1) in cat.calls
            assert app.browse_ui["tab"] == 0

    asyncio.run(go())


def test_page_navigation(tmp_path):
    cat = FakeCatalog()
    cat.recent[1] = site.CardPage([card(1)], 1, 2, None)
    cat.recent[2] = site.CardPage([card(2)], 2, 2, None)
    app = make_app(tmp_path, cat)

    async def go():
        async with app.run_test(size=(100, 32)) as pilot:
            assert await wait_until(pilot, lambda: _browse_screen(app))
            pane = app.screen.query_one("#pane-recent", browse.BookListPane)
            assert await wait_until(pilot, lambda: pane.cards)
            await pilot.press("n")
            assert await wait_until(pilot, lambda: pane.page == 2)
            assert [c.bid for c in pane.cards] == [2]
            await pilot.press("p")
            assert await wait_until(pilot, lambda: pane.page == 1)
            assert [c.bid for c in pane.cards] == [1]

    asyncio.run(go())


def test_search_submit(tmp_path):
    cat = FakeCatalog()
    cat.searches[("斗罗", 1)] = site.CardPage([card(7, "斗罗大陆")], 1, 1, 3)
    app = make_app(tmp_path, cat)

    async def go():
        async with app.run_test(size=(100, 32)) as pilot:
            assert await wait_until(pilot, lambda: _browse_screen(app))
            await pilot.press("3")
            assert await wait_until(
                pilot, lambda: isinstance(app.focused, Input))
            inp = app.screen.query_one("#search-input", Input)
            inp.value = "斗罗"
            await pilot.press("enter")
            pane = app.screen.query_one("#pane-search", browse.BookListPane)
            assert await wait_until(pilot, lambda: pane.cards)
            assert ("search", "斗罗", 1) in cat.calls
            assert pane.total == 3
            assert app.browse_ui["query"] == "斗罗"

    asyncio.run(go())


def test_category_select(tmp_path):
    cat = FakeCatalog()
    cat.categories[(2, 1)] = site.CardPage([card(9, "武侠书")], 1, 1, None)
    app = make_app(tmp_path, cat)

    async def go():
        async with app.run_test(size=(100, 32)) as pilot:
            assert await wait_until(pilot, lambda: _browse_screen(app))
            await pilot.press("2")
            ol = app.screen.query_one("#category-list", OptionList)
            assert await wait_until(pilot, lambda: ol.option_count == 10)
            ol.highlighted = 1  # category id 2
            ol.action_select()
            pane = app.screen.query_one("#pane-category", browse.BookListPane)
            assert await wait_until(pilot, lambda: pane.cards)
            assert ("category", 2, 1) in cat.calls
            assert app.browse_ui["category"] == 2

    asyncio.run(go())


def test_enter_opens_first_chapter(tmp_path):
    cat = FakeCatalog()
    cat.recent[1] = site.CardPage([card(1, "Book One")], 1, 1, None)
    cat.details["1"] = site.BookDetail(
        meta("Book One"),
        [u.Chapter(1, 10, "第一章", "https://uukanshu.cc/book/1/10.html")])
    app = make_app(tmp_path, cat)
    seen = []
    app.load_chapter = lambda url: seen.append(url)

    async def go():
        async with app.run_test(size=(100, 32)) as pilot:
            assert await wait_until(pilot, lambda: _browse_screen(app))
            pane = app.screen.query_one("#pane-recent", browse.BookListPane)
            assert await wait_until(pilot, lambda: pane.cards)
            await pilot.press("enter")
            assert await wait_until(
                pilot, lambda: seen == ["https://uukanshu.cc/book/1/10.html"])
            assert await wait_until(pilot, lambda: len(app.screen_stack) == 1)
            assert app.chapters_cache == cat.details["1"].chapters

    asyncio.run(go())


def test_simplified_toggle_rerenders(tmp_path):
    cat = FakeCatalog()
    cat.recent[1] = site.CardPage([card(1, "斗羅大陸")], 1, 1, None)
    app = make_app(tmp_path, cat)

    async def go():
        async with app.run_test(size=(100, 32)) as pilot:
            assert await wait_until(pilot, lambda: _browse_screen(app))
            pane = app.screen.query_one("#pane-recent", browse.BookListPane)
            assert await wait_until(pilot, lambda: pane.cards)
            await pilot.press("z")
            assert app.simplified is True
            ol = pane.query_one(".book-list", OptionList)
            assert "斗罗大陆" in str(ol.get_option_at_index(0).prompt)

    asyncio.run(go())


def test_mouse_click_selects_and_opens(tmp_path):
    cat = FakeCatalog()
    cat.recent[1] = site.CardPage([card(1, "Book One")], 1, 1, None)
    cat.details["1"] = site.BookDetail(
        meta("Book One"),
        [u.Chapter(1, 10, "第一章", "https://uukanshu.cc/book/1/10.html")])
    app = make_app(tmp_path, cat)
    seen = []
    app.load_chapter = lambda url: seen.append(url)

    async def go():
        async with app.run_test(size=(100, 32)) as pilot:
            assert await wait_until(pilot, lambda: _browse_screen(app))
            pane = app.screen.query_one("#pane-recent", browse.BookListPane)
            assert await wait_until(pilot, lambda: pane.cards)
            await pilot.click(pane.query_one(".book-list"), offset=(3, 1))
            assert await wait_until(pilot, lambda: bool(seen))

    asyncio.run(go())
