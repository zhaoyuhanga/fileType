"""本机媒体代理测试（对应方案 3.5 的 6 条 + V1-l/V1-o/V1-q 的安全与缓存语义）。

全部离线：本地 `http.server` 当"假源站"，记录请求次数、请求头与并发峰值，
因此"连接复用""流式首字节""缓存命中""Range 语义""Referer 回退"
都能被真正断言，而不是只看状态码。

注意：代理 v1.1.0 起默认拒绝环回/私网目标（SSRF 收口 V1-q），
本文件测的是**本机 fixture**，所以 fixture 里显式打开 `MODU_MEDIA_ALLOW_PRIVATE`。
"""
from __future__ import annotations

import http.server
import socketserver
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from modu_workbench.services import media_proxy
from modu_workbench.services.media_proxy.config import ProxyConfig


# --------------------------------------------------------------------------- 假源站


class _Upstream:
    """假源站：可控制分片内容、失败次数、首字节延迟与是否返回 Content-Length。"""

    def __init__(self, *, payload: bytes = b"", delay: float = 0.0,
                 fail_times: int = 0, chunked: bool = False,
                 status: int = 200) -> None:
        self.payload = payload
        self.delay = delay
        self.fail_times = fail_times
        self.chunked = chunked
        self.status = status
        self.requests: dict[str, int] = {}
        self.headers: list[dict] = []
        self.inflight = 0
        self.max_inflight = 0
        self.lock = threading.Lock()
        self._server = None
        self.port = 0

    def __enter__(self) -> _Upstream:
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?")[0]
                with fixture.lock:
                    fixture.requests[path] = fixture.requests.get(path, 0) + 1
                    fixture.headers.append({key: value for key, value in self.headers.items()})
                    fixture.inflight += 1
                    fixture.max_inflight = max(fixture.max_inflight, fixture.inflight)
                try:
                    if fixture.delay:
                        time.sleep(fixture.delay)
                    if fixture.fail_times > 0:
                        fixture.fail_times -= 1
                        self.send_response(503)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    body = fixture.payload
                    range_header = self.headers.get("Range")
                    status = fixture.status
                    if range_header and range_header.startswith("bytes=") and status == 200:
                        start_text, _, end_text = range_header[6:].partition("-")
                        start = int(start_text or 0)
                        end = int(end_text) if end_text else len(body) - 1
                        body = body[start:end + 1]
                        status = 206
                    self.send_response(status)
                    self.send_header("Content-Type", "video/mp2t")
                    if status == 206:
                        self.send_header("Content-Range",
                                         f"bytes {start}-{end}/{len(fixture.payload)}")
                    self.send_header("Accept-Ranges", "bytes")
                    if not fixture.chunked:
                        self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                finally:
                    with fixture.lock:
                        fixture.inflight -= 1

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

    def url(self, name: str = "seg0.ts") -> str:
        return f"http://127.0.0.1:{self.port}/{name}"

    def count(self, name: str = "seg0.ts") -> int:
        return self.requests.get(f"/{name}", 0)


@pytest.fixture(autouse=True)
def _proxy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """每个用例独立：允许本机 fixture + 独立缓存目录 + 关闭预热干扰。"""
    monkeypatch.setenv("MODU_MEDIA_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("MODU_MEDIA_NO_PREFETCH", "1")
    monkeypatch.setenv("MODU_VIDEO_CACHE_DIR", str(tmp_path / "cache"))
    media_proxy.reset_client()
    media_proxy.reset_cache()
    media_proxy.set_config(ProxyConfig(
        cache_dir=tmp_path / "cache", cache_quota_bytes=1 * 1024 * 1024,
        prefetch_segments=0, concurrency=16, playlist_ttl=3.0,
    ))
    media_proxy.get_stats().reset()
    yield
    media_proxy.reset_client()
    media_proxy.reset_cache()
    media_proxy.reset_config()


def _get(url: str, **headers) -> tuple[int, bytes, dict]:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=20) as response:
        return response.status, response.read(), dict(response.headers)


# --------------------------------------------------------------------------- 用例


def test_proxy_streams_without_full_buffering() -> None:
    """大响应（4 MB）在收到第一个 chunk 后就开始回写 —— 不再全量缓冲（V1-b）。

    做法：上游先发一半、`sleep` 0.4s 再发另一半；若代理是全量缓冲，
    客户端拿到**第一个字节**的时间必然 > 0.4s；流式转发则 < 0.4s。
    """
    first, second = b"A" * (2 * 1024 * 1024), b"B" * (2 * 1024 * 1024)
    holder: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", str(len(first) + len(second)))
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            time.sleep(0.4)
            self.wfile.write(second)
            self.wfile.flush()

        def log_message(self, *_args) -> None:  # noqa: ANN002
            return

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    holder["server"] = server
    try:
        target = f"http://127.0.0.1:{server.server_address[1]}/big.ts"
        proxied = media_proxy.stream_url(target)
        started = time.monotonic()
        request = Request(proxied)
        with urlopen(request, timeout=20) as response:
            first_chunk = response.read(64)
            elapsed = time.monotonic() - started
            rest = response.read()
        assert first_chunk[:1] == b"A"
        assert elapsed < 0.35, f"第一个字节等了 {elapsed:.2f}s，说明仍在全量缓冲"
        assert len(first_chunk) + len(rest) == len(first) + len(second)
    finally:
        server.shutdown()
        server.server_close()


def test_proxy_cache_hit_avoids_second_upstream_call() -> None:
    """同一分片请求两次 → 上游只被请求 1 次；第二次显著更快（V1-c）。"""
    payload = b"S" * 256 * 1024
    with _Upstream(payload=payload) as upstream:
        proxied = media_proxy.stream_url(upstream.url("seg1.ts"))
        status_a, body_a, _ = _get(proxied)
        started = time.monotonic()
        status_b, body_b, headers_b = _get(proxied)
        second_ms = (time.monotonic() - started) * 1000

    assert status_a == status_b == 200
    assert body_a == body_b == payload
    assert upstream.count("seg1.ts") == 1, "缓存命中时不应再打上游"
    assert second_ms < 100, f"缓存命中耗时 {second_ms:.0f}ms，偏慢"
    assert "max-age=300" in headers_b.get("Cache-Control", ""), "分片应可被浏览器缓存"


def test_proxy_playlist_is_no_store() -> None:
    """清单必须 `no-store`（要能及时反映源站变化），分片可缓存（V1-m）。"""
    playlist = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
    with _Upstream(payload=playlist.encode()) as upstream:
        proxied = media_proxy.hls_url(upstream.url("index.m3u8"))
        _status, body, headers = _get(proxied)

    assert b"#EXT-X-ENDLIST" in body
    assert headers.get("Cache-Control") == "no-store"
    # 清单里的分片被改写成继续走本机代理
    lines = [line for line in body.decode().splitlines()
             if line.strip() and not line.startswith("#")]
    assert lines and lines[0].startswith("http://127.0.0.1:")


def test_proxy_range_semantics_pass_through_and_cache() -> None:
    """`Range: bytes=100-199` → 上游收到相同 Range、响应 206、只缓存该区间（V1-e）。"""
    payload = bytes(range(256)) * 40          # 10240 字节
    with _Upstream(payload=payload) as upstream:
        proxied = media_proxy.stream_url(upstream.url("seg2.ts"))
        status, body, headers = _get(proxied, Range="bytes=100-199")
        assert status == 206
        assert body == payload[100:200]
        assert headers.get("Content-Range") == f"bytes 100-199/{len(payload)}"
        # 同一区间再取一次：命中缓存（上游仍只被请求 1 次）
        status2, body2, _ = _get(proxied, Range="bytes=100-199")
        assert status2 == 206 and body2 == body

    assert upstream.count("seg2.ts") == 1, "同一 Range 应命中缓存而不是回源"
    assert upstream.headers[0].get("Range") == "bytes=100-199", "Range 必须透传给上游"


def test_proxy_segment_failure_returns_502_quickly() -> None:
    """分片失败只快速重试一次就 502（不再阻塞播放线程 1.8s+，V1-f）。"""
    with _Upstream(payload=b"x" * 1024, fail_times=99) as upstream:
        proxied = media_proxy.stream_url(upstream.url("bad.ts"))
        started = time.monotonic()
        with pytest.raises(Exception) as exc:
            _get(proxied)
        elapsed = time.monotonic() - started

    assert "502" in str(exc.value) or "503" in str(exc.value)
    assert elapsed < 1.0, f"分片失败耗时 {elapsed:.2f}s，仍会堵住播放器"


def test_proxy_cache_quota_evicts_lru(tmp_path: Path) -> None:
    """配额 1 MB 时写入多个 512 KB 分片 → 总占用不超过配额（V1-d）。"""
    media_proxy.set_config(ProxyConfig(
        cache_dir=tmp_path / "cache", cache_quota_bytes=1024 * 1024,
        prefetch_segments=0, concurrency=8, playlist_ttl=3.0,
    ))
    media_proxy.reset_cache()
    payload = b"Q" * (512 * 1024)
    with _Upstream(payload=payload) as upstream:
        for index in range(4):
            proxied = media_proxy.stream_url(upstream.url(f"q{index}.ts"))
            status, body, _ = _get(proxied)
            assert status == 200 and len(body) == len(payload)

    cache = media_proxy.get_cache()
    assert cache.total_bytes <= 1024 * 1024, f"缓存超配额：{cache.total_bytes}"
    assert cache.count() <= 2


def test_proxy_prefetch_warms_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """清单里的前 N 片被后台预热进缓存（V1-i）。

    预热是**后台**的，所以这里显式等线程池排空（而不是 sleep 猜时间）——
    否则在测试套件里跑时容易变成"时快时慢"的不稳定用例。
    """
    # 默认夹具关掉了预热（避免干扰其它用例），这里显式打开
    monkeypatch.delenv("MODU_MEDIA_NO_PREFETCH", raising=False)
    media_proxy.set_config(ProxyConfig(
        cache_dir=media_proxy.get_config().cache_dir, cache_quota_bytes=8 * 1024 * 1024,
        prefetch_segments=3, concurrency=8, playlist_ttl=0.0,
    ))
    media_proxy.reset_cache()
    from modu_workbench.services.media_proxy import playlist as playlist_module

    playlist = ("#EXTM3U\n#EXT-X-TARGETDURATION:6\n"
                + "".join(f"#EXTINF:6.0,\nseg{i}.ts\n" for i in range(5))
                + "#EXT-X-ENDLIST\n")
    with _Upstream(payload=b"W" * (64 * 1024)) as upstream:
        # 清单与分片共用一个假源站：清单放 /list.m3u8
        upstream.payload = playlist.encode()
        proxied = media_proxy.hls_url(upstream.url("list.m3u8"))
        _status, _body, _headers = _get(proxied)
        upstream.payload = b"W" * (64 * 1024)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and playlist_module.warmup_pending():
            time.sleep(0.02)
        # `shutdown_warmup()` 会等已提交的预热跑完再关闭，下次访问会重建线程池
        playlist_module.shutdown_warmup()
        cache_count = media_proxy.get_cache().count()
        prefetched = media_proxy.get_stats().prefetched

    assert cache_count >= 3, "预热没有把前 3 片放进缓存"
    assert prefetched >= 3
    assert upstream.count("seg0.ts") >= 1, "预热应当真的去取了分片"


def test_proxy_rejects_private_targets_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认拒绝环回/私网目标（SSRF 收口 V1-q）；显式打开才放行。"""
    monkeypatch.delenv("MODU_MEDIA_ALLOW_PRIVATE", raising=False)
    with _Upstream(payload=b"p" * 1024) as upstream:
        proxied = media_proxy.stream_url(upstream.url("seg.ts"))
        with pytest.raises(Exception) as exc:
            _get(proxied)
        assert "403" in str(exc.value)

    # 也直接验证校验函数：非法 scheme 与私网都被拒
    from modu_workbench.services.media_proxy.upstream import BlockedTarget, validate_target

    for bad in ("file:///etc/passwd", "http://127.0.0.1/x", "http://10.0.0.5/x",
                "http://[::1]/x", "http://169.254.1.1/x"):
        with pytest.raises(BlockedTarget):
            validate_target(bad)


def test_proxy_referer_three_stage_fallback() -> None:
    """Referer 三段回退：前两段被 403 后，第三段（不带 Referer）成功（V1-p）。"""
    accepted: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            referer = self.headers.get("Referer", "")
            accepted.append(referer)
            if referer:                      # 只认"完全不带 Referer"的请求
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"OK" * 1024
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:  # noqa: ANN002
            return

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        target = f"http://127.0.0.1:{server.server_address[1]}/seg.ts"
        proxied = media_proxy.stream_url(target, referer="https://site.example/")
        status, body, _ = _get(proxied)
    finally:
        server.shutdown()
        server.server_close()

    assert status == 200 and body.startswith(b"OK")
    # 第一段 = 源站 origin，第二段 = 目标 URL 自身，第三段 = 空
    assert accepted[:3] == ["https://site.example/", target, ""] or accepted[-1] == "" and \
        len(accepted) >= 3


def test_proxy_uses_connection_pool_across_requests() -> None:
    """连续 3 次同主机请求复用同一个 Session（不再每片新建连接，V1-a）。"""
    client = media_proxy.get_client()
    first = client.session_for("https://cdn.example.com/a.ts")
    second = client.session_for("https://cdn.example.com/b.ts")
    third = client.session_for("https://cdn.example.com/c.ts")
    other = client.session_for("https://other.example.com/d.ts")

    assert first is second is third, "同主机必须复用 Session"
    assert other is not first, "不同主机各自一个 Session"
    assert client.session_count() == 2
    # 池参数按 V1-n 的量级设置
    adapter = first.get_adapter("https://cdn.example.com")
    assert adapter._pool_connections >= 8 and adapter._pool_maxsize >= 16  # noqa: SLF001


def test_proxy_media_url_serves_local_files(tmp_path: Path) -> None:
    """本地文件走 /media/，支持 Range（回归：旧实现同样能力）。"""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0123456789")
    url = media_proxy.media_url(str(clip))
    assert media_proxy.is_local_url(url)
    status, body, _ = _get(url, Range="bytes=2-4")
    assert status == 206 and body == b"234"


def test_proxy_image_url_long_cache() -> None:
    """图片代理：长缓存 + 正确的 Content-Type（海报防盗链用，V3-f）。"""
    payload = b"\x89PNG\r\n\x1a\n" + b"0" * 512
    with _Upstream(payload=payload) as upstream:
        proxied = media_proxy.image_url(upstream.url("poster.png"))
        status, body, headers = _get(proxied)

    assert status == 200 and body == payload
    assert "max-age=86400" in headers.get("Cache-Control", "")


def test_proxy_stats_snapshot_counts_hits_and_misses() -> None:
    """代理计数器可供诊断面板读取（请求/命中/回源字节/延迟）。"""
    with _Upstream(payload=b"Z" * 4096) as upstream:
        proxied = media_proxy.stream_url(upstream.url("s.ts"))
        _get(proxied)
        _get(proxied)

    snapshot = media_proxy.get_stats().snapshot()
    assert snapshot["requests"] >= 2
    assert snapshot["hits"] >= 1
    assert snapshot["upstream_bytes"] > 0
    assert snapshot["latency_p50_ms"] >= 0
