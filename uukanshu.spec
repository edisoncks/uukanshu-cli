# PyInstaller spec for uukanshu — builds a single-file executable.
#
# Build:  uv run --with pyinstaller --with . pyinstaller uukanshu.spec --noconfirm
# Output: dist/uukanshu (dist/uukanshu.exe on Windows)
#
# Notes:
#   * PyInstaller cannot cross-compile: build each platform's binary ON that
#     platform (the release workflow runs this spec on ubuntu/macos/windows).
#   * The entry script imports the package (relative imports inside
#     uukanshu/ cannot run as a top-level script).
#   * opencc ships dictionary data files and textual ships .tcss styles that
#     static analysis misses, hence the collect_all() calls.
#   * console=True keeps stdin/stdout so the TUI works in a normal terminal.

import os
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
for pkg in ("opencc", "textual"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join("src", "uukanshu_main.py")],
    pathex=[os.path.join("src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="uukanshu",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    icon="assets/uukanshu.ico",   # embedded on Windows; ignored elsewhere
)
