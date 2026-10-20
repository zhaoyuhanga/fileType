"""本机媒体代理（v1.1.0 重写，v2.0.0 起是**唯一**实现）。

设计目标：把代理从「瓶颈」变成「加速器」。

v1.0.x 的旧实现（已删除）有三个致命点：
1. 每个分片都 `requests.get(...)` —— **不复用连接**，境外源每片多付一个 TLS 握手；
2. `body = upstream.content` —— **全量缓冲**才回第一个字节；
3. 所有响应 `Cache-Control: no-store` —— seek / 换清晰度 / 重试都要重新出外网。

本包把这三件事分别改成：**连接池**（`upstream.py`）、**流式转发**（`server.py`）、
**磁盘 LRU 缓存**（`cache.py`）。

对外 API：`stream_url` / `hls_url` / `media_url` / `image_url` / `is_local_url` /
`wrap_playlist` / `local_base_url` / `ensure_server` / `shutdown_server` / `get_stats` /
`get_cache`。**v2.0.0 起不再有 `services/media_server.py` 转发壳** —— 同名函数
以前在新旧两个模块各存在一份，现在只保留这里一处，调用方直接
`from modu_workbench.services import media_proxy`。

模块划分：

    upstream.py   每主机一个 requests.Session + 分层超时/重试 + 请求头注入
    cache.py      分片磁盘缓存（LRU + 配额 + Range 语义）
    playlist.py   m3u8 清单重写 + 短时缓存 + 前 N 片预热
    server.py     ThreadingHTTPServer 路由、流式转发、并发闸门、安全收口
    stats.py      计数器（请求/命中/回源字节/延迟 P50/502 数）
"""
from __future__ import annotations

from .cache import SegmentCache, cache_key, get_cache, reset_cache
from .config import ProxyConfig, get_config, load_config, reset_config, set_config
from .playlist import origin, rewrite_playlist, wrap_playlist
from .server import (
    ensure_server,
    hls_path,
    hls_url,
    image_url,
    is_local_url,
    local_base_url,
    media_url,
    proxy_path,
    shutdown_server,
    stream_url,
)
from .stats import ProxyStats, get_stats, reset_stats
from .upstream import UpstreamClient, get_client, reset_client

__all__ = [
    "ProxyConfig", "ProxyStats", "SegmentCache", "UpstreamClient",
    "cache_key", "ensure_server", "get_cache", "get_client", "get_config",
    "get_stats", "hls_path", "hls_url", "image_url", "is_local_url",
    "load_config", "local_base_url", "media_url", "origin", "proxy_path",
    "reset_cache", "reset_client", "reset_config", "reset_stats",
    "rewrite_playlist", "set_config", "shutdown_server", "stream_url",
    "wrap_playlist",
]
