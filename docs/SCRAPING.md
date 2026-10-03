# Scraping

`uukanshu.cc` has no API — the app fetches plain HTML and parses it. Base:
`https://uukanshu.cc`. TOC: `/book/<ID>/`. Chapter: `/book/<ID>/<N>.html`.
All logic is in `fetch()`, `chapter_list()`, `extract_chapter()`, `link()`,
`parse_cards()`, `parse_search_page()`, `parse_book_meta()`. Anchors are
scanned once via `_iter_anchors()` so all parsers share one source;
whitespace around `href = "..."` is tolerated (legal HTML).

## Fetch

- Browser-like headers (`User-Agent` Chrome, `Accept`, `Accept-Language zh-TW`, `Upgrade-Insecure-Requests`). No headless browser needed.
- No `Accept-Encoding` sent, but CDNs may gzip anyway: supports `gzip` / `identity` only. Other encodings (`br`, `zstd`) → loud `RuntimeError`, never silent mojibake.
- 10 MB cap (`_MAX_BYTES`). Gzip path streams with incremental cap so a zip-bomb can't OOM before the length check. Truncated gzip → `EOFError` (retried); corrupt → `zlib.error` (retried).
- 3 attempts, backoff `1.5s * attempt`. Retryable: `408/429/5xx` + transport/`HTTPException`/`EOFError`/`zlib.error`. Hard `4xx` + deterministic URL errors (`InvalidURL`/`ValueError`, e.g. spaces) → fail fast. Unsupported-encoding/size errors never retry.
- TLS via `certifi` CA store (frozen binaries don't see system store reliably). See [TLS fingerprinting](#tls-fingerprinting).
- Cloudflare interstitial sniffed in `<title>` only (`Attention Required` / `Just a moment` / `you have been blocked`) → `blocked by Cloudflare — try again later or from a different network`. Title-only so novel body text never false-positives.

## TOC parsing (`chapter_list`)

- Anchors scanned via `_iter_anchors()`; href query/fragment stripped via `urlsplit`, host must be empty or `uukanshu.cc`/`www` (case-insensitive). Title inner tags (`<b>`) stripped.
- Canonical URLs `BASE + /book/<int>/<int>.html` (no query, no leading zeros).
- TOC leads with a "latest updates" block duplicating tail chapters: keeps **last** occurrence per `(int(book), int(chap))` → reading order.
- `book_id` filter compares numerically (`00123` matches `123`); non-numeric `--book` matches nothing. `None` accepts every book.
- Returns `[(pos, chap_page_id, title, url)]` with `pos` 1-based in reading order.

## Chapter parsing (`extract_chapter`)

- Title: first `<h1>`, tags stripped, entities unescaped; fallback = URL.
- Book name: breadcrumb anchor for *this* book ID (inner tags stripped); last-match fallback only. Prevents footer/recommendation links renaming the header.
- Body: `<div>` with `readcotent` as a class token (any attr order, extra classes; note source typo `readcotent`, not `readcontent`). If missing → `could not find chapter content ... (is this a chapter URL?)`.
- Cut at the `<div>` with `mulu-box` as a class token (any attr order, extra classes; nav/footer/copyright/GTM noise, case-insensitive like the `readcotent` search). Strip `<script>/<style>/<noscript>/<iframe>`, `<br>` → `\n`, tags → text, `&emsp;` dropped, blank lines collapsed, `\n\n` joined.
- Belt-and-braces: cut at last `\n上一章 章节/章節目录 下一章` row (tolerates simp/trad prefix, whitespace optional since stripped anchors may abut; no trailing guard so abutting footers still cut). Requires leading newline so in-body "上一章" mentions don't truncate.
- Nav: `link()` resolves href via `urljoin` *before* chapter-shape check (so `456.html` validates after absolutize), single canonical fullmatch on scheme/host/path. Query/fragment stripped and the canonical URL without query is returned (consistent with `chapter_list`). Host compared case-insensitively. Anchor inner tags (`<span>`) and case variations tolerated. TOC-index / `lastchapter.php` stubs → `None` = end-of-book notice, not a parse failure.

## Browse pages (cards)

- Recently updated: `GET /top/lastupdate_<page>.html`; categories:
  `GET /class_<id>_<page>.html` (ids 1–10, fixed `CATEGORIES`); search:
  `POST /search` with fields `searchkey` + `searchtype=all` (results page 1),
  later pages `GET /search/<quote(keyword)>_<page>.html`. `fetch(url, data=...)`
  sends the form body as `application/x-www-form-urlencoded`.
- 30 cards per page. The pager carries `<em id="pagestats">p/total</em>`
  (absent on pages without a pager → `parse_page_stats()` returns `None`).
- Card: `div.bookbox` → `.bookname > a` (book id; an `h4` on
  recent/category pages, a `div` on search pages — class token, not tag),
  `div.author` rows
  (`作者`/`字數`/`閱讀量`), `div.cat > a` (latest chapter, canonicalized via
  `canonical_chapter_url()`), `div.update` (`簡介`, label stripped). Class
  **tokens** are matched, never exact class strings, so extra classes and
  attribute order cannot break a parse.
- Search pages carry `共有<b …> N </b>條結果`; `0` means no results. An
  exact-title hit redirects to the full book page (`og:type=novel`,
  `og:book_id`) — parsed as one card so the UI has a single shape.
- **Search paging is upstream-unreliable**: the `_<n>.html` URLs are exactly
  what the site's own pager links to, but for some keywords `page > 1`
  returns the first page again while `pagestats` still shows the requested
  page (verified live: identical 30 card ids for several keywords, others
  page correctly). Nothing to parse around — keep the site's URL and surface
  the pager as-is.
- Book meta: `h1.booktitle`, `p.booktag` (`a.red` author, `span.blue`
  words/category, `span.red` status), `p.bookintro` (embeds an `<img>` —
  tags are stripped), `p.booktime`, `a.bookchapter`. Meta tags are read
  attribute-order-tolerantly via `_meta_map()`.

## TLS fingerprinting

Cloudflare scores the TLS ClientHello. Some Python/OpenSSL builds get 403 from residential IPs while others pass — the frozen OpenSSL differs per toolchain. Hence:

- Local and CI builds must use the same uv-managed Python 3.14 (`.python-version` + committed `uv.lock` + `mise.toml`).
- Release smoke test does a **real fetch** (`--book 18957 --list`), not just `--help`, so a blocked fingerprint fails the workflow instead of shipping.
- Escape hatch if blocking becomes persistent: switch `fetch()` to browser-TLS impersonation (e.g. `curl_cffi`) at heavier-dependency cost.

## When it breaks

| Symptom | Likely cause | Where to fix |
| ------- | ------------ | ------------ |
| `could not find chapter content` on all chapters | Site renamed `readcotent` / changed layout | `extract_chapter()` div regex |
| Chapter text includes nav/footer or cuts early | `mulu-box` renamed or nav-row wording changed | `mulu-box` split + `_nav_pat` |
| `l` shows empty / wrong book's chapters | TOC markup or recommendation block changed | `chapter_list()` anchor scan + `book_id` filter |
| `n`/`p` always says end-of-book | Prev/next labels or href shape changed | `link()` label match + canonical chapter check |
| `blocked by Cloudflare` everywhere | Cloudflare challenge tightened / TLS fingerprint blocked | Check toolchain first ([above](#tls-fingerprinting)), then consider `curl_cffi` |
| `failed to fetch ...` + `zlib.error`/`EOFError` spikes | Middlebox truncating gzip | Retry/backoff in `fetch()`; don't swallow as parse error |
| `unsupported Content-Encoding` | CDN started `br`/`zstd` | Add decoder or force `identity` — never ignore |
| Browse lists empty / missing fields | `bookbox` card markup changed | `parse_cards()` + `_BOOKBOX_RE` |
| Search total wrong or single hit not detected | `/search` count or `og:*` meta changed | `parse_search_page()` + `_meta_map()` |
| Search page 2+ repeats page 1 | Site's search backend ignores the `_<n>` suffix for some keywords | Upstream only — keep the site's own pager URL; never retry/reparse |
| Book detail header empty | `booktitle`/`booktag`/`bookintro` renamed | `parse_book_meta()` |

Keep request rate low (single fetch per navigation, 12h updater cache). If markup changed, update regexes + this table in the same commit — see [CONTRIBUTING.md](CONTRIBUTING.md).
