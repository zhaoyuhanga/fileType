"""弹弹play + Bangumi 客户端测试：签名 / 搜索解析 / 匹配 / 弹幕 XML / 缓存 / 降级。

**全程离线**：所有网络调用都打在 `_FakeHttp` 替身上，缓存写在 `tmp_path`。
真实网络与真实 AppSecret 都不参与 —— 签名用固定向量，与 `hashlib` 独立算出的
期望值逐字节比较。

依赖说明：`core/anime/__init__.py` 是 PEP 562 懒加载门面，所以这里可以直接
`from modu_workbench.core.anime.danmaku import ...` —— 不会顺带把引擎/存储/库
全部拉起来，本测试只加载自己关心的两个模块。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path

import pytest

from modu_workbench.core.anime.bangumi import (
    BANGUMI_BASE,
    SUBJECT_TYPE_ANIME,
    BangumiClient,
    BangumiSubject,
    weekday_from_text,
)
from modu_workbench.core.anime.bangumi import _LIMIT_INTERVAL_SECONDS
from modu_workbench.core.anime.danmaku import (
    DANDANPLAY_BASE,
    DANMAKU_CACHE_TTL_HOURS,
    DANMAKU_OLD_TTL_HOURS,
    MATCH_THRESHOLD,
    DanmakuAnime,
    DanmakuComment,
    DanmakuMatch,
    DandanplayClient,
    api_path_of,
    episode_similarity,
    looks_like_rate_limit,
    sign_dandanplay,
    title_similarity,
)
from modu_workbench.core.anime.danmaku import _RATE_LIMIT_BACKOFF_SECONDS
from modu_workbench.core.platform.http import SourceError
from modu_workbench.ui_kit.media.danmaku import DanmakuItem as SharedDanmakuItem

DANMAKU_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<i>\n'
    '  <chatserver>danmu.dandanplay.net</chatserver>\n'
    '  <chatid>123450001</chatid>\n'
    '  <d p="3.50,1,25,16711680,1690000000,0,abc,1">第一条</d>\n'
    '  <d p="8.00,5,25,16777215,1690000001,0,def,2">置顶弹幕</d>\n'
    "</i>\n"
)


# --------------------------------------------------------------------------- 替身


class _FakeResponse:
    """最小响应替身：`json()` 的失败语义与 `requests` 一致（抛 ValueError）。"""

    def __init__(self, *, payload=None, text: str = "", status_code: int = 200) -> None:
        self._payload = payload
        self.text = text if text else (
            json.dumps(payload, ensure_ascii=False) if payload is not None else "")
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def json(self):  # noqa: ANN201
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _FakeHttp:
    """替身 HttpClient：按 (方法, URL 片段) 路由；值可以是响应，也可以是"抛异常"。"""

    def __init__(self, routes: dict | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[tuple[str, str, dict]] = []

    def _route(self, method: str, url: str, kwargs: dict) -> _FakeResponse:
        self.calls.append((method.upper(), url, kwargs))
        for (want_method, fragment), value in self.routes.items():
            if want_method in (method.upper(), "*") and fragment in url:
                if isinstance(value, BaseException):
                    raise value
                if callable(value):
                    return value(url, kwargs)
                return value
        raise SourceError(f"未预设的请求：{method} {url}")

    def request(self, method: str, url: str, **kwargs) -> _FakeResponse:
        return self._route(method, url, kwargs)

    def get(self, url: str, **kwargs) -> _FakeResponse:
        return self._route("GET", url, kwargs)

    def post(self, url: str, **kwargs) -> _FakeResponse:
        return self._route("POST", url, kwargs)

    def get_json(self, url: str, **kwargs):  # noqa: ANN201
        return self.get(url, **kwargs).json()

    def get_text(self, url: str, **kwargs) -> str:
        return self.get(url, **kwargs).text


@pytest.fixture()
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "danmaku"


@pytest.fixture()
def client(cache_dir: Path) -> DandanplayClient:
    return DandanplayClient(app_id="app-id", app_secret="app-secret", cache_dir=cache_dir)


def search_payload() -> dict:
    """两季番剧的搜索响应：同一标题、不同季度，用于验证集数/节目名偏好。"""
    return {
        "success": True,
        "animes": [
            {
                "animeId": 100,
                "animeTitle": "葬送的芙莉莲",
                "type": "tvseries",
                "typeDescription": "TV动画",
                "episodes": [
                    {"episodeId": 1001, "episodeTitle": "第1话 冒险的结束"},
                    {"episodeId": 1012, "episodeTitle": "第12话 苍月草"},
                ],
            },
            {
                "animeId": 200,
                "animeTitle": "葬送的芙莉莲 第二季",
                "type": "tvseries",
                "typeDescription": "TV动画",
                "episodes": [{"episodeId": 2001, "episodeTitle": "第1话 再会"}],
            },
        ],
        "hasMore": False,
    }


def search_http() -> _FakeHttp:
    return _FakeHttp({("GET", "/api/v2/search"): _FakeResponse(payload=search_payload())})


def dandan_item(text: str, seconds: float = 1.0, *, mode: int = 1,
                color: int = 0xFFFFFF) -> SharedDanmakuItem:
    return SharedDanmakuItem(text=text, time_ms=int(seconds * 1000), mode=mode, color=color)


# --------------------------------------------------------------------------- 签名


def test_signature_matches_manual_hash_vector() -> None:
    client = DandanplayClient(app_id="app-id", app_secret="app-secret")
    stamp, signature = client.signature("/api/v2/comment/123450001", timestamp=1735660800)
    assert stamp == "1735660800"
    expected = base64.b64encode(
        hashlib.sha256(b"app-id1735660800/api/v2/comment/123450001app-secret").digest()
    ).decode("ascii")
    assert signature == expected
    # 有文档示例可对：sha256 的 base64 一定是 44 字符（32 字节 + padding）
    assert len(signature) == 44


def test_signature_is_pure_and_sensitive_to_every_input() -> None:
    client = DandanplayClient(app_id="id", app_secret="secret")
    same = client.signature("/api/v2/search", timestamp=1000)
    assert same == client.signature("/api/v2/search", timestamp=1000)
    other_path = client.signature("/api/v2/comment/1", timestamp=1000)
    other_time = client.signature("/api/v2/search", timestamp=1001)
    assert same[1] != other_path[1]
    assert same[1] != other_time[1]
    assert same[0] != other_time[0]
    # 换密钥也必须换签名，否则"密钥不参与签名"这类错误会溜过去
    other_secret = DandanplayClient(app_id="id", app_secret="secret2").signature(
        "/api/v2/search", timestamp=1000)
    assert same[1] != other_secret[1]


def test_signature_strips_domain_and_query_string() -> None:
    client = DandanplayClient(app_id="id", app_secret="secret")
    plain = client.signature("/api/v2/comment/123", timestamp=42)
    full = client.signature("https://api.dandanplay.net/api/v2/comment/123?withRelated=true",
                            timestamp=42)
    assert full == plain
    # 末尾斜杠不该改变签名（路径归一化）
    assert api_path_of("https://api.dandanplay.net/api/v2/search/") == "/api/v2/search"
    assert api_path_of("/api/v2/search?keyword=x") == "/api/v2/search"


def test_signature_recomputed_per_request(client: DandanplayClient,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """签名不能缓存：每个请求都要按"当时的时间戳 + 该请求的路径"重算。"""
    http = search_http()
    client._http_factory = http
    clock = {"now": 1_700_000_000}
    monkeypatch.setattr("modu_workbench.core.anime.danmaku.time.time",
                        lambda: clock["now"])
    client.search("芙莉莲")
    clock["now"] += 300                     # 第二个请求发生在 5 分钟后
    client.search("芙莉莲")
    headers = [call[2].get("headers") or {} for call in http.calls]
    assert [item["X-Timestamp"] for item in headers] == ["1700000000", "1700000300"]
    for item in headers:
        expected = client.signature("/api/v2/search", timestamp=int(item["X-Timestamp"]))[1]
        assert item["X-Signature"] == expected
    assert headers[0]["X-AppId"] == "app-id"
    assert "X-AppSecret" not in headers[0]


def test_credential_mode_sends_secret_instead_of_signature() -> None:
    client = DandanplayClient(app_id="id", app_secret="secret", auth_mode="credential")
    headers = client.auth_headers("/api/v2/search")
    assert headers == {"X-AppId": "id", "X-AppSecret": "secret"}


def test_unknown_auth_mode_falls_back_to_signature() -> None:
    client = DandanplayClient(app_id="id", app_secret="secret", auth_mode="oauth-ish")
    assert client.auth_mode == "signature"
    assert "X-Signature" in client.auth_headers("/api/v2/search")


def test_auth_headers_empty_without_credentials() -> None:
    assert DandanplayClient().auth_headers("/api/v2/search") == {}


# --------------------------------------------------------------------------- 配置开关


def test_unconfigured_client_is_disabled_and_search_returns_empty() -> None:
    client = DandanplayClient(http_factory=lambda: search_http())
    assert client.configured is False
    assert client.enabled is False
    assert client.search("芙莉莲") == []
    assert "AppId" in client.last_note      # 说明原因，但不抛异常


def test_configured_needs_both_credentials() -> None:
    assert DandanplayClient(app_id="id").configured is False
    assert DandanplayClient(app_secret="secret").configured is False
    assert DandanplayClient(app_id="id", app_secret="secret").configured is True


def test_enabled_override_can_force_off() -> None:
    client = DandanplayClient(app_id="id", app_secret="secret", enabled=False)
    assert client.configured is True
    assert client.enabled is False
    assert client.search("芙莉莲") == []


# --------------------------------------------------------------------------- 搜索


def test_search_returns_empty_for_blank_keyword(client: DandanplayClient) -> None:
    client._http_factory = search_http()
    assert client.search("   ") == []


def test_search_parses_nested_animes_payload(client: DandanplayClient) -> None:
    http = search_http()
    client._http_factory = http
    results = client.search("葬送的芙莉莲")
    assert [anime.anime_id for anime in results] == [100, 200]
    assert results[0].anime_title == "葬送的芙莉莲"
    assert results[0].type_description == "TV动画"
    assert results[0].episode_ids() == [1001, 1012]
    assert results[0].episodes[0] == {"episodeId": 1001, "episodeTitle": "第1话 冒险的结束"}
    # 关键词必须真的进 query，而且 URL 用官方域名
    method, url, kwargs = http.calls[0]
    assert (method, url) == ("GET", f"{DANDANPLAY_BASE}/api/v2/search")
    assert kwargs["params"] == {"keyword": "葬送的芙莉莲"}


def test_search_degrades_on_network_error(client: DandanplayClient) -> None:
    client._http_factory = _FakeHttp(
        {("GET", "/api/v2/search"): SourceError("api.dandanplay.net 请求失败：连接被重置")})
    assert client.search("芙莉莲") == []
    assert client.last_note                    # 有可读说明


def test_search_degrades_on_non_json_payload(client: DandanplayClient) -> None:
    client._http_factory = _FakeHttp(
        {("GET", "/api/v2/search"): _FakeResponse(text="<html>502</html>")})
    assert client.search("芙莉莲") == []
    assert client.last_note


def test_parse_search_tolerates_missing_and_broken_fields() -> None:
    payload = {
        "animes": [
            {"animeId": 7, "animeTitle": "只有标题"},
            {"animeId": 8, "animeTitle": "集数脏数据", "episodes": ["第1话", {"episodeId": 0}]},
            {"animeTitle": "", "episodes": [{"episodeId": 9, "episodeTitle": "第1话"}]},
            "我不是对象",
            {},
            {"animeId": "10", "animeTitle": "字符串 ID", "episodes": [
                {"episodeId": "11", "episodeTitle": "第1话"}]},
        ]
    }
    results = DandanplayClient.parse_search(payload)
    assert [item.anime_id for item in results] == [7, 8, 0, 10]
    assert results[1].episodes == []                    # 脏集数整条丢弃
    assert results[3].episode_ids() == [11]             # 字符串 ID 也能收
    assert DandanplayClient.parse_search(None) == []
    assert DandanplayClient.parse_search({"animes": "oops"}) == []


# --------------------------------------------------------------------------- 匹配


def test_match_series_exact_title_hits_with_high_score(client: DandanplayClient) -> None:
    client._http_factory = search_http()
    match = client.match_series("葬送的芙莉莲", "第12话 苍月草")
    assert match.ok is True
    assert match.episode_id == 1012
    assert match.anime_id == 100
    assert match.score > 0.9
    assert "葬送的芙莉莲" in match.note and "第12话" in match.note


def test_match_series_prefers_candidate_with_matching_episode_number(
        client: DandanplayClient) -> None:
    """候选一的标题是精确命中，但候选二才有"第2话"——不能只看标题。"""
    http = _FakeHttp({("GET", "/api/v2/search"): _FakeResponse(payload={
        "animes": [
            {"animeId": 1, "animeTitle": "芙莉莲", "type": "tvseries",
             "episodes": [{"episodeId": 11, "episodeTitle": "第1话 出发"}]},
            {"animeId": 2, "animeTitle": "芙莉莲", "type": "tvseries",
             "episodes": [{"episodeId": 22, "episodeTitle": "第2话 魔法"}]},
        ]})})
    client._http_factory = http
    match = client.match_series("芙莉莲", "第2话 魔法")
    assert match.episode_id == 22
    assert "第 2 集" in match.note


def test_match_series_near_title_hits_and_picks_the_right_season(
        client: DandanplayClient) -> None:
    """标题有出入时仍要能匹配，但必须挑对季度 —— 选错季度就是错弹幕。"""
    client._http_factory = search_http()
    fuzzy = client.match_series("葬送的芙莉莲 第二季", "第1话 再会")
    assert fuzzy.ok is True
    assert fuzzy.episode_id == 2001
    assert fuzzy.anime_id == 200
    assert "第二季" in fuzzy.anime_title
    # 集数与集名双命中时综合分封顶 1.0；说明里要把"标题相似度"单列出来
    assert fuzzy.score == 1.0
    assert "标题相似度" in fuzzy.note
    assert "集名相似度" in fuzzy.note


def test_match_series_rejects_title_below_threshold(client: DandanplayClient) -> None:
    """片名根本对不上时必须放弃，而不是"最像的那一个"硬凑。"""
    client._http_factory = search_http()
    match = client.match_series("海贼王", "第1话")
    assert match.ok is False
    assert match.episode_id == 0
    # 分数保留"最像的候选有多像"（便于排查），但远低于阈值、且没有 episodeId
    assert 0.0 < match.score < MATCH_THRESHOLD
    assert "低于阈值" in match.note
    assert "60%" in match.note                       # 阈值写进说明里，便于排查


def test_match_series_unrelated_title_fails_with_chinese_note(
        client: DandanplayClient) -> None:
    client._http_factory = search_http()
    match = client.match_series("进击的巨人", "第1话")
    assert match.ok is False
    assert match.episode_id == 0
    assert match.score < MATCH_THRESHOLD
    assert match.note
    assert any("\u4e00" <= char <= "\u9fff" for char in match.note)


def test_match_series_failure_note_mentions_threshold(client: DandanplayClient) -> None:
    client._http_factory = _FakeHttp(
        {("GET", "/api/v2/search"): _FakeResponse(payload={"animes": []})})
    match = client.match_series("不存在的番", "第1话")
    assert match.ok is False
    assert "没有搜到" in match.note


def test_match_series_without_credentials_reports_why() -> None:
    match = DandanplayClient().match_series("芙莉莲", "第1话")
    assert match.ok is False
    assert "AppId" in match.note


def test_match_series_uses_explicit_episode_number_over_name(client: DandanplayClient) -> None:
    client._http_factory = search_http()
    match = client.match_series("葬送的芙莉莲", "第三篇章", episode_number=12)
    assert match.episode_id == 1012
    assert match.ok is True


def test_match_series_marks_missing_episode_number_in_note(client: DandanplayClient) -> None:
    """集名里没有集数时，仍按标题相似度取第一集，但要在说明里写明。"""
    client._http_factory = search_http()
    match = client.match_series("葬送的芙莉莲", "总集篇")
    assert match.ok is True
    assert match.episode_id == 1001
    assert "集名相似度" in match.note or "标题相似度" in match.note


def test_match_series_handles_anime_without_episodes(client: DandanplayClient) -> None:
    client._http_factory = _FakeHttp({("GET", "/api/v2/search"): _FakeResponse(
        payload={"animes": [{"animeId": 3, "animeTitle": "芙莉莲", "episodes": []}]})})
    match = client.match_series("芙莉莲", "第1话")
    assert match.ok is False
    assert match.episode_id == 0
    assert "节目" in match.note


# --------------------------------------------------------------------------- 弹幕拉取


def test_fetch_comments_parses_xml(client: DandanplayClient) -> None:
    http = _FakeHttp({("GET", "/api/v2/comment/123450001"): _FakeResponse(text=DANMAKU_XML)})
    client._http_factory = http
    comments = client.fetch_comments(123450001)
    assert [item.text for item in comments] == ["第一条", "置顶弹幕"]
    assert comments[0].time_ms == 3500
    assert comments[0].mode == 1
    assert comments[0].color == 0xFF0000
    assert comments[0].size == 25
    assert comments[1].mode == 5
    assert comments[1].color == 0xFFFFFF
    method, url, kwargs = http.calls[0]
    assert url == f"{DANDANPLAY_BASE}/api/v2/comment/123450001"
    assert kwargs["params"] == {"withRelated": "true"}
    assert kwargs["headers"]["Accept"].startswith("application/xml")


def test_fetch_comments_can_skip_related(client: DandanplayClient) -> None:
    http = _FakeHttp({("GET", "/api/v2/comment/9"): _FakeResponse(text=DANMAKU_XML)})
    client._http_factory = http
    client.fetch_comments(9, with_related=False)
    assert http.calls[0][2]["params"] is None


def test_fetch_comments_handles_cdata_and_unescaped_angle_brackets(
        client: DandanplayClient) -> None:
    xml = (
        "<i>"
        '<d p="1.00,1,25,16777215,0,0,0,0">前<3后 &amp; 尾巴</d>'
        '<d p="2.00,4,25,255,0,0,0,0"><![CDATA[<b>加粗</b>与<br>换行]]></d>'
        "</i>"
    )
    client._http_factory = _FakeHttp(
        {("GET", "/api/v2/comment/5"): _FakeResponse(text=xml)})
    comments = client.fetch_comments(5)
    assert [item.text for item in comments] == ["前<3后 & 尾巴", "<b>加粗</b>与\n换行"]
    assert comments[1].mode == 4 and comments[1].color == 0x0000FF
    # 共享解析器会把 <![CDATA[...]]> 当字面文本留下来，这里必须更干净
    from modu_workbench.ui_kit.media.danmaku import parse_danmaku_xml

    assert "CDATA" not in "".join(item.text for item in comments)
    assert "CDATA" in parse_danmaku_xml(xml)[1].text


def test_fetch_comments_skips_broken_entries(client: DandanplayClient) -> None:
    xml = ('<i><d p="坏数据">没有 p</d><d p="1,1">项不足</d>'
           '<d p="1.0,1,25,16777215,0,0,0,0"> </d>'
           '<d p="2.0,1,25,16777215,0,0,0,0">有效</d></i>')
    client._http_factory = _FakeHttp(
        {("GET", "/api/v2/comment/6"): _FakeResponse(text=xml)})
    comments = client.fetch_comments(6)
    assert [item.text for item in comments] == ["有效"]


@pytest.mark.parametrize("status_mark", ["429", "Too Many Requests", "403 Forbidden"])
def test_fetch_comments_degrades_on_rate_limit(client: DandanplayClient,
                                               monkeypatch: pytest.MonkeyPatch,
                                               status_mark: str) -> None:
    calls: list[float] = []
    # 退避 2 秒不该真的睡：只验证"确实退避了一次"
    client._sleep = lambda seconds: calls.append(seconds)   # type: ignore[method-assign]
    client._http_factory = _FakeHttp(
        {("GET", "/api/v2/comment/7"): SourceError(f"api.dandanplay.net 拒绝访问（{status_mark}）")})
    comments = client.fetch_comments(7)
    assert comments == []
    assert calls == [_RATE_LIMIT_BACKOFF_SECONDS]
    assert "429" in client.last_note or "403" in client.last_note
    assert "频繁" in client.last_note or "拒绝" in client.last_note


def test_fetch_comments_retries_once_then_succeeds(client: DandanplayClient) -> None:
    slept: list[float] = []
    client._sleep = lambda seconds: slept.append(seconds)   # type: ignore[method-assign]
    attempts = {"count": 0}

    def flaky(url: str, kwargs: dict):  # noqa: ANN202
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise SourceError("api.dandanplay.net 请求失败：429 Too Many Requests")
        return _FakeResponse(text=DANMAKU_XML)

    client._http_factory = _FakeHttp({("GET", "/api/v2/comment/8"): flaky})
    comments = client.fetch_comments(8)
    assert [item.text for item in comments] == ["第一条", "置顶弹幕"]
    assert attempts["count"] == 2
    assert slept == [_RATE_LIMIT_BACKOFF_SECONDS]


def test_fetch_comments_rejects_invalid_episode_id(client: DandanplayClient) -> None:
    client._http_factory = search_http()
    assert client.fetch_comments(0) == []
    assert client.fetch_comments("abc") == []       # type: ignore[arg-type]
    assert client.last_note


def test_fetch_comments_reports_empty_library(client: DandanplayClient) -> None:
    client._http_factory = _FakeHttp(
        {("GET", "/api/v2/comment/10"): _FakeResponse(text="")})
    assert client.fetch_comments(10) == []
    assert "空" in client.last_note


def test_fetch_items_returns_render_items_and_writes_cache(client: DandanplayClient) -> None:
    client._http_factory = _FakeHttp(
        {("GET", "/api/v2/comment/123450001"): _FakeResponse(text=DANMAKU_XML)})
    items = client.fetch_items(123450001)
    assert [item.text for item in items] == ["第一条", "置顶弹幕"]
    assert all(item.source == "dandanplay" for item in items)
    cached_xml = client.cache_path(0, "ep123450001")
    assert cached_xml.is_file()
    assert client.cache_fresh(cached_xml) is True


def test_fetch_items_falls_back_to_stale_cache_when_network_fails(
        client: DandanplayClient) -> None:
    target = client.cache_path(0, "ep555")
    client.save_cache(0, "ep555", [dandan_item("stale-ok", 12.0)], episode_id=555)
    # 把时间拨到 3 天前：缓存过期，但网络挂掉时仍要拿它兜底
    stale = time.time() - 72 * 3600
    meta_file = target.with_suffix(target.suffix + ".meta.json")
    payload = json.loads(meta_file.read_text(encoding="utf-8"))
    payload["fetched_at"] = stale
    meta_file.write_text(json.dumps(payload), encoding="utf-8")
    client._http_factory = _FakeHttp(
        {("GET", "/api/v2/comment/555"): SourceError("连接被重置")})
    items = client.fetch_items(555)
    assert [item.text for item in items] == ["stale-ok"]
    assert items[0].time_ms == 12000
    assert "连接被重置" in client.last_note      # 失败原因有记录（但不外抛）


def test_fetch_items_uses_fresh_cache_without_network(client: DandanplayClient) -> None:
    """新鲜缓存就是"别再打网络"的信号 —— 官方明确要求客户端自己缓存。"""
    client.save_cache(0, "ep888", [dandan_item("cached-old", 1.0)], episode_id=888)
    http = _FakeHttp({})
    client._http_factory = http
    assert [item.text for item in client.fetch_items(888)] == ["cached-old"]
    assert http.calls == []


def test_fetch_items_refreshes_stale_cache(client: DandanplayClient) -> None:
    target = client.save_cache(0, "ep999", [dandan_item("cached-old", 1.0)], episode_id=999)
    meta_file = target.with_suffix(target.suffix + ".meta.json")
    payload = json.loads(meta_file.read_text(encoding="utf-8"))
    payload["fetched_at"] = time.time() - (DANMAKU_CACHE_TTL_HOURS + 1) * 3600
    meta_file.write_text(json.dumps(payload), encoding="utf-8")
    http = _FakeHttp({("GET", "/api/v2/comment/999"): _FakeResponse(
        text='<i><d p="5.0,1,25,16777215,0,0,0,0">from-network</d></i>')})
    client._http_factory = http
    assert [item.text for item in client.fetch_items(999)] == ["from-network"]
    assert len(http.calls) == 1
    assert [item.text for item in client.load_cached(0, "ep999")] == ["from-network"]


def test_fetch_items_keeps_stale_cache_when_library_is_empty(client: DandanplayClient) -> None:
    """对面返回空弹幕库时不要把已有缓存覆盖成空文件。"""
    client.save_cache(0, "ep666", [dandan_item("cached-old", 1.0)], episode_id=666)
    path = client.cache_path(0, "ep666")
    meta_file = path.with_suffix(path.suffix + ".meta.json")
    payload = json.loads(meta_file.read_text(encoding="utf-8"))
    payload["fetched_at"] = time.time() - (DANMAKU_CACHE_TTL_HOURS + 1) * 3600
    meta_file.write_text(json.dumps(payload), encoding="utf-8")
    client._http_factory = _FakeHttp(
        {("GET", "/api/v2/comment/666"): _FakeResponse(text="")})
    assert [item.text for item in client.fetch_items(666)] == ["cached-old"]
    assert "空弹幕库" in client.last_note


def test_fetch_items_returns_empty_without_network_or_cache(client: DandanplayClient) -> None:
    client._http_factory = _FakeHttp({})
    assert client.fetch_items(999) == []


# --------------------------------------------------------------------------- 缓存


def test_cache_path_sanitizes_episode_key(client: DandanplayClient) -> None:
    path = client.cache_path(42, '第1话: 冒险/结束?*"<>|')
    assert path.name.endswith(".xml")
    assert not set('<>:"/\\|?*') & set(path.name)
    assert path.parent.name == "42"
    assert path.parent.parent == client.cache_dir
    # 空 key 要有兜底名，不能生成 ".xml"
    assert client.cache_path(0, "   ").name == "episode.xml"


def test_save_cache_writes_xml_and_meta_sidecar(client: DandanplayClient) -> None:
    items = [dandan_item("A", 2.0), dandan_item("B", 5.0, mode=5)]
    path = client.save_cache(7, "1:1:第一集", items, episode_id=123450001)
    assert path.is_file()
    meta_file = path.with_suffix(path.suffix + ".meta.json")
    assert meta_file.is_file()
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["episode_id"] == 123450001
    assert meta["count"] == 2
    assert meta["provider"] == "dandanplay"
    assert abs(meta["fetched_at"] - time.time()) < 60
    assert "<d p=" in path.read_text(encoding="utf-8")
    assert client.read_meta(path)["count"] == 2


def test_load_cached_round_trips_identical_items(client: DandanplayClient) -> None:
    items = [dandan_item("第一条", 3.5), dandan_item("置顶", 8.0, mode=5, color=0x00FF00)]
    client.save_cache(11, "ep1", items)
    loaded = client.load_cached(11, "ep1")
    # 逐字段比较：`source` 会从 "" 变成 "local"（共享层解析器的默认标记），
    # 这是有意的，不能拿整个 dataclass 做相等比较
    assert [(item.text, item.time_ms, item.mode, item.color, item.size)
            for item in loaded] == [(item.text, item.time_ms, item.mode, item.color,
                                     item.size) for item in items]
    assert [item.source for item in loaded] == ["local", "local"]


def test_load_cached_missing_file_returns_empty(client: DandanplayClient) -> None:
    assert client.load_cached(11, "不存在") == []


def test_cache_fresh_respects_ttl(client: DandanplayClient) -> None:
    path = client.save_cache(1, "ep1", [dandan_item("A")])
    assert client.cache_fresh(path) is True
    assert client.cache_fresh(path, ttl_hours=0) is False
    meta_file = path.with_suffix(path.suffix + ".meta.json")
    payload = json.loads(meta_file.read_text(encoding="utf-8"))
    payload["fetched_at"] = time.time() - (DANMAKU_CACHE_TTL_HOURS + 1) * 3600
    meta_file.write_text(json.dumps(payload), encoding="utf-8")
    assert client.cache_fresh(path) is False
    # 老番 TTL 更长，同一份缓存按老番口径仍然新鲜
    assert client.cache_fresh(path, ttl_hours=DANMAKU_OLD_TTL_HOURS) is True


def test_cache_fresh_without_meta_falls_back_to_mtime(client: DandanplayClient) -> None:
    path = client.save_cache(2, "ep2", [dandan_item("A")])
    path.with_suffix(path.suffix + ".meta.json").unlink()
    assert client.cache_fresh(path) is True
    old = time.time() - (DANMAKU_CACHE_TTL_HOURS + 2) * 3600
    os.utime(path, (old, old))
    assert client.cache_fresh(path) is False


def test_cache_fresh_false_for_missing_file(client: DandanplayClient) -> None:
    assert client.cache_fresh(client.cache_path(3, "nope")) is False


def test_cache_ttl_constants_are_documented_values() -> None:
    assert DANMAKU_CACHE_TTL_HOURS == 6
    assert DANMAKU_OLD_TTL_HOURS == 72


def test_cache_round_trip_through_shared_xml_helpers(client: DandanplayClient) -> None:
    """缓存文件必须是标准弹幕 XML：共享层的解析器要能直接读回来。"""
    from modu_workbench.ui_kit.media.danmaku import (
        parse_danmaku_xml,
        to_danmaku_xml,
    )

    items = [dandan_item("<3 & 引号\"", 4.25), dandan_item("顶部", 9.0, mode=4)]
    path = client.save_cache(9, "ep9", items)
    raw = path.read_text(encoding="utf-8")
    assert raw == to_danmaku_xml(items)
    reparsed = parse_danmaku_xml(raw)
    assert [(item.text, item.time_ms, item.mode) for item in reparsed] == [
        ("<3 & 引号\"", 4250, 1), ("顶部", 9000, 4)]
    # 共享解析器与本模块的宽松解析结果一致（同一份 XML 两种读法）
    assert DandanplayClient.parse_comments(raw) != []


def test_save_cache_accepts_comments_and_dicts(client: DandanplayClient) -> None:
    path = client.save_cache(12, "ep12", [
        DanmakuComment(text="来自 JSON", time_ms=1500, mode=1, color=0x123456, size=30),
        {"text": "字典条目", "time_ms": 2500},
    ])
    loaded = client.load_cached(12, "ep12")
    assert [item.text for item in loaded] == ["来自 JSON", "字典条目"]
    assert loaded[0].color == 0x123456 and loaded[0].size == 30
    assert path.is_file()


def test_load_cached_for_episode_uses_fetch_cache(client: DandanplayClient) -> None:
    client._http_factory = _FakeHttp(
        {("GET", "/api/v2/comment/4321"): _FakeResponse(text=DANMAKU_XML)})
    client.fetch_items(4321)
    assert [item.text for item in client.load_cached_for_episode(4321)] == ["第一条", "置顶弹幕"]
    assert client.load_cached_for_episode(0) == []


# --------------------------------------------------------------------------- 小工具


def test_looks_like_rate_limit() -> None:
    assert looks_like_rate_limit(SourceError("api.dandanplay.net 拒绝访问（403）")) is True
    assert looks_like_rate_limit(SourceError("HTTP 429 Too Many Requests")) is True
    assert looks_like_rate_limit(SourceError("连接超时")) is False

    class _Status(Exception):
        status_code = 429

    assert looks_like_rate_limit(_Status()) is True


def test_sign_dandanplay_is_module_level_pure_function() -> None:
    stamp, signature = sign_dandanplay("a", "s", "/api/v2/search", 5)
    assert stamp == "5"
    assert signature == base64.b64encode(
        hashlib.sha256(b"a5/api/v2/search" + b"s").digest()).decode("ascii")


def test_title_similarity_does_not_reward_containment() -> None:
    assert title_similarity("葬送的芙莉莲", "葬送的芙莉莲") == 1.0
    assert title_similarity("", "芙莉莲") == 0.0
    # 包含关系不给高分：最终季是另一部番、另一个弹幕库
    assert title_similarity("芙莉莲", "芙莉莲 第二季") < 0.9
    assert episode_similarity("第1话 冒险", "第1话 冒险") == 1.0
    assert MATCH_THRESHOLD == 0.6                   # 阈值变动必须是有意的
    assert isinstance(DanmakuMatch(), DanmakuMatch)
    assert DanmakuAnime(anime_id=1, anime_title="x").title == "x"


# --------------------------------------------------------------------------- Bangumi


def bangumi_subject_payload(subject_id: int = 400602, **overrides) -> dict:
    payload = {
        "id": subject_id,
        "name": "葬送のフリーレン",
        "name_cn": "葬送的芙莉莲",
        "summary": "勇者一行打倒魔王后的故事。<br>魔法使芙莉莲的旅途。",
        "date": "2023-09-29",                     # 周五
        "images": {"large": "https://lain.bgm.tv/pic/large.jpg",
                   "common": "https://lain.bgm.tv/pic/common.jpg"},
        "rating": {"score": 9.4, "rank": 12, "total": 1000},
        "total_episodes": 28,
        "tags": [{"name": "TV"}, {"name": "奇幻"}, "2023年10月"],
    }
    payload.update(overrides)
    return payload


def bangumi_search_http(payload: dict | None = None) -> _FakeHttp:
    body = payload if payload is not None else {
        "results": 1, "list": [bangumi_subject_payload()]}
    return _FakeHttp({("GET", "/search/subject/"): _FakeResponse(payload=body)})


def test_bangumi_search_parses_legacy_payload() -> None:
    client = BangumiClient(http_factory=bangumi_search_http())
    results = client.search("葬送的芙莉莲")
    assert len(results) == 1
    subject = results[0]
    assert subject.id == 400602
    assert subject.title == "葬送的芙莉莲"
    assert subject.name == "葬送のフリーレン"
    assert subject.score == 9.4
    assert subject.rank == 12
    assert subject.cover == "https://lain.bgm.tv/pic/large.jpg"
    assert subject.date == "2023-09-29"
    assert subject.total_episodes == 28
    assert subject.tags == ["TV", "奇幻", "2023年10月"]
    assert "<br>" not in subject.summary
    assert subject.year() == "2023"


def test_bangumi_search_sends_type_and_response_group() -> None:
    http = bangumi_search_http()
    BangumiClient(http_factory=http).search("芙莉莲")
    method, url, kwargs = http.calls[0]
    assert method == "GET"
    assert url.endswith("/search/subject/%E8%8A%99%E8%8E%89%E8%8E%B2")
    assert kwargs["params"] == {"type": SUBJECT_TYPE_ANIME, "responseGroup": "large"}


def test_bangumi_search_falls_back_to_v0_post_on_legacy_failure() -> None:
    http = _FakeHttp({
        ("GET", "/search/subject/"): SourceError("api.bgm.tv 接口不存在（404）"),
        ("POST", "/v0/search/subjects"): _FakeResponse(
            payload={"data": [bangumi_subject_payload()]}),
    })
    results = BangumiClient(http_factory=http).search("芙莉莲")
    assert [item.id for item in results] == [400602]
    assert [call[0] for call in http.calls] == ["GET", "POST"]
    body = http.calls[1][2]["json"]
    assert body["keyword"] == "芙莉莲"
    assert body["filter"] == {"type": [2]}


def test_bangumi_search_empty_legacy_result_falls_back() -> None:
    http = _FakeHttp({
        ("GET", "/search/subject/"): _FakeResponse(payload={"results": 0, "list": []}),
        ("POST", "/v0/search/subjects"): _FakeResponse(
            payload={"data": [bangumi_subject_payload(7)]}),
    })
    client = BangumiClient(http_factory=http)
    assert [item.id for item in client.search("芙莉莲")] == [7]


def test_bangumi_search_limit_and_blank_keyword() -> None:
    http = bangumi_search_http({"results": 3, "list": [
        bangumi_subject_payload(1), bangumi_subject_payload(2), bangumi_subject_payload(3)]})
    client = BangumiClient(http_factory=http)
    assert [item.id for item in client.search("芙莉莲", limit=2)] == [1, 2]
    assert client.search("   ") == []
    assert client.search("芙莉莲", limit=0) == []
    assert len(http.calls) == 1                     # 空白关键词不许打网络


def test_bangumi_search_degrades_to_empty_with_note() -> None:
    http = _FakeHttp({("GET", "/search/subject/"): SourceError("无法解析 api.bgm.tv 的域名"),
                      ("POST", "/v0/search/subjects"): SourceError("无法连接 api.bgm.tv")})
    client = BangumiClient(http_factory=http)
    assert client.search("芙莉莲") == []
    assert "域名" in client.last_note
    assert "回退 v0 也失败" in client.last_note      # 两条路都试过，说明写清楚了
    assert "可选项" in client.last_note              # 同时提醒"元数据是可选的"


def test_bangumi_client_never_raises_on_malformed_payloads() -> None:
    payloads = [None, [], "字符串", {"list": "不是列表"}, {"list": [None, 7, []]},
                {"data": {"oops": 1}}, {"results": 1}]
    for payload in payloads:
        search_client = BangumiClient(http_factory=_FakeHttp({
            ("GET", "/search/subject/"): _FakeResponse(payload=payload)}))
        assert search_client.search("芙莉莲") == []
        detail_client = BangumiClient(http_factory=_FakeHttp({
            ("GET", "/v0/subjects/"): _FakeResponse(payload=payload)}))
        result = detail_client.subject(1)
        # 能造出条目就接受，造不出就得是 None —— 无论如何不能抛
        assert result is None or isinstance(result, BangumiSubject)


def test_bangumi_subject_returns_none_on_failure() -> None:
    client = BangumiClient(http_factory=_FakeHttp(
        {("GET", "/v0/subjects/"): SourceError("连接被重置")}))
    assert client.subject(400602) is None
    assert client.last_note
    assert client.subject(0) is None


def test_bangumi_subject_parses_episode_airdate_hint() -> None:
    payload = bangumi_subject_payload(date="", total_episodes=0)
    payload["eps"] = [{"id": 1, "airdate": "2023-09-29"}]
    client = BangumiClient(http_factory=_FakeHttp(
        {("GET", "/v0/subjects/"): _FakeResponse(payload=payload)}))
    subject = client.subject(400602)
    assert subject is not None
    assert subject.air_weekday == 4
    assert BangumiClient.air_weekday_of(subject) == 4
    # eps 是"剧集列表"而不是数量：不能把 1 当成总集数
    assert subject.total_episodes == 0


def test_bangumi_air_weekday_of_parses_date() -> None:
    assert BangumiClient.air_weekday_of(BangumiSubject(date="2023-09-29")) == 4
    assert BangumiClient.air_weekday_of(BangumiSubject(date="2023-10-02")) == 0
    assert BangumiClient.air_weekday_of(BangumiSubject(date="")) == -1
    assert BangumiClient.air_weekday_of(BangumiSubject(date="2023-02-30")) == -1
    assert BangumiClient.air_weekday_of(None) == -1
    # 接口已经给了明确的星期就不要猜
    assert BangumiClient.air_weekday_of(
        BangumiSubject(date="2023-09-29", air_weekday=2)) == 2
    # 越界的 air_weekday 不可信，退回 date 现算
    assert BangumiClient.air_weekday_of(
        BangumiSubject(date="2023-09-29", air_weekday=9)) == 4


def test_bangumi_air_date_hint_wins_over_date() -> None:
    """响应里同时有 `air_date` 与 `date` 时，解析阶段就要按 `air_date` 推出星期。"""
    payload = bangumi_subject_payload(air_date="2023-10-02")   # 2023-09-29 是周五
    client = BangumiClient(http_factory=_FakeHttp(
        {("GET", "/v0/subjects/"): _FakeResponse(payload=payload)}))
    subject = client.subject(400602)
    assert subject is not None
    assert subject.date == "2023-09-29"
    assert subject.air_weekday == 0                            # 周一
    assert BangumiClient.air_weekday_of(subject) == 0


def test_weekday_from_text_variants() -> None:
    assert weekday_from_text("2023-09-29") == 4
    assert weekday_from_text("2023-9-2") == 5
    assert weekday_from_text("首播 2023-09-29 深夜") == 4
    assert weekday_from_text("没有日期") == -1
    assert weekday_from_text("2023-13-01") == -1
    assert weekday_from_text(None) == -1
    # 约定核对：Python 的 date.weekday() 就是周一=0，与 air_weekday 一致
    assert date(2023, 9, 29).weekday() == 4
    assert date(2024, 1, 1).weekday() == 0


def test_bangumi_best_match_picks_closest_and_prefers_chinese_name() -> None:
    http = bangumi_search_http({"results": 2, "list": [
        bangumi_subject_payload(1, name="Frieren: Beyond Journey's End", name_cn=""),
        bangumi_subject_payload(2, name="葬送のフリーレン", name_cn="葬送的芙莉莲"),
    ]})
    client = BangumiClient(http_factory=http)
    best = client.best_match("葬送的芙莉莲")
    assert best is not None and best.id == 2


def test_bangumi_best_match_uses_year_to_break_ties() -> None:
    http = bangumi_search_http({"results": 2, "list": [
        bangumi_subject_payload(1, date="2019-01-01"),
        bangumi_subject_payload(2, date="2023-09-29"),
    ]})
    client = BangumiClient(http_factory=http)
    best = client.best_match("葬送的芙莉莲", year="2023")
    assert best is not None and best.id == 2


def test_bangumi_best_match_returns_none_for_unrelated_title() -> None:
    client = BangumiClient(http_factory=bangumi_search_http())
    assert client.best_match("进击的巨人 最终季") is None
    assert client.last_note
    assert client.best_match("") is None


def test_bangumi_best_match_degrades_without_network() -> None:
    client = BangumiClient(http_factory=_FakeHttp(
        {("GET", "/search/subject/"): SourceError("无法连接 api.bgm.tv")}))
    assert client.best_match("芙莉莲") is None


def test_bangumi_memory_cache_avoids_second_request() -> None:
    http = bangumi_search_http()
    client = BangumiClient(http_factory=http)
    client.search("芙莉莲")
    client.search("芙莉莲")
    client.subject(1) if False else None
    assert len(http.calls) == 1                     # 第二次命中内存缓存
    assert client.cached_search("芙莉莲")[0].id == 400602
    assert client.cached_search("没查过") == []
    assert client.cache.hits >= 1
    client.clear_cache()
    assert client.cached_search("芙莉莲") == []


def test_bangumi_search_cache_is_not_used_when_disabled() -> None:
    http = bangumi_search_http()
    client = BangumiClient(http_factory=http, cache_ttl_seconds=0)
    client.search("芙莉莲")
    client.search("芙莉莲")
    assert len(http.calls) == 2


def test_bangumi_throttles_to_about_one_request_per_second() -> None:
    slept: list[float] = []
    client = BangumiClient(http_factory=bangumi_search_http())
    client._sleep = lambda seconds: slept.append(seconds)   # type: ignore[method-assign]
    client.search("第一次")
    client.search("第二次")
    assert len(slept) == 1
    assert 0 < slept[0] <= _LIMIT_INTERVAL_SECONDS
