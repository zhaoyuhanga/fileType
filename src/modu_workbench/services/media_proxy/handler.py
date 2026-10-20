"""本机媒体代理的 HTTP 处理器：路由、流式转发、缓存头、Range。

路由（都带随机路径令牌，只监听 127.0.0.1）：

    /<token>/media/<abs-path>   本地文件（Range）
    /<token>/proxy/<name>?u=…   远程直通（补 Referer/UA + 磁盘缓存）
    /<token>/hls/<name>?u=…     m3u8 清单（重写内部 URI + 短缓存）
    /<token>/img/<name>?u=…     图片（海报防盗链，长缓存）

**流式转发的关键约定**（HTTP/1.1 下响应必须能界定长度）：
- 上游给了 `Content-Length` → 原样带上，边收边发；
- 上游没给（chunked / 连接关闭定界）且内容是**非清单**媒体 → 用 `Connection: close`
  流式转发（"读到 EOF"就是结束信号）。绝不伪造长度（V1-n）；
- 上游没给且内容**是清单** → 读完（清单很小，且有 4MB 上限）再发，显式给长度
  —— 旧实现就是为了这个问题才把**所有**内容都读全量，代价是全量缓冲。
"""
from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import state
from .cache import cache_key, get_cache
from .config import (
    IMAGE_MAX_AGE,
    MAX_IMAGE_BYTES,
    MAX_PLAYLIST_BYTES,
    MAX_SEGMENT_BYTES,
    SEGMENT_MAX_AGE,
    get_config,
)
from .fetch import identity_key, looks_like_playlist, read_all
from .playlist import (
    cached_playlist,
    hls_path,
    image_path,
    proxy_path,
    rewrite_playlist,
    store_playlist,
)
from .stats import get_stats
from .upstream import BlockedTarget, get_client, validate_target

CHUNK = 64 * 1024

_MEDIA_EXTS = {
    ".mp4", ".mov", ".avi", ".m4a", ".mp3", ".wav", ".webm", ".mkv", ".m4v",
    ".flac", ".aac", ".ogg", ".opus", ".wma",
}
_MIME = {
    ".mp4": "video/mp4", ".m4v": "video/x-m4v", ".mov": "video/quicktime",
    ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".flac": "audio/flac", ".aac": "audio/aac", ".ogg": "audio/ogg",
    ".opus": "audio/opus", ".wma": "audio/x-ms-wma",
}

_PLAYLIST_TYPES = ("application/vnd.apple.mpegurl", "application/x-mpegurl",
                   "audio/mpegurl", "audio/x-mpegurl")


# --------------------------------------------------------------------------- 缓存与取流



class MediaProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ModuMediaProxy/2.0"

    # ---------------------------------------------------------- 分发

    def do_GET(self) -> None:  # noqa: N802
        self._guarded(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._guarded(head_only=True)

    def _guarded(self, *, head_only: bool) -> None:
        """并发闸门 + 统一异常兜底（V1-h）。"""
        semaphore = state.concurrency_semaphore
        acquired = False
        if semaphore is not None:
            acquired = semaphore.acquire(timeout=30.0)
            if not acquired:
                self._fail(503, "proxy busy")
                return
        try:
            self._dispatch(head_only=head_only)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except BlockedTarget as error:
            self._fail(403, str(error))
        except Exception as error:  # noqa: BLE001  代理绝不能因单请求异常而崩
            if os.environ.get("MODU_MEDIA_LOG"):
                print(f"[media] handler error: {error!r}", file=sys.stderr)
            self._fail(502, "proxy error")
        finally:
            if acquired and semaphore is not None:
                semaphore.release()

    def _dispatch(self, *, head_only: bool) -> None:
        path = unquote(urlsplit(self.path).path)
        if path.startswith(state.HLS_PREFIX):
            self._serve_remote(head_only=head_only, forced_kind="playlist")
        elif path.startswith(state.PROXY_PREFIX):
            self._serve_remote(head_only=head_only, forced_kind="")
        elif path.startswith(state.IMAGE_PREFIX):
            self._serve_remote(head_only=head_only, forced_kind="image")
        elif path.startswith(state.MEDIA_PREFIX):
            self._serve_local(head_only=head_only)
        else:
            self._fail(404, "not found")

    # ---------------------------------------------------------- 错误响应

    def _fail(self, code: int, message: str = "") -> None:
        body = (message or "").encode("utf-8")
        get_stats().record_error(code)
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ---------------------------------------------------------- 本地文件

    def _serve_local(self, head_only: bool = False) -> None:
        try:
            path = unquote(urlsplit(self.path).path)
            file_path = Path(path[len(state.MEDIA_PREFIX):])
            ext = file_path.suffix.lower()
            if ext not in _MEDIA_EXTS or not file_path.is_file():
                self._fail(404, "not found")
                return
            size = file_path.stat().st_size
            start, end, status = 0, size - 1, 200
            range_header = self.headers.get("Range")
            if range_header and range_header.startswith("bytes="):
                parts = range_header[6:].split("-", 1)
                if parts[0].strip().isdigit():
                    start = int(parts[0])
                    if len(parts) == 2 and parts[1].strip().isdigit():
                        end = int(parts[1])
                    status = 206
                elif len(parts) == 2 and parts[1].strip().isdigit() and not parts[0].strip():
                    start = max(0, size - int(parts[1]))
                    status = 206
            end = min(end, size - 1)
            if start > end or start >= size:
                self._fail(416, "range not satisfiable")
                return
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", _MIME.get(ext, "application/octet-stream"))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Access-Control-Allow-Origin", "*")
            # 本地文件是不可变的下载产物 → 允许浏览器缓存（旧版是 no-store，
            # 导致"下载完再播放还要重新读一遍"）
            self.send_header("Cache-Control", f"private, max-age={SEGMENT_MAX_AGE}")
            self.end_headers()
            if head_only:
                return
            with open(file_path, "rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    data = handle.read(min(CHUNK, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ---------------------------------------------------------- 远程代理

    def _serve_remote(self, *, head_only: bool, forced_kind: str) -> None:
        query = parse_qs(urlsplit(self.path).query)
        target = (query.get("u") or [""])[0]
        referer = (query.get("r") or [""])[0]
        extra = (query.get("h") or [""])[0]
        episode_key = (query.get("k") or [""])[0]
        if not target:
            self._fail(400, "missing target url")
            return
        try:
            validate_target(target, allow_private=allow_private())
        except BlockedTarget as error:
            self._fail(403, str(error))
            return
        range_header = self.headers.get("Range") or ""
        kind = forced_kind or ("segment" if range_header else "segment")
        stats = get_stats()
        stats.record_request(kind)

        # 清单：短缓存 → 抓取 → 重写 → 缓存
        if kind == "playlist":
            self._serve_playlist(target, referer, extra, episode_key, head_only=head_only)
            return

        cache = get_cache()
        key = cache_key(identity_key(target, episode_key), range_header)
        entry = cache.get(key)
        if entry is not None:
            payload = cache.read(entry)
            if payload:
                stats.record_hit(len(payload))
                self._send_buffered(payload, entry.content_type or "application/octet-stream",
                                    status=entry.status or 200,
                                    content_range=entry.content_range,
                                    max_age=_max_age_for(kind), head_only=head_only)
                return
        stats.record_miss()

        response = get_client().get(target, referer=referer, extra=extra, kind=kind,
                                    range_header=range_header)
        if response is None:
            self._fail(502, "upstream request failed")
            return
        with response:
            if response.status >= 400:
                self._fail(response.status, "upstream error")
                return
            head = b""
            if not response.content_length:
                # 无长度（chunked / 连接关闭定界）：预读一点判断是不是清单。
                # 有长度时不预读 —— 那会把整段吞掉，等于又回到"全量缓冲"。
                head = self._peek(response)
                if looks_like_playlist(head, response.content_type):
                    self._serve_playlist_body(target, referer, extra, episode_key,
                                              response, range_header, head_only=head_only,
                                              head=head)
                    return
            self._relay_stream(response, key, kind, range_header, head=head,
                               head_only=head_only)

    def _peek(self, response, size: int = 1024) -> bytes:  # noqa: ANN001
        """预读一小段用于类型判定（不改动 iter_content 的后续读取）。"""
        try:
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=size):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= size:
                    break
            return b"".join(chunks)
        except requests.RequestException:
            return b""

    # ---------------------------------------------------------- 清单

    def _serve_playlist(self, target: str, referer: str, extra: str, episode_key: str,
                        *, head_only: bool) -> None:
        cached = cached_playlist(target, referer, extra)
        if cached is not None:
            get_stats().record_hit(len(cached))
            self._send_buffered(cached.encode("utf-8"), "application/vnd.apple.mpegurl",
                                max_age=0, head_only=head_only)
            return
        response = get_client().get(target, referer=referer, extra=extra, kind="playlist")
        if response is None:
            self._fail(502, "upstream request failed")
            return
        with response:
            if response.status >= 400:
                self._fail(response.status, "upstream error")
                return
            self._serve_playlist_body(target, referer, extra, episode_key, response,
                                      "", head_only=head_only)

    def _serve_playlist_body(self, target: str, referer: str, extra: str, episode_key: str,
                             response, range_header: str, *, head_only: bool,  # noqa: ANN001
                             head: bytes = b"") -> None:
        declared = response.content_length
        if declared and declared > MAX_PLAYLIST_BYTES:
            self._fail(502, "playlist too large")
            return
        payload = head + read_all(response, MAX_PLAYLIST_BYTES)
        get_stats().record_upstream(len(payload))
        text = payload.decode("utf-8", "replace")
        base = response.url or target
        if "#EXTM3U" in text[:512]:
            rewritten = rewrite_playlist(text, base, referer, extra, warmup=True,
                                        episode_key=episode_key)
        else:
            # 不是清单（比如拿到的其实是媒体文件）→ 原样透传，但照样进缓存
            rewritten = text
        store_playlist(target, referer, extra, rewritten)
        body = rewritten.encode("utf-8")
        self._send_buffered(body, "application/vnd.apple.mpegurl", max_age=0,
                            head_only=head_only)

    # ---------------------------------------------------------- 流式转发

    def _relay_stream(self, response, cache_key_value: str, kind: str,  # noqa: ANN001
                      range_header: str, *, head: bytes = b"", head_only: bool = False) -> None:
        """流式转发（V1-b）：有 Content-Length 就带上，没有就 `Connection: close`。

        同时把**已转发的字节**顺路写进磁盘缓存 —— 不再为缓存重读一遍内容
        （那等于把"流式"又变回"全量缓冲"）。
        """
        declared = response.content_length
        status = response.status
        collect: list[bytes] = [head] if head else []
        collect_size = len(head)
        truncated = False

        self.send_response(status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Accept-Ranges", response.headers.get("Accept-Ranges", "bytes"))
        if declared:
            self.send_header("Content-Length", str(declared))
        content_range = response.headers.get("Content-Range")
        if content_range:
            self.send_header("Content-Range", content_range)
        if not declared:
            # 无长度：用连接关闭界定响应体结束（HTTP/1.1 下的正确做法）
            self.send_header("Connection", "close")
            self.close_connection = True
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers",
                         "Content-Length, Content-Range, Accept-Ranges")
        self.send_header("Cache-Control", _cache_control(kind))
        self.end_headers()
        if head_only:
            response.close()
            return

        limit = MAX_IMAGE_BYTES if kind == "image" else 64 * 1024 * 1024
        total = collect_size
        try:
            for chunk in collect:
                self.wfile.write(chunk)
            for chunk in response.iter_content(chunk_size=CHUNK):
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    # 超过上限：停止转发并断开（播放器会自己重试/报错）
                    truncated = True
                    self.close_connection = True
                    break
                self.wfile.write(chunk)
                if total <= MAX_SEGMENT_BYTES:
                    collect.append(chunk)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            response.close()
        get_stats().record_upstream(total)

        # 只在"内容完整 + 是整段响应"时落缓存（避免把截断内容当真）
        complete = not truncated and (declared == 0 or total == declared)
        cacheable = complete and (not range_header or status == 206)
        if cacheable and total <= MAX_SEGMENT_BYTES:
            get_cache().put(cache_key_value, b"".join(collect),
                            content_type=response.content_type, status=status,
                            content_range=content_range or "", url=response.url)

    # ---------------------------------------------------------- 响应工具

    def _send_buffered(self, body: bytes, content_type: str, *, status: int = 200,
                       content_range: str = "", max_age: int = 0,
                       head_only: bool = False) -> None:
        get_stats().record_request("buffered")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if content_range:
            self.send_header("Content-Range", content_range)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers",
                         "Content-Length, Content-Range, Accept-Ranges")
        self.send_header("Cache-Control", "no-store" if max_age <= 0
                         else f"public, max-age={max_age}")
        self.end_headers()
        if head_only:
            return
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, fmt, *args) -> None:  # noqa: ANN002
        if os.environ.get("MODU_MEDIA_LOG"):
            print("[media] " + (fmt % args), file=sys.stderr)


def _max_age_for(kind: str) -> int:
    if kind == "image":
        return IMAGE_MAX_AGE
    if kind == "playlist":
        return 0
    return SEGMENT_MAX_AGE


def _cache_control(kind: str) -> str:
    max_age = _max_age_for(kind)
    # 清单必须 no-store（要能及时反映源站变化）；分片/图片可缓存（V1-m）
    return "no-store" if max_age <= 0 else f"public, max-age={max_age}"


def allow_private() -> bool:
    """是否允许访问环回/私网（测试与本地 fixture 用，默认拒绝）。"""
    return os.environ.get("MODU_MEDIA_ALLOW_PRIVATE") == "1"


__all__ = ["CHUNK", "MediaProxyHandler", "allow_private", "looks_like_playlist"]
