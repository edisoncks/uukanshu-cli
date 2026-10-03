"""Bookshelf persistence tests: atomic writes, corrupt-file tolerance,
resume identity order, relative time. No network, no real user data."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from uukanshu import Chapter
from uukanshu import shelf as sh


def _shelf(tmp_path):
    return sh.Shelf(path=str(tmp_path / "uukanshu" / "bookshelf.json"))


def test_record_and_get(tmp_path):
    s = _shelf(tmp_path)
    p = s.record("123", title="Book", author="Author", chapter_pos=3,
                 chapter_id=456, chapter_title="Ch3",
                 chapter_url="https://uukanshu.cc/book/123/456.html",
                 updated_at=1000.0)
    assert p is not None and s.get(123) == p
    data = json.loads(Path(s.path).read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["books"]["123"]["title"] == "Book"


def test_all_sorted_newest_first(tmp_path):
    s = _shelf(tmp_path)
    s.record("1", title="One", updated_at=10.0)
    s.record("2", title="Two", updated_at=30.0)
    s.record("3", title="Three", updated_at=20.0)
    assert [p.book_id for p in s.all()] == ["2", "3", "1"]


def test_upsert_keeps_previous_metadata(tmp_path):
    s = _shelf(tmp_path)
    s.record("1", title="Book", author="A", chapter_id=1, updated_at=1.0)
    p = s.record("1", chapter_id=2, updated_at=2.0)
    assert (p.title, p.author, p.chapter_id) == ("Book", "A", 2)
    assert len(s.all()) == 1


def test_remove(tmp_path):
    s = _shelf(tmp_path)
    s.record("1", title="Book", updated_at=1.0)
    assert s.remove("001") is True
    assert s.get("1") is None
    assert s.remove("1") is False


def test_corrupt_file_reads_empty_and_recovers(tmp_path):
    path = tmp_path / "uukanshu" / "bookshelf.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    s = sh.Shelf(path=str(path))
    assert s.all() == []
    s.record("9", title="Recovered", updated_at=1.0)
    assert sh.Shelf(path=str(path)).get("9").title == "Recovered"


def test_bad_rows_ignored(tmp_path):
    path = tmp_path / "bookshelf.json"
    path.write_text(json.dumps({
        "version": 1,
        "books": {
            "1": {"book_id": "1", "title": "ok", "updated_at": 5.0},
            "2": {"book_id": "2", "title": "bad", "updated_at": True},
            "3": {"book_id": "x", "title": "bad", "updated_at": 5.0},
            "4": "not-a-dict",
        },
    }), encoding="utf-8")
    s = sh.Shelf(path=str(path))
    assert [p.book_id for p in s.all()] == ["1"]


def test_atomic_write_leaves_no_tmp(tmp_path):
    s = _shelf(tmp_path)
    s.record("1", title="Book", updated_at=1.0)
    assert not Path(s.path + ".tmp").exists()
    assert Path(s.path).exists()


def test_save_failure_is_silent(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    s = sh.Shelf(path=str(blocker / "bookshelf.json"))
    p = s.record("1", title="Book", updated_at=1.0)
    assert p is not None


def test_shelf_path_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("UUKANSHU_DATA_DIR", str(tmp_path / "data"))
    assert sh.shelf_path() == str(
        tmp_path / "data" / "uukanshu" / "bookshelf.json")


def test_resolve_chapter_identity_order():
    chapters = [Chapter(1, 10, "a", "u1"), Chapter(2, 20, "b", "u2")]
    assert sh.resolve_chapter(chapters, None) == "u1"
    # Stable pageId wins over a shifted position.
    assert sh.resolve_chapter(
        chapters, sh.Progress("1", "", "", 2, 10, "", "", 0)) == "u1"
    # Position fallback when the pageId vanished.
    assert sh.resolve_chapter(
        chapters, sh.Progress("1", "", "", 2, 99, "", "", 0)) == "u2"
    # Stored URL when the TOC no longer matches.
    assert sh.resolve_chapter(
        chapters, sh.Progress("1", "", "", 0, 0, "", "u9", 0)) == "u9"
    assert sh.resolve_chapter(
        [], sh.Progress("1", "", "", 0, 0, "", "u9", 0)) is None


def test_relative_time():
    now = 1_000_000.0
    assert sh.relative_time(now - 10, now) == "刚刚"
    assert sh.relative_time(now - 120, now) == "2 分钟前"
    assert sh.relative_time(now - 7200, now) == "2 小时前"
    assert sh.relative_time(now - 90000, now) == "昨天"
    assert sh.relative_time(now - 10 * 86400, now).count("-") == 2
