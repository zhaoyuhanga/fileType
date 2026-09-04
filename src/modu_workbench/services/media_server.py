"""本地媒体 HTTP 流服务（供 QtWebEngine 内嵌 <video> 播放）。

- 仅监听 127.0.0.1 随机端口，随应用懒启动（守护线程）；
- 支持 Range 请求（拖动进度条）；
- 只服务本机绝对路径且扩展名在媒体白名单内的文件。
"""
from __future__ import annotations

import os
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote

_MEDIA_EXTS = {".mp4", ".mov", ".avi", ".m4a", ".mp3", ".wav", ".webm", ".mkv", ".m4v"}
_MIME = {
    ".mp4": "video/mp4", ".m4v": "video/x-m4v", ".mov": "video/quicktime",
    ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
}

_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_base_url: str | None = None


def _make_handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):  # type: ignore[no-redef]
        def do_GET(self) -> None:  # noqa: N802
            self._serve()

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve(head_only=True)

        def _serve(self, head_only: bool = False) -> None:
            try:
                path = unquote(urlsplit(self.path).path)
                if not path.startswith("/media/"):
                    self.send_error(404)
                    return
                file_path = Path(path[len("/media/"):])
                ext = file_path.suffix.lower()
                if ext not in _MEDIA_EXTS or not file_path.is_file():
                    self.send_error(404)
                    return
                size = file_path.stat().st_size
                start = 0
                end = size - 1
                status = 200
                range_header = self.headers.get("Range")
                if range_header and range_header.startswith("bytes="):
                    parts = range_header[6:].split("-", 1)
                    if parts[0].strip().isdigit():
                        start = int(parts[0])
                        if len(parts) == 2 and parts[1].strip().isdigit():
                            end = int(parts[1])
                        status = 206
                    elif len(parts) == 2 and parts[1].strip().isdigit() and not parts[0].strip():
                        suffix = int(parts[1])
                        start = max(0, size - suffix)
                        status = 206
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_error(416)
                    return
                length = end - start + 1
                self.send_response(status)
                self.send_header("Content-Type", _MIME.get(ext, "application/octet-stream"))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if head_only:
                    return
                with open(file_path, "rb") as fh:
                    fh.seek(start)
                    remaining = length
                    chunk = 64 * 1024
                    while remaining > 0:
                        data = fh.read(min(chunk, remaining))
                        if not data:
                            break
                        self.wfile.write(data)
                        remaining -= len(data)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def log_message(self, *_args) -> None:  # noqa: ANN002
            return

    return Handler


def ensure_server() -> str:
    """启动本地流服务（幂等），返回 base URL。"""
    global _server, _base_url
    if _base_url:
        return _base_url
    with _lock:
        if _base_url:
            return _base_url
        handler = _make_handler()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        _server = server
        _base_url = f"http://127.0.0.1:{port}/media/"
        thread = threading.Thread(target=server.serve_forever, name="modu-media-server", daemon=True)
        thread.start()
        return _base_url


def media_url(file_path: str) -> str:
    """返回可在 <video src> 中使用的本地流地址。"""
    base = ensure_server()
    from urllib.parse import quote

    return base + quote(str(Path(file_path)), safe="")
