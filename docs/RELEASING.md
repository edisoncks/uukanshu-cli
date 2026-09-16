# Releasing

How to cut a `uukanshu` release with prebuilt binaries for Linux, macOS, and
Windows. The whole pipeline is automated in
[`.github/workflows/release.yml`](.github/workflows/release.yml) — a release
is just a git tag.

## Prerequisites

- Push access to `main` (maintainers)
- GitHub Actions enabled on the repository
- All changes merged to `main` and CI-green

## Release steps

### 1. Bump the version

The version lives in one place: `__version__` at the top of
[`src/uukanshu/__init__.py`](src/uukanshu/__init__.py) — `pyproject.toml`
reads it from there (hatchling), and `uukanshu --version` prints it. Keep the
tag in sync:

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

The `release` workflow starts automatically on any `v*` tag push.

### 3. Watch the build

On the **Actions** tab, the `release` workflow runs one build job per platform
(PyInstaller cannot cross-compile, so each binary is built on its own OS):

| Runner           | Asset                         |
| ---------------- | ----------------------------- |
| `ubuntu-latest`  | `uukanshu-linux-x86_64`       |
| `macos-latest`   | `uukanshu-macos-arm64`        |
| `windows-latest` | `uukanshu-windows-x86_64.exe` |

Each job installs the project, builds `uukanshu.spec`, renames the binary to
its asset name, and runs a `--help` smoke test before uploading it as an
artifact.

### 4. Verify the release

When all builds finish, the `release` job creates a GitHub Release named after
the tag (with auto-generated notes) and attaches the three binaries.

1. Open the **Releases** page and confirm all three assets are attached.
2. Download each binary and run it once:
   - Linux/macOS: `chmod +x <binary>` then `./<binary> --help`
   - Windows: run `.\uukanshu-windows-x86_64.exe --help` in a terminal
3. For a real check, fetch something:
   `<binary> --book 18957 --list`.

## Manual / dry-run builds

To build all three binaries **without** publishing a release (e.g. to test a
workflow change), trigger the workflow manually:

**Actions → release → Run workflow** (or `gh workflow run release`).

The build jobs run and their artifacts are downloadable from the run page; no
GitHub Release is created because the trigger isn't a tag.

## Troubleshooting

**A build job failed** — open the run, check the failing matrix leg's logs,
fix, and re-run only the failed jobs from the run page (the release job waits
for all builds, so nothing is published until every leg succeeds).

**Wrong tag / need to redo the release** — delete both the release and the
tag, fix, and re-tag:

```sh
gh release delete vX.Y.Z --yes --cleanup-tag
git push origin :refs/tags/vX.Y.Z   # only if --cleanup-tag didn't remove it
```

**First-run workflow validation** — the CI matrix was validated locally only
on Linux (the spec is tested via `uukanshu.spec`; see below). Expect to check
the macOS/Windows legs closely on the very first tagged run.

## Building binaries locally

Any platform can build its own binary (must run **on** the target platform):

```sh
uv run --with pyinstaller --with . pyinstaller uukanshu.spec --noconfirm
# → dist/uukanshu (dist/uukanshu.exe on Windows)
```

## Notes on the binaries

- They bundle the Python runtime, `textual`, and `opencc` (including OpenCC's
  dictionaries) — users need nothing else installed.
- They are **not code-signed**: macOS Gatekeeper and Windows SmartScreen will
  warn on first launch. See the README's [Getting Started](README.md) for the
  end-user workarounds, or sign/notarize with paid developer accounts if that
  ever becomes worth it. Users see a one-time Gatekeeper/SmartScreen prompt;
  the README's [Troubleshooting](README.md#troubleshooting) covers the
  workarounds.
