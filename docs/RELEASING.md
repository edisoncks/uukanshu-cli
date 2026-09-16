# Releasing

How to cut a `uukanshu` release with prebuilt binaries for Linux, macOS,
and Windows. The pipeline in [`.github/workflows/release.yml`](../.github/workflows/release.yml)
is automated — a release is just a git tag.

Reader-facing update behavior is the [Updater contract](#updater-contract)
below. Dev setup is in [DEVELOPMENT.md](DEVELOPMENT.md).

## Prerequisites

- Push access to `main` (maintainers)
- GitHub Actions enabled
- All changes merged to `main`

## Release steps

### 1. Bump the version

Version lives in one place: `__version__` at the top of
[`src/uukanshu/__init__.py`](../src/uukanshu/__init__.py) — `pyproject.toml`
reads it via hatchling, `uukanshu --version` prints it. Keep the tag in sync:

```sh
# edit __version__ in src/uukanshu/__init__.py, then:
git add src/uukanshu/__init__.py
git commit -m "chore: bump version to X.Y.Z"
```

### 2. Push the tag

```sh
git tag vX.Y.Z
git push origin main vX.Y.Z
```

The `release` workflow starts on any `v*` tag push.

### 3. Watch the build

One build job per platform (PyInstaller cannot cross-compile):

| Runner           | Asset                         |
| ---------------- | ----------------------------- |
| `ubuntu-latest`  | `uukanshu-linux-x86_64`       |
| `macos-latest`   | `uukanshu-macos-arm64`        |
| `windows-latest` | `uukanshu-windows-x86_64.exe` |

Each job: `uv sync --frozen --group build` → `pyinstaller uukanshu.spec`
→ rename to asset name → smoke test → upload artifact.

Smoke test is **real** (catches TLS-fingerprint blocks — see
[SCRAPING.md](SCRAPING.md#tls-fingerprinting)):

```sh
dist/<asset> --version
dist/<asset> --book 18957 --list > /dev/null
```

A build whose handshake is Cloudflare-blocked fails here instead of shipping.

### 4. Verify the release

The `release` job creates a GitHub Release named after the tag
(`--generate-notes`) and attaches the three binaries.

1. Open Releases, confirm all three assets attached.
2. Download each, run once (`chmod +x` on Linux/macOS).
3. Real check: `<binary> --book 18957 --list`.

## Manual / dry-run builds

Build without publishing (e.g. workflow change test):

**Actions → release → Run workflow** (or `gh workflow run release`).

Artifacts are downloadable from the run; no GitHub Release is created
because the trigger isn't a tag.

## Troubleshooting

- **Build leg failed:** open the run, check that leg's logs, fix, re-run failed jobs only. Release waits for all legs.
- **Wrong tag:** delete release + tag, fix, re-tag:

  ```sh
  gh release delete vX.Y.Z --yes --cleanup-tag
  git push origin :refs/tags/vX.Y.Z   # only if --cleanup-tag didn't remove it
  ```

## Notes on the binaries

- Bundle Python runtime + `textual` + `opencc` + OpenCC dicts — users need nothing else.
- **Not code-signed:** Gatekeeper/SmartScreen warn on first launch. Workarounds are in the [README](../README.md#troubleshooting) (one-time only). Sign/notarize only if paid accounts become worth it.

## Updater contract

Stable promise — don't break without a major version + docs update:

- **Source:** GitHub API `https://api.github.com/repos/edisoncks/uukanshu-cli/releases/latest`, JSON field `tag_name`. Never scrape release HTML (layout changes would silently break it).
- **Parsing:** strip leading `v`/`V` + whitespace, `parse_version("X.Y.Z") -> tuple[int]`. Malformed either side → `is_newer() == False` (fail silent, never nag wrongly). Zero-padded compare (`1.2` vs `1.2.0` equal).
- **Cache:** 12h TTL (`_UPDATE_TTL = 12*3600`) in `_update_cache_path()`:
  - Linux: `$XDG_CACHE_HOME/uukanshu/update.json` or `~/.cache/uukanshu/update.json`
  - macOS: `~/Library/Caches/uukanshu/update.json`
  - Windows: `%LOCALAPPDATA%/uukanshu/update.json`
  - Shape `{"latest": "X.Y.Z", "checked_at": epoch}`. Corrupt/missing → refetch. Write failures silent.
- **Rate:** ~2 req/day steady-state vs 60 req/hr/IP unauthenticated limit — never near the limit by design.
- **Behavior:** background `@work(group="update")`, never blocks chapter load, never raises into TUI. Cached fresh + newer → `有新版本 vX / new version available`. Stale → `latest_release_version(timeout=5)` via `to_thread`, save, notify only if newer. `None` (offline/rate-limit/bad JSON/oversize) → silent.
- **Opt-out:** `--no-update-check` or `UUKANSHU_NO_UPDATE_CHECK=1/true/yes/on` (case-insensitive). Flag wins for that run; env wins as default.
- **Testing:** change `tag_name` parsing or cache path only with a manual check: fresh cache miss, stale refresh, corrupt cache, opt-out set — all must not break reading.
