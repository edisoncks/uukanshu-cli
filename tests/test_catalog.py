"""Catalogue parsing tests: cards, pagination, search pages, book meta.

Fixtures mirror the live markup (see docs/SCRAPING.md); no network is used.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from uukanshu import site


BOX1 = (
    '<div class="bookbox"><div class="p10"><span class="num">1</span>'
    '<div class="bookinfo"><h4 class="bookname">'
    '<a href="https://uukanshu.cc/book/27544/">半島：網球王子，從藝人報警開始</a></h4>'
    '<div class="author">作者：厲肥魚</div>'
    '<div class="author">字數：88843</div>'
    '<div class="author">閱讀量：0</div>'
    '<div class="cat"><span>更新到：</span>'
    '<a href="https://uukanshu.cc/book/27544/17877037.html">第23章 我把寶藏全都藏在新韓銀行杯，去拿吧</a></div>'
    '<div class="update"><span>簡介：</span>【半島+網球】多年以後，面對大滿貫。</div>'
    '</div><div class="delbutton"><a class="del_but" '
    'href="https://uukanshu.cc/book/27544/">閱讀</a></div></div></div>'
)

BOX2 = (
    '<div class="bookbox extra"><div class="bookinfo"><h4 class="bookname">'
    '<a href="/book/123/"><b>斗罗大陆</b></a></h4>'
    '<div class="author">作者：唐家三少</div>'
    '<div class="author">字数：123456</div>'
    '<div class="cat"><a href="/book/123/456.html?from=list">第456章</a></div>'
    '<div class="update"><span>简介：</span> 少年唐三 ...  </div>'
    '</div></div>'
)

BOOK_PAGE = (
    '<html><head>'
    '<meta property="og:type" content="novel" />'
    '<meta content="22532" property="og:book_id" />'
    '</head><body>'
    '<h1 class="booktitle">吞噬古帝</h1>'
    '<p class="booktag"><a class="red" '
    'href="/modules/article/authorarticle.php?author=佚名">佚名</a> '
    '<span class="blue">18148487字</span> <span class="blue">玄幻奇幻</span> '
    '<span class="red">連載</span></p>'
    '<p class="bookintro"><img class="thumbnail" src="x.jpg" alt="吞噬古帝">'
    '少年蘇辰被人奪帝骨，廢血輪。</p>'
    '<p class="booktime">更新時間：2026-10-03 09:00:46</p>'
    '<a class="bookchapter" href="/book/22532/17876772.html">'
    '第6855章 我勸你們還是不要再拼命</a>'
    '</body></html>'
)


def test_parse_cards_basic():
    cards = site.parse_cards("<html>" + BOX1 + BOX2 + "</html>")
    assert len(cards) == 2
    a = cards[0]
    assert (a.bid, a.title, a.author) == (
        27544, "半島：網球王子，從藝人報警開始", "厲肥魚")
    assert a.words == "字數：88843" and a.reads == "閱讀量：0"
    assert a.latest_title == "第23章 我把寶藏全都藏在新韓銀行杯，去拿吧"
    assert a.latest_url == "https://uukanshu.cc/book/27544/17877037.html"
    assert a.intro.startswith("【半島+網球】")
    b = cards[1]
    assert (b.bid, b.title, b.author, b.words) == (
        123, "斗罗大陆", "唐家三少", "字数：123456")
    assert b.latest_url == "https://uukanshu.cc/book/123/456.html"
    assert b.intro == "少年唐三 ..."




def test_parse_cards_div_bookname_hot_span():
    # Search results use div.bookname (recent/category use h4) and wrap the
    # matched keyword in <span class="hot">.
    page = (
        '<div class="bookbox"><div class="bookname">'
        '<a href="https://uukanshu.cc/book/26986/">'
        '<span class="hot">斗羅</span>：龍王之聖耀本體</a></div>'
        '<div class="author">作者：<a href="/author/x.html">雨下聽石</a></div>'
        '<div class="cat"><a href="/book/26986/1.html">第1章</a></div></div>'
    )
    cards = site.parse_cards(page)
    assert len(cards) == 1
    c = cards[0]
    assert (c.bid, c.title, c.author) == (
        26986, "斗羅：龍王之聖耀本體", "雨下聽石")
    assert c.latest_url == "https://uukanshu.cc/book/26986/1.html"


def test_parse_cards_skips_bad_boxes():
    page = (
        '<div class="bookbox"><h4 class="bookname">'
        '<a href="https://example.com/book/9/">Other</a></h4></div>'
        '<div class="bookbox"><h4 class="bookname">'
        '<a href="/book/7/">Seven</a></h4></div>'
        '<div class="bookbox"><div>no bookname</div></div>'
    )
    assert [c.bid for c in site.parse_cards(page)] == [7]


def test_parse_cards_missing_fields():
    cards = site.parse_cards(
        '<div class="bookbox"><h4 class="bookname">'
        '<a href="/book/7/">Seven</a></h4></div>')
    assert len(cards) == 1
    c = cards[0]
    assert c.author == "" and c.words == "" and c.reads == ""
    assert c.latest_title == "" and c.latest_url is None and c.intro == ""


def test_parse_page_stats():
    assert site.parse_page_stats('<em id="pagestats">1/900</em>') == (1, 900)
    assert site.parse_page_stats(
        '<em class="x" id="pagestats" > 12 / 124 </em>') == (12, 124)
    assert site.parse_page_stats("<html></html>") is None


def test_parse_search_page_total_and_cards():
    page = ('<h2>搜索「<b class="hottext">斗羅</b>」，共有'
            '<b class="hottext"> 200 </b>條結果</h2>' + BOX1)
    total, cards = site.parse_search_page(page)
    assert total == 200
    assert [c.bid for c in cards] == [27544]


def test_parse_search_page_zero_results():
    page = ('<h2>共有<b class="hottext"> 0 </b>條結果</h2>'
            '<span>無結果：寧可少字，不可錯字</span>')
    assert site.parse_search_page(page) == (0, [])


def test_parse_search_page_single_book():
    total, cards = site.parse_search_page(BOOK_PAGE)
    assert total == 1 and len(cards) == 1
    c = cards[0]
    assert (c.bid, c.title, c.author, c.words) == (
        22532, "吞噬古帝", "佚名", "18148487字")
    assert c.latest_url == "https://uukanshu.cc/book/22532/17876772.html"
    assert "img" not in c.intro and c.intro.startswith("少年蘇辰")


def test_parse_book_meta():
    meta = site.parse_book_meta(BOOK_PAGE, "https://uukanshu.cc/book/22532/")
    assert (meta.title, meta.author) == ("吞噬古帝", "佚名")
    assert (meta.words, meta.category, meta.status) == (
        "18148487字", "玄幻奇幻", "連載")
    assert meta.updated_at == "2026-10-03 09:00:46"
    assert meta.latest_title == "第6855章 我勸你們還是不要再拼命"
    assert meta.latest_url == "https://uukanshu.cc/book/22532/17876772.html"
    assert "<" not in meta.intro and "少年蘇辰" in meta.intro




def test_parse_book_meta_author_scoped_to_booktag():
    # A red anchor before the booktag (login/nav) must not become the author.
    page = ('<a class="red" href="/login">登录</a>'
            '<h1 class="booktitle">T</h1>'
            '<p class="booktag"><a class="red" href="/author/x">RealAuthor</a>'
            ' <span class="blue">1字</span></p>')
    meta = site.parse_book_meta(page, site.BASE)
    assert meta.author == "RealAuthor"


def test_parse_book_meta_label_fallbacks():
    page = ('<h1>Title</h1>'
            '<p class="booktag">作者：Someone <span>123字</span></p>')
    meta = site.parse_book_meta(page, "https://uukanshu.cc/book/1/")
    assert meta.author == "Someone"
    assert meta.words == "123字"
    assert meta.category == ""
    assert meta.status == ""
    assert meta.latest_url is None


def test_catalogue_urls():
    assert site.recent_url(2) == "https://uukanshu.cc/top/lastupdate_2.html"
    assert site.category_url(3, 4) == "https://uukanshu.cc/class_3_4.html"
    assert site.search_url("斗罗", 2) == (
        "https://uukanshu.cc/search/%E6%96%97%E7%BD%97_2.html")


def test_categories_catalogue():
    assert len(site.CATEGORIES) == 10
    assert site.CATEGORIES[0] == (1, "玄幻奇幻")
    assert site.CATEGORIES[-1] == (10, "其他类型")


def test_canonical_chapter_url():
    assert site.canonical_chapter_url(
        "/book/1/2.html?x=1#f", site.BASE) == (
        "https://uukanshu.cc/book/1/2.html")
    assert site.canonical_chapter_url(
        "2.html", "https://uukanshu.cc/book/1/") == (
        "https://uukanshu.cc/book/1/2.html")
    assert site.canonical_chapter_url(
        "https://other.cc/book/1/2.html", site.BASE) is None
    assert site.canonical_chapter_url("/book/1/", site.BASE) is None

SEARCH_PAGE = (
    '<h2>搜索「<b class="hottext">斗羅</b>」，共有'
    '<b class="hottext"> 200 </b>條結果</h2>'
    '<em id="pagestats">1/7</em>' + BOX1
)


def test_fetch_post_sets_form_content_type(monkeypatch):
    seen = {}

    class Resp:
        headers: dict = {}

        def read(self, n=-1):
            return b"<html>ok</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        seen["req"] = req
        return Resp()

    monkeypatch.setattr(site.urllib.request, "urlopen", fake_urlopen)
    body = site.fetch("https://uukanshu.cc/search",
                      data=b"searchkey=x&searchtype=all")
    assert body == "<html>ok</html>"
    req = seen["req"]
    assert req.get_method() == "POST"
    assert req.data == b"searchkey=x&searchtype=all"
    assert req.get_header("Content-type") == (
        "application/x-www-form-urlencoded")


def test_catalog_search_posts_then_gets(monkeypatch):
    calls = []

    def fake_fetch(url, data=None):
        calls.append((url, data))
        return SEARCH_PAGE

    monkeypatch.setattr(site, "fetch", fake_fetch)
    cat = site.Catalog()
    page = cat.search_page("斗罗")
    assert calls[0][0] == "https://uukanshu.cc/search"
    assert calls[0][1] == b"searchkey=%E6%96%97%E7%BD%97&searchtype=all"
    assert (page.page, page.pages, page.total) == (1, 7, 200)
    assert [c.bid for c in page.cards] == [27544]
    cat.search_page("斗罗", 2)
    assert calls[1] == (
        "https://uukanshu.cc/search/%E6%96%97%E7%BD%97_2.html", None)


def test_catalog_recent_and_category(monkeypatch):
    calls = []

    def fake_fetch(url, data=None):
        calls.append(url)
        return "<html>" + BOX1 + '<em id="pagestats">3/900</em></html>'

    monkeypatch.setattr(site, "fetch", fake_fetch)
    cat = site.Catalog()
    recent = cat.recent_page(3)
    assert calls[0] == "https://uukanshu.cc/top/lastupdate_3.html"
    assert (recent.page, recent.pages, recent.total) == (3, 900, None)
    assert recent.cards[0].bid == 27544
    cat.category_page(5, 2)
    assert calls[1] == "https://uukanshu.cc/class_5_2.html"


def test_catalog_pager_fallback(monkeypatch):
    monkeypatch.setattr(site, "fetch", lambda url, data=None: "<html></html>")
    page = site.Catalog().recent_page(4)
    assert page.cards == []
    assert (page.page, page.pages, page.total) == (4, 4, None)


def test_catalog_book_detail(monkeypatch):
    page_html = BOOK_PAGE + (
        '<a href="/book/22532/1.html">第一章</a>'
        '<a href="/book/22532/2.html">第二章</a>')

    def fake_fetch(url, data=None):
        assert url == "https://uukanshu.cc/book/22532/"
        return page_html

    monkeypatch.setattr(site, "fetch", fake_fetch)
    detail = site.Catalog().book_detail("22532")
    assert detail.meta.title == "吞噬古帝"
    assert {c.cid for c in detail.chapters} >= {1, 2, 17876772}

