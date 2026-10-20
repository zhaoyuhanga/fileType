"""本机媒体代理的服务生命周期与对外 URL API。

对应方案里的 V1-k（可观测）。**取流/缓存原语在 `fetch.py`，HTTP 路由在 `handler.py`**，
本文件只负责"起停服务"和"把远程/本地地址包装成本机代理地址"。

公开 API（`media_proxy/__init__.py` 与调用方都用这些名字）：

    ensure_server() / shutdown_server()      起停（幂等）
    media_url / stream_url / hls_url / image_url / is_local_url / local_base_url
"""

from __future__ import annotations

import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

from . import state
from .config import get_config
from .fetch import fetch_to_cache, looks_like_playlist  # noqa: F401  对外沿用旧名字
from .handler import MediaProxyHandler
from .playlist import hls_path, image_path, proxy_path

# --------------------------------------------------------------------------- 服务生命周期


def ensure_server() -> str:
    """启动本地代理服务（幂等），返回 base URL（本地文件前缀）。"""
    with state.lock:
        if state.base_url:
            return state.base_url
        config = get_config()
        server = ThreadingHTTPServer(("127.0.0.1", 0), MediaProxyHandler)
        server.daemon_threads = True
        port = server.server_address[1]
        state.server = server
        state.base_url = f"http://127.0.0.1:{port}{state.MEDIA_PREFIX}"
        state.concurrency_semaphore = threading.Semaphore(config.concurrency)
        state.concurrency_limit = config.concurrency
        thread = threading.Thread(target=server.serve_forever, name="modu-media-proxy", daemon=True)
        thread.start()
        state.server_thread = thread
        return state.base_url


def shutdown_server() -> None:
    """关闭代理服务（应用退出/测试清理用）。"""
    from .playlist import shutdown_warmup

    with state.lock:
        server = state.server
        state.server = None
        state.base_url = None
        state.server_thread = None
        state.concurrency_semaphore = None
        state.concurrency_limit = 0
    shutdown_warmup()
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except OSError as error:  # 端口已关/已释放：退出流程里不值得再抛
            print(f"[media] 关闭本机代理时出现 OSError（可忽略）：{error}", file=sys.stderr)


# --------------------------------------------------------------------------- 对外 API


def media_url(file_path: str) -> str:
    """返回可在播放器中使用的本地流地址（带 Range 支持）。"""
    base = ensure_server()
    return base + quote(str(Path(file_path)), safe="")


def is_local_url(url: str) -> bool:
    """是否已经是本机代理地址（避免重复代理）。"""
    if not url.startswith("http://127.0.0.1:"):
        return False
    return any(prefix in url for prefix in state.ALL_PREFIXES)


def stream_url(url: str, referer: str = "", headers: dict | None = None) -> str:
    """把远程地址包装成带 Referer/UA 的本机播放地址（绝对 URL）。"""
    if not url:
        return ""
    if is_local_url(url):
        return url
    extra = "|".join(f"{key}={value}" for key, value in (headers or {}).items() if value)
    return _origin() + proxy_path(url, referer, extra)


def hls_url(url: str, referer: str = "", headers: dict | None = None) -> str:
    """把 m3u8 地址包装成经过本机清单重写的地址（绝对 URL）。"""
    if not url:
        return ""
    extra = "|".join(f"{key}={value}" for key, value in (headers or {}).items() if value)
    return _origin() + hls_path(url, referer, extra)


def image_url(url: str, referer: str = "") -> str:
    """把海报/封面地址包装成本机图片代理地址（防防盗链 403）。"""
    if not url or is_local_url(url):
        return url
    return _origin() + image_path(url, referer)


def local_base_url() -> str:
    """本机代理前缀（调试用）。"""
    return ensure_server()


def _origin() -> str:
    ensure_server()
    return state.current_origin()


__all__ = [
    "fetch_to_cache",
    "looks_like_playlist",
    "ensure_server",
    "hls_path",
    "hls_url",
    "image_url",
    "is_local_url",
    "local_base_url",
    "media_url",
    "proxy_path",
    "shutdown_server",
    "stream_url",
]
