"""Reader failure-state tests (B7/B8, Linus-revised)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uukanshu as u


def test_on_toc_choice_defers_url_until_success():
    r = u.Reader("https://uukanshu.cc/book/123/1.html", None, False, 2,
                 update_check=False)
    seen = []
    r.load_chapter = lambda url: seen.append(url)
    r.url = "OLD"
    r.on_toc_choice("NEW")
    assert r.url == "OLD"
    assert seen == ["NEW"]
    r.on_toc_choice(None)
    assert seen == ["NEW"]


async def _pilot():
    app = u.Reader("https://uukanshu.cc/book/123/1.html", None, False, 2,
                   update_check=False)
    # Prevent background fetch from hitting network.
    app.load_chapter = lambda url: None  # type: ignore
    return app


def test_toggle_on_error_rerenders_error_not_stale():
    import asyncio

    async def go():
        app = await _pilot()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._raw = ("old-book", "old-title", "old-text")
            app._load_error = "RuntimeError: boom"
            was = app.simplified
            app.action_toggle_simplified()
            assert app.simplified is not was
            # Error pane must not contain stale chapter text.
            from textual.widgets import Static
            rendered = str(app.query_one("#doc", Static).render())
            assert "boom" in rendered
            assert "old-text" not in rendered

    asyncio.run(go())


def test_toggle_with_nothing_loaded_warns_without_flip():
    import asyncio

    async def go():
        app = await _pilot()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._raw = None
            app._load_error = None
            was = app.simplified
            notes = []
            app.notify = lambda *a, **k: notes.append(a)  # type: ignore
            app.action_toggle_simplified()
            assert app.simplified is was
            assert notes

    asyncio.run(go())
