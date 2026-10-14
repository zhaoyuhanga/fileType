"""播放页静态嗅探测试（`core/anime/sniffer.py`）。

全部离线：要么直接把 `html=` 交给嗅探器（此时断言**一个请求都没发**），要么用本地
`http.server` 假站点喂真实 HTML。断言的都是"命中哪条策略、地址是什么、说明里写了什么"，
不是"没抛异常"。
"""
from __future__ import annotations

import base64
import http.server
import threading

import pytest

from modu_workbench.core.anime.models import SniffedMedia
from modu_workbench.core.anime.sniffer import (
    Sniffer,
    decode_media_literal,
    extract_iframes,
    extract_player_aaaa,
    extract_player_endpoints,
    find_media_urls,
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# --------------------------------------------------------------------------- 假站点


class _Fixture:
    """本地假站点：只提供几个固定页面，用来验证"真发请求"的那几条策略。"""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = dict(pages)
        self.paths: list[str] = []
        self.headers: list[dict] = []
        self.port = 0
        self._server = None
        self._thread = None

    def __enter__(self) -> _Fixture:
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # noqa: D102  测试里不需要访问日志
                return

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                fixture.paths.append(self.path)
                fixture.headers.append({key: value for key, value in self.headers.items()})
                body = fixture.pages.get(path)
                if body is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                payload = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

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

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"


class _BoomHttp:
    """一被调用就抛 requests 风格的异常：验证网络异常不会外泄。"""

    def __init__(self) -> None:
        self.calls = 0

    def get_text(self, url, **kwargs):  # noqa: ANN001, ANN003
        self.calls += 1
        raise ConnectionError("Connection reset by peer")


# --------------------------------------------------------------------------- 解码字面量


def test_decode_media_literal_handles_js_escaping() -> None:
    assert decode_media_literal(r"https:\/\/cdn.example.com\/a\/index.m3u8") == \
        "https://cdn.example.com/a/index.m3u8"
    assert decode_media_literal(r"https:\u002F\u002Fcdn.example.com\u002Fx.mp4") == \
        "https://cdn.example.com/x.mp4"


def test_decode_media_literal_handles_base64_wrappers() -> None:
    target = "https://cdn.example.com/b/index.m3u8"
    payload = _b64(target)
    assert decode_media_literal(f"atob('{payload}')") == target
    assert decode_media_literal(f'unescape("{payload}")') == target
    assert decode_media_literal(f"decodeURIComponent('{payload}')") == target


def test_decode_media_literal_percent_decoding() -> None:
    assert decode_media_literal("https%3A%2F%2Fcdn.example.com%2Fa%2Findex.m3u8") == \
        "https://cdn.example.com/a/index.m3u8"


def test_decode_media_literal_returns_empty_for_garbage() -> None:
    # 解不出地址时必须返回空串：半截字符串拿去播放只会得到一个诡异的播放失败
    assert decode_media_literal("") == ""
    assert decode_media_literal("just some text") == ""
    assert decode_media_literal("not base64 at all !!!") == ""


# --------------------------------------------------------------------------- player_aaaa


def test_extract_player_aaaa_tolerates_js_style_object() -> None:
    text = ("<script>var player_aaaa = {'flag':'play','encrypt':0,"
            "'url':'https:\\/\\/cdn.example.com\\/a\\/index.m3u8',}</script>")
    data = extract_player_aaaa(text)
    assert data is not None
    assert data["url"] == "https://cdn.example.com/a/index.m3u8"
    assert data["flag"] == "play"


def test_extract_player_aaaa_returns_none_without_assignment() -> None:
    assert extract_player_aaaa("<html><body>没有播放器脚本</body></html>") is None
    assert extract_player_aaaa("") is None


def test_extract_player_aaaa_skips_comments() -> None:
    # `//` 也出现在 `https://` 里：注释清理必须是引号感知的，否则会把地址剪断
    text = ('<script>var player_aaaa={ // 播放地址\n "url":"https:\\/\\/cdn.example.com\\/a.m3u8"'
            ' /* 备用 */ }; </script>')
    data = extract_player_aaaa(text)
    assert data is not None
    assert data["url"] == "https://cdn.example.com/a.m3u8"


def test_sniff_player_aaaa_uses_url_field_only() -> None:
    # link_next/link_pre 是"下一集/上一集"页面地址，误用会跳到相邻集：
    # 提取器照常能读出它们，但嗅探只认 url。
    html = ('<script>var player_aaaa={"url":"https:\\/\\/cdn.example.com\\/ep1.m3u8",'
            '"link_next":"\\/play\\/9-2.html","link_pre":""}</script>')
    data = extract_player_aaaa(html)
    assert data is not None and data["link_next"] == "/play/9-2.html"
    result = Sniffer().sniff("https://site.example.com/play/9-1.html", html=html)
    assert result.ok
    assert result.strategy == "player_aaaa"
    assert result.url == "https://cdn.example.com/ep1.m3u8"


def test_sniff_player_aaaa_with_quot_entities_and_trailing_comma() -> None:
    payload = _b64("https://cdn.example.com/b/index.m3u8")
    html = ('<script>var player_aaaa = {&quot;flag&quot;:&quot;play&quot;,'
            f'&quot;encrypt&quot;:&quot;1&quot;,&quot;url&quot;:&quot;{payload}&quot;,}}</script>')
    result = Sniffer(referer="https://site.example.com/").sniff(
        "https://site.example.com/play/1", html=html)
    assert result.strategy == "player_aaaa"
    assert result.url == "https://cdn.example.com/b/index.m3u8"
    assert "base64" in result.note
    # referer 要原样带出去，下载/代理层依赖它
    assert result.referer == "https://site.example.com/"


def test_sniff_player_aaaa_ignores_non_media_url() -> None:
    # url 指向另一个播放页（不是媒体文件）时不能当成命中，要继续走后面的策略
    html = '<script>var player_aaaa={"url":"https:\\/\\/site.example.com\\/play\\/9-2.html"}</script>'
    result = Sniffer().sniff("https://site.example.com/play/9-1.html", html=html)
    assert result.strategy == "failed"


# --------------------------------------------------------------------------- 直链 / JSON


def test_sniff_returns_direct_for_media_url() -> None:
    # 直链判定不该发网络请求，所以这里只给 `html=""`（离线环境里也不该去连 CDN）
    result = Sniffer().sniff("https://cdn.example.com/a/index.m3u8", html="")
    assert result.ok
    assert result.strategy == "direct"
    assert result.url == "https://cdn.example.com/a/index.m3u8"
    assert result.referer == "https://cdn.example.com"


def test_sniff_direct_wins_over_page_content() -> None:
    # 直链判定在最前面：页面里有什么都不该影响它（也意味着零额外请求）
    result = Sniffer().sniff("https://cdn.example.com/a/index.m3u8",
                             html='<script>var player_aaaa={"url":"https://x/other.mp4"}</script>')
    assert result.strategy == "direct"
    assert result.url == "https://cdn.example.com/a/index.m3u8"


def test_sniff_json_scan_finds_nested_media() -> None:
    body = ('{"code":0,"data":{"playlist":[{"name":"第1集",'
            '"src":"https:\\/\\/cdn.example.com\\/json\\/index.m3u8"}]}}')
    result = Sniffer().sniff("https://api.example.com/detail/1", html=body)
    assert result.strategy == "json_scan"
    assert result.url == "https://cdn.example.com/json/index.m3u8"


def test_sniff_html_page_is_not_mistaken_for_json() -> None:
    # 页面里有 `{`、`[` 的普通 HTML 不能被 parse_json 啃掉：应该落到 regex 上
    html = '<html><body><img src="[a].jpg"><div>{"not":"json"}</div>' \
           '<script>player.load("https://cdn.example.com/plain/index.m3u8")</script></body></html>'
    result = Sniffer().sniff("https://site.example.com/play/1", html=html)
    assert result.strategy == "regex"
    assert result.url == "https://cdn.example.com/plain/index.m3u8"


# --------------------------------------------------------------------------- 正则兜底


def test_find_media_urls_handles_escaped_json_strings() -> None:
    text = 'var u = "https:\\/\\/cdn.example.com\\/a\\/index.m3u8?sign=1&t=2";'
    assert find_media_urls(text) == ["https://cdn.example.com/a/index.m3u8?sign=1&t=2"]


def test_find_media_urls_handles_unicode_escapes() -> None:
    text = r'{"url":"https:\u002F\u002Fcdn.example.com\u002Fx.mp4"}'
    assert find_media_urls(text) == ["https://cdn.example.com/x.mp4"]


def test_find_media_urls_decodes_atob_and_unescape() -> None:
    text = "<script>var a=atob('%s'); var b=unescape('%s');</script>" % (
        _b64("https://cdn.example.com/a/index.m3u8"),
        _b64("https://cdn.example.com/b/x.flv"),
    )
    assert find_media_urls(text) == [
        "https://cdn.example.com/a/index.m3u8",
        "https://cdn.example.com/b/x.flv",
    ]


def test_find_media_urls_deduplicates_and_keeps_order() -> None:
    text = ("a http://x/2.m3u8 b http://x/1.mp4 c http://x/2.m3u8 "
            "d http://x/1.mp4 e http://x/3.mpd")
    assert find_media_urls(text) == ["http://x/2.m3u8", "http://x/1.mp4", "http://x/3.mpd"]


def test_find_media_urls_is_empty_without_media() -> None:
    assert find_media_urls("") == []
    assert find_media_urls("<html><body>没有地址</body></html>") == []


def test_sniff_regex_reads_common_js_variables() -> None:
    for html in (
        '<script>var p={url:"https://cdn.example.com/js/index.m3u8"};</script>',
        "<script>var p={source:'https://cdn.example.com/js/index.m3u8'};</script>",
        '<video src="https://cdn.example.com/js/index.m3u8"></video>',
        '<script>var videoUrl = "https://cdn.example.com/js/index.m3u8";</script>',
    ):
        result = Sniffer().sniff("https://site.example.com/play/1", html=html)
        assert result.ok, html
        assert result.url == "https://cdn.example.com/js/index.m3u8"


# --------------------------------------------------------------------------- 播放器接口


def test_extract_player_endpoints() -> None:
    html = ('<iframe src="https://site.example.com/player/?id=123&sid=1"></iframe>'
            '<iframe src="/player/?url=abc"></iframe>'
            '<iframe src="/static/player/x.js"></iframe>')
    found = extract_player_endpoints(html)
    assert found[0].endswith("/player/?id=123&sid=1")
    assert found[1] == "/player/?url=abc"
    assert len(found) == 2


def test_sniff_player_api_requests_endpoint_with_referer() -> None:
    with _Fixture({
        "/play/1.html": '<html><iframe src="/player/?id=abc"></iframe></html>',
        "/player/": '{"url":"https:\\/\\/cdn.example.com\\/api\\/index.m3u8"}',
    }) as fixture:
        sniffer = Sniffer(referer=fixture.url("/play/1.html"))
        result = sniffer.sniff(fixture.url("/play/1.html"))
    assert result.strategy == "player_api"
    assert result.url == "https://cdn.example.com/api/index.m3u8"
    assert "/player/?id=abc" in fixture.paths
    assert fixture.headers[-1].get("Referer") == fixture.url("/play/1.html")


# --------------------------------------------------------------------------- iframe


def test_extract_iframes_handles_quotes_and_drops_javascript() -> None:
    html = ('<iframe src="//a.example.com/x"></iframe>'
            "<iframe SRC='/b.example.com/y'></iframe>"
            '<iframe src="javascript:void(0)"></iframe>'
            '<iframe src="about:blank"></iframe>'
            '<iframe src="//a.example.com/x"></iframe>')
    assert extract_iframes(html) == ["//a.example.com/x", "/b.example.com/y"]


def test_sniff_recurses_into_iframe_one_level() -> None:
    with _Fixture({
        "/play/1.html": '<html><iframe src="/frame/inner.html"></iframe></html>',
        "/frame/inner.html": '<script>var player_aaaa={"url":"https:\\/\\/cdn.example.com'
                             '\\/frame\\/index.m3u8"}</script>',
    }) as fixture:
        result = Sniffer().sniff(fixture.url("/play/1.html"))
    assert result.strategy == "iframe"
    assert result.url == "https://cdn.example.com/frame/index.m3u8"
    assert "iframe 第 1 层" in result.note
    # referer 用**外层播放页**：下载/代理层发请求时也该带这个，而不是 iframe 自己的地址
    assert result.referer == fixture.url("/play/1.html")
    assert "/frame/inner.html" in fixture.paths


def test_sniff_joins_relative_iframe_against_parent() -> None:
    with _Fixture({
        "/deep/play/1.html": '<html><iframe src="../frame/inner.html"></iframe></html>',
        "/deep/frame/inner.html": '<video src="https://cdn.example.com/rel/index.mp4"></video>',
    }) as fixture:
        result = Sniffer().sniff(fixture.url("/deep/play/1.html"))
    assert result.strategy == "iframe"
    assert result.url == "https://cdn.example.com/rel/index.mp4"
    assert "/deep/frame/inner.html" in fixture.paths


def test_iframe_recursion_respects_depth_limit() -> None:
    pages = {
        "/l0.html": '<html><iframe src="/l1.html"></iframe></html>',
        "/l1.html": '<html><iframe src="/l2.html"></iframe></html>',
        "/l2.html": '<script>var player_aaaa={"url":"https:\\/\\/cdn.example.com\\/deep.m3u8"}'
                    "</script>",
    }
    with _Fixture(pages) as fixture:
        reached = Sniffer(max_iframe_depth=2).sniff(fixture.url("/l0.html"))
    assert reached.ok, "两层的 iframe 链应该能走到"
    assert reached.strategy == "iframe"

    with _Fixture(pages) as fixture:
        blocked = Sniffer(max_iframe_depth=1).sniff(fixture.url("/l0.html"))
    assert not blocked.ok
    assert blocked.strategy == "failed"
    assert "/l2.html" not in fixture.paths, "超过层数就不该再请求下一层了"


def test_sniff_does_not_refetch_when_html_given() -> None:
    http = _BoomHttp()
    sniffer = Sniffer(http=http)
    result = sniffer.sniff("https://site.example.com/play/1",
                           html='<script>var player_aaaa={"url":"https://c/x.m3u8"}</script>')
    assert result.ok
    assert http.calls == 0, "给了 html 就不该再抓页面（试跑面板依赖这一点）"


# --------------------------------------------------------------------------- 失败与异常


def test_sniff_failure_note_is_actionable_chinese() -> None:
    result = Sniffer().sniff("https://site.example.com/play/1",
                             html="<html><body><div id='app'></div>"
                                  "<script src='/bundle.js'></script></body></html>")
    assert isinstance(result, SniffedMedia)
    assert not result.ok
    assert result.strategy == "failed"
    # 说明里要能看出"试过什么"和"下一步怎么办"，不能只有一句"解析失败"
    for keyword in ("player_aaaa", "iframe", "浏览器", "JS"):
        assert keyword in result.note, result.note


def test_sniff_network_error_never_escapes() -> None:
    http = _BoomHttp()
    result = Sniffer(http=http, referer="https://site.example.com/").sniff(
        "https://site.example.com/play/1")
    assert result.strategy == "failed"
    assert not result.ok
    assert "Connection reset by peer" in result.note
    assert "重试" in result.note


def test_sniff_player_api_failure_falls_through_to_iframe() -> None:
    # /player/ 接口 404 时要继续往下走（不能整条链断在这里）
    with _Fixture({
        "/play/1.html": ('<html><iframe src="/player/?id=abc"></iframe>'
                         '<iframe src="/frame/inner.html"></iframe></html>'),
        "/frame/inner.html": '<video src="https://cdn.example.com/last/index.m3u8"></video>',
    }) as fixture:
        result = Sniffer().sniff(fixture.url("/play/1.html"))
    assert result.strategy == "iframe"
    assert result.url == "https://cdn.example.com/last/index.m3u8"


@pytest.mark.parametrize("depth", [1, 3])
def test_sniff_failure_note_mentions_depth_for_inner_page(depth: int) -> None:
    pages = {"/l0.html": '<html><iframe src="/l1.html"></iframe></html>',
             "/l1.html": "<html><body>什么都没有</body></html>"}
    with _Fixture(pages) as fixture:
        result = Sniffer(max_iframe_depth=depth).sniff(fixture.url("/l0.html"))
    assert result.strategy == "failed"
    assert "JS" in result.note


def test_sniff_reports_site_specific_encryption() -> None:
    """真机实测：站点用私有加密（encrypt=3）时必须说清**具体原因**。

    这类站（如 2kdm.com 这种mute系）player_aaaa 存在、encrypt 是 3/4，
    明文地址由站点自己的 JS 还原 —— 我们刻意不执行页面 JS，静态嗅探必然拿不到。
    泛泛地说没找到地址会让用户以为是我们没实现，说不清楚就没法排查。
    """
    from modu_workbench.core.anime.sniffer import Sniffer, _player_block_reason

    html = '<script>var player_aaaa={"encrypt":3,"url":"mute-amHuRt0ecfHuMn"}</script>'
    assert "encrypt=3" in _player_block_reason(html)

    result = Sniffer().sniff("https://example.com/vodplay/1-1-1.html", html=html)
    assert result.strategy == "failed"
    assert "encrypt=3" in result.note, "必须指出是站点自定义加密，而不是含糊的失败"
    assert "player_aaaa" in result.note


def test_player_block_reason_distinguishes_cases() -> None:
    from modu_workbench.core.anime.sniffer import _player_block_reason

    assert "不是可识别的媒体地址" in _player_block_reason(
        '<script>var player_aaaa={"encrypt":0,"url":"https://x/a.html"}</script>')
    assert "缺少 url" in _player_block_reason(
        '<script>var player_aaaa={"encrypt":0}</script>')
    assert _player_block_reason("<html>no player here</html>") == ""
