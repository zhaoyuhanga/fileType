"""影视源 HTTP 客户端：统一实现在 `core.platform.http`，这里只做视频源默认值。

视频源相比音源的两点差异固定在本模块：
1. 默认更多次退避重试（第三方采集站抖动更频繁）；
2. 错误提示里建议"改用其他视频源 / 在源设置里换接口地址"。
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

_HINT = "可改用其他视频源，或在「源设置」里更换接口地址"


def describe_network_error(error: Exception | None, host: str = "") -> str:
    return _describe_network_error(error, host, hint=_HINT)


class HttpClient(_HttpClient):
    """视频源默认参数（超时 15s、退避 0.9s）的 HTTP 客户端。"""

    def __init__(self, *, headers: dict | None = None, timeout: float = 15.0,
                 attempts: int = 3, backoff: float = 0.9, verify: bool = True):
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
