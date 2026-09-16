"""Regression tests for URL helpers, TOC/chapter parsing, CLI resolution.

Seeds Linus review repros B1-B6. Pure functions only; network mocked.
Supports both tuple and Chapter NamedTuple rows (pos, cid, title, url).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uukanshu as u


def _row(r):
    # Chapter NamedTuple is tuple-compatible; normalize for asserts.
    return (r.pos, r.cid, r.title, r.url) if hasattr(r, "pos") else tuple(r)


def _rows(rs):
    return [_row(r) for r in rs]


# --- book_url_from_arg ---

def test_book_url_basic():
    assert u.book_url_from_arg("https://uukanshu.cc/book/123/") == \
        "https://uukanshu.cc/book/123/"


def test_book_url_index_query_frag():
    assert u.book_url_from_arg("https://uukanshu.cc/book/123/index.html") == \
        "https://uukanshu.cc/book/123/"
    assert u.book_url_from_arg("https://uukanshu.cc/book/123?utm=x#frag") == \
        "https://uukanshu.cc/book/123/"


def test_book_url_uppercase_host():
    # B3: DNS is case-insensitive
    assert u.book_url_from_arg("https://WWW.uukanshu.cc/book/123/") == \
        "https://uukanshu.cc/book/123/"
    assert u.book_url_from_arg("https://www.UUKANSHU.cc/book/123/") == \
        "https://uukanshu.cc/book/123/"


def test_book_url_chapter_is_none():
    assert u.book_url_from_arg("https://uukanshu.cc/book/123/456.html") is None


# --- link ---

BASE_CH = "https://uukanshu.cc/book/123/456.html"


def test_link_basic():
    assert u.link('<a href="/book/123/457.html">下一章</a>', BASE_CH, "下一章") == \
        "https://uukanshu.cc/book/123/457.html"


def test_link_relative():
    assert u.link('<a href="457.html">下一章</a>', BASE_CH, "下一章") == \
        "https://uukanshu.cc/book/123/457.html"


def test_link_query_frag_canonical():
    # B2: strip query/fragment for matching, return canonical
    assert u.link('<a href="/book/123/457.html?foo=1">下一章</a>', BASE_CH, "下一章") == \
        "https://uukanshu.cc/book/123/457.html"
    assert u.link('<a href="/book/123/457.html#frag">下一章</a>', BASE_CH, "下一章") == \
        "https://uukanshu.cc/book/123/457.html"


def test_link_uppercase_host():
    assert u.link('<a href="https://WWW.uukanshu.cc/book/123/457.html">下一章</a>',
                  BASE_CH, "下一章") == "https://uukanshu.cc/book/123/457.html"


def test_link_nested_and_uppercase_tag():
    # B4
    assert u.link('<a href="/book/123/457.html"><span>下一章</span></a>',
                  BASE_CH, "下一章") == "https://uukanshu.cc/book/123/457.html"
    assert u.link('<A HREF="/book/123/457.html">下一章</A>', BASE_CH, "下一章") == \
        "https://uukanshu.cc/book/123/457.html"


def test_link_rejects_non_chapter():
    assert u.link('<a href="/book/123/">目錄</a>', BASE_CH, "目[录錄]") is None
    assert u.link('<a href="/book/123/lastchapter.php">下一章</a>', BASE_CH, "下一章") is None


def test_chapter_id_agrees_with_link():
    assert u.chapter_id("https://uukanshu.cc/book/123/457.html?foo=1") == 457


# --- chapter_list ---

def test_chapter_list_basic_order():
    page = ('<a href="/book/123/1.html">第一章</a>'
            '<a href="/book/123/2.html">第二章</a>')
    rows = _rows(u.chapter_list(page, "123"))
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][1] == 1 and rows[1][2] == "第二章"


def test_chapter_list_nested_title():
    # B4: inner tags stripped, chapter kept
    rows = _rows(u.chapter_list('<a href="/book/123/5.html"><b>第5章</b></a>', "123"))
    assert len(rows) == 1 and rows[0][2] == "第5章"


def test_chapter_list_latest_updates_dup_keeps_last():
    page = ('<a href="/book/123/2.html">第二章</a>'
            '<a href="/book/123/1.html">第一章</a>'
            '<a href="/book/123/2.html">第二章</a>')
    rows = _rows(u.chapter_list(page, "123"))
    assert [r[1] for r in rows] == [1, 2]


def test_chapter_list_filters_other_book():
    page = ('<a href="/book/123/1.html">A1</a>'
            '<a href="/book/999/1.html">B1</a>')
    rows = _rows(u.chapter_list(page, "123"))
    assert len(rows) == 1 and rows[0][2] == "A1"


# --- extract_chapter ---

def _chap_page(body_inner, nav_bottom=True):
    nav = ('<a href="/book/123/1.html">上一章</a>'
           '<a href="/book/123/">章节目录</a>'
           '<a href="/book/123/3.html">下一章</a>') if nav_bottom else ''
    return (f'<html><h1>第1章 測試</h1><a href="/book/123/">測試書</a>'
            f'<div class="readcotent">{body_inner}{nav}</div></html>')


def test_extract_basic_mulu_cut():
    page = ('<html><h1>第1章</h1><a href="/book/123/">書</a>'
            '<div class="readcotent">正文<br>第二行<div class="mulu-box">footer</div></div></html>')
    _b, _t, text, _p, _c, _n = u.extract_chapter(page, "https://uukanshu.cc/book/123/2.html")
    assert "正文" in text and "footer" not in text


def test_extract_mulu_case_insensitive():
    # B5
    page = ('<html><h1>T</h1><a href="/book/123/">B</a>'
            '<div class="readcotent">正文<div CLASS="MULU-BOX">foot</div></div></html>')
    _b, _t, text, *_ = u.extract_chapter(page, "https://uukanshu.cc/book/123/2.html")
    assert "foot" not in text


def test_extract_adjacent_anchors_cut():
    # B6: anchors without whitespace must still cut footer when mulu-box absent
    inner = "正文第一行<br>正文第二行<br><a>上一章</a><a>章节目录</a><a>下一章</a><br>footer copyright"
    page = (f'<html><h1>第1章</h1><a href="/book/123/">書</a>'
            f'<div class="readcotent">{inner}</div></html>')
    _b, _t, text, *_ = u.extract_chapter(page, "https://uukanshu.cc/book/123/2.html")
    assert "footer" not in text and "正文第一行" in text


def test_extract_inbody_mention_preserved():
    page = _chap_page("有人說上一章很好笑<br>正文<br>", nav_bottom=False)
    _b, _t, text, *_ = u.extract_chapter(page, "https://uukanshu.cc/book/123/2.html")
    assert "有人說上一章很好笑" in text


# --- resolve_start_url ---

class _Args:
    def __init__(self, url=None, book=None, chapter=None):
        self.url = url
        self.book = book
        self.chapter = chapter


def test_resolve_strips_chapter_url(monkeypatch):
    # B1: surrounding whitespace must not leak into fetch URL
    monkeypatch.setattr(u, "fetch", lambda url: (_ for _ in ()).throw(AssertionError(url)))
    args = _Args(url=" https://uukanshu.cc/book/123/456.html \n")
    url, chaps = u.resolve_start_url(args)
    assert url == "https://uukanshu.cc/book/123/456.html"
    assert chaps is None


def test_resolve_rejects_bare_host():
    import pytest
    with pytest.raises(SystemExit):
        u.resolve_start_url(_Args(url="uukanshu.cc/book/123/"))


def test_book_target_shared_validation():
    import pytest
    # Whitespace-only URL counts as no URL.
    assert u._book_target("   ", "123") == (None, "123")
    with pytest.raises(SystemExit):
        u._book_target("https://uukanshu.cc/book/123/", "123")
