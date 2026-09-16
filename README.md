# uukanshu

Read novels from [uukanshu.cc](https://uukanshu.cc) right in your terminal —
just the story: no ads, no clutter, no pop-ups.

- 📖 A clean, comfortable reading view that gets wider/narrower with your window
- 🧭 Jump to the next chapter, go back, or pick from the full chapter list
- 🀄 One key to switch between Traditional and Simplified Chinese
- 🎨 8 color themes (night, sepia, paper, Catppuccin, Tokyo Night, Matrix)

---

## What you need

Just your computer and internet. You don't need to install Python or
anything else — one downloaded file is the whole app.

## Quick Start

### 1. Download

Get one file from the
[Releases page](https://github.com/edisoncks/uukanshu-cli/releases/latest).
Pick the row for your computer, download that file, and **rename it** as
shown so the examples below work:

| Your computer           | Download this file            | Rename it to   |
| ----------------------- | ----------------------------- | -------------- |
| Mac (M1 / M2 / M3 / M4) | `uukanshu-macos-arm64`        | `uukanshu`     |
| Windows 64-bit          | `uukanshu-windows-x86_64.exe` | `uukanshu.exe` |
| Linux 64-bit            | `uukanshu-linux-x86_64`       | `uukanshu`     |

### 2. Run it

#### Windows

1. Open **PowerShell** (press Start, type "PowerShell", press Enter).
2. Go to your Downloads folder:

   ```powershell
   cd $env:USERPROFILE\Downloads
   ```

3. Start reading (paste any book or chapter address after the program name):

   ```powershell
   .\uukanshu.exe https://uukanshu.cc/book/18957/
   ```

4. The first time, Windows says **"Windows protected your PC"** — click
   **More info → Run anyway**. This shows once only because the app isn't
   code-signed.

#### Mac (Apple Silicon)

1. Open **Terminal** (press ⌘-Space, type "Terminal", press Enter).
2. Go to Downloads and allow the file to run:

   ```sh
   cd ~/Downloads
   chmod +x uukanshu
   ```

3. Start reading:

   ```sh
   ./uukanshu https://uukanshu.cc/book/18957/
   ```

4. If macOS says it "cannot verify the developer": open
   **System Settings → Privacy & Security**, scroll to **Security**, and
   click **Open Anyway**. Needed once only. If that doesn't help, run:
   `xattr -d com.apple.quarantine uukanshu`.

#### Linux

1. Open a terminal.
2. Go to Downloads and allow the file to run:

   ```sh
   cd ~/Downloads
   chmod +x uukanshu
   ```

3. Start reading:

   ```sh
   ./uukanshu https://uukanshu.cc/book/18957/
   ```

### 3. Pick something to read

The easiest way: open [uukanshu.cc](https://uukanshu.cc) in your browser,
find a book you like, copy its **book address** (looks like
`https://uukanshu.cc/book/18957/`), and paste it after the program name.
Reading starts at chapter 1:

```sh
./uukanshu https://uukanshu.cc/book/18957/           # Mac / Linux
.\uukanshu.exe https://uukanshu.cc/book/18957/       # Windows
```

You can also paste a **chapter address** to jump straight there, or use the
**book number** — the number in the book address (here `18957`):

```sh
./uukanshu --book 18957                 # open chapter 1
./uukanshu --book 18957 --chapter 6     # open chapter 6
./uukanshu --book 18957 --list          # show all chapter titles and exit
```

Replace `./uukanshu` with `.\uukanshu.exe` on Windows.

Browse the library at <https://uukanshu.cc/class_1_1.html> or search by
title at `https://uukanshu.cc/modules/article/search.php?q=<title>`.

---

## While reading

| Key                             | What it does                                                 |
| ------------------------------- | ------------------------------------------------------------ |
| <kbd>n</kbd> / <kbd>→</kbd>     | Next chapter                                                 |
| <kbd>p</kbd> / <kbd>←</kbd>     | Previous chapter                                             |
| <kbd>l</kbd>                    | Chapter list — <kbd>Enter</kbd> jumps, <kbd>Esc</kbd> closes |
| <kbd>d</kbd> / <kbd>u</kbd>     | Half a page down / up                                        |
| <kbd>↑</kbd> / <kbd>↓</kbd>, PgUp/PgDn, Home/End | Scroll                                         |
| <kbd>z</kbd>                    | Switch Traditional / Simplified Chinese                      |
| <kbd>t</kbd> / <kbd>T</kbd>     | Change color theme (forward / backward)                      |
| <kbd>q</kbd>                    | Quit                                                         |

---

## Options

The most-used options. For everything, run `uukanshu --help` — that list is
always correct.

```txt
uukanshu [chapter URL] [options]
```

| Option               | What it does                                                               |
| -------------------- | -------------------------------------------------------------------------- |
| `URL`                | Chapter address to open, **or** a book address to start at chapter 1       |
| `-b`, `--book <ID>`  | Open a book by its number (starts at chapter 1)                            |
| `-c`, `--chapter N`  | Which chapter to open (default: 1)                                         |
| `-l`, `--list`       | Show the chapter titles and exit                                           |
| `-z`, `--simplified` | Show Simplified Chinese instead of Traditional                             |
| `-t`, `--theme NAME` | Start with a color theme (default: `night`)                                |
| `-p`, `--print`      | Print the chapter as plain text instead of opening the reader              |

`--chapter` only works with a book address or `--book <ID>`. A chapter
address already says which chapter it is, so combining the two gives an
error.

**Themes:** `night` · `sepia` · `paper` · `catppuccin-frappe` ·
`catppuccin-macchiato` · `catppuccin-mocha` · `tokyo-night` · `matrix`.
You can also press <kbd>t</kbd> while reading to try them.

Save a chapter as a text file:

```sh
./uukanshu --book 18957 --chapter 6 -z --print > chapter6.txt
```

```powershell
.\uukanshu.exe --book 18957 --chapter 6 -z --print > chapter6.txt
```

---

## Troubleshooting

**"Windows protected your PC" / Mac "cannot verify the developer"** — the
app isn't code-signed, so the first launch warns you. On Windows: **More
info → Run anyway**. On Mac: **System Settings → Privacy & Security → Open
Anyway**. Happens once.

**"failed to fetch …"** — usually a short network hiccup; the app retries
by itself. If it keeps failing, check your connection and try again later.
If you see **"blocked by Cloudflare"**, try again later or from a different
network.

**"error: give a chapter URL or --book \<id\>"** — you need to say what to
read: paste a chapter or book address, or use `--book <ID>`.

**"could not find chapter content"** — the address must be a **chapter**
(`…/book/<ID>/<CHAPTER>.html`), not the book front page. Going past the
first or last chapter is safe: the reader says "start of book" / "end of
book" instead.

**Colors look washed out** — your terminal may not use full color. Try
setting `COLORTERM=truecolor` first (PowerShell:
`$env:COLORTERM="truecolor"`).

---

## Updating

The reader checks the Releases page on startup (at most once every 12
hours) and tells you when a newer version exists.

To update, download the newest file from the
[Releases page](https://github.com/edisoncks/uukanshu-cli/releases/latest)
and replace your old file. Nothing else to save or move.

To hide the reminder, start with `--no-update-check`.

---

## For developers

This page is for readers only. To work on the app itself, build it, or cut
a release, start at [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
