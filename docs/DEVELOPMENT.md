# Development

Notes for anyone working on `uukanshu` itself. Readers start at the
[README](../README.md); cutting a release is in [RELEASING.md](RELEASING.md);
how the code fits together is in [ARCHITECTURE.md](ARCHITECTURE.md);
site/f network details are in [SCRAPING.md](SCRAPING.md).

## Project layout

| Path                            | What it is                                                                               |
| ------------------------------- | ---------------------------------------------------------------------------------------- |
| `src/uukanshu/__init__.py`      | The whole app in one module: fetching, parsing, reader UI, CLI                           |
| `pyproject.toml`                | Package metadata, dependencies, the `uukanshu` entry point                               |
| `uukanshu.spec`                 | PyInstaller spec for the standalone release binaries                                     |
| `.github/workflows/release.yml` | Release pipeline: per-platform builds attached to a GitHub Release                       |
| `assets/uukanshu.ico`           | Windows icon for the binary                                                              |
| `uv.lock / .python-version`     | Pinned dependencies and Python 3.14 — must match CI (see below)                          |
| `docs/`                         | All internals docs (this file + `ARCHITECTURE`, `SCRAPING`, `RELEASING`, `CONTRIBUTING`) |

Inside the module — details in [ARCHITECTURE.md](ARCHITECTURE.md):

- `fetch()` — HTTPS fetch, gzip handling, retries. See [SCRAPING.md](SCRAPING.md).
- `chapter_list()` / `extract_chapter()` / `link()` — site parsing. See [SCRAPING.md](SCRAPING.md).
- `TocScreen` / `Reader` — Textual UI.
- `run()` / `main()` — CLI entry, error handling.

## Setup

Install [uv](https://docs.astral.sh/uv/), then:

```sh
git clone https://github.com/edisoncks/uukanshu-cli && cd uukanshu-cli
uv sync --group build
uv run uukanshu --help
```

`uv.lock` is committed. After `uv add` / `uv remove`, commit the updated
lock with `pyproject.toml`. CI builds with `uv sync --frozen`, so a stale
lock breaks the release build — see [RELEASING.md](RELEASING.md).

`mise.toml` pins the toolchain (`python = "3.14"`, `uv = "latest"`).
The Python version matters for TLS fingerprinting — see
[SCRAPING.md](SCRAPING.md#tls-fingerprinting).

## Install from source for daily use

```sh
uv tool install .                                   # from a local clone
uv tool install git+https://github.com/edisoncks/uukanshu-cli   # or from git
uv tool upgrade uukanshu                            # after pulling changes
uv tool uninstall uukanshu
```

If the command isn't found: `uv tool update-shell`, then restart the shell.

## Build binaries

PyInstaller cannot cross-compile — build **on** the target OS:

```sh
uv run --no-sync pyinstaller uukanshu.spec --noconfirm
# → dist/uukanshu (dist/uukanshu.exe on Windows)
```

Smoke-test with a real fetch, not just `--help`:

```sh
./dist/uukanshu --version
./dist/uukanshu --book 18957 --list > /dev/null
```

Why a real fetch: Cloudflare scores the frozen TLS handshake — see
[SCRAPING.md](SCRAPING.md#tls-fingerprinting). The release workflow does
the same check so a blocked build never ships.

`uukanshu.spec` uses `collect_all()` for `opencc` (dictionary data) and
`textual` (styles) because static analysis misses them. `console=True`
keeps stdin/stdout for the TUI.

## Windows console encoding

`main()` forces stdout/stderr to UTF-8 (`_force_utf8_stdio()`). Windows
defaults to a legacy codepage (e.g. cp1252) that can't encode help arrows,
CJK text, or novel content. Don't remove it.

`main()` also handles `KeyboardInterrupt` (exit 130) and `BrokenPipeError`
(piping `--list` / `--print` to `head` exits 0 quietly, like Unix tools).

## Troubleshooting (dev)

- **Lock drift:** `uv sync --frozen` fails in CI → run `uv lock` locally and commit `uv.lock`.
- **`uv tool` command not found:** `uv tool update-shell`, restart shell.
- **Simplified conversion fails:** OpenCC dicts missing — reinstall deps; the reader falls back to raw text in-app but the CLI with `-z` exits with an error by design.
- **`UUKANSHU_*` weirdness:** see Appendix below — bad values exit with `error: ...`, never traceback.

## Appendix: full CLI reference

`uukanshu --help` is authoritative. Summary for contributors:

```txt
uukanshu [url] [--book ID] [--chapter N] [--list] [--simplified]
         [--pad N] [--theme NAME] [--print] [--no-update-check] [--version]
```

| Flag | Env default | Notes |
| ---- | ----------- | ----- |
| `url` | — | Chapter or book URL. Must start with `http://` / `https://`. Book URLs match `.../book/<ID>/` or `.../book/<ID>/index.html` (query/fragment ignored). |
| `-b`, `--book <ID>` | — | Book ID from `/book/<ID>/`. Whitespace stripped. Conflicts with `url` → error. |
| `-c`, `--chapter N` | — | 1-based TOC position, range-checked. Ignored with `--list` → error. Ignored with chapter URL → error. |
| `-l`, `--list` | — | Needs book URL or `--book`. Conflicts with `--chapter` / `--print` → error. Prints `pos + title`. |
| `-z`, `--simplified` | `UUKANSHU_SIMPLIFIED=1` | Builds `opencc.OpenCC("t2s")` at startup; missing dict → `error: ...`. |
| `--pad N` | `UUKANSHU_PAD` (default `2`, `>=0`) | Non-number / negative → clean `error:`, not traceback. Passed to Textual padding. |
| `-t`, `--theme NAME` | `UUKANSHU_THEME` (default `night`) | Must be one of the 8 theme names; validated even when from env. In-app `t` / `T` cycles forward/backward. |
| `-p`, `--print` | — | Fetch + `extract_chapter` + optional convert + `print`. No TUI. |
| `--no-update-check` | `UUKANSHU_NO_UPDATE_CHECK=1/true/yes/on` | Disables updater. See updater contract in [RELEASING.md](RELEASING.md#updater-contract). |
| `--version` | — | Prints `uukanshu X.Y.Z` from `__version__`. |

Resolution order (`resolve_start_url`): book URL → chapter URL → `--book`.
Returns `(chapter_url, chapters)` where `chapters` seeds the reader's TOC
cache so the first `l` press doesn't refetch. See
[ARCHITECTURE.md](ARCHITECTURE.md).
