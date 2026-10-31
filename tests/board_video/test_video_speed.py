"""源测速（v3-e）与图片代理（v3-f）的离线测试。

**全部离线**：HTTP 由 `_FakeHttp` 替身提供，没有任何真实网络请求。
覆盖四组契约：

1. 测速内核：首片解析、`bytes/耗时` 换算、单位（Mbit/s vs MB/s）；
2. 批量调度：提前停止（只要 1 个分片）、单源失败不拖垮整批、并发上限、
   取消及时、20 个上限与「权重最高的前 5 + 随机 15」的挑选；
3. 打分落到 `SourceHealth` 与 `ordered_keys(by_speed=True)`；
4. 图片代理 `proxied_image` / `load_pixmap` 与源设置对话框的新控件。
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field

import pytest
from PySide6.QtWidgets import QApplication

from modu_workbench.boards.video import widgets as video_widgets
from modu_workbench.boards.video.sources import VideoSourceDialog
from modu_workbench.core.video import Episode, Quality, RemoteVideo
from modu_workbench.core.video.sources import (
    SPEED_TEST_CONCURRENCY,
    SPEED_TEST_MAX_SOURCES,
    SPEED_TEST_TOP_SOURCES,
    SourceError,
    SourceInfo,
    VideoRegistry,
    VideoSource,
)
from modu_workbench.core.video.sources import probe as probe_module
from modu_workbench.core.video.sources.probe import (
    SEGMENT_BYTES,
    ProbeResult,
    hls_first_segment,
    payload_size,
    phase_timeout,
    transfer_rate,
)

MEDIA_PLAYLIST = (
    "#EXTM3U\n"
    "#EXT-X-VERSION:3\n"
    "#EXT-X-TARGETDURATION:10\n"
    "#EXTINF:9.0,\n"
    "seg0.ts\n"
    "#EXTINF:9.0,\n"
    "seg1.ts\n"
)

MASTER_PLAYLIST = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=1920x1080\n"
    "1080/index.m3u8\n"
)


# --------------------------------------------------------------------------- 替身


class _FakeResponse:
    """requests 风格的极简响应（只要 `.content` / `.text`）。"""

    def __init__(self, content: bytes = b"", text: str = "") -> None:
        self.content = content
        self.text = text if text else content.decode("utf-8", "ignore")
        self.status_code = 200
        self.headers = {"Content-Type": "application/octet-stream"}


@dataclass
class _FakeHttp:
    """可控 HTTP 替身：按 URL 给出预设响应，并记录每次调用。

    - `playlist_for` 为空 → 清单请求抛 SourceError（模拟源站挂了）；
    - `fail_playlist` / `fail_segment` 用来分别打坏两个阶段；
    - `delay` 让每次调用慢下来（测并发上限用）；
    - `content` 控制分片响应体大小（算吞吐）。
    """

    playlist_for: dict = field(default_factory=dict)
    default_playlist: str = MEDIA_PLAYLIST
    content: bytes = b"x" * 65536
    delay: float = 0.0
    fail_playlist: bool = False
    fail_segment: bool = False
    calls: list = field(default_factory=list)
    segments: list = field(default_factory=list)
    heads: list = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    active: int = 0
    peak: int = 0

    # ---------- requests 风格接口 ----------
    def _enter(self) -> None:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        if self.delay:
            time.sleep(self.delay)

    def _leave(self) -> None:
        with self.lock:
            self.active -= 1

    def request(self, method: str = "", url: str = "", **kwargs):  # noqa: ANN003, ANN201
        self._enter()
        try:
            self.heads.append((method, url))
            if method.upper() == "HEAD":
                # HEAD 只记录，不进 segments（否则"只取了一个分片"就测不准了）
                return _FakeResponse(b"")
        finally:
            self._leave()
        return self.get(url, **kwargs)

    def get(self, url: str, **kwargs):  # noqa: ANN003, ANN201
        self._enter()
        try:
            self.calls.append(url)
            if not url.lower().split("?")[0].endswith(".m3u8"):
                self.segments.append(url)
                if self.fail_segment:
                    raise SourceError("分片 403")
                return _FakeResponse(self.content)
            if self.fail_playlist:
                raise SourceError("清单 404")
            return _FakeResponse(b"", text=self.playlist_for.get(url, self.default_playlist))
        finally:
            self._leave()

    def get_text(self, url: str, **kwargs) -> str:  # noqa: ANN003
        response = self.get(url, **kwargs)
        return response.text

    def get_json(self, url: str, **kwargs):  # noqa: ANN003, ANN201
        raise SourceError("替身不提供 JSON")


def _sleep_at_least(seconds: float) -> None:
    """至少睡够 `seconds`。

    直接 `time.sleep(0.005)` 在 Windows 上会被调度粒度吃成 0，延迟样本就变成 0.0ms，
    测速的"记录延迟"这条断言会随机失败。这里用 `sleep` 拿大头、`time.sleep(0)` 空转补差额，
    保证耗时可预期又不长时间占着 CPU。
    """
    if seconds <= 0:
        return
    started = time.monotonic()
    time.sleep(seconds * 0.8)
    while time.monotonic() - started < seconds:
        time.sleep(0)


class _FakeProvider(VideoSource):
    """可离线测速的源替身：搜索返回一条带剧集的 1080P 结果。

    搜索默认耗 10ms（见 `_sleep_at_least`）：真实网络往返不可能为 0，
    而"延迟样本"正是测速的核心产物之一。
    """

    def __init__(self, key: str, *, http: _FakeHttp | None = None,
                 search_error: Exception | None = None, results=None,  # noqa: ANN001
                 search_delay: float = 0.01, quality_label: str = "1080P"):
        self.info = SourceInfo(key=key, label=f"源{key}", note="test")
        self._http = http if http is not None else _FakeHttp()
        self._search_error = search_error
        self._search_delay = search_delay
        self._quality_label = quality_label
        self._results = results
        super().__init__(http=self._http)

    def search(self, keyword: str, kind: str = "all", limit: int = 30, page: int = 1):  # noqa: ANN001
        if self._search_delay:
            _sleep_at_least(self._search_delay)
        if self._search_error is not None:
            raise self._search_error
        if self._results is not None:
            return list(self._results)
        video = RemoteVideo(source=self.key, remote_id="1", title="电影", kind="movie")
        video.episodes = [Episode(
            name="第01集", url=f"https://{self.key}.example.com/index.m3u8", index=0,
            qualities=[Quality(label=self._quality_label, url=f"https://{self.key}.example.com/1080.m3u8")],
        )]
        return [video]


def _remote_video(source: str, remote_id: str = "1", title: str = "片") -> RemoteVideo:
    video = RemoteVideo(source=source, remote_id=remote_id, title=title)
    video.episodes = [Episode(name="第01集", url=f"https://{source}/index.m3u8", index=0)]
    return video


# --------------------------------------------------------------------------- 测速内核


def test_hls_first_segment_returns_first_media_segment() -> None:
    url = hls_first_segment(MEDIA_PLAYLIST, "https://cdn.example.com/a/index.m3u8")
    assert url == "https://cdn.example.com/a/seg0.ts"
    # 必须是**第一**个，不能是最后一个
    assert "seg1" not in url


def test_hls_first_segment_rejects_master_playlist_and_garbage() -> None:
    # 主清单里只有变体、没有分片：宁可返回空（只测延迟）也不多钻一层
    assert hls_first_segment(MASTER_PLAYLIST, "https://cdn.example.com/a/index.m3u8") == ""
    assert hls_first_segment("", "https://cdn.example.com/a/index.m3u8") == ""
    assert hls_first_segment("#EXTM3U\n", "https://cdn.example.com/a/index.m3u8") == ""


def test_transfer_rate_uses_bits_per_second() -> None:
    """256 KiB / 0.25s = 1 MiB/s = 8.388608 Mbit/s。"""
    rate = transfer_rate(_FakeResponse(b"x" * SEGMENT_BYTES), 0.25)
    assert rate == pytest.approx(8.388608, rel=1e-6)
    # 极短耗时按下限 10ms 钳制：不能除出 2000 Mbit/s 这种物理上不可能的数字
    assert transfer_rate(_FakeResponse(b"x" * SEGMENT_BYTES), 0.0) == pytest.approx(209.7152)
    assert transfer_rate(_FakeResponse(b"x" * SEGMENT_BYTES), 0.002) == pytest.approx(209.7152)
    assert transfer_rate(_FakeResponse(b""), 1.0) == 0.0


def test_payload_size_prefers_content_then_raw_then_text() -> None:
    assert payload_size(_FakeResponse(b"12345")) == 5
    assert payload_size(_FakeResponse(b"", text="中文")) == len("中文".encode())
    assert payload_size(object()) == 0


def test_phase_timeout_never_goes_below_floor() -> None:
    assert phase_timeout(deadline=10.0, now=10.0) == probe_module.MIN_PHASE_TIMEOUT
    assert phase_timeout(deadline=10.5, now=10.0) == pytest.approx(0.5)


def test_probe_result_unit_helpers_are_consistent() -> None:
    result = ProbeResult(key="a", ok=True, latency_ms=120.0, mbit_per_s=64.0)
    assert result.speed_mbps == pytest.approx(64.0)
    assert result.speed_mbytes == pytest.approx(8.0)


# --------------------------------------------------------------------------- 单源测速


def test_probe_measures_latency_and_first_segment_only() -> None:
    """提前停止：拿到元数据 + 1 个分片就收工，绝不去要第二个分片。"""
    http = _FakeHttp(content=b"y" * 131072)
    provider = _FakeProvider("a", http=http)

    result = provider.probe("电影", timeout=3.0)

    assert result.ok is True
    assert result.latency_ms > 0
    assert result.mbit_per_s > 0
    assert result.height == 1080
    assert result.resolution == "1080P"
    assert result.error == ""
    assert result.segments == 1
    assert http.segments == ["https://a.example.com/seg0.ts"]   # 只请求了一个分片
    assert len(http.calls) == 2                                  # 清单 + 分片，仅此两次
    # HEAD 探活失败也不该影响结果，这里确认确实探过
    assert http.heads and http.heads[0][1] == "https://a.example.com/index.m3u8"


def test_probe_sends_range_and_referer() -> None:
    """分片请求要带 Range（少下一大截）与 Referer（防盗链）。"""
    seen: dict = {}

    class _RecordingHttp(_FakeHttp):
        def get(self, url: str, **kwargs):  # noqa: ANN003, ANN201
            seen[url] = kwargs.get("headers") or {}
            return super().get(url, **kwargs)

    http = _RecordingHttp()
    provider = _FakeProvider("a", http=http)
    provider.probe("电影", timeout=3.0)

    segment_headers = seen["https://a.example.com/seg0.ts"]
    assert segment_headers["Range"] == f"bytes=0-{SEGMENT_BYTES - 1}"
    assert segment_headers["Referer"] == "https://a.example.com/"


def test_probe_returns_latency_only_when_no_episode_url() -> None:
    """结果里没有分集地址（直链/接口不返回剧集的源）：只测延迟，不抛异常。"""
    provider = _FakeProvider("a", http=_FakeHttp())
    bare = RemoteVideo(source="a", remote_id="1", title="电影", kind="movie")
    provider._results = [bare]        # noqa: SLF001  这里就是要造这个边界

    result = provider.probe("电影", timeout=2.0)

    assert result.ok is True
    assert result.latency_ms > 0
    assert result.mbit_per_s == 0.0
    assert result.segments == 0
    assert "分集" in result.error


def test_probe_reports_search_failure_without_raising() -> None:
    provider = _FakeProvider("a", search_error=SourceError("接口 503"))
    result = provider.probe("电影", timeout=2.0)
    assert result.ok is False
    assert result.mbit_per_s == 0.0
    assert "503" in result.error


def test_probe_survives_playlist_and_segment_failures() -> None:
    """两个阶段各自失败都不能抛：清单失败只丢速度，分片失败同理。"""
    broken_manifest = _FakeProvider("a", http=_FakeHttp(fail_playlist=True))
    result = broken_manifest.probe("电影", timeout=2.0)
    assert result.ok is True                 # 延迟测到了
    assert result.mbit_per_s == 0.0
    assert "清单失败" in result.error

    broken_segment = _FakeProvider("b", http=_FakeHttp(fail_segment=True))
    result = broken_segment.probe("电影", timeout=2.0)
    assert result.ok is True
    assert result.mbit_per_s == 0.0
    assert "分片失败" in result.error
    assert broken_segment.http.segments == ["https://b.example.com/seg0.ts"]


def test_probe_handles_source_that_ignores_timeout_kwarg() -> None:
    """替身/老实现只写 `get_text(url, **kwargs)` 时，不该因为多一个 timeout 参数炸掉。"""

    class _LegacyHttp:
        def get_text(self, url, **kwargs):  # noqa: ANN003
            return MEDIA_PLAYLIST

        def get(self, url, **kwargs):  # noqa: ANN003, ANN201
            return _FakeResponse(b"z" * 1024)

        def request(self, method, url, **kwargs):  # noqa: ANN003, ANN201
            return _FakeResponse(b"")

    provider = _FakeProvider("a", http=_LegacyHttp())  # type: ignore[arg-type]
    result = provider.probe("电影", timeout=1.0)
    assert result.ok is True
    assert result.mbit_per_s > 0


def test_probe_without_http_client_is_not_a_crash() -> None:
    class _NoHttp:
        key = "x"
        info = SourceInfo(key="x", label="x", note="")

        def search(self, keyword, kind="all", limit=30, page=1):  # noqa: ANN001
            return []

    result = probe_module.probe_source(_NoHttp(), timeout=0.5, http=None)
    assert result.ok is False
    assert result.error


# --------------------------------------------------------------------------- 批量测速


def test_measure_sources_records_health_and_scores() -> None:
    fast = _FakeProvider("fast", http=_FakeHttp(content=b"x" * 262144, delay=0.0),
                         quality_label="1080P")
    slow = _FakeProvider("slow", http=_FakeHttp(content=b"x" * 2048, delay=0.05),
                         quality_label="480P")
    registry = VideoRegistry([fast, slow])
    registry.set_order(["fast", "slow"])

    results = registry.measure_sources(keys=["fast", "slow"], concurrency=2, timeout=2.0)

    assert [item.key for item in results] == ["fast", "slow"]     # 按用户顺序返回
    by_key = {item.key: item for item in results}
    assert by_key["fast"].ok and by_key["slow"].ok
    assert by_key["fast"].mbit_per_s > by_key["slow"].mbit_per_s
    assert by_key["fast"].height == 1080 and by_key["slow"].height == 480
    # 结果写回健康度，供排序与界面使用
    assert registry.health("fast").last_speed_mbps == pytest.approx(by_key["fast"].mbit_per_s)
    assert registry.health("fast").latency_ms > 0
    assert registry.health("slow").measured_height == 480
    assert registry.ordered_keys(by_speed=True)[0] == "fast"
    # SpeedResult 自带一行摘要（界面直接用）
    assert "评分" in by_key["fast"].as_text()
    assert "MB/s" in by_key["fast"].as_text()


def test_measure_sources_one_failure_does_not_fail_the_batch() -> None:
    good = _FakeProvider("good", http=_FakeHttp())
    bad = _FakeProvider("bad", search_error=SourceError("接口 500"))
    registry = VideoRegistry([good, bad])

    results = registry.measure_sources(keys=["good", "bad"], concurrency=2, timeout=2.0)

    assert len(results) == 2
    by_key = {item.key: item for item in results}
    assert by_key["good"].ok is True
    assert by_key["bad"].ok is False
    assert "500" in by_key["bad"].error
    # good 完全不受影响
    assert by_key["good"].mbit_per_s > 0
    # 失败计入健康度（连续失败会熔断）
    assert registry.health("bad").failures == 1


def test_measure_sources_cancel_after_first_result_stops_the_rest() -> None:
    """取消要"及时"：已测完的保留，还没开工的源一个请求都不许再发。"""
    cancel = threading.Event()
    seen: list[str] = []

    def _on_progress(result, done, total):  # noqa: ANN001
        seen.append(result.key)
        cancel.set()          # 第一个源一回来就取消

    providers = [_FakeProvider(f"s{index}", http=_FakeHttp(), search_delay=0.02)
                 for index in range(6)]
    registry = VideoRegistry(providers)
    registry.set_order([provider.key for provider in providers])

    started = time.monotonic()
    results = registry.measure_sources(concurrency=1, timeout=2.0, cancel=cancel,
                                       on_progress=_on_progress)
    elapsed = time.monotonic() - started

    by_key = {item.key: item for item in results}
    assert len(results) == 6                          # 每个源都有交代（含"已取消"）
    assert [item.key for item in results if item.ok] == ["s0"]
    assert by_key["s0"].mbit_per_s > 0
    assert all(by_key[f"s{index}"].error == "已取消" for index in range(1, 6))
    # 被跳过的源没有发过任何请求，健康度也没被记一笔失败
    assert all(registry.health(f"s{index}").latency_samples == [] for index in range(1, 6))
    assert all(registry.health(f"s{index}").failures == 0 for index in range(1, 6))
    assert elapsed < 1.0                              # 不会把剩下 5 个也跑完


def test_measure_sources_already_cancelled_never_sends_a_request() -> None:
    """取消后再点一次"测速"：一个请求都不该发，但每个源都要有"已取消"的交代。"""
    cancel = threading.Event()
    cancel.set()
    providers = [_FakeProvider("a"), _FakeProvider("b")]
    registry = VideoRegistry(providers)

    results = registry.measure_sources(cancel=cancel, concurrency=2, timeout=1.0)

    assert [item.key for item in results] == ["a", "b"]
    assert all(item.error == "已取消" and not item.ok for item in results)
    assert all(provider.http.calls == [] for provider in providers)


@pytest.mark.parametrize("concurrency", [1, 2])
def test_measure_sources_caps_concurrency(concurrency: int) -> None:
    """并发上限必须真的生效：每个源的 HTTP 调用要占住一会儿，才能看出峰值。"""
    providers = []
    for index in range(5):
        http = _FakeHttp(delay=0.06)
        providers.append(_FakeProvider(f"s{index}", http=http, search_delay=0.06))
    registry = VideoRegistry(providers)

    results = registry.measure_sources(concurrency=concurrency, timeout=3.0)

    assert len(results) == 5
    peak = max(provider.http.peak for provider in providers)
    assert 1 <= peak <= concurrency


def _registry_with_keys(count: int, seed: int) -> VideoRegistry:
    registry = VideoRegistry([_FakeProvider(f"s{index:02d}") for index in range(count)],
                             rng=random.Random(seed))
    registry.set_order([f"s{index:02d}" for index in range(count)])
    return registry


def test_select_speed_test_keys_takes_top_five_plus_random_fifteen() -> None:
    """20 个上限 = 用户顺序前 5 + 其余随机 15（计划 3.3.5 的 LunaTV-EE 口径）。"""
    registry = _registry_with_keys(30, seed=7)

    chosen = registry.select_speed_test_keys()

    assert len(chosen) == SPEED_TEST_MAX_SOURCES == 20
    assert len(set(chosen)) == 20
    assert chosen[:SPEED_TEST_TOP_SOURCES] == [f"s{index:02d}" for index in range(5)]
    assert set(chosen[5:]) <= {f"s{index:02d}" for index in range(5, 30)}
    # 同一颗种子（新的注册表实例）要给出同一批；不同种子要给出不同的随机部分
    assert _registry_with_keys(30, seed=7).select_speed_test_keys() == chosen
    assert _registry_with_keys(30, seed=8).select_speed_test_keys() != chosen


def test_select_speed_test_keys_advances_and_keeps_top_five() -> None:
    """连续调用会往前走 RNG（每次随机补足的那批可能不同），但前 5 个始终不变。"""
    registry = _registry_with_keys(30, seed=11)
    first = registry.select_speed_test_keys()
    second = registry.select_speed_test_keys()
    assert first[:5] == second[:5] == [f"s{index:02d}" for index in range(5)]
    assert len(first) == len(second) == 20


def test_select_speed_test_keys_respects_smaller_pools_and_explicit_keys() -> None:
    registry = VideoRegistry([_FakeProvider(f"s{index}") for index in range(3)],
                             rng=random.Random(1))
    registry.set_order(["s2", "s0", "s1"])
    # 源比上限少：全测，且顺序按用户顺序
    assert registry.select_speed_test_keys() == ["s2", "s0", "s1"]
    # 显式指定并按用户顺序归一
    assert registry.select_speed_test_keys(["s1", "s2"]) == ["s2", "s1"]
    assert registry.select_speed_test_keys(limit=0) == []
    # 未知 key 被忽略
    assert registry.select_speed_test_keys(["s0", "不存在"]) == ["s0"]


def test_measure_sources_limit_and_selection_are_applied() -> None:
    """`measure_sources` 也得吃 20 个上限：30 个源只测 20 个。"""
    providers = [_FakeProvider(f"s{index:02d}", http=_FakeHttp()) for index in range(30)]
    registry = VideoRegistry(providers, rng=random.Random(3))
    registry.set_order([f"s{index:02d}" for index in range(30)])

    results = registry.measure_sources(concurrency=SPEED_TEST_CONCURRENCY, timeout=2.0)

    assert len(results) == 20
    assert [item.key for item in results[:5]] == [f"s{index:02d}" for index in range(5)]
    measured = {item.key for item in results}
    untouched = [key for key in registry.order if key not in measured]
    assert len(untouched) == 10
    assert all(registry.health(key).latency_samples == [] for key in untouched)


def test_measure_sources_progress_callback_cannot_break_the_batch() -> None:
    registry = VideoRegistry([_FakeProvider("a")])

    def _boom(result, done, total):  # noqa: ANN001
        raise RuntimeError("界面槽函数炸了")

    results = registry.measure_sources(keys=["a"], concurrency=1, timeout=2.0, on_progress=_boom)
    assert len(results) == 1 and results[0].ok is True


# --------------------------------------------------------------------------- 图片代理（v3-f）


def test_proxied_image_routes_through_media_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []

    def _fake_image_url(url: str, referer: str = "") -> str:
        calls.append((url, referer))
        return f"http://127.0.0.1:9/img?u={url}"

    import modu_workbench.services.media_proxy as media_proxy

    monkeypatch.setattr(media_proxy, "image_url", _fake_image_url)

    result = video_widgets.proxied_image("https://img.example.com/a.jpg",
                                         "https://site.example.com/")
    assert result.startswith("http://127.0.0.1:9/img")
    assert calls == [("https://img.example.com/a.jpg", "https://site.example.com/")]


def test_proxied_image_handles_empty_local_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import modu_workbench.services.media_proxy as media_proxy

    assert video_widgets.proxied_image("") == ""
    assert video_widgets.proxied_image(None) == ""       # type: ignore[arg-type]

    # 已经是本机代理地址：幂等，不再套一层
    local = "http://127.0.0.1:9/img?u=https://img.example.com/a.jpg"
    monkeypatch.setattr(media_proxy, "image_url", lambda url, referer="": url)
    assert video_widgets.proxied_image(local) == local

    def _boom(url: str, referer: str = "") -> str:
        raise RuntimeError("代理端口起不来")

    monkeypatch.setattr(media_proxy, "image_url", _boom)
    # 任何异常都必须退回原地址（"宁可不代理"也不能变成破图）
    assert video_widgets.proxied_image("https://img.example.com/b.jpg") == "https://img.example.com/b.jpg"


def test_load_pixmap_placeholder_and_async_missing_cover(qapp: QApplication,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """没有封面 / 下载失败时给占位结果，绝不给破图，也不能抛异常。"""
    monkeypatch.setattr(video_widgets, "proxied_image", lambda url, referer="": url)
    # 空地址：立刻回调 None（调用方据此保留既有占位行为）
    empty: list = []
    assert video_widgets.load_pixmap("", on_loaded=empty.append, size=(64, 64)) is None
    qapp.processEvents()
    assert empty == [None]

    # 指向必然连不上的本机端口：不能抛，最终也要回调 None
    got: list = []
    done = threading.Event()

    def _done(pixmap) -> None:  # noqa: ANN001
        got.append(pixmap)
        done.set()

    worker = video_widgets.load_pixmap("http://127.0.0.1:9/img?u=x.jpg",
                                       on_loaded=_done, size=(64, 64))
    assert worker is not None
    try:
        for _ in range(500):
            qapp.processEvents()
            if done.is_set():
                break
            time.sleep(0.01)
        assert done.is_set(), "封面线程必须回调（否则界面会一直显示占位）"
        assert got[-1] is None
    finally:
        # 关键：必须在测试结束前把线程收干净，否则 QThread 被回收会直接把进程打崩
        worker.wait(3000)
        qapp.processEvents()


def test_source_dialog_exposes_speed_controls(qapp: QApplication) -> None:
    """源设置对话框要有：测速按钮、取消、测速列、排序复选框。"""
    dialog = VideoSourceDialog()
    try:
        assert dialog._speed_button.text() == "测速"          # noqa: SLF001
        assert dialog._cancel_speed_button.isEnabled() is False   # noqa: SLF001
        assert dialog._sort_by_speed.text() == "按测速结果排序"     # noqa: SLF001
        assert dialog._speed_concurrency.minimum() == 1        # noqa: SLF001
        assert dialog._speed_concurrency.maximum() == 6        # noqa: SLF001
        assert dialog._speed_concurrency.value() == SPEED_TEST_CONCURRENCY   # noqa: SLF001
        # 每个源都有一行测速摘要（未测速时明确写「未测速」而不是空白）
        from modu_workbench.boards.video.sources import COLUMN_SPEED, COLUMNS

        assert dialog._table.columnCount() == len(COLUMNS) == 6     # noqa: SLF001
        assert dialog._table.horizontalHeaderItem(COLUMN_SPEED).text() == "测速"   # noqa: SLF001
        for row in range(dialog._table.rowCount()):
            assert dialog._table.item(row, COLUMN_SPEED).text()     # noqa: SLF001
    finally:
        dialog.reject()


def test_source_dialog_sort_toggle_updates_registry(qapp: QApplication) -> None:
    """勾选「按测速结果排序」要同步到注册表（核心层不读 QSettings，靠这里注入）。"""
    from modu_workbench.app import context as app_context

    registry = app_context.video_registry()
    before = registry.sort_by_speed
    dialog = VideoSourceDialog()
    try:
        dialog._sort_by_speed.setChecked(not before)           # noqa: SLF001
        assert registry.sort_by_speed is (not before)
    finally:
        dialog._sort_by_speed.setChecked(before)               # noqa: SLF001
        dialog.reject()
    assert registry.sort_by_speed is before


def test_source_dialog_speed_display_uses_result() -> None:
    """测速结果落到表格行的文案（延迟 ms / 速度 MB/s / 评分）。"""
    from modu_workbench.core.video.sources.registry import SpeedResult

    result = SpeedResult(key="a", label="源A", ok=True, latency_ms=180.0,
                         mbit_per_s=64.0, height=1080, score=0.91)
    text = result.as_text()
    assert "180 ms" in text and "8.0 MB/s" in text and "0.91" in text


def test_measure_sources_ignores_unknown_keys() -> None:
    registry = VideoRegistry([_FakeProvider("a", http=_FakeHttp())])
    results = registry.measure_sources(keys=["a", "不存在"], concurrency=1, timeout=2.0)
    assert [item.key for item in results] == ["a"]
