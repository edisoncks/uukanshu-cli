"""Local bookshelf: reading progress persisted as one JSON file.

One process, one writer: every change rewrites the file through a temp file
plus os.replace, so a crash mid-write can never leave a truncated shelf.
A missing or corrupt file reads as an empty shelf - progress must never
break reading. No network, no server; the file stays on this machine.
See docs/ARCHITECTURE.md.
"""

import json
import os
import sys
import time
from typing import NamedTuple


class Progress(NamedTuple):
    """Bookmarked position for one book (stable chapter id first)."""
    book_id: str
    title: str
    author: str
    chapter_pos: int
    chapter_id: int
    chapter_title: str
    chapter_url: str
    updated_at: float


def shelf_path() -> str:
    """Platform data file; UUKANSHU_DATA_DIR overrides the base directory."""
    override = os.environ.get("UUKANSHU_DATA_DIR")
    if override:
        base = override
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "uukanshu", "bookshelf.json")


def _str(value) -> str:
    return value if isinstance(value, str) else ""


def _int(value) -> int:
    return value if type(value) is int else 0


def _clean_id(value) -> str | None:
    """Normalize a book id (int, or digit string like '00123') to str."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip().isdigit():
        return str(int(value.strip()))
    return None


def relative_time(ts: float, now: float | None = None) -> str:
    """Human age for a shelf row. Simplified chrome; the UI converts it."""
    now = time.time() if now is None else now
    delta = max(0, int(now - ts))
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{delta // 60} 分钟前"
    if delta < 86400:
        return f"{delta // 3600} 小时前"
    if delta < 172800:
        return "昨天"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def resolve_chapter(chapters, progress: Progress | None) -> str | None:
    """Chapter URL to resume at.

    Stable pageId first, then TOC position, then the stored URL (which may
    still work when the TOC shifted), then chapter 1. Returns None only for
    an empty TOC. See docs/ARCHITECTURE.md.
    """
    if not chapters:
        return None
    if progress is None:
        return chapters[0].url
    if progress.chapter_id:
        hit = next((c for c in chapters if c.cid == progress.chapter_id), None)
        if hit:
            return hit.url
    if progress.chapter_pos:
        hit = next((c for c in chapters if c.pos == progress.chapter_pos), None)
        if hit:
            return hit.url
    if progress.chapter_url:
        return progress.chapter_url
    return chapters[0].url


class Shelf:
    """Read/write facade; loads once, saves atomically on every change."""

    def __init__(self, path: str | None = None):
        self.path = path or shelf_path()
        self._books: dict[str, Progress] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        books = data.get("books")
        if not isinstance(books, dict):
            return
        for key, row in books.items():
            if not isinstance(row, dict):
                continue
            bid = _clean_id(row.get("book_id") or key)
            updated = row.get("updated_at")
            # bool is an int subclass; exclude it explicitly (same rule as
            # the update-check cache).
            if bid is None or type(updated) not in (int, float):
                continue
            self._books[bid] = Progress(
                bid, _str(row.get("title")), _str(row.get("author")),
                _int(row.get("chapter_pos")), _int(row.get("chapter_id")),
                _str(row.get("chapter_title")), _str(row.get("chapter_url")),
                float(updated))

    def all(self) -> list[Progress]:
        """Newest read first."""
        return sorted(self._books.values(),
                      key=lambda p: p.updated_at, reverse=True)

    def get(self, book_id: str | int) -> Progress | None:
        key = _clean_id(book_id)
        return self._books.get(key) if key is not None else None

    def record(self, book_id: str | int, title: str = "", author: str = "",
               chapter_pos: int = 0, chapter_id: int = 0,
               chapter_title: str = "", chapter_url: str = "",
               updated_at: float | None = None) -> Progress | None:
        """Upsert progress and persist. Bad ids are ignored, never raise.

        Empty fields keep the previous value, so a bare chapter-URL start
        with no breadcrumb cannot erase the stored title.
        """
        key = _clean_id(book_id)
        if key is None:
            return None
        old = self._books.get(key)
        p = Progress(
            key,
            title or (old.title if old else ""),
            author or (old.author if old else ""),
            int(chapter_pos or (old.chapter_pos if old else 0)),
            int(chapter_id or (old.chapter_id if old else 0)),
            chapter_title or (old.chapter_title if old else ""),
            chapter_url or (old.chapter_url if old else ""),
            time.time() if updated_at is None else float(updated_at))
        self._books[key] = p
        self._save()
        return p

    def remove(self, book_id: str | int) -> bool:
        key = _clean_id(book_id)
        if key is None or key not in self._books:
            return False
        del self._books[key]
        self._save()
        return True

    def _save(self) -> None:
        """Best-effort atomic write; a read-only data dir must not crash."""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1,
                           "books": {k: p._asdict()
                                     for k, p in self._books.items()}},
                          f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass
