"""规则执行引擎测试（`core/anime/engine.py`）。

全部离线：本地 `http.server` 提供**苹果 CMS 风格的假站点**（搜索页/详情页/播放页）
与两个 JSON 接口站点（nested / flat）。断言的是"URL 拼对了没、选择器取到的是哪几条、
每条线路几集、最终解析出哪个地址、失败原因写清楚没"。

顺带覆盖：`usePost` 表单、`@keyword` 编码、`@roadIndex`/`@episodeIndex` 模板替换、
限速不炸、单条规则失败不影响其它规则。
"""
from __future__ import annotations

import http.server
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from modu_workbench.core.anime.engine import (
    EMPTY_CHAPTERS,
    EMPTY_HITS,
    AnimeEngine,
    SearchHit,
    Throttle,
)
from modu_workbench.core.anime.models import (
    AnimeEpisode,
    AnimeRule,
    AnimeSeries,
    Road,
    SeriesDetail,
)
from modu_workbench.core.anime.sniffer import Sniffer

# --------------------------------------------------------------------------- 假站点页面

#: 苹果 CMS v10 经典结构：module-items / module-item-title / module-play-list
SEARCH_HTML = """<html><body>
<div class="module-items">
  <div class="module-item">
    <a class="module-item-title" title="葬送的芙莉莲">葬送的芙莉莲</a>
    <a class="module-item-cover" href="/detail/9.html">封面</a>
  </div>
  <div class="module-item">
    <a class="module-item-title" title="第二次的人生">第二次的人生</a>
    <a class="module-item-cover" href="/detail/10.html">封面</a>
  </div>
  <div class="module-item">
    <a class="module-item-title" title=""></a>
    <a class="module-item-cover" href="/detail/11.html">封面</a>
  </div>
</div>
</body></html>"""

DETAIL_HTML = """<html><body>
<div class="module-play-list">
  <h3 class="module-play-list-title">量子线路</h3>
  <a href="/play/9-1-1.html">第1集</a>
  <a href="/play/9-1-2.html">第2集</a>
  <a href="/play/9-1-3.html">第3集</a>
</div>
<div class="module-play-list">
  <a href="/play/9-2-1.html">第1集</a>
</div>
</body></html>"""

#: 只有一个播放列表、没有 chapterRoads 容器的详情页（走"整页当一条线路"的兜底）
DETAIL_FLAT_HTML = """<html><body>
<div class="play-list">
  <a href="/play/12-1.html">第1集</a>
  <a href="/play/12-2.html">第2集</a>
</div>
</body></html>"""

PLAY_HTML = """<html><body>
<script>var player_aaaa={"flag":"play","encrypt":0,
"url":"https:\\/\\/cdn.example.com\\/9-1-1\\/index.m3u8","link_next":"\\/play\\/9-1-2.html"}</script>
</body></html>"""

FLAT_PLAY_HTML = """<html><body>
<video src="https://cdn.example.com/flat/index.m3u8"></video>
</body></html>"""

API_SEARCH_JSON = json.dumps({
    "code": 0,
    "data": {"list": [
        {"name": "葬送的芙莉莲", "id": "api-9", "cover": "https://img.example.com/9.jpg",
         "year": "2023", "note": "更新至第28集"},
        {"name": "第二次的人生", "id": "api-10", "cover": "", "year": "2024", "note": ""},
    ]},
}, ensure_ascii=False)

API_NESTED_JSON = json.dumps({
    "data": {
        "slug": "frieren",
        "roads": [
            {"name": "线路A", "episodes": [
                {"name": "第1集", "url": "https://cdn.example.com/a/1.m3u8"},
                {"name": "第2集", "url": "https://cdn.example.com/a/2.m3u8"},
            ]},
            {"name": "线路B", "episodes": [
                {"name": "第1集", "url": "https://cdn.example.com/b/1.m3u8"},
            ]},
        ],
    },
}, ensure_ascii=False)

API_FLAT_JSON = json.dumps({
    "data": {
        "slug": "frieren",
        "episodes": [
            {"name": "第1集", "url": "/watch/page"},
            {"name": "第2集", "url": ""},
        ],
    },
}, ensure_ascii=False)

#: 剧集地址是**播放页**（不是媒体文件）的 nested 接口：走 episodePage 模板那一步
API_NESTED_PAGE_JSON = json.dumps({
    "data": {
        "slug": "frieren",
        "roads": [
            {"name": "线路A", "episodes": [{"name": "第1集", "url": "/watch/page"}]},
            {"name": "线路B", "episodes": [{"name": "第1集", "url": "/watch/page"}]},
        ],
    },
}, ensure_ascii=False)


class _Fixture:
    """本地假站点：既提供 HTML/JSON，也记录所有请求（方法/路径/查询串/表单/头）。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.port = 0
        self._server = None
        self._thread = None

    # -- 路由 --------------------------------------------------------------

    def _route(self, method: str, path: str, query: str, form: dict) -> tuple[int, bytes, str]:
        if path == "/search.html" or path == "/search/-------------.html":
            return 200, SEARCH_HTML.encode("utf-8"), "text/html"
        if path == "/detail/9.html":
            return 200, DETAIL_HTML.encode("utf-8"), "text/html"
        if path == "/detail/12.html":
            return 200, DETAIL_FLAT_HTML.encode("utf-8"), "text/html"
        if path.startswith("/play/9-1"):
            return 200, PLAY_HTML.encode("utf-8"), "text/html"
        if path == "/watch/page":
            # 剧集自带的播放页：里面没有可静态提取的地址（真实站点里靠 JS 拼接）
            return 200, b"<html><body><div id='player'></div></body></html>", "text/html"
        if path.startswith("/watch/"):
            # episodePage 模板拼出来的播放页：服务端渲染，能静态提取
            return 200, FLAT_PLAY_HTML.encode("utf-8"), "text/html"
        if path == "/media/jump.mp4":
            return 200, b"\x00\x00\x00\x18ftypmp42", "video/mp4"
        # ---- JSON 接口 ----
        if path == "/api/search":
            keyword = (form.get("q") or [""])[0]
            # POST 搜索：字段在表单体里；GET 搜索：在查询串里（parse_qs 已解码）
            if not keyword:
                keyword = parse_qs(query).get("q", [""])[0]
            if "空" in keyword:
                return 200, json.dumps({"data": {"list": []}}).encode("utf-8"), "application/json"
            return 200, API_SEARCH_JSON.encode("utf-8"), "application/json"
        if path == "/api/detail-nested/api-9":
            return 200, API_NESTED_JSON.encode("utf-8"), "application/json"
        if path == "/api/detail-flat/api-9":
            return 200, API_FLAT_JSON.encode("utf-8"), "application/json"
        if path == "/api/detail-page/api-9":
            return 200, API_NESTED_PAGE_JSON.encode("utf-8"), "application/json"
        return 404, b"", "text/plain"

    # -- 生命周期 ----------------------------------------------------------

    def __enter__(self) -> _Fixture:
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # noqa: D102
                return

            def _handle(self, method: str) -> None:
                parts = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                form = parse_qs(raw.decode("utf-8", "replace")) if raw else {}
                fixture.requests.append({
                    "method": method,
                    "path": parts.path,
                    "query": parts.query,
                    "form": form,
                    "headers": dict(self.headers.items()),
                })
                status, body, kind = fixture._route(method, parts.path, parts.query, form)
                self.send_response(status)
                self.send_header("Content-Type", f"{kind}; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # -- 断言辅助 ----------------------------------------------------------

    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.port}/{path.lstrip('/')}"

    def only(self, path: str) -> dict:
        found = [item for item in self.requests if item["path"] == path]
        assert found, f"没有请求过 {path}（实际：{[item['path'] for item in self.requests]}）"
        return found[-1]

    def paths(self) -> list[str]:
        return [item["path"] for item in self.requests]


def query_fields(request: dict) -> dict[str, list[str]]:
    return parse_qs(request["query"])


@pytest.fixture()
def site():
    with _Fixture() as fixture:
        yield fixture


# --------------------------------------------------------------------------- 规则工厂


def xpath_rule(base: str, **overrides) -> AnimeRule:
    values = dict(
        name="苹果CMS示例站",
        key="cms",
        base_url=base,
        type="anime",
        search_mode="xpath",
        chapter_mode="xpath",
        # 站点校验 Referer（真实采集站很常见）：嗅探结果应该把它带出来
        referer=base,
        qps=1000.0,                      # 测试不测限速时别真的等
        search_url=f"{base}search/-------------.html?wd=@keyword",
        search_list="//div[@class='module-item']",
        search_name="//a[@class='module-item-title']/@title",
        search_result="//a[@class='module-item-cover']/@href",
        chapter_roads="//div[@class='module-play-list']",
        chapter_result=".//a",
    )
    values.update(overrides)
    return AnimeRule(**values)


def api_rule(base: str, **overrides) -> AnimeRule:
    values = dict(
        name="API示例站",
        key="api",
        base_url=base,
        type="anime",
        search_mode="api",
        chapter_mode="api",
        qps=1000.0,
        search_api={
            "request": {"method": "GET", "url": f"{base}api/search", "query": {"q": "@keyword"}},
            "listPath": "$.data.list[*]",
            "namePath": "$.name",
            "sourcePath": "$.id",
            "coverPath": "$.cover",
            "yearPath": "$.year",
            "notePath": "$.note",
        },
        chapter_api={
            "request": {"method": "GET", "url": f"{base}api/detail-nested/@source"},
            "format": "nested",
            "roadsPath": "$.data.roads[*]",
            "roadNamePath": "$.name",
            "episodesPath": "$.episodes[*]",
            "episodeNamePath": "$.name",
            "episodeUrlPath": "$.url",
            "variables": {"slug": "$.data.slug"},
            "episodePage": {
                "url": f"{base}watch/@slug",
                "query": {"road": "@roadIndex", "ep": "@episodeIndex"},
            },
        },
    )
    values.update(overrides)
    return AnimeRule(**values)


def flat_api_rule(base: str, **overrides) -> AnimeRule:
    rule = api_rule(base, key="api-flat", name="API扁平静态站")
    rule.chapter_api = {
        "request": {"method": "GET", "url": f"{base}api/detail-flat/@source"},
        "format": "flat",
        "roadsPath": "$.data.roads[*]",
        "episodesPath": "$.data.episodes[*]",
        "episodeNamePath": "$.name",
        "episodeUrlPath": "$.url",
        "variables": {"slug": "$.data.slug"},
        "episodePage": {"url": f"{base}watch/@slug", "query": {"ep": "@episodeIndex"}},
    }
    rule.search_api = dict(api_rule(base).search_api)
    for key, value in overrides.items():
        setattr(rule, key, value)
    return rule


# --------------------------------------------------------------------------- XPath 搜索


def test_search_xpath_returns_hits_and_encodes_keyword(site: _Fixture) -> None:
    rule = xpath_rule(site.base())
    engine = AnimeEngine([rule])
    hits = engine.search("葬送的芙莉莲")
    assert [hit.title for hit in hits] == ["葬送的芙莉莲", "第二次的人生"]
    assert hits[0].remote_id == site.url("detail/9.html")
    assert hits[0].url == site.url("detail/9.html")
    assert hits[0].rule_key == "cms"
    assert hits[0].key() == f"cms:{site.url('detail/9.html')}"
    # @keyword 必须 URL 编码（中文关键词直接塞进 URL 会被站点截断）
    assert query_fields(site.only("/search/-------------.html"))["wd"] == ["葬送的芙莉莲"]


def test_search_xpath_skips_items_without_title(site: _Fixture) -> None:
    engine = AnimeEngine([xpath_rule(site.base())])
    hits = engine.search("芙莉莲")
    assert all(hit.title for hit in hits)
    assert site.url("detail/11.html") not in [hit.remote_id for hit in hits]


def test_search_xpath_limit_is_honoured(site: _Fixture) -> None:
    engine = AnimeEngine([xpath_rule(site.base())])
    assert len(engine.search("芙莉莲", limit=1)) == 1


def test_search_rule_with_empty_selector_yields_no_hits_and_note(site: _Fixture) -> None:
    rule = xpath_rule(site.base(), search_list="//div[@class='不存在的容器']")
    engine = AnimeEngine([rule])
    assert engine.search("芙莉莲") == []
    note = engine.note_for("cms")
    assert note == EMPTY_HITS
    assert "searchList" in note


def test_search_rule_reports_missing_content_as_empty_hits(site: _Fixture) -> None:
    # 接口正常返回但列表为空：也要给出可读原因，而不是一片空白
    rule = flat_api_rule(site.base())
    engine = AnimeEngine([rule])
    assert engine.search_rule(rule, "空空如也") == []
    assert engine.note_for("api-flat") == EMPTY_HITS


def test_use_post_sends_query_fields_as_form_body(site: _Fixture) -> None:
    rule = xpath_rule(site.base(), use_post=True,
                      search_url=f"{site.base()}search.html?wd=@keyword")
    engine = AnimeEngine([rule])
    hits = engine.search("芙莉莲")
    assert len(hits) == 2
    form = site.only("/search.html")
    assert form["method"] == "POST"
    assert form["form"]["wd"] == ["芙莉莲"]


def test_search_xpath_relative_href_uses_search_page_as_base(site: _Fixture) -> None:
    # href 是相对路径时要拼成绝对地址（下载/嗅探只吃绝对地址）
    engine = AnimeEngine([xpath_rule(site.base())])
    hits = engine.search("芙莉莲")
    assert all(hit.remote_id.startswith("http://127.0.0.1:") for hit in hits)


# --------------------------------------------------------------------------- XPath 详情


def test_detail_xpath_builds_roads_and_episodes(site: _Fixture) -> None:
    rule = xpath_rule(site.base())
    engine = AnimeEngine([rule])
    hit = engine.search("芙莉莲")[0]
    detail = engine.detail(rule, hit)
    assert [road.name for road in detail.roads] == ["量子线路", "线路 2"]
    assert [road.count for road in detail.roads] == [3, 1]
    assert detail.episode_count == 4
    first = detail.roads[0].episodes[0]
    assert first.name == "第1集"
    assert first.index == 0
    assert first.road_index == 0
    assert first.road_name == "量子线路"
    assert first.number == 1
    assert first.url == site.url("play/9-1-1.html")
    assert detail.road(1) is not None and detail.road(5) is None
    assert detail.series.rule_key == "cms"


def test_detail_xpath_falls_back_to_single_road(site: _Fixture) -> None:
    # chapterRoads 匹配不到时的兜底：整页当一条线路（很多单线路站就是这样）
    rule = xpath_rule(site.base(), chapter_roads="//div[@class='module-play-list']",
                      chapter_result="//div[@class='play-list']//a")
    engine = AnimeEngine([rule])
    series = AnimeSeries(rule_key="cms", remote_id=site.url("detail/12.html"), title="单线路")
    detail = engine.detail(rule, series)
    assert len(detail.roads) == 1
    assert detail.roads[0].name == "线路 1"
    assert [episode.name for episode in detail.roads[0].episodes] == ["第1集", "第2集"]


def test_detail_xpath_without_matches_sets_readable_note(site: _Fixture) -> None:
    rule = xpath_rule(site.base(), chapter_result="//a[@class='不存在']")
    engine = AnimeEngine([rule])
    series = AnimeSeries(rule_key="cms", remote_id=site.url("detail/9.html"), title="芙莉莲")
    detail = engine.detail(rule, series)
    assert detail.roads == []
    assert detail.note == EMPTY_CHAPTERS
    assert "chapterRoads" in detail.note


# --------------------------------------------------------------------------- API 搜索


def test_search_api_reads_list_and_fields(site: _Fixture) -> None:
    rule = api_rule(site.base())
    engine = AnimeEngine([rule])
    hits = engine.search("芙莉莲")
    assert [hit.title for hit in hits] == ["葬送的芙莉莲", "第二次的人生"]
    assert hits[0].remote_id == "api-9"
    assert hits[0].rule_name == "API示例站"
    assert hits[0].cover == "https://img.example.com/9.jpg"
    assert hits[0].year == "2023"
    assert hits[0].note == "更新至第28集"
    # 查询串要真的发出去；中文关键词经 requests 编码后服务端解出来仍是原文
    request = site.only("/api/search")
    assert request["method"] == "GET"
    assert query_fields(request)["q"] == ["芙莉莲"]


def test_search_api_post_body_is_sent(site: _Fixture) -> None:
    rule = api_rule(site.base())
    rule.search_api = dict(rule.search_api)
    rule.search_api["request"] = {"method": "POST", "url": f"{site.base()}api/search",
                                  "body": {"keyword": "@keyword"}}
    engine = AnimeEngine([rule])
    assert len(engine.search("芙莉莲")) == 2
    request = site.only("/api/search")
    assert request["method"] == "POST"


# --------------------------------------------------------------------------- API 详情


def test_detail_api_nested_builds_roads(site: _Fixture) -> None:
    rule = api_rule(site.base())
    engine = AnimeEngine([rule])
    hit = engine.search("芙莉莲")[0]
    detail = engine.detail(rule, hit)
    assert [road.name for road in detail.roads] == ["线路A", "线路B"]
    assert [road.count for road in detail.roads] == [2, 1]
    episode = detail.roads[0].episodes[0]
    assert episode.url == "https://cdn.example.com/a/1.m3u8"
    assert episode.number == 1
    assert episode.road_index == 0
    # @source 要替换成搜索结果的 sourcePath
    assert site.only("/api/detail-nested/api-9")["method"] == "GET"


def test_detail_api_flat_treats_response_as_single_road(site: _Fixture) -> None:
    rule = flat_api_rule(site.base())
    engine = AnimeEngine([rule])
    hit = engine.search("芙莉莲")[0]
    detail = engine.detail(rule, hit)
    assert len(detail.roads) == 1
    assert detail.roads[0].name == "线路 1"
    assert [episode.name for episode in detail.roads[0].episodes] == ["第1集", "第2集"]
    assert detail.roads[0].episodes[0].url == "/watch/page"


def test_detail_api_without_episodes_sets_note(site: _Fixture) -> None:
    rule = api_rule(site.base(), chapter_api={
        "request": {"method": "GET", "url": f"{site.base()}api/detail-nested/@source"},
        "format": "nested",
        "roadsPath": "$.data.roads[*]",
        "episodesPath": "$.episodes[*]",
        "episodeNamePath": "$.不存在字段",
        "episodeUrlPath": "$.也不存在",
    })
    engine = AnimeEngine([rule])
    series = AnimeSeries(rule_key="api", remote_id="api-9", title="芙莉莲")
    detail = engine.detail(rule, series)
    assert detail.roads == []
    assert detail.note


# --------------------------------------------------------------------------- 播放地址


def test_resolve_direct_when_episode_url_is_media(site: _Fixture) -> None:
    rule = api_rule(site.base())
    engine = AnimeEngine([rule])
    series = AnimeSeries(rule_key="api", remote_id="api-9", title="芙莉莲")
    episode = AnimeEpisode(index=0, name="第1集", url="https://cdn.example.com/a/1.m3u8")
    result = engine.resolve(rule, series, episode)
    assert result.strategy == "direct"
    assert result.url == "https://cdn.example.com/a/1.m3u8"
    # 直链不该产生任何页面请求
    assert all(item["path"].startswith("/api/") for item in site.requests)


def test_resolve_xpath_sniffs_play_page(site: _Fixture) -> None:
    rule = xpath_rule(site.base())
    engine = AnimeEngine([rule])
    hit = engine.search("芙莉莲")[0]
    detail = engine.detail(rule, hit)
    result = engine.resolve(rule, detail.series, detail.roads[0].episodes[0])
    assert result.ok
    assert result.strategy == "player_aaaa"
    assert result.url == "https://cdn.example.com/9-1-1/index.m3u8"
    # 规则的 Referer 要沿用到嗅探结果上（下载/代理层发请求时得带）
    assert result.referer == site.base()
    assert site.only("/play/9-1-1.html")["method"] == "GET"


def test_resolve_api_falls_back_to_episode_page_template(site: _Fixture) -> None:
    # 剧集地址是播放页（不是媒体文件）时，走 episodePage 模板 + 变量构造播放页再嗅探
    rule = flat_api_rule(site.base())
    engine = AnimeEngine([rule])
    hit = engine.search("芙莉莲")[0]
    detail = engine.detail(rule, hit)
    episode = detail.roads[0].episodes[0]
    result = engine.resolve(rule, detail.series, episode)
    assert result.ok
    assert result.url == "https://cdn.example.com/flat/index.m3u8"
    # 剧集地址本身嗅不出来（/watch/page 无地址），最终地址来自模板拼出的播放页
    assert "episodePage" in result.note
    request = site.only("/watch/frieren")
    assert query_fields(request) == {"ep": ["0"]}, "模板 query 要用 @episodeIndex 替换后拼上去"
    # 先嗅探剧集地址本身（/watch/page 不是媒体文件），再嗅探模板拼出来的播放页
    assert "/watch/page" in site.paths()


def test_resolve_api_episode_page_uses_road_and_episode_index(site: _Fixture) -> None:
    rule = api_rule(site.base())
    rule.chapter_api = dict(rule.chapter_api)
    rule.chapter_api["request"] = {"method": "GET", "url": f"{site.base()}api/detail-page/@source"}
    engine = AnimeEngine([rule])
    hit = engine.search("芙莉莲")[0]
    detail = engine.detail(rule, hit)
    episode = detail.roads[1].episodes[0]
    result = engine.resolve(rule, detail.series, episode)
    assert result.ok
    assert result.url == "https://cdn.example.com/flat/index.m3u8"
    # @slug 来自 chapterApiConfig.variables，@roadIndex/@episodeIndex 来自当前剧集
    request = site.only("/watch/frieren")
    assert query_fields(request) == {"road": ["1"], "ep": ["0"]}


def test_resolve_gives_up_with_actionable_note(site: _Fixture) -> None:
    rule = xpath_rule(site.base(), chapter_result="//a[@class='不存在']",
                      chapter_roads="//div[@class='module-play-list']")
    engine = AnimeEngine([rule])
    series = AnimeSeries(rule_key="cms", remote_id=site.url("detail/9.html"), title="芙莉莲")
    episode = AnimeEpisode(index=0, name="第1集", url=site.url("detail/9.html"))
    result = engine.resolve(rule, series, episode)
    assert not result.ok
    assert result.strategy == "failed"
    assert "浏览器" in result.note and "JS" in result.note


def test_resolve_without_any_url_is_explained(site: _Fixture) -> None:
    rule = xpath_rule(site.base())
    engine = AnimeEngine([rule])
    series = AnimeSeries(rule_key="cms", remote_id=site.url("detail/9.html"), title="芙莉莲")
    result = engine.resolve(rule, series, AnimeEpisode(index=0, name="第1集", url=""))
    assert not result.ok
    assert "play" not in result.note  # 不该给出误导性的地址
    assert "地址" in result.note


# --------------------------------------------------------------------------- 容错 / 限速 / 注入


def test_search_continues_after_one_rule_fails(site: _Fixture) -> None:
    good = xpath_rule(site.base(), key="good", name="能用")
    broken = xpath_rule(site.base(), key="broken", name="挂了",
                        search_url=f"{site.base()}never-exists?wd=@keyword")
    engine = AnimeEngine([broken, good])
    hits = engine.search("芙莉莲")
    assert [hit.rule_key for hit in hits] == ["good", "good"]
    assert engine.note_for("broken"), "失败规则必须留下可读原因"
    assert "404" in engine.note_for("broken") or "请求失败" in engine.note_for("broken")
    assert engine.note_for("good") == ""


def test_search_skips_unsupported_and_deprecated_rules(site: _Fixture) -> None:
    engine = AnimeEngine([
        xpath_rule(site.base(), key="old", api="9"),
        xpath_rule(site.base(), key="dead", deprecated=True),
        xpath_rule(site.base(), key="comic", type="comic"),
    ])
    assert engine.search("芙莉莲") == []
    assert "客户端" in engine.note_for("old")
    assert "退役" in engine.note_for("dead")
    assert "anime" in engine.note_for("comic")


def test_search_rule_does_not_raise_on_transport_error(site: _Fixture) -> None:
    # 连不上的端口：`search_rule` 必须返回空列表并留下可读原因，而不是把 requests 异常抛出去
    rule = xpath_rule(site.base(), search_url="http://127.0.0.1:1/search.html?wd=@keyword",
                      retries=1, timeout=1.0)
    engine = AnimeEngine([rule])
    assert engine.search_rule(rule, "芙莉莲") == []
    note = engine.note_for("cms")
    assert note and ("请求失败" in note or "无法" in note)


def test_throttle_waits_between_requests() -> None:
    # 用注入的假时钟/假 sleep：不靠真实计时，断言"第二次调用等了多少"
    now = [0.0]
    slept: list[float] = []

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    throttle = Throttle(qps=2.0, clock=clock, sleep=sleep)   # 间隔 0.5s
    assert throttle.wait() == 0.0
    now[0] += 0.1
    assert throttle.wait() == pytest.approx(0.4)
    assert slept == [pytest.approx(0.4)]


def test_search_with_high_qps_still_works(site: _Fixture) -> None:
    engine = AnimeEngine([xpath_rule(site.base())])
    for _ in range(3):
        assert engine.search("芙莉莲")
    assert len([item for item in site.requests
                if item["path"] == "/search/-------------.html"]) == 3


def test_http_factory_is_injected_and_reused() -> None:
    class FakeResponse:
        text = SEARCH_HTML

        def json(self):  # noqa: ANN201
            return json.loads(API_SEARCH_JSON)

    class FakeHttp:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_text(self, url, **kwargs):  # noqa: ANN001, ANN003
            self.calls.append(url)
            return SEARCH_HTML

        def get(self, url, **kwargs):  # noqa: ANN001, ANN003
            self.calls.append(url)
            return FakeResponse()

        def get_json(self, url, **kwargs):  # noqa: ANN001, ANN003
            self.calls.append(url)
            return json.loads(API_SEARCH_JSON)

        def post(self, url, **kwargs):  # noqa: ANN001, ANN003
            self.calls.append(url)
            return FakeResponse()

    built: list[FakeHttp] = []

    def factory(rule: AnimeRule) -> FakeHttp:
        client = FakeHttp()
        built.append(client)
        return client

    rule = xpath_rule("https://fake.example.com/")
    engine = AnimeEngine([rule], http_factory=factory)
    hits = engine.search("芙莉莲")
    assert [hit.title for hit in hits] == ["葬送的芙莉莲", "第二次的人生"]
    assert len(built) == 1, "同一条规则只应建一份客户端（构造里带 UA/超时/重试）"
    assert built[0].calls, "请求确实走了注入的客户端"
    assert engine.search("芙莉莲")


def test_detail_requires_remote_id() -> None:
    from modu_workbench.core.anime.rules import RuleError

    engine = AnimeEngine([xpath_rule("https://x.example.com/")])
    with pytest.raises(RuleError):
        engine.detail(xpath_rule("https://x.example.com/"), SearchHit(title="没有地址"))


def test_sniffer_injection_is_used(site: _Fixture) -> None:
    class RecordingSniffer:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def sniff(self, url, *, html=""):  # noqa: ANN001, ANN003
            self.urls.append(url)
            from modu_workbench.core.anime.models import SniffedMedia

            return SniffedMedia(url="https://cdn.example.com/injected.m3u8",
                                strategy="fake", referer=url)

    recorder = RecordingSniffer()
    rule = xpath_rule(site.base())
    engine = AnimeEngine([rule], sniffer=recorder)
    hit = engine.search("芙莉莲")[0]
    detail = engine.detail(rule, hit)
    result = engine.resolve(rule, detail.series, detail.roads[0].episodes[0])
    assert result.url == "https://cdn.example.com/injected.m3u8"
    assert recorder.urls == [site.url("play/9-1-1.html")]


def test_default_sniffer_uses_rule_referer_and_user_agent() -> None:
    rule = xpath_rule("https://x.example.com/", referer="https://x.example.com/",
                      user_agent="ModuTest/1.0")
    engine = AnimeEngine([rule])
    sniffer = engine._sniffer_instance(rule)
    assert isinstance(sniffer, Sniffer)
    assert sniffer.referer == "https://x.example.com/"
    assert sniffer.http.headers["User-Agent"] == "ModuTest/1.0"


# --------------------------------------------------------------------------- 与 library 共用的调用形态


class _RuleManagerStub:
    def __init__(self, rules: list[AnimeRule]) -> None:
        self.rules = list(rules)
        self.calls = 0

    def discover(self) -> list[AnimeRule]:
        self.calls += 1
        return list(self.rules)


def test_engine_can_be_built_with_rule_manager(site: _Fixture) -> None:
    # library 是这样建引擎的：AnimeEngine(rule_manager=…)（规则集可变，按需发现）
    manager = _RuleManagerStub([xpath_rule(site.base(), key="cms")])
    engine = AnimeEngine(rule_manager=manager)
    hits = engine.search("芙莉莲")
    assert [hit.title for hit in hits] == ["葬送的芙莉莲", "第二次的人生"]
    # 规则集只发现一次（结果留在引擎里，避免每次搜索都去扫规则目录）
    assert manager.calls == 1


def test_library_call_shapes_detail_and_resolve(site: _Fixture) -> None:
    # 真实的 RuleManager 每次 discover() 都会重新读文件，所以引擎必须靠 rule_key 定位，
    # 不能指望"搜索结果里那个规则对象"和"规则表里的对象"是同一个实例。
    rule = xpath_rule(site.base())
    twin = xpath_rule(site.base())          # 内容相同、**实例不同**
    engine = AnimeEngine(rule_manager=_RuleManagerStub([twin]))
    hit = engine.search_rule(rule, "芙莉莲")[0]
    # library 的形态：detail(hit) —— 不带规则，靠 rule_key 反查
    detail = engine.detail(hit)
    assert detail.series.remote_id == hit.remote_id
    assert detail.roads
    # library 的形态：resolve(detail, road, episode)
    result = engine.resolve(detail, detail.roads[0], detail.roads[0].episodes[0])
    assert result.ok
    assert result.strategy == "player_aaaa"
    assert result.url == "https://cdn.example.com/9-1-1/index.m3u8"


def test_detail_accepts_anime_series_without_rule(site: _Fixture) -> None:
    # library 的另一条路径：库里取出的 AnimeSeries 直接丢给 detail()
    rule = xpath_rule(site.base())
    engine = AnimeEngine([rule])
    series = AnimeSeries(rule_key="cms", remote_id=site.url("detail/9.html"), title="芙莉莲")
    detail = engine.detail(series)
    assert detail.roads[0].episodes[0].name == "第1集"


def test_detail_without_matching_rule_raises() -> None:
    from modu_workbench.core.anime.rules import RuleError

    engine = AnimeEngine([xpath_rule("https://x.example.com/")])
    with pytest.raises(RuleError):
        engine.detail(SearchHit(rule_key="不存在的规则", remote_id="https://x.example.com/a"))


def test_resolve_without_rule_manager_raises(site: _Fixture) -> None:
    from modu_workbench.core.anime.rules import RuleError

    engine = AnimeEngine([])      # 既没有规则，也没有 rule_manager
    detail = SeriesDetail(series=AnimeSeries(rule_key="cms", remote_id=site.url("detail/9.html")),
                          roads=[Road(index=0, name="线路 1",
                                      episodes=[AnimeEpisode(index=0, name="第1集",
                                                             url=site.url("play/9-1-1.html"))])])
    with pytest.raises(RuleError):
        engine.resolve(detail, detail.roads[0], detail.roads[0].episodes[0])
