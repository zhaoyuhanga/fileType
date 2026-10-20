"""上游取流 + 磁盘缓存：给预热、Range 补齐与 handler 共用的取流原语。

**为什么单独成文件**：这几件事只跟"怎么从上游拿字节、怎么落到磁盘缓存"有关，
与 HTTP 路由/响应头无关。拆出来后 `playlist.warmup` 可以直接用，不必 import 整个
handler；也更好单独测（`tests/board_video/test_video_proxy.py`）。
"""
from __future__ import annotations

import requests

from .cache import cache_key, get_cache
from .config import MAX_IMAGE_BYTES, MAX_PLAYLIST_BYTES
from .stats import get_stats
from .upstream import get_client

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



def identity_key(url: str, episode_key: str, index: str = "") -> str:
    """缓存身份：有 episode 上下文用 `episode_key#序号`，否则退回 URL（V1-l）。"""
    if episode_key:
        return f"{episode_key}#{index}" if index else episode_key
    return url


def fetch_to_cache(url: str, *, referer: str = "", extra: str = "", kind: str = "segment",
                   byte_range: str = "", episode_key: str = "", index: str = "",
                   attempts: int | None = None) -> bytes:
    """取一个资源并写入磁盘缓存，返回内容（预热与 Range 补齐都走这里）。"""
    cache = get_cache()
    key = cache_key(identity_key(url, episode_key, index), byte_range)
    entry = cache.get(key)
    if entry is not None:
        payload = cache.read(entry)
        if payload:
            get_stats().record_hit(len(payload))
            return payload
    response = get_client().get(url, referer=referer, extra=extra, kind=kind,
                                range_header=byte_range, attempts=attempts)
    if response is None:
        raise requests.RequestException(f"上游请求失败：{url[:120]}")
    with response:
        if response.status >= 400:
            raise requests.HTTPError(f"上游返回 {response.status}")
        limit = MAX_PLAYLIST_BYTES if kind == "playlist" else (
            MAX_IMAGE_BYTES if kind == "image" else 64 * 1024 * 1024)
        payload = read_all(response, limit)
        get_stats().record_upstream(len(payload))
        cache.put(key, payload, content_type=response.content_type, status=response.status,
                  content_range=response.headers.get("Content-Range", ""), url=url)
        return payload


def read_all(response, limit: int) -> bytes:  # noqa: ANN001
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content():
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            raise ValueError(f"响应超过上限 {limit} 字节")
        chunks.append(chunk)
    return b"".join(chunks)


def looks_like_playlist(head: bytes, content_type: str = "") -> bool:
    """清单判定：优先看正文魔数，其次看 Content-Type。"""
    lowered = (content_type or "").split(";")[0].strip().lower()
    if lowered in _PLAYLIST_TYPES:
        return True
    return b"#EXTM3U" in (head or b"")[:1024]


# --------------------------------------------------------------------------- Handler




__all__ = ["fetch_to_cache", "identity_key", "looks_like_playlist", "read_all"]
