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


def test_extract_class_token_and_extra_attrs():
    page = ('<html><h1>T</h1><a href="/book/123/">B</a>'
            '<div class="foo readcotent bar">正文'
            '<div id="nav" class="mulu-box clearfix">foot</div></div></html>')
    _b, _t, text, *_ = u.extract_chapter(page, "https://uukanshu.cc/book/123/2.html")
    assert "正文" in text and "foot" not in text


def test_extract_strips_non_content_tags():
    page = ('<html><h1>T</h1><a href="/book/123/">B</a>'
            '<div class="readcotent">body'
            '<style>.foo{color:red}</style><noscript>nojs</noscript>'
            '<iframe src="x">fallback</iframe>'
            '<div class="mulu-box">x</div></div></html>')
    _b, _t, text, *_ = u.extract_chapter(page, "https://uukanshu.cc/book/123/2.html")
    assert text == "body"


def test_extract_abutting_footer_cut():
    inner = "bodytext<br><a>上一章</a><a>章节目录</a><a>下一章</a>footer"
    page = (f'<html><h1>第1章</h1><a href="/book/123/">書</a>'
            f'<div class="readcotent">{inner}</div></html>')
    _b, _t, text, *_ = u.extract_chapter(page, "https://uukanshu.cc/book/123/2.html")
    assert "footer" not in text and "bodytext" in text


def test_extract_breadcrumb_inner_tags():
    page = ('<html><h1>T</h1><a href="/book/123/"><b>MyBook</b></a>'
            '<div class="readcotent">text<div class="mulu-box">x</div></div></html>')
    book, *_ = u.extract_chapter(page, "https://uukanshu.cc/book/123/2.html")
    assert book == "MyBook"


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


def test_chapter_list_query_frag_canonical():
    rows = u.chapter_list('<a href="/book/123/5.html?from=toc">T</a>', "123")
    assert len(rows) == 1 and rows[0].url == \
        "https://uukanshu.cc/book/123/5.html"
    rows = u.chapter_list('<a href="/book/123/5.html#frag">T</a>', "123")
    assert len(rows) == 1 and rows[0].cid == 5


def test_chapter_list_zero_pad_dedup_canonical():
    rows = u.chapter_list(
        '<a href="/book/123/001.html">A</a><a href="/book/123/1.html">A</a>',
        "123")
    assert len(rows) == 1 and rows[0].cid == 1
    assert rows[0].url == "https://uukanshu.cc/book/123/1.html"


def test_retryable_fail_fast_url_errors():
    import http.client
    import urllib.error
    assert u._retryable(http.client.InvalidURL("bad")) is False
    assert u._retryable(ValueError("bad")) is False
    err404 = urllib.error.HTTPError("u", 404, "nf", None, None)
    err500 = urllib.error.HTTPError("u", 500, "se", None, None)
    assert u._retryable(err404) is False
    assert u._retryable(err500) is True


def test_url_smalls_chapter_id_case_double_slash():
    assert u.chapter_id("https://uukanshu.cc/BOOK/123/457.html") == 457
    assert u.book_url_from_arg("https://uukanshu.cc/book/123//") == \
        "https://uukanshu.cc/book/123/"
    assert u.parse_version("v") is None


def test_cached_latest_rejects_bool_checked_at(tmp_path, monkeypatch):
    import json
    p = tmp_path / "update.json"
    p.write_text(json.dumps({"latest": "9.9.9", "checked_at": True}),
                 encoding="utf-8")
    monkeypatch.setattr(u, "_update_cache_path", lambda: str(p))
    assert u._load_cached_latest() == (None, None)


def test_href_spaces_around_equals():
    rows = u.chapter_list('<a href = "/book/123/5.html">T</a>', "123")
    assert len(rows) == 1 and rows[0].cid == 5
    assert u.link('<a href = "/book/123/457.html">下一章</a>',
                  BASE_CH, "下一章") == \
        "https://uukanshu.cc/book/123/457.html"
    book, _t, _x, *_ = u.extract_chapter(
        '<html><h1>T</h1><a href = "/book/123/">B</a>'
        '<div class="readcotent">text<div class="mulu-box">x</div></div></html>',
        "https://uukanshu.cc/book/123/2.html")
    assert book == "B"
