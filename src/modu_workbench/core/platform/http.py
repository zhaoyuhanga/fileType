"""统一 HTTP 客户端：UA、超时、退避重试与可读的网络错误说明。

各板块（音源 / 视频源 / 图库 AI / 大模型）此前各自实现了一份几乎相同的客户端，
这里合并为唯一实现，板块只通过 `hint` 定制错误提示里的"下一步建议"。

约定：
- 失败统一抛 `SourceError`（RuntimeError 子类），调用方按"可换源/可重试"处理；
- 重试只针对 `requests.RequestException`（连接重置、超时、5xx 等），
  非 2xx 会经 `raise_for_status()` 进入重试路径，最终以可读中文抛出。
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_ACCEPT = "application/json, text/plain, */*"

# 常见媒体地址后缀：用来判断一个地址是「媒体文件本身」还是「HTML 播放页」
MEDIA_URL_SUFFIXES = (
    ".m3u8", ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts",
    ".m4v", ".mpg", ".mpeg", ".wmv", ".mpd",
)


class SourceError(RuntimeError):
    """数据源不可用、抓取失败或内容受限（音源/视频源/模型接口通用）。"""


def host_of(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def origin_of(url: str) -> str:
    """返回 scheme://host[:port]，用于拼接 Referer。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}/"


def guess_referer(url: str) -> str:
    """为直链推断 Referer（站点根地址最稳妥，带完整路径反而容易被 403）。"""
    return origin_of(url)


def looks_like_media_url(url: str) -> bool:
    """地址是否本身就是媒体文件（只看路径后缀，忽略查询串）。

    采集接口的分集地址有两种形态：直接是 `…/index.m3u8`，或者是带播放器的 HTML 页
    （如 `…/share/<hash>`）。后者必须先解析，否则会被当成视频去解码。
    """
    path = urlsplit(url or "").path.lower()
    return path.endswith(MEDIA_URL_SUFFIXES)


def describe_network_error(error: Exception | None, host: str = "", *, hint: str = "") -> str:
    """把网络异常翻译成用户看得懂的说明（DNS/连接/超时/被拦截）。

    `hint` 是板块自带的一句"下一步建议"，例如音源提示换音源、视频源提示换源。
    """
    text = str(error or "")
    lowered = text.lower()
    name = host or "目标站点"
    tail = f"。{hint}" if hint else ""
    if "getaddrinfo" in lowered or "name or service not known" in lowered or "nodename" in lowered:
        return f"无法解析 {name} 的域名（DNS/网络问题）{tail}"
    if "certificate" in lowered or "ssl" in lowered:
        return f"{name} 的 HTTPS 证书校验失败（可能被网络中间设备拦截）{tail}"
    if "timed out" in lowered or "timeout" in lowered:
        return f"连接 {name} 超时，请稍后重试{tail}"
    if "403" in lowered or "forbidden" in lowered:
        return f"{name} 拒绝访问（403）：对方可能已限制外链{tail}"
    if "404" in lowered:
        return f"{name} 接口不存在（404）：地址可能已失效{tail}"
    if "connection" in lowered or "max retries" in lowered:
        return f"无法连接 {name}（连接被重置/拒绝）{tail}"
    return f"{name} 请求失败：{text[:200]}"


class HttpClient:
    """带退避重试的轻量 HTTP 客户端（每个源一份，便于注入测试替身）。"""

    def __init__(self, *, headers: dict | None = None, timeout: float = 15.0,
                 attempts: int = 3, backoff: float = 0.9, verify: bool = True,
                 accept: str = DEFAULT_ACCEPT, hint: str = ""):
        self.headers = {"User-Agent": DEFAULT_UA, "Accept": accept, **(headers or {})}
        self.timeout = timeout
        self.attempts = max(1, attempts)
        self.backoff = backoff
        self.verify = verify
        self.hint = hint

    # ---------- 基础请求 ----------

    def request(self, method: str, url: str, *, headers: dict | None = None,
                timeout: float | None = None, referer: str = "", **kwargs) -> requests.Response:
        merged = {**self.headers, **(headers or {})}
        if referer:
            merged.setdefault("Referer", referer)
        last: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                response = requests.request(
                    method, url, headers=merged, timeout=timeout or self.timeout,
                    verify=self.verify, **kwargs,
                )
                response.raise_for_status()
                return response
            except requests.RequestException as error:
                last = error
                if attempt < self.attempts:
                    time.sleep(self.backoff * attempt)
        raise SourceError(describe_network_error(last, host_of(url), hint=self.hint))

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)

    # ---------- 便捷方法 ----------

    def get_json(self, url: str, **kwargs) -> Any:
        response = self.get(url, **kwargs)
        try:
            return response.json()
        except ValueError as error:
            raise SourceError(
                f"{host_of(url)} 返回内容不是有效 JSON（可能是站点改版或被拦截）"
            ) from error

    def get_text(self, url: str, **kwargs) -> str:
        return self.get(url, **kwargs).text
