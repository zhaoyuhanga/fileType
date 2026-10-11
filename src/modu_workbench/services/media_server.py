"""本地媒体 HTTP 流服务（**兼容壳**）。

v1.1.0 起，实现全部迁到 `services/media_proxy/`（连接池 + 流式转发 + 磁盘缓存）。
本模块只保留**同名同签名**的转发函数与少量旧内部函数别名，
因此 `boards/video/player.py`、`core/video/downloader.py` 与既有测试无需改动
即可获得新代理的全部收益：

- `requests.Session` 连接池（不再每个分片新开 TLS）；
- `iter_content` 流式转发（不再全量读进内存才回第一个字节）；
- 分片磁盘 LRU 缓存（seek / 换清晰度 / 重播不再重新出外网）；
- 清单 3s 短缓存 + 前 N 片后台预热；
- 分层超时/重试（分片失败快速 502，不再阻塞播放线程 1.8s+）；
- SSRF 收口（拒绝环回/私网/链路本地/组播目标）。

新代码请直接 `from modu_workbench.services import media_proxy`。
"""
from __future__ import annotations

from modu_workbench.services import media_proxy as _proxy
from modu_workbench.services.media_proxy import state as _state

#: 旧测试/旧调用方用到的内部函数（保持原名）
_rewrite_playlist = _proxy.rewrite_playlist

#: 令牌与路径前缀（旧代码里可能直接读过）
_TOKEN = _state.token
_PREFIX = _state.MEDIA_PREFIX
_PROXY_PREFIX = _state.PROXY_PREFIX
_HLS_PREFIX = _state.HLS_PREFIX

_CHUNK = 64 * 1024
_UPSTREAM_TIMEOUT = (5.0, 20.0)


def _hls_path(target: str, referer: str = "", extra: str = "") -> str:
    """构造 HLS 代理路径（不含 origin）——旧签名保留。"""
    return _proxy.hls_path(target, referer, extra)


def _proxy_path(target: str, referer: str = "", extra: str = "") -> str:
    """构造直通代理路径（不含 origin）——旧签名保留。"""
    return _proxy.proxy_path(target, referer, extra)


def _name_hint(target: str) -> str:
    from modu_workbench.services.media_proxy.playlist import name_hint

    return name_hint(target)


def _upstream_headers(referer: str = "", extra: str = "") -> dict:
    from modu_workbench.services.media_proxy.upstream import build_headers

    return build_headers(referer, extra)


def wrap_playlist(text: str, base_url: str, referer: str = "",
                  headers: dict | None = None) -> str:
    """把 m3u8 内容改写为「全部经本机代理」的形式（绝对地址）。"""
    return _proxy.wrap_playlist(text, base_url, referer, headers)


def ensure_server() -> str:
    """启动本地流服务（幂等），返回 base URL（本地文件前缀）。"""
    return _proxy.ensure_server()


def media_url(file_path: str) -> str:
    """返回可在播放器中使用的本地流地址。"""
    return _proxy.media_url(file_path)


def is_local_url(url: str) -> bool:
    """是否已经是本机流服务地址（避免重复代理）。"""
    return _proxy.is_local_url(url)


def stream_url(url: str, referer: str = "", headers: dict | None = None) -> str:
    """把远程地址包装成带 Referer/UA 的本机播放地址（绝对 URL）。"""
    return _proxy.stream_url(url, referer, headers)


def hls_url(url: str, referer: str = "", headers: dict | None = None) -> str:
    """把 m3u8 地址包装成经过本机清单重写的地址（适合播放器，绝对 URL）。"""
    return _proxy.hls_url(url, referer, headers)


def image_url(url: str, referer: str = "") -> str:
    """把海报/封面地址包装成本机图片代理地址（防防盗链 403）。"""
    return _proxy.image_url(url, referer)


def local_base_url() -> str:
    """本机流服务前缀（调试用）。"""
    return _proxy.local_base_url()


def stats() -> dict:
    """代理计数器快照（F12 播放诊断面板用）。"""
    return _proxy.get_stats().snapshot()


def cache_stats() -> dict:
    """分片缓存占用情况。"""
    return _proxy.get_cache().stats()


def clear_cache() -> int:
    """清空分片缓存，返回清理的字节数。"""
    return _proxy.get_cache().clear()


def shutdown_server() -> None:
    """关闭本地流服务（应用退出/测试清理）。"""
    _proxy.shutdown_server()


__all__ = [
    "cache_stats", "clear_cache", "ensure_server", "hls_url", "image_url",
    "is_local_url", "local_base_url", "media_url", "shutdown_server", "stats",
    "stream_url", "wrap_playlist",
]
