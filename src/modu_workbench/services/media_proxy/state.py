"""代理服务的进程级状态（独立成模块以打破 playlist ↔ server 的循环导入）。"""
from __future__ import annotations

import secrets
import threading
from http.server import ThreadingHTTPServer

#: 每次运行随机生成的路径令牌：同机其他进程无法枚举读取任意媒体文件
token = secrets.token_urlsafe(18)

#: 路径前缀（`/media/` 本地文件、`/proxy/` 直通远程、`/hls/` 清单重写、`/img/` 图片）
MEDIA_PREFIX = f"/{token}/media/"
PROXY_PREFIX = f"/{token}/proxy/"
HLS_PREFIX = f"/{token}/hls/"
IMAGE_PREFIX = f"/{token}/img/"
ALL_PREFIXES = (MEDIA_PREFIX, PROXY_PREFIX, HLS_PREFIX, IMAGE_PREFIX)

#: 令牌可能被刷新（进程内只刷新一次，用于测试与"重启服务"）
_token_history: list[str] = [token]

server: ThreadingHTTPServer | None = None
base_url: str | None = None
server_thread: threading.Thread | None = None

#: 请求并发闸门（V1-h）
concurrency_semaphore: threading.Semaphore | None = None
concurrency_limit: int = 0

lock = threading.RLock()


def current_origin() -> str:
    """本机代理的 origin（`http://127.0.0.1:<port>`）。"""
    if base_url:
        return base_url.split(MEDIA_PREFIX, 1)[0]
    if _token_history and server is not None:
        host, port = server.server_address[0], server.server_address[1]
        return f"http://{host}:{port}"
    return ""


def rotate_token() -> str:
    """重新生成路径令牌（仅供测试：模拟"服务重启换令牌"）。"""
    global token, MEDIA_PREFIX, PROXY_PREFIX, HLS_PREFIX, IMAGE_PREFIX, ALL_PREFIXES
    token = secrets.token_urlsafe(18)
    _token_history.append(token)
    MEDIA_PREFIX = f"/{token}/media/"
    PROXY_PREFIX = f"/{token}/proxy/"
    HLS_PREFIX = f"/{token}/hls/"
    IMAGE_PREFIX = f"/{token}/img/"
    ALL_PREFIXES = (MEDIA_PREFIX, PROXY_PREFIX, HLS_PREFIX, IMAGE_PREFIX)
    return token


__all__ = [
    "ALL_PREFIXES", "HLS_PREFIX", "IMAGE_PREFIX", "MEDIA_PREFIX", "PROXY_PREFIX",
    "base_url", "concurrency_limit", "concurrency_semaphore", "current_origin",
    "lock", "rotate_token", "server", "server_thread", "token",
]
