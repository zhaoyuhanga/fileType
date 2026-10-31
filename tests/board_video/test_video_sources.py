"""内置视频源清单的结构自检（离线：不联网、不依赖任何采集站存活）。

维护约定见 `core/video/sources/providers/__init__.py` 里 `CMS_SITES` 上方注释：
- 清单里每一项都必须是「人工实测能用」的地址，失效的源直接替换而不是留着；
- 同一上游换域名的站点只保留一个（避免聚合搜索重复请求同名结果）。

因此这里只锁定「清单本身不会写错」的结构性约束：
必填字段、key 唯一且命名规范、接口地址不重复、清单与默认顺序表一致、
构造内置源不产生任何网络请求。**接口是否依然可用属于人工实测，不写成联网测试。**

v3-e（源测速与排序）后本文件再加两块**纯离线**的自检：
`SourceHealth` 的延迟滑动窗口/打分公式，以及"用户顺序 vs 测速排序"的契约
（批量测速本身在 `test_video_speed.py`）。
"""
from __future__ import annotations

import re

import pytest
import requests

from modu_workbench.core.video import Episode, Quality, RemoteVideo
from modu_workbench.core.video.sources import SourceInfo, VideoRegistry
from modu_workbench.core.video.sources.base import (
    LATENCY_WINDOW,
    SourceHealth,
    resolution_score,
    weight_bonus,
)
from modu_workbench.core.video.sources.providers import (
    CMS_SITE_BY_KEY,
    CMS_SITES,
    DEFAULT_PROVIDER_ORDER,
    build_cms_providers,
    build_default_providers,
)

# 非采集类内置源（自由授权 / 直链 / 自定义），必须始终留在默认顺序表里
NON_CMS_KEYS = ("archive", "wikimedia", "url", "custom")

CMS_KEY_RE = re.compile(r"^cms_[a-z0-9]+$")


def test_cms_sites_have_required_fields_and_unique_keys() -> None:
    """每个采集站都要有 key/label/base_url/note，且 key、地址不重复。"""
    assert CMS_SITES, "内置采集站清单不能为空"
    keys: list[str] = []
    urls: list[str] = []
    for site in CMS_SITES:
        assert set(site) == {"key", "label", "base_url", "note"}, f"字段不合法：{site}"
        key, label = site["key"], site["label"]
        assert CMS_KEY_RE.match(key), f"key 命名不规范：{key}"
        assert label.strip(), f"{key} 缺少中文名称"
        assert site["base_url"].startswith(("http://", "https://")), f"{key} 接口地址不合法"
        # note 沿用「站名 · 说明」的既有风格，源设置里直接展示给用户
        assert site["note"].startswith(label), f"{key} 的说明应以站名开头"
        keys.append(key)
        urls.append(site["base_url"].rstrip("/"))
    assert len(keys) == len(set(keys)), f"key 重复：{keys}"
    assert len(urls) == len(set(urls)), f"接口地址重复：{urls}"


def test_cms_sites_match_default_provider_order() -> None:
    """清单与默认顺序表必须一一对应，且顺序一致（顺序即聚合优先级）。"""
    cms_keys = [site["key"] for site in CMS_SITES]
    order = list(DEFAULT_PROVIDER_ORDER)

    assert len(order) == len(set(order)), f"默认顺序表有重复项：{order}"
    assert set(cms_keys) <= set(order), "有采集站没写进 DEFAULT_PROVIDER_ORDER"
    assert set(order) - set(cms_keys) == set(NON_CMS_KEYS), "默认顺序表多了未知的源"
    # 顺序一致：采集站在顺序表里的先后关系要和清单完全相同
    assert [key for key in order if key in set(cms_keys)] == cms_keys
    assert set(CMS_SITE_BY_KEY) == set(cms_keys)


def test_cms_sites_api_url_is_built_correctly() -> None:
    """适配器要能把这些 base_url 拼成 `…/api.php/provide/vod` 接口地址。"""
    for provider in build_cms_providers():
        api = provider._api()  # noqa: SLF001  这里就是要锁拼接规则
        assert api.endswith("provide/vod"), f"{provider.key} 接口地址拼接异常：{api}"
        assert api.startswith("http"), f"{provider.key} 接口地址不合法：{api}"


def test_build_default_providers_needs_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """构造内置源清单不能联网（离线可用）；每个采集站都要带默认接口地址。"""

    def _offline(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("构造视频源时不应该发起网络请求")

    monkeypatch.setattr(requests, "request", _offline)
    monkeypatch.setattr(requests, "get", _offline)

    providers = build_default_providers()
    by_key = {provider.key: provider for provider in providers}
    assert list(by_key) == list(DEFAULT_PROVIDER_ORDER), "构造顺序应与默认顺序表一致"
    assert len(providers) == len(DEFAULT_PROVIDER_ORDER)

    for site in CMS_SITES:
        provider = by_key[site["key"]]
        assert provider.available(), f"{site['key']} 缺少接口地址"
        assert provider.credential == site["base_url"].rstrip("/")
        assert provider.info.editable, f"{site['key']} 应允许在源设置里改地址"

    for key in NON_CMS_KEYS:
        assert by_key[key].key == key


# --------------------------------------------------------------------------- 健康度：延迟滑动窗口


def test_latency_ring_buffer_keeps_only_window_and_reports_p50() -> None:
    """延迟窗口只留最近 LATENCY_WINDOW 个样本，P50 按线性插值。"""
    health = SourceHealth()
    # 前 20 个样本故意给很慢的值，后面 10 个给快值：
    # 如果窗口没截断，P50 会被旧样本拖到 1s 附近
    for index in range(20):
        health.note_latency(1.0 + index)
    for _ in range(LATENCY_WINDOW):
        health.note_latency(0.2)

    assert len(health.latency_samples) == LATENCY_WINDOW
    assert health.latency_samples == [0.2] * LATENCY_WINDOW
    assert health.latency_ms == pytest.approx(200.0)
    assert health.percentile(0.5) == pytest.approx(200.0)


def test_latency_percentile_interpolates_and_handles_empty() -> None:
    health = SourceHealth()
    assert health.latency_ms == 0.0            # 没样本不能抛异常
    health.note_latency(0.1)                   # 单样本：P50 就是它
    assert health.latency_ms == pytest.approx(100.0)

    fresh = SourceHealth()
    for value in (0.1, 0.2, 0.3, 0.4):
        fresh.note_latency(value)
    # 0.1/0.2/0.3/0.4 → P50 落在 0.2 与 0.3 中间
    assert fresh.percentile(0.5) == pytest.approx(250.0)
    assert fresh.percentile(0.0) == pytest.approx(100.0)
    assert fresh.percentile(1.0) == pytest.approx(400.0)


def test_latency_ignores_bad_samples() -> None:
    """负值/NaN/非数字不该污染 P50（调用方给错值时不能把源判死）。"""
    health = SourceHealth()
    for bad in (-1.0, float("nan"), "abc", None):
        health.note_latency(bad)  # type: ignore[arg-type]
    assert health.latency_samples == []
    health.note_latency("0.3")     # type: ignore[arg-type]  数字字符串可以接受
    assert health.latency_ms == pytest.approx(300.0)


def test_note_success_and_failure_keep_legacy_behaviour() -> None:
    """`note_success` / `note_failure` 必须保持旧语义（熔断计数与状态文字不变）。"""
    health = SourceHealth()
    health.note_failure("接口 404")
    assert (health.failures, health.consecutive_failures) == (1, 1)
    assert health.last_error == "接口 404"
    assert health.as_text(0.0) == "不稳定"

    health.note_success()
    assert health.successes == 1
    assert health.consecutive_failures == 0
    assert health.last_ok_at > 0
    assert health.as_text(0.0) == "可用"
    # 成功时不传耗时就不该凭空产生延迟样本
    assert health.latency_samples == []


def test_note_success_records_latency_speed_and_height() -> None:
    health = SourceHealth()
    health.note_success(0.4, speed_mbps=64.0, height=1080)
    assert health.latency_ms == pytest.approx(400.0)
    assert health.last_speed_mbps == pytest.approx(64.0)      # Mbit/s
    assert health.last_speed_mbytes == pytest.approx(8.0)     # MB/s（界面展示单位）
    assert health.measured_height == 1080
    assert health.measured_at > 0
    assert health.speed_score() == pytest.approx(1.0)         # 8 MB/s = 满参考线


def test_registry_record_failure_degrades_but_records_latency_on_success() -> None:
    """注册表这一层：失败 3 次熔断；成功时把耗时顺手写进延迟窗口。"""
    registry = VideoRegistry([_StubSource("a")])
    for _ in range(3):
        registry.record_failure("a", "挂了")
    assert registry.status_text("a") == "临时降级"

    registry.record_success("a", latency=0.25)
    assert registry.status_text("a") == "可用"
    assert registry.health("a").latency_ms == pytest.approx(250.0)


# --------------------------------------------------------------------------- 打分公式


def test_resolution_score_normalizes_known_heights() -> None:
    assert resolution_score(2160) == pytest.approx(1.0)
    assert resolution_score(1080) == pytest.approx(0.75)
    assert resolution_score(720) == pytest.approx(0.5)
    assert resolution_score(480) == pytest.approx(0.3)
    # 未知分辨率给保守的 0.4（不能给 0：否则所有拿不到 RESOLUTION 的源都排最后）
    assert resolution_score(0) == pytest.approx(0.4)
    # 单调：高度越高分越高
    assert resolution_score(2160) > resolution_score(1080) > resolution_score(720) > resolution_score(480)


def test_weight_bonus_follows_luna_formula() -> None:
    assert weight_bonus(50) == pytest.approx(1.0)
    assert weight_bonus(100) == pytest.approx(1.25)
    assert weight_bonus(0) == pytest.approx(0.75)


def test_health_score_orders_fast_1080p_above_slow_480p() -> None:
    """核心契约：快的 1080p 源必须排到慢的 480p 源前面。"""
    fast = SourceHealth()
    fast.note_success(0.15, speed_mbps=64.0, height=1080)       # 150ms / 8 MB/s / 1080P
    slow = SourceHealth()
    slow.note_success(2.4, speed_mbps=3.2, height=480)          # 2400ms / 0.4 MB/s / 480P

    assert fast.health_score(weight=50) > slow.health_score(weight=50)

    # 分项也能单独核对：延迟项按 1 - clamp(ms/3000) 归一
    lonely = SourceHealth()
    lonely.note_latency(1.5)
    # 只有延迟样本：0.4*0.4（未知分辨率）+ 0.4*0 + 0.2*(1-0.5) = 0.26
    assert lonely.health_score(weight=50) == pytest.approx(0.26)


def test_health_score_multiplies_weight_bonus() -> None:
    """权重加成是**乘**在总分上的：同一份观测，权重 100 比权重 0 高 1.25/0.75 倍。"""
    first, second = SourceHealth(), SourceHealth()
    for health in (first, second):
        health.note_success(0.3, speed_mbps=32.0, height=1080)
    high = first.health_score(weight=100)
    low = second.health_score(weight=0)
    assert high == pytest.approx(low * (1.25 / 0.75))


def test_unmeasured_source_scores_below_measured_one() -> None:
    measured = SourceHealth()
    measured.note_success(0.3, speed_mbps=32.0, height=1080)
    assert SourceHealth().health_score(weight=50) < measured.health_score(weight=50)


def test_speed_text_shows_units_and_unmeasured_hint() -> None:
    assert SourceHealth().speed_text() == "未测速"
    health = SourceHealth()
    health.note_success(0.25, speed_mbps=64.0, height=1080)
    text = health.speed_text()
    assert "250 ms" in text          # 延迟单位
    assert "8.0 MB/s" in text        # 速度单位（内部是 Mbit/s，展示换算成 MB/s）
    assert "评分" in text


# --------------------------------------------------------------------------- 排序契约


class _StubSource:
    """最小源替身：只够注册表用（本文件不测网络，所以不实现 search 的真实行为）。"""

    def __init__(self, key: str, results=None):  # noqa: ANN001
        self.info = SourceInfo(key=key, label=key, note="test")
        self._results = list(results or [])

    @property
    def key(self) -> str:
        return self.info.key

    @property
    def label(self) -> str:
        return self.info.label

    def available(self) -> bool:
        return True

    def supports_keyword_search(self) -> bool:
        return True

    def search(self, keyword: str, kind: str = "all", limit: int = 30, page: int = 1):  # noqa: ANN001
        return list(self._results)

    def play_url(self, video, episode, quality=None):  # noqa: ANN001
        return episode.url


def _remote(source: str, remote_id: str = "1", title: str = "片") -> RemoteVideo:
    video = RemoteVideo(source=source, remote_id=remote_id, title=title)
    video.episodes = [Episode(name="第01集", url=f"https://{source}/{remote_id}/0.m3u8", index=0)]
    return video


def test_weight_is_derived_from_user_order() -> None:
    """权重来自用户顺序：第 1 位 100 分、最后一位 0 分、等步长。"""
    registry = VideoRegistry([_StubSource("a"), _StubSource("b"), _StubSource("c")])
    registry.set_order(["a", "b", "c"])
    assert registry.weight_of("a") == pytest.approx(100.0)
    assert registry.weight_of("b") == pytest.approx(50.0)
    assert registry.weight_of("c") == pytest.approx(0.0)
    # 只有一个源时不偏不倚（中性 50）
    solo = VideoRegistry([_StubSource("only")])
    assert solo.weight_of("only") == pytest.approx(50.0)


def test_ordered_keys_keeps_user_order_by_default() -> None:
    registry = VideoRegistry([_StubSource("a"), _StubSource("b"), _StubSource("c")])
    registry.set_order(["c", "a", "b"])
    # 即使 c 毫无健康度数据，默认也必须原样返回用户顺序
    assert registry.ordered_keys() == ["c", "a", "b"]
    assert registry.ordered_keys(by_speed=False) == ["c", "a", "b"]
    # 未启用/停用的源在 enabled_only 下被过滤
    registry.set_enabled("a", False)
    assert registry.ordered_keys() == ["c", "b"]
    assert registry.ordered_keys(enabled_only=False) == ["c", "a", "b"]


def test_ordered_keys_by_speed_reorders_and_is_stable() -> None:
    registry = VideoRegistry([_StubSource("a"), _StubSource("b"), _StubSource("c")])
    registry.set_order(["a", "b", "c"])
    # a 排第一（权重最高）但慢；c 排最后但快得多；b 完全没测过
    registry.health("a").note_success(2.8, speed_mbps=1.6, height=480)
    registry.health("c").note_success(0.1, speed_mbps=96.0, height=1080)

    assert registry.ordered_keys() == ["a", "b", "c"]
    # c（快）> b（没数据，分辨率项保守给 0.4）> a（慢且只有 480P）
    assert registry.ordered_keys(by_speed=True) == ["c", "b", "a"]
    assert registry.score_of("c") > registry.score_of("b") > registry.score_of("a")
    # 稳定排序：重复调用结果一致
    assert registry.ordered_keys(by_speed=True) == registry.ordered_keys(by_speed=True)


def test_search_all_uses_user_order_unless_sort_by_speed_enabled() -> None:
    """搜索先后必须可预期：默认用户顺序，只有显式打开开关才按评分重排。"""
    registry = VideoRegistry([
        _StubSource("a", [_remote("a", "1", "同名片")]),
        _StubSource("b", [_remote("b", "2", "同名片")]),
    ])
    registry.set_order(["a", "b"])
    registry.health("a").note_success(2.8, speed_mbps=1.6, height=480)
    registry.health("b").note_success(0.1, speed_mbps=96.0, height=1080)

    # 默认：保留先出现的源 = 用户顺序里的 a
    videos, _errors = registry.search_all("同名片")
    assert [video.source for video in videos] == ["a"]

    registry.set_sort_by_speed(True)
    assert registry.sort_by_speed is True
    videos, _errors = registry.search_all("同名片")
    assert [video.source for video in videos] == ["b"]

    registry.sort_by_speed = False
    videos, _errors = registry.search_all("同名片")
    assert [video.source for video in videos] == ["a"]


def test_resolve_fallback_order_follows_health_when_enabled() -> None:
    """换源顺序同理：默认用户顺序，开关打开后优先试评分高的源。"""

    class BadPlay(_StubSource):
        """能搜到、但解析不出地址的源（换源兜底的触发条件）。"""

        def play_url(self, video, episode, quality=None):  # noqa: ANN001
            from modu_workbench.core.video.sources import SourceError

            raise SourceError("线路已失效")

    registry = VideoRegistry([
        _StubSource("a", [_remote("a", "1", "片")]),
        BadPlay("b"),
        _StubSource("c", [_remote("c", "3", "片")]),
    ])
    registry.set_order(["a", "b", "c"])
    # a 是用户顺序里的首选但很慢；c 排最后却快得多
    registry.health("a").note_success(2.8, speed_mbps=1.6, height=480)
    registry.health("c").note_success(0.1, speed_mbps=96.0, height=1080)

    base = _remote("b", "9", "片")          # 从坏源 b 开始解析
    resolved = registry.resolve(base, base.episodes[0], allow_cross_source=True)
    assert resolved.source == "a"           # 默认：用户顺序里的第一个可用源

    registry.set_sort_by_speed(True)
    resolved = registry.resolve(base, base.episodes[0], allow_cross_source=True)
    assert resolved.source == "c"           # 打开开关：优先评分高的源
