"""自研分片下载器测试（对应方案 3.5 的四条用例）。

全部离线：本地 `http.server` 提供假 HLS（分片内容是**可验证的序号字节**），
因此"保序写入""断点续传""坏片重试"都能被断言，而不是只看"文件非空"。
"""
from __future__ import annotations

import http.server
import socketserver
import threading
from pathlib import Path

import pytest

from modu_workbench.core.video.hls import parse_m3u8
from modu_workbench.core.video.segment_downloader import (
    SegmentDownloader,
    download_hls,
)

SEGMENT_COUNT = 12
SEGMENT_SIZE = 4096


def _segment_payload(index: int) -> bytes:
    """可校验的分片：首 4 字节是大端序号，其余填充固定字节。"""
    body = bytes([(index + 1) % 256]) * (SEGMENT_SIZE - 4)
    return index.to_bytes(4, "big") + body


class _HlsFixture:
    """本地假 HLS 站点：清单 + 分片，并记录并发峰值与每片请求次数。"""

    def __init__(self, *, fail_times: dict[int, int] | None = None,
                 probe: bool = False) -> None:
        self.fail_times = dict(fail_times or {})
        self.probe = probe
        self.inflight = 0
        self.max_inflight = 0
        self.requests: dict[str, int] = {}
        self.lock = threading.Lock()
        self._server = None
        self._thread = None
        self.port = 0

    def __enter__(self) -> _HlsFixture:
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                with fixture.lock:
                    fixture.requests[path] = fixture.requests.get(path, 0) + 1
                if path.endswith("index.m3u8"):
                    body = fixture.playlist().encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path.startswith("/seg") and path.endswith(".ts"):
                    index = int(path[4:-3])
                    if fixture.probe:
                        with fixture.lock:
                            fixture.inflight += 1
                            fixture.max_inflight = max(fixture.max_inflight, fixture.inflight)
                    try:
                        remaining = fixture.fail_times.get(index, 0)
                        if remaining > 0:
                            fixture.fail_times[index] = remaining - 1
                            self.send_response(500)
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            return
                        payload = _segment_payload(index)
                        self.send_response(200)
                        self.send_header("Content-Type", "video/mp2t")
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                    finally:
                        if fixture.probe:
                            with fixture.lock:
                                fixture.inflight -= 1
                    return
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args) -> None:  # noqa: ANN002
                return

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    @property
    def playlist_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/index.m3u8"

    def playlist(self, count: int = SEGMENT_COUNT) -> str:
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:4",
                 "#EXT-X-MEDIA-SEQUENCE:0"]
        for index in range(count):
            lines.append("#EXTINF:4.000,")
            lines.append(f"seg{index}.ts")
        lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"


def _expected_bytes(count: int = SEGMENT_COUNT) -> bytes:
    return b"".join(_segment_payload(index) for index in range(count))


def _parse(url: str) -> object:
    """把 fixture 的清单解析成 HlsPlaylist（避免每处重复）。"""
    import urllib.request

    with urllib.request.urlopen(url) as response:
        text = response.read().decode()
    return parse_m3u8(text, url)


def test_segment_downloader_concurrency_and_order(tmp_path: Path) -> None:
    """并发 4：同时在线的请求数 ∈ [2,4]，且产物字节序正确（保序写入）。"""
    with _HlsFixture(probe=True) as fixture:
        downloader = SegmentDownloader(concurrency=4, retries=2)
        try:
            playlist = downloader.fetch_playlist(fixture.playlist_url)
            outcome = downloader.download(playlist, tmp_path / "第01集.ts")
        finally:
            downloader.close()

    assert outcome.ok, outcome.message
    assert 2 <= fixture.max_inflight <= 4, f"并发峰值 {fixture.max_inflight} 不在 [2,4]"
    assert outcome.path is not None
    assert outcome.path.read_bytes() == _expected_bytes()
    assert not (tmp_path / "第01集.ts.parts").exists(), "完成后应清理分片目录"


def test_segment_downloader_resume_skips_done_parts(tmp_path: Path) -> None:
    """中断后重跑：已完成分片不再下载，最终文件仍然完整。"""
    target = tmp_path / "第02集.ts"
    parts = target.with_suffix(".ts.parts")

    with _HlsFixture(probe=True) as fixture:
        first = SegmentDownloader(concurrency=2, retries=1)
        try:
            playlist = first.fetch_playlist(fixture.playlist_url)
            # 手工造"上次下到第 5 片"的状态：写前 5 片 + manifest
            parts.mkdir(parents=True, exist_ok=True)
            for index in range(5):
                (parts / f"{index:06d}.part").write_bytes(_segment_payload(index))
            first._write_manifest(parts, parts / "manifest.json",  # noqa: SLF001
                                  first._build_plan(playlist))       # noqa: SLF001
            before = dict(fixture.requests)
            outcome = first.download(playlist, target, resume=True)
        finally:
            first.close()

    assert outcome.ok, outcome.message
    assert outcome.from_resume == 5, "应当从第 5 片继续"
    assert target.read_bytes() == _expected_bytes()
    # 前 5 片一次都不该被请求
    for index in range(5):
        assert fixture.requests.get(f"/seg{index}.ts", 0) == before.get(f"/seg{index}.ts", 0), \
            f"已完成分片 seg{index}.ts 被重复下载"


def test_segment_downloader_retries_transient_failure(tmp_path: Path) -> None:
    """瞬时失败 1 次 → 重试后成功，产物完整。"""
    with _HlsFixture(fail_times={3: 1}) as fixture:
        outcome = download_hls(fixture.playlist_url, tmp_path / "第03集.mp4",
                               concurrency=3, resume=False)

    assert outcome.ok, outcome.message
    assert outcome.bad_parts == 0
    assert (tmp_path / "第03集.ts").read_bytes() == _expected_bytes()


def test_segment_downloader_persistent_failure_produces_no_file(tmp_path: Path) -> None:
    """某片**总是**失败 → 报错且不产出残片（.parts 保留供续传）。"""
    with _HlsFixture(fail_times={7: 99}) as fixture:
        outcome = download_hls(fixture.playlist_url, tmp_path / "第04集.mp4",
                               concurrency=3, resume=False)

    assert not outcome.ok
    assert outcome.bad_parts >= 1
    assert not (tmp_path / "第04集.ts").exists(), "不应产出残片"
    assert (tmp_path / "第04集.ts.parts").is_dir(), "分片应保留以便续传"


def test_segment_downloader_pause_keeps_parts(tmp_path: Path) -> None:
    """暂停：不产出成品文件，但已完成分片与 manifest 保留。"""
    pause = threading.Event()
    pause.set()                       # 一开始就暂停 → 首片检查点即退出
    with _HlsFixture() as fixture:
        outcome = download_hls(fixture.playlist_url, tmp_path / "第05集.mp4",
                               concurrency=2, pause_event=pause, resume=False,
                               pause_timeout=0.2)

    assert not outcome.ok
    assert "暂停" in outcome.message
    assert (tmp_path / "第05集.ts.parts" / "manifest.json").is_file()


def test_segment_downloader_progress_reports_speed_and_eta(tmp_path: Path) -> None:
    """进度回调要给出片数、字节、速度与 ETA（"下载慢"要能被量化）。"""
    reports: list = []

    with _HlsFixture() as fixture:
        list_path = fixture.playlist_url
        download_hls(list_path, tmp_path / "第06集.mp4", concurrency=4,
                     on_progress=reports.append, resume=False)

    assert reports, "应当有进度回调"
    last = reports[-1]
    assert last.total_parts == SEGMENT_COUNT
    assert last.done_parts == SEGMENT_COUNT
    assert last.written_bytes == SEGMENT_COUNT * SEGMENT_SIZE
    assert last.percent == 100
    assert last.speed_bps > 0


def test_segment_downloader_rate_limiter_is_honoured() -> None:
    """限速令牌桶：给定配额，`take` 必须真的等待。"""
    import time

    from modu_workbench.core.video.segment_downloader import RateLimiter

    limiter = RateLimiter(64 * 1024)      # 64 KB/s
    started = time.monotonic()
    limiter.take(32 * 1024)
    limiter.take(32 * 1024)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.2, f"限速未生效（只用了 {elapsed:.3f}s）"

    # 不限速时不应有任何等待
    free = RateLimiter(0)
    started = time.monotonic()
    free.take(10 * 1024 * 1024)
    assert time.monotonic() - started < 0.05


def test_segment_downloader_raises_for_sample_aes(tmp_path: Path) -> None:
    """SAMPLE-AES 等无法自行解密的流要明确报错，而不是产出坏文件。"""
    playlist = ('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="k.bin"\n'
                "#EXTINF:9.0,\nseg-1.ts\n#EXT-X-ENDLIST\n")
    parsed = parse_m3u8(playlist, "https://cdn/enc/index.m3u8")
    downloader = SegmentDownloader(concurrency=2)
    try:
        from modu_workbench.core.video.sources import SourceError

        with pytest.raises(SourceError):
            downloader.download(parsed, tmp_path / "加密.ts")
    finally:
        downloader.close()


def test_segment_downloader_stops_at_manifest_change(tmp_path: Path) -> None:
    """清单分片数变化时不续传（避免把两集的碎片拼成一个文件）。"""
    target = tmp_path / "第07集.ts"
    parts = target.with_suffix(".ts.parts")

    with _HlsFixture() as fixture:
        downloader = SegmentDownloader(concurrency=2, retries=1)
        try:
            short = downloader.fetch_playlist(fixture.playlist_url)
            short.segments = short.segments[:6]
            parts.mkdir(parents=True, exist_ok=True)
            (parts / "000000.part").write_bytes(_segment_payload(0))
            downloader._write_manifest(parts, parts / "manifest.json",  # noqa: SLF001
                                       downloader._build_plan(short))    # noqa: SLF001
            full = downloader.fetch_playlist(fixture.playlist_url)
            outcome = downloader.download(full, target, resume=True)
        finally:
            downloader.close()

    assert outcome.ok, outcome.message
    assert outcome.from_resume == 0, "清单变了就不该续传"
    assert target.read_bytes() == _expected_bytes()
