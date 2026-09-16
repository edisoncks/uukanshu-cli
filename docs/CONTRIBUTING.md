# Contributing

## Commits

- Atomic conventional commits: one behavior per commit (`fix:`, `feat:`, `chore:`, `docs:`). Example: `fix(fetch): catch zlib.error from corrupt gzip bodies`.
- Version bumps are isolated (`chore: bump version to X.Y.Z`) — see [RELEASING.md](RELEASING.md).
- Moves use `git mv` so history follows (e.g. root docs → `docs/`).

## Docs rule

- `README.md` is user-only (install + use). No `uv`, Python, PyInstaller, TLS, argparse internals. If a behavior changes what a reader does, update `README.md` in the **same commit**.
- Internals go in `docs/`:
  - `DEVELOPMENT.md` — setup, build, CLI reference
  - `ARCHITECTURE.md` — code map
  - `SCRAPING.md` — site contract + `When it breaks`
  - `RELEASING.md` — release runbook + updater contract
  - `CONTRIBUTING.md` — this file
- Code comments are why-only, link to `docs/`, no history retelling. Don't duplicate `--help` text in `README.md` — reference it (`uukanshu --help` is authoritative).

## Code

- Fetching stays stdlib-first (`urllib`). New scraping deps (e.g. `curl_cffi`) need discussion — see [SCRAPING.md](SCRAPING.md#tls-fingerprinting).
- Don't re-add an OpenCC whitelist post-pass (it corrupted `土著` etc. — see [ARCHITECTURE.md](ARCHITECTURE.md)).
- `main()` stays UTF-8-forcing + quiet `BrokenPipeError` handling (Windows cp1252 + `| head` crashes otherwise).
- Parser changes must update [SCRAPING.md](SCRAPING.md#when-it-breaks) in the same commit.

## Checklist before push

1. `uv run uukanshu --help` still matches `README.md` Options + `docs/DEVELOPMENT.md` Appendix.
2. Real smoke: `uv run uukanshu --book 18957 --list` and one `--print` fetch.
3. Links resolve (`../README.md`, `docs/*.md`, workflow path). Markdownlint clean (line-length off, inline HTML allowed).
4. If you touched `fetch`/`chapter_list`/`extract_chapter`/`link`/updater: updated the relevant `docs/` table/contract.
