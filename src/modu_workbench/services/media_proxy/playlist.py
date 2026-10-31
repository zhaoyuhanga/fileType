"""m3u8 清单重写 + 短时缓存 + 预热（V1-i / V1-j / V1-l）。

重写的目标只有一个：**让清单内的每一个 URI 都继续走本机代理**，
这样分片请求到达时才能在服务端补上 Referer/UA，并被磁盘缓存接住。

短时缓存（默认 3s）：同一份清单被反复拉取（播放器探测 + ffmpeg + 下载器）
是常态，TTL 内直接复用上次的重写结果，避免"一首歌反复拉清单"。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin

from . import state
from .config import get_config
from .stats import get_stats
from .upstream import get_client
from modu_workbench.core.platform.logging import swallow

#: 清单改写结果缓存：key = (target, referer, extra) → (到期时间, 文本)
_playlist_cache: dict[tuple[str, str, str], tuple[float, str]] = {}
_playlist_lock = threading.Lock()


def origin() -> str:
    """本机代理 origin（绝对地址最稳妥：hls.js/ffmpeg 都不接受相对路径）。"""
    from .server import ensure_server

    ensure_server()
    return state.current_origin()


def name_hint(target: str) -> str:
    """取出目标 URL 的文件名，让代理路径以真实扩展名结尾。

    必要性：ffmpeg 的 HLS 解复用器按 URL 扩展名判断分片类型，
    形如 `/proxy/?u=...` 的地址会被判为「不在 allowed_segment_extensions」
    而直接拒绝（报 Invalid data）。服务端只按路径前缀分发，附加文件名不影响处理。
    """
    import re
    from urllib.parse import urlsplit

    try:
        path = urlsplit(target).path
    except ValueError:
        return ""
    name = path.rsplit("/", 1)[-1]
    if "." not in name or len(name) > 64:
        return ""
    return re.sub(r"[^A-Za-z0-9._-]", "", name)


def _params(target: str, referer: str, extra: str) -> str:
    from urllib.parse import urlencode

    params = {"u": target}
    if referer:
        params["r"] = referer
    if extra:
        params["h"] = extra
    return urlencode(params)


def hls_path(target: str, referer: str = "", extra: str = "", episode_key: str = "") -> str:
    """HLS 代理路径（不含 origin）。"""
    from .server import ensure_server

    ensure_server()
    query = _params(target, referer, extra)
    if episode_key:
        query += "&k=" + episode_key
    return f"{state.HLS_PREFIX}{name_hint(target)}?{query}"


def proxy_path(target: str, referer: str = "", extra: str = "", episode_key: str = "") -> str:
    """直通代理路径（不含 origin）。"""
    from .server import ensure_server

    ensure_server()
    query = _params(target, referer, extra)
    if episode_key:
        query += "&k=" + episode_key
    return f"{state.PROXY_PREFIX}{name_hint(target)}?{query}"


def image_path(target: str, referer: str = "", extra: str = "") -> str:
    """图片代理路径（不含 origin）。"""
    from .server import ensure_server

    ensure_server()
    return f"{state.IMAGE_PREFIX}{name_hint(target)}?{_params(target, referer, extra)}"


def _rewrite_key_uri(line: str, base_url: str, referer: str, extra: str) -> str:
    """改写 `#EXT-X-KEY` 里的 URI，让密钥也走本机代理。

    AES-128 加密流必须取到密钥才能解密；若密钥 URI 仍指向原站，
    播放器会因跨域/缺少 Referer 而失败。
    """
    marker = 'URI="'
    head, sep, tail = line.partition(marker)
    if not sep:
        return line
    uri, sep2, rest = tail.partition('"')
    if not uri:
        return line
    url = uri if uri.lower().startswith(("http://", "https://")) else urljoin(base_url, uri)
    return f"{head}{marker}{origin()}{proxy_path(url, referer, extra)}{sep2}{rest}"


def _segment_lines(text: str, base_url: str, referer: str, extra: str) -> list[str]:
    """列出清单里的分片绝对地址（用于预热）。"""
    found: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        found.append(
            stripped
            if stripped.lower().startswith(("http://", "https://"))
            else urljoin(base_url, stripped)
        )
    return found


def rewrite_playlist(
    text: str,
    base_url: str,
    referer: str = "",
    extra: str = "",
    *,
    warmup: bool = True,
    episode_key: str = "",
) -> str:
    """把 m3u8 里所有 URI 重写为继续走本机代理（保留请求头）。

    既包括普通行（分片 / 子清单），也包括 `#EXT-X-KEY` 的密钥 URI。
    `warmup=True` 时顺手把前 N 个分片排进后台预热队列（V1-i）。
    """
    out_lines: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue
        if stripped.startswith("#"):
            if stripped.startswith("#EXT-X-KEY") and 'URI="' in stripped:
                out_lines.append(_rewrite_key_uri(stripped, base_url, referer, extra))
            else:
                out_lines.append(line)
            continue
        absolute = (
            stripped
            if stripped.lower().startswith(("http://", "https://"))
            else urljoin(base_url, stripped)
        )
        out_lines.append(origin() + hls_path(absolute, referer, extra, episode_key))
    rewritten = "\n".join(out_lines)

    if warmup:
        _schedule_warmup(text, base_url, referer, extra)
    return rewritten


# ---------------------------------------------------------------- 预热

_warmup_pool = None
_warmup_lock = threading.Lock()
_warmup_pending: set[str] = set()


def _warmup_executor():  # noqa: ANN201
    global _warmup_pool
    if _warmup_pool is None:
        from concurrent.futures import ThreadPoolExecutor

        _warmup_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="media-warmup")
    return _warmup_pool


def warmup_executor():  # noqa: ANN201
    """预热线程池（测试要等"预热真的做完"时用它，不要靠 sleep 猜）。"""
    return _warmup_executor()


def warmup_pending() -> int:
    """仍在预热队列里的分片数（诊断面板/测试用）。"""
    with _warmup_lock:
        return len(_warmup_pending)


def warmup_enabled() -> bool:
    import os

    if os.environ.get("MODU_MEDIA_NO_PREFETCH") == "1":
        return False
    return get_config().prefetch_segments > 0


def _schedule_warmup(text: str, base_url: str, referer: str, extra: str) -> None:
    """后台预热前 N 个分片到磁盘缓存（不阻塞清单响应）。"""
    if not warmup_enabled():
        return
    config = get_config()
    segments = _segment_lines(text, base_url, referer, extra)[: config.prefetch_segments]
    if not segments:
        return
    with _warmup_lock:
        queue = [url for url in segments if url not in _warmup_pending]
        _warmup_pending.update(queue)
    for url in queue:
        try:
            _warmup_executor().submit(_warmup_one, url, referer, extra)
        except RuntimeError:  # 解释器退出中
            with _warmup_lock:
                _warmup_pending.discard(url)


def _warmup_one(url: str, referer: str, extra: str) -> None:
    """取一个分片并写入缓存（失败静默 —— 预热是"锦上添花"）。"""
    try:
        from .server import fetch_to_cache

        fetch_to_cache(url, referer=referer, extra=extra, kind="segment")
        get_stats().record_prefetch(1)
    except Exception as error:  # noqa: BLE001  外部回调/第三方边界
        swallow("playlist 中的可选步骤", error)
    finally:
        with _warmup_lock:
            _warmup_pending.discard(url)


def shutdown_warmup() -> None:
    """关闭预热线程池（应用退出/测试清理）。"""
    global _warmup_pool
    if _warmup_pool is not None:
        _warmup_pool.shutdown(wait=False)
        _warmup_pool = None


# ---------------------------------------------------------------- 清单短缓存


def cached_playlist(target: str, referer: str, extra: str) -> str | None:
    """命中短缓存则返回上次的重写结果（V1-j）。"""
    key = (target, referer, extra)
    with _playlist_lock:
        item = _playlist_cache.get(key)
        if item is None:
            return None
        expires, text = item
        if expires < time.time():
            _playlist_cache.pop(key, None)
            return None
        return text


def store_playlist(target: str, referer: str, extra: str, text: str) -> None:
    ttl = get_config().playlist_ttl
    if ttl <= 0:
        return
    key = (target, referer, extra)
    with _playlist_lock:
        _playlist_cache[key] = (time.time() + ttl, text)
        if len(_playlist_cache) > 64:  # 防止无界增长
            oldest = min(_playlist_cache, key=lambda item: _playlist_cache[item][0])
            _playlist_cache.pop(oldest, None)


def clear_playlist_cache() -> None:
    with _playlist_lock:
        _playlist_cache.clear()


# ---------------------------------------------------------------- 对外便捷函数


def wrap_playlist(text: str, base_url: str, referer: str = "", headers: dict | None = None) -> str:
    """把 m3u8 内容改写为「全部经本机代理」的形式（绝对地址）。

    供 ffmpeg 下载与调试使用：请求头由本机代理在服务端补，
    因此 ffmpeg 不需要任何 HTTP 选项（跨版本稳定）。
    """
    extra = "|".join(f"{key}={value}" for key, value in (headers or {}).items() if value)
    return rewrite_playlist(text, base_url, referer, extra)


@dataclass
class PlaylistFetch:
    """一次清单抓取的结果。"""

    text: str
    base_url: str
    from_cache: bool = False


def fetch_playlist(target: str, referer: str = "", extra: str = "") -> PlaylistFetch:
    """抓取并（可选）复用短缓存；调用方负责改写。"""
    cached = cached_playlist(target, referer, extra)
    if cached is not None:
        return PlaylistFetch(text=cached, base_url=target, from_cache=True)
    client = get_client()
    raw = client.get_bytes(target, referer=referer, extra=extra, kind="playlist")
    return PlaylistFetch(text=raw.decode("utf-8", "replace"), base_url=target)


__all__ = [
    "PlaylistFetch",
    "cached_playlist",
    "clear_playlist_cache",
    "fetch_playlist",
    "hls_path",
    "image_path",
    "name_hint",
    "origin",
    "proxy_path",
    "rewrite_playlist",
    "shutdown_warmup",
    "store_playlist",
    "warmup_enabled",
    "warmup_executor",
    "warmup_pending",
    "wrap_playlist",
]
