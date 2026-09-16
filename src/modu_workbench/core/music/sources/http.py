"""音源 HTTP 客户端：统一实现在 `core.platform.http`，这里只做音源默认值。

保留本模块是为了让各音源（`core.music.sources.providers.*`）继续
`from ..http import HttpClient, SourceError`，同时把"换音源"的建议文案固定下来。
"""
from __future__ import annotations

from modu_workbench.core.platform.http import (
    DEFAULT_UA,
    MEDIA_URL_SUFFIXES,
    SourceError,  # noqa: F401  对外沿用同名异常
    describe_network_error as _describe_network_error,
    guess_referer,
    host_of,
    looks_like_media_url,
    origin_of,
)
from modu_workbench.core.platform.http import HttpClient as _HttpClient

_HINT = "可改用其他音源（酷我 / iTunes / Audius / Internet Archive / 直链）"


def describe_network_error(error: Exception | None, host: str = "") -> str:
    return _describe_network_error(error, host, hint=_HINT)


class HttpClient(_HttpClient):
    """音源默认参数（超时 12s、退避 0.8s）的 HTTP 客户端。"""

    def __init__(self, *, headers: dict | None = None, timeout: float = 12.0,
                 attempts: int = 3, backoff: float = 0.8, verify: bool = True):
        super().__init__(headers=headers, timeout=timeout, attempts=attempts,
                         backoff=backoff, verify=verify, hint=_HINT)


__all__ = [
    "DEFAULT_UA",
    "MEDIA_URL_SUFFIXES",
    "HttpClient",
    "SourceError",
    "describe_network_error",
    "guess_referer",
    "host_of",
    "looks_like_media_url",
    "origin_of",
]
