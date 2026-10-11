"""规则源包装测试（`core/anime/providers.py`）。

离线：假引擎/假嗅探器注入，断言的是"映射对不对、失败怎么说清楚"——
规则源存在的意义就是让 `core/video` 的播放器/下载器能直接吃动漫站的内容，
所以这里重点验证字段映射与 `play_url()` 是否给的是**真实媒体地址**。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.anime.engine import SearchHit
from modu_workbench.core.anime.models import (
    AnimeEpisode,
    AnimeRule,
    AnimeSeries,
    Road,
    SeriesDetail,
    SniffedMedia,
)
from modu_workbench.core.anime.providers import (
    RuleProvider,
    RuleRegistry,
    provider_key,
    series_remote_id,
)
from modu_workbench.core.video.sources.http import SourceError

# --------------------------------------------------------------------------- 假引擎


class _FakeEngine:
    """只实现 `RuleProvider` 用到的那几个方法，记录调用参数。"""

    def __init__(self, *, hits=None, roads=None, media=None, note="") -> None:
        # 默认给一条命中：这里多数用例只关心"详情→剧集→播放地址"的映射，
        # 不想每条都写一遍搜索结果（真正测搜索的用例会显式传 hits）
        self.hits = list(hits) if hits is not None else [SearchHit(
            rule_key="cms", rule_name="苹果CMS示例站", title="葬送的芙莉莲",
            remote_id="https://site.example.com/detail/9.html",
            url="https://site.example.com/detail/9.html")]
        self.roads = list(roads or [])
        self.media = media if media is not None else SniffedMedia(
            url="https://cdn.example.com/1/index.m3u8", strategy="player_aaaa",
            referer="https://site.example.com/")
        self.note = note
        self.calls: list[tuple] = []
        self.rules: list[AnimeRule] = []

    def search_rule(self, rule, keyword, *, limit=30):
        self.calls.append(("search", rule.key, keyword, limit))
        return list(self.hits)[:limit]

    def note_for(self, rule_key):
        return self.note

    def detail(self, rule, hit_or_series=None):
        series = hit_or_series if isinstance(hit_or_series, AnimeSeries) else hit_or_series
        self.calls.append(("detail", series.remote_id if series else ""))
        return SeriesDetail(series=series, roads=list(self.roads))

    def resolve(self, rule, series=None, episode=None):
        self.calls.append(("resolve", episode.url if episode else ""))
        return self.media


def _rule(**overrides) -> AnimeRule:
    values = dict(key="cms", name="苹果CMS示例站", base_url="https://site.example.com/",
                  referer="https://site.example.com/", user_agent="ModuTest/1.0",
                  search_url="https://site.example.com/search?wd=@keyword",
                  search_list="//div[@class='item']",
                  search_name="//a/@title", search_result="//a/@href",
                  chapter_roads="//div[@class='play-list']", chapter_result=".//a")
    values.update(overrides)
    return AnimeRule(**values)


def _roads() -> list[Road]:
    return [
        Road(index=0, name="量子线路", episodes=[
            AnimeEpisode(index=0, name="第1集", url="https://site.example.com/play/1.html",
                         road_index=0, road_name="量子线路", number=1),
            AnimeEpisode(index=1, name="第2集", url="https://site.example.com/play/2.html",
                         road_index=0, road_name="量子线路", number=2),
        ]),
        Road(index=1, name="备用线路", episodes=[
            AnimeEpisode(index=0, name="第1集", url="https://site.example.com/play/b1.html",
                         road_index=1, road_name="备用线路", number=1),
        ]),
    ]


# --------------------------------------------------------------------------- 元信息


def test_provider_metadata_comes_from_rule() -> None:
    rule = _rule()
    provider = RuleProvider(rule, engine=_FakeEngine())
    assert provider.key == "anime:cms"
    assert provider_key(rule) == "anime:cms"
    assert provider.label == "苹果CMS示例站"
    assert "XPATH" in provider.info.note          # describe() 的内容要透出来
    assert provider.available()
    assert provider.unavailable_reason() == ""
    # UA/Referer 要从规则带到源请求头里
    assert provider.default_headers() == {"User-Agent": "ModuTest/1.0",
                                         "Referer": "https://site.example.com/"}


def test_provider_unavailable_for_unsupported_and_deprecated() -> None:
    future = RuleProvider(_rule(key="future", api="9"), engine=_FakeEngine())
    assert not future.available()
    assert "客户端" in future.unavailable_reason()

    dead = RuleProvider(_rule(key="dead", deprecated=True), engine=_FakeEngine())
    assert not dead.available()
    assert "退役" in dead.unavailable_reason()


def test_provider_search_raises_when_unavailable() -> None:
    provider = RuleProvider(_rule(api="9"), engine=_FakeEngine())
    with pytest.raises(SourceError):
        provider.search("芙莉莲")


# --------------------------------------------------------------------------- 搜索映射


def test_search_maps_hits_to_remote_video() -> None:
    engine = _FakeEngine(hits=[
        SearchHit(rule_key="cms", rule_name="苹果CMS示例站", title="葬送的芙莉莲",
                  remote_id="https://site.example.com/detail/9.html",
                  url="https://site.example.com/detail/9.html",
                  cover="https://img.example.com/9.jpg", year="2023", note="更新至第28集"),
    ])
    provider = RuleProvider(_rule(), engine=engine)
    videos = provider.search("芙莉莲", limit=5)
    assert len(videos) == 1
    video = videos[0]
    assert video.source == "anime:cms"
    assert video.title == "葬送的芙莉莲"
    assert video.remote_id == "https://site.example.com/detail/9.html"
    assert video.cover_url == "https://img.example.com/9.jpg"
    assert video.year == "2023"
    assert video.remarks == "更新至第28集"
    assert series_remote_id(video) == "https://site.example.com/detail/9.html"
    # limit 要透传给引擎（否则一次搜索会拉满整页）
    assert ("search", "cms", "芙莉莲", 5) in engine.calls


def test_search_without_hits_reports_reason_instead_of_empty() -> None:
    # 空列表在影视侧等价于"这个源没有内容"，会把真实原因（选择器没命中）吞掉
    engine = _FakeEngine(hits=[], note="选择器没有匹配到任何结果：站点可能已改版")
    provider = RuleProvider(_rule(), engine=engine)
    with pytest.raises(SourceError) as info:
        provider.search("芙莉莲")
    assert "站点可能已改版" in str(info.value)


def test_search_without_hits_and_without_note_says_so() -> None:
    provider = RuleProvider(_rule(), engine=_FakeEngine(hits=[]))
    with pytest.raises(SourceError) as info:
        provider.search("不存在的番")
    assert "没有搜索到" in str(info.value)


def test_search_requires_keyword() -> None:
    provider = RuleProvider(_rule(), engine=_FakeEngine())
    with pytest.raises(SourceError):
        provider.search("   ")


# --------------------------------------------------------------------------- 详情映射


def test_detail_maps_roads_to_episodes() -> None:
    engine = _FakeEngine(roads=_roads())
    provider = RuleProvider(_rule(), engine=engine)
    video = provider.search("芙莉莲")[0]
    detailed = provider.detail(video)
    assert [item.route for item in detailed.episodes] == \
        ["量子线路", "量子线路", "备用线路"]
    assert [item.index for item in detailed.episodes] == [0, 1, 2]
    # 多线路时名字里要带线路名，否则用户分不清"第1集"是哪条线
    assert detailed.episodes[0].name == "量子线路 · 第1集"
    assert detailed.episodes[2].name == "备用线路 · 第1集"
    # 真实地址要嗅探才知道：这里绝不能塞一个假直链进 qualities
    assert all(item.qualities == [] for item in detailed.episodes)
    assert series_remote_id(detailed) == video.remote_id


def test_detail_returns_video_untouched_without_remote_id() -> None:
    provider = RuleProvider(_rule(), engine=_FakeEngine())
    from modu_workbench.core.video.models import RemoteVideo

    empty = RemoteVideo(source="anime:cms", remote_id="", title="没有地址")
    assert provider.detail(empty) is empty


# --------------------------------------------------------------------------- 播放地址


def test_play_url_returns_sniffed_media_and_referer() -> None:
    engine = _FakeEngine(roads=_roads())
    provider = RuleProvider(_rule(), engine=engine)
    video = provider.detail(provider.search("芙莉莲")[0])
    episode = video.episodes[0]
    assert provider.play_url(video, episode) == "https://cdn.example.com/1/index.m3u8"
    # 下载/代理必须带嗅探出来的 Referer（站点校验的是它，不是源首页）
    assert provider.download_headers("https://cdn.example.com/1/index.m3u8") == \
        {"Referer": "https://site.example.com/"}
    # 第二次解析走缓存（同一个地址不再嗅探一次）
    assert provider.play_url(video, episode) == "https://cdn.example.com/1/index.m3u8"
    assert [call[0] for call in engine.calls].count("resolve") == 1


def test_play_url_maps_episode_back_by_url() -> None:
    engine = _FakeEngine(roads=_roads())
    provider = RuleProvider(_rule(), engine=engine)
    video = provider.detail(provider.search("芙莉莲")[0])
    provider.play_url(video, video.episodes[2])
    # 第二集的地址要能被还原成 AnimeEpisode（含 road_index），否则模板变量会错
    assert ("resolve", "https://site.example.com/play/b1.html") in engine.calls


def test_play_url_raises_with_readable_reason_on_sniff_failure() -> None:
    engine = _FakeEngine(roads=_roads(), media=SniffedMedia(
        url="", strategy="failed",
        note="页面里没有可静态提取的媒体地址：该站可能依赖 JS 渲染（我们无浏览器内核）"))
    provider = RuleProvider(_rule(), engine=engine)
    video = provider.detail(provider.search("芙莉莲")[0])
    with pytest.raises(SourceError) as info:
        provider.play_url(video, video.episodes[0])
    assert "JS 渲染" in str(info.value)


def test_download_headers_falls_back_to_rule_referer() -> None:
    provider = RuleProvider(_rule(), engine=_FakeEngine())
    assert provider.download_headers("https://cdn.example.com/other.m3u8") == \
        {"Referer": "https://site.example.com/"}


# --------------------------------------------------------------------------- 注册表


def test_registry_registers_and_searches_one_rule() -> None:
    registry = RuleRegistry([_rule()], engine=_FakeEngine(hits=[
        SearchHit(rule_key="cms", title="葬送的芙莉莲", remote_id="id-9"),
    ]))
    assert registry.keys() == ["anime:cms"]
    assert len(registry) == 1
    videos = registry.search_one("anime:cms", "芙莉莲")
    assert [video.title for video in videos] == ["葬送的芙莉莲"]
    assert registry.get("anime:cms") is not None
    assert registry.get("anime:missing") is None
    assert registry.require("anime:cms").rule.key == "cms"
    with pytest.raises(SourceError):
        registry.require("anime:missing")


def test_registry_merges_same_title_across_rules() -> None:
    registry = RuleRegistry(engine=_FakeEngine())
    registry.register(_rule(key="a", name="A站"))
    registry.register(_rule(key="b", name="B站"))
    # 两条规则给的是同一部番，标题写法不同（归一化后应合并）
    registry.get("anime:a").search = lambda keyword, **kw: [
        _video("anime:a", "葬送的芙莉莲", "a-9")]
    registry.get("anime:b").search = lambda keyword, **kw: [
        _video("anime:b", "葬送的芙莉莲", "b-9")]
    results = registry.search_all("芙莉莲")
    assert len(results) == 1
    assert results[0].source == "anime:a"
    assert results[0].extra["anime_sources"] == [
        {"source": "anime:b", "remote_id": "b-9", "cover": "", "remarks": ""}]


def test_registry_keeps_going_when_one_rule_fails() -> None:
    registry = RuleRegistry(engine=_FakeEngine())
    registry.register(_rule(key="broken", name="坏站"))
    registry.register(_rule(key="good", name="好站"))
    errors: list[tuple] = []

    def boom(keyword, **kwargs):
        raise SourceError("坏站：请求失败（404）")

    registry.get("anime:broken").search = boom
    registry.get("anime:good").search = lambda keyword, **kw: [
        _video("anime:good", "葬送的芙莉莲", "g-9")]
    results = registry.search_all("芙莉莲", on_error=lambda provider, error: errors.append(
        (provider.key, str(error))))
    assert [video.source for video in results] == ["anime:good"]
    assert errors and errors[0][0] == "anime:broken"
    assert "404" in errors[0][1]


def test_registry_skips_unavailable_rules() -> None:
    registry = RuleRegistry([_rule(key="future", api="9")], engine=_FakeEngine())
    errors: list[str] = []
    assert registry.search_all("芙莉莲", on_error=lambda p, e: errors.append(str(e))) == []
    assert errors and "客户端" in errors[0]


def test_registry_unregister_and_rules_sync_to_engine() -> None:
    engine = _FakeEngine()
    registry = RuleRegistry(engine=engine)
    registry.register(_rule(key="a", name="A站"))
    registry.register(_rule(key="b", name="B站"))
    assert [rule.key for rule in engine.rules] == ["a", "b"]
    assert registry.unregister("anime:a")
    assert not registry.unregister("anime:a")
    assert [rule.key for rule in engine.rules] == ["b"]
    assert [rule.key for rule in registry.rules()] == ["b"]


def _video(source: str, title: str, remote_id: str):
    from modu_workbench.core.video.models import RemoteVideo

    return RemoteVideo(source=source, remote_id=remote_id, title=title, kind="anime",
                       extra={"anime_remote_id": remote_id})
