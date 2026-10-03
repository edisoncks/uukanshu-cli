# Architecture

One-line flow: `CLI resolve -> fetch() -> parse -> Reader / --print / --list`.

The app is a small package: `site.py` owns the site contract (fetching
+ parsing) and `__init__.py` owns the TUI + CLI. Details on fetching/parsing
live in [SCRAPING.md](SCRAPING.md).

## Module map

- `site.py` — the site contract. `fetch(url) -> str`: plain HTTPS + browser headers + retries. See [SCRAPING.md](SCRAPING.md).
- `_iter_anchors(page)`: single anchor source for `chapter_list`/`link`/breadcrumb. See [SCRAPING.md](SCRAPING.md).
- `chapter_list(toc_page, book_id)`: regex TOC scan → `list[Chapter(pos, cid, title, url)]` (tuple-compatible NamedTuple). Keeps last occurrence per chapter (reading order), drops other-book links.
- `extract_chapter(page, url)`: `(book, title, text, prev, toc, next)` via `readcotent` div + `mulu-box` cut. See [SCRAPING.md](SCRAPING.md).
- `link(page, url, label)`: prev/TOC/next anchor → canonical chapter URL or `None` (= end-of-book notice). Host case-insensitive, query/fragment stripped, inner tags tolerated.
- `chapter_id(url)`, `absolutize(href, url)`: URL helpers (`site.py`); `book_url_from_arg(url)`: CLI-side book-URL normalizer (`__init__.py`).
- `parse_cards(page)` / `parse_search_page(page)` / `parse_book_meta(page, url)`: browse cards (`bookbox`), search totals + single hits, book-detail headers. Class tokens, tags stripped. See [SCRAPING.md](SCRAPING.md).
- `Catalog`: network facade (`recent_page` / `category_page` / `search_page` / `book_detail`); one user action = one fetch, injectable for UI tests. `CardPage` / `BookDetail` / `Card` / `BookMeta` are the browse data shapes. Book URLs accept `http(s)`, `www` (any case), trailing `/index.html`, redundant slashes collapsed, query/fragment stripped, host lowercased; chapter URLs stripped of pasted whitespace; chapter path match case-insensitive.
- `browse.py` — catalogue screens pushed over the reader pane: `BrowseScreen` (recent / category / search tabs), `BookListPane` (one paginated card list), `PageCache` (app-owned LRU so returning from the reader is instant). See [Browse screens](#browse-screens).
- `shelf.py` — local bookshelf: one `Progress` per book, JSON + atomic replace, corrupt file reads empty (`UUKANSHU_DATA_DIR` override). `resolve_chapter()` resume order: stable `pageId` → TOC `position` → stored URL → chapter 1.
- `TocScreen` / `TocOptionList`: modal chapter picker. Opens scrolled to current chapter (`scroll_to_highlight(top=True)`); `d/u` move half-page with selection.
- `Reader(App)`: Textual reader. `load_chapter` (`@work exclusive, group="nav"`), `fetch_toc` (`group="toc"`), `check_update` (`group="update"`). Never raises into TUI — fetch errors render in-pane.
- `run()` / `main()`: argparse CLI + `resolve_start_url()` + `_book_target()` shared URL-vs-`--book` validation + env helpers. `main()` forces UTF-8 stdio, maps `KeyboardInterrupt` → 130, `BrokenPipeError` → 0, `RuntimeError/OSError/UnicodeError` → `error: ...`.

## Browse screens

`browse.py` holds the catalogue UI pushed over the reader pane. Tabs load
lazily; `n`/`p` page a list; `Enter` opens a book; `Esc` pops back to the
reader. Opening a chapter pops the whole browse stack
(`Reader._close_browse`) and loads it in the reader. Screens talk to the app
through `ui` / `display` / `open_book` / `open_chapter` / `catalog` /
`browse_cache` / `browse_ui` only, so pilot tests inject a fake catalog and
run without network. `z` is handled at screen level (re-render in place)
because the reader's own `z` refuses when no chapter is loaded.

## CLI resolution

| Input | Result |
| ----- | ------ |
| Book URL [+ `--chapter N`] | Fetch TOC, range-check `N`, open `chapters[N-1]`. Returns TOC for cache. |
| Chapter URL (no `--chapter`) | Open directly. `--chapter` + chapter URL → error. |
| `--book ID` [+ `--chapter N`] | Same as book URL via `${BASE}/book/<ID>/`. |
| `--book` / book URL + `--list` | Print TOC, exit. `--chapter` / `--print` + `--list` → error. |
| `--print` | Fetch one chapter, print `book\\ntitle\\n\\ntext`, exit. No TUI. |
| Nothing | `error: give a chapter URL or --book <id>`. |

`_check_chapter()` never clamps — out-of-range exits with the book's chapter count.

## Reader state

- `url`, `book_id`, `prev_url`/`next_url`, `_raw = (book, title, text)` last fetched (always Traditional), `_load_error` raw error of last failed load.
- Failed `load_chapter` keeps `url`/`_raw` at the last good chapter and records `_load_error`; `on_toc_choice` never sets `url` itself so a failed jump keeps highlighting the displayed chapter. `z` on an error pane re-renders the error in the new mode instead of resurrecting stale `_raw`.
- `chapters_cache` + `_cache_book`: TOC seeded by CLI or `fetch_toc()`; stays raw, converted at render/populate time (OpenCC round-trips aren't lossless). `fetch_toc` captures `book_id` at open; a racing chapter nav (`n`/`→`, `p`/`←`) that changes `book_id` mid-flight only wastes one refetch on next open (`_cache_book != book_id`), never shows the wrong book. `self.screen is screen` guard prevents writing to a dismissed modal.
- Chapter-nav keys (`n`/`→`, `p`/`←`) no-op on open modal; `None` next/prev → "end/start of book" notice.
- `z` toggles `simplified`, re-renders `_raw` + TOC in place, preserves list position.
- `t`/`T` cycles the 8 `READER_THEMES` (`night` default); notifies `主题 / theme: <name>`.
- `ui(s)`: chrome strings stored Simplified, converted via lazy `s2t` when in Traditional mode; content via lazy `t2s` when in Simplified mode. Missing dict or convert failure → fall back to raw, never crash in-app. `_render` falls back whole-triple; `_toc_converted` falls back per-title.
- No whitelist post-pass on conversion (a prior one corrupted `土著` etc. — do not re-add).

## Bookshelf

`shelf.py` persists one `Progress` per book (JSON, atomic replace, corrupt
file reads empty; a read-only data dir never crashes reading). The file
lives in the platform data dir (`XDG_DATA_HOME` / `~/Library/Application
Support` / `%APPDATA%`, overridden by `UUKANSHU_DATA_DIR`). No network, no
server — progress stays on this machine.

## Config precedence

Flag > env (`UUKANSHU_*`) > default. `UUKANSHU_THEME` whitespace-stripped and validated against theme names; `UUKANSHU_PAD` / `UUKANSHU_SIMPLIFIED` / `UUKANSHU_NO_UPDATE_CHECK` parsed with clean `error:` exits. See [DEVELOPMENT.md](DEVELOPMENT.md#appendix-full-cli-reference) and updater contract in [RELEASING.md](RELEASING.md#updater-contract).

## Themes

`night` (default, dark) · `sepia` · `paper` · `catppuccin-frappe` · `catppuccin-macchiato` · `catppuccin-mocha` · `tokyo-night` · `matrix`. Registered via `textual.theme.Theme`; `self.theme` cycled by index.
