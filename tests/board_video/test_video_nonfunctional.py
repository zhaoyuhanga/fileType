"""影视链路的性能预算测试（挡住"每请求新建连接/全量缓冲"的回归）。

对应方案 3.5 的 `test_video_nonfunctional.py`。全部本机回环，只测**我们自己的
开销**，不掺外网：

| 场景 | 预算 |
|---|---|
| 代理吞吐（4 并发、有 Content-Length） | ≥ 60 MB/s |
| 缓存命中路径 | ≥ 60 MB/s（且不打上游 —— "不回源"比"多快"更重要） |
| 分片下载器（8 并发） | ≥ 40 MB/s |
| 代理首字节延迟 | < 120 ms |
| 弹幕轨道分配（1000 条） | < 50 ms（不能成为播放负担） |

预算取的是**保守下限**（CI 机器比开发机慢、全量跑时还与其它用例抢 CPU），
目的是"抓住数量级回归"（例如又变成每请求新建连接 / 全量缓冲），而不是跑分。
所以断言重点放在**结构性事实**上：缓存命中不打上游、顺序正确、首字节不等全量。
"""
from __future__ import annotations

import http.server
import socketserver
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

import pytest

from modu_workbench.services import media_proxy
from modu_workbench.services.media_proxy.config import ProxyConfig

SEGMENT_SIZE = 512 * 1024
SEGMENT_COUNT = 64


class _PayloadServer:
    """稳定的假源站：所有路径都返回 `SEGMENT_SIZE` 的固定内容。"""

    def __init__(self) -> None:
        self.payload = b"P" * SEGMENT_SIZE
        self.calls = 0
        self.lock = threading.Lock()
        self._server = None
        self.port = 0

    def __enter__(self) -> _PayloadServer:
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                with fixture.lock:
                    fixture.calls += 1
                body = fixture.payload
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:  # noqa: ANN002
                return

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def url(self, index: int) -> str:
        return f"http://127.0.0.1:{self.port}/seg{index}.ts"


@pytest.fixture(autouse=True)
def _proxy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MODU_MEDIA_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("MODU_MEDIA_NO_PREFETCH", "1")
    monkeypatch.setenv("MODU_VIDEO_CACHE_DIR", str(tmp_path / "cache"))
    media_proxy.reset_client()
    media_proxy.reset_cache()
    media_proxy.set_config(ProxyConfig(
        cache_dir=tmp_path / "cache", cache_quota_bytes=256 * 1024 * 1024,
        prefetch_segments=0, concurrency=16, playlist_ttl=0.0,
    ))
    yield
    media_proxy.reset_client()
    media_proxy.reset_cache()
    media_proxy.reset_config()


def test_proxy_throughput_budget() -> None:
    """4 并发拉 64 片（32 MB）：吞吐 ≥ 60 MB/s（含缓存写入）。"""
    with _PayloadServer() as upstream:
        urls = [media_proxy.stream_url(upstream.url(index))
                for index in range(SEGMENT_COUNT)]
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=4) as pool:
            sizes = list(pool.map(lambda url: len(urlopen(url, timeout=30).read()), urls))
        elapsed = time.monotonic() - started

    total = sum(sizes)
    assert total == SEGMENT_COUNT * SEGMENT_SIZE
    throughput = total / elapsed / 1048576
    assert throughput >= 60, f"代理吞吐只有 {throughput:.1f} MB/s（预算 ≥ 60）"


def test_proxy_cache_hit_throughput_budget() -> None:
    """缓存命中路径 ≥ 60 MB/s，且**完全不碰上游**（这是关键的结构性断言）。

    用 `requests.Session` 请求（播放器/ffmpeg 也是长连接），避免把
    "每请求新建 TCP" 的测试客户端开销算到代理头上。
    阈值刻意保守：全量跑测试时本机还要跑别的用例，这里要抓的是
    "又变成回源/全量缓冲"这类数量级回归，而不是跑分。
    """
    import requests

    with _PayloadServer() as upstream:
        url = media_proxy.stream_url(upstream.url(0))
        session = requests.Session()
        try:
            session.get(url, timeout=30).content          # 先灌一次缓存
            calls_after_fill = upstream.calls
            started = time.monotonic()
            for _ in range(32):
                assert len(session.get(url, timeout=30).content) == SEGMENT_SIZE
            elapsed = time.monotonic() - started
            assert upstream.calls == calls_after_fill, "缓存命中时不应回源"
        finally:
            session.close()

    throughput = (32 * SEGMENT_SIZE) / elapsed / 1048576
    assert throughput >= 60, f"缓存命中吞吐只有 {throughput:.1f} MB/s（预算 ≥ 60）"


def test_proxy_first_byte_latency_budget() -> None:
    """首字节延迟 < 120 ms（不再"整个响应体读完才回第一字节"）。"""
    with _PayloadServer() as upstream:
        url = media_proxy.stream_url(upstream.url(1))
        started = time.monotonic()
        with urlopen(url, timeout=30) as response:
            response.read(4096)
            first_byte_ms = (time.monotonic() - started) * 1000

    assert first_byte_ms < 120, f"首字节 {first_byte_ms:.0f}ms（预算 < 120ms）"


def test_segment_downloader_throughput_budget(tmp_path: Path) -> None:
    """分片下载器 8 并发 ≥ 40 MB/s，且产物字节序正确。"""
    from modu_workbench.core.video.hls import parse_m3u8
    from modu_workbench.core.video.segment_downloader import SegmentDownloader

    with _PayloadServer() as upstream:
        playlist_text = ("#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
                         + "".join(f"#EXTINF:4.0,\nseg{index}.ts\n"
                                   for index in range(SEGMENT_COUNT))
                         + "#EXT-X-ENDLIST\n")
        playlist = parse_m3u8(playlist_text, upstream.url(0).rsplit("/", 1)[0] + "/index.m3u8")
        downloader = SegmentDownloader(concurrency=8)
        try:
            started = time.monotonic()
            outcome = downloader.download(playlist, tmp_path / "bulk.ts")
            elapsed = time.monotonic() - started
        finally:
            downloader.close()

    assert outcome.ok, outcome.message
    assert outcome.path is not None
    assert outcome.path.stat().st_size == SEGMENT_COUNT * SEGMENT_SIZE
    throughput = outcome.bytes_written / max(0.001, elapsed) / 1048576
    assert throughput >= 40, f"分片下载吞吐只有 {throughput:.1f} MB/s（预算 ≥ 40）"


def test_download_many_parallel_beats_serial(tmp_path: Path, monkeypatch) -> None:
    """4 个任务、并发 2 → 总耗时 < 串行的 70%（证明集间真的并行）。"""
    import modu_workbench.core.video.downloader as downloader_module

    def fake_download(video, episode, dest_dir, **kwargs):  # noqa: ANN001, ANN003
        time.sleep(0.35)
        target = Path(dest_dir) / f"{video.title}.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * 1024)
        return target, _FakeResolved(video, episode)

    class _FakeResolved:
        def __init__(self, video, episode) -> None:  # noqa: ANN001
            self.video = video
            self.episode = episode
            self.quality = None
            self.source = video.source
            self.switched = False
            self.switched_from = ""
            self.note = "完成"

    monkeypatch.setattr(downloader_module, "download_episode", fake_download)
    from modu_workbench.core.video.models import Episode, RemoteVideo
    from modu_workbench.core.video.downloader import download_many

    tasks = [(RemoteVideo(source="s", remote_id=str(index), title=f"片{index}"),
              Episode(name="正片", url="u", index=0), None) for index in range(4)]

    started = time.monotonic()
    results = download_many(list(tasks), tmp_path, parallel=2)
    parallel_elapsed = time.monotonic() - started
    assert len(results) == 4 and all(item.ok for item in results)
    assert [item.video.title for item in results] == ["片0", "片1", "片2", "片3"], \
        "并发返回顺序必须与入参一致"
    # 串行需要 ~1.4s；并发 2 应在 ~0.7s 附近
    assert parallel_elapsed < 1.4 * 0.75, f"并发没生效（耗时 {parallel_elapsed:.2f}s）"


def test_danmaku_allocation_budget() -> None:
    """1000 条弹幕的轨道分配 < 50 ms（弹幕层不能成为播放负担）。"""
    from modu_workbench.ui_kit.media.danmaku import DanmakuItem, TrackAllocator

    allocator = TrackAllocator(track_count=16, scroll_seconds=8.0)
    items = [DanmakuItem(f"弹幕{index}", index * 50) for index in range(1000)]
    started = time.monotonic()
    placed = 0
    for item in items:
        track = allocator.assign(item)
        if track is not None:
            allocator.occupy(track, item)
            placed += 1
    elapsed_ms = (time.monotonic() - started) * 1000

    assert placed > 0
    assert elapsed_ms < 50, f"轨道分配耗时 {elapsed_ms:.1f}ms（预算 < 50ms）"


def test_playlist_rewrite_budget() -> None:
    """2000 行的清单改写 < 200 ms（长清单也不能拖慢起播）。"""
    from modu_workbench.services.media_proxy.playlist import rewrite_playlist

    text = ("#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
            + "".join(f"#EXTINF:4.0,\nseg{index}.ts\n" for index in range(2000))
            + "#EXT-X-ENDLIST\n")
    started = time.monotonic()
    rewritten = rewrite_playlist(text, "https://cdn.example.com/hls/index.m3u8",
                                 "https://site.example/", "", warmup=False)
    elapsed_ms = (time.monotonic() - started) * 1000

    assert rewritten.count("http://127.0.0.1:") == 2000
    assert elapsed_ms < 200, f"清单改写耗时 {elapsed_ms:.1f}ms（预算 < 200ms）"
