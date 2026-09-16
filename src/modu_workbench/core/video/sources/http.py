"""影视源 HTTP 客户端：统一 UA/Referer、超时、退避重试与可读的网络错误说明。

与 `core.music.sources.http` 同构，但按视频站点的实际情况做了两点针对性增强：
1. `referer` 参数：m3u8/分片普遍校验 Referer，解析与下载时必须带上；
2. 默认更多次退避重试（视频源多为第三方采集站，抖动比音乐源更频繁）。
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

_DEFAULT_ACCEPT = "application/json, text/plain, */*"

# 常见媒体地址后缀：用来判断一个地址是「媒体文件本身」还是「HTML 播放页」
MEDIA_URL_SUFFIXES = (
    ".m3u8", ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts",
    ".m4v", ".mpg", ".mpeg", ".wmv", ".mpd",
)


class SourceError(RuntimeError):
    """视频源不可用、抓取失败或内容受限。"""


def looks_like_media_url(url: str) -> bool:
    """地址是否本身就是媒体文件（只看路径后缀，忽略查询串）。

    采集接口给的分集地址有两种形态：直接是 `…/index.m3u8`，
    或者是带播放器的 HTML 页（如 `…/share/<hash>`）。后者必须先解析，
    否则会被当成视频去解码 —— 表现就是「播放和下载都失败」。
    """
    path = urlsplit(url or "").path.lower()
    return path.endswith(MEDIA_URL_SUFFIXES)


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
    """为直链推断 Referer。

    视频站的 m3u8/分片普遍校验 Referer，同源根地址是最稳妥的取值
    （带完整路径反而容易被 403）。拿不到 host 时返回空串，由调用方决定是否省略。
    """
    return origin_of(url)


def describe_network_error(error: Exception | None, host: str = "") -> str:
    """把网络异常翻译成用户看得懂的说明（DNS/连接/超时/被拦截）。"""
    text = str(error or "")
    lowered = text.lower()
    name = host or "目标站点"
    if "getaddrinfo" in lowered or "name or service not known" in lowered or "nodename" in lowered:
        return (
            f"无法解析 {name} 的域名（DNS/网络问题）。"
            "可在「源设置」里改用其他视频源，或添加你自己的采集接口。"
        )
    if "certificate" in lowered or "ssl" in lowered:
        return f"{name} 的 HTTPS 证书校验失败（可能被网络中间设备拦截），可改用其他视频源。"
    if "timed out" in lowered or "timeout" in lowered:
        return f"连接 {name} 超时，请稍后重试或改用其他视频源。"
    if "connection" in lowered or "max retries" in lowered:
        return f"无法连接 {name}（连接被重置/拒绝），请检查网络或改用其他视频源。"
    if "403" in lowered or "forbidden" in lowered:
        return f"{name} 拒绝访问（403）：该源可能已限制外链，请改用其他视频源。"
    if "404" in lowered:
        return f"{name} 接口不存在（404）：该源地址可能已失效，请在「源设置」中更换。"
    return f"{name} 请求失败：{text[:200]}"


class HttpClient:
    """带退避重试的轻量 HTTP 客户端（每个视频源一份，便于注入测试替身）。"""

    def __init__(self, *, headers: dict | None = None, timeout: float = 15.0,
                 attempts: int = 3, backoff: float = 0.9, verify: bool = True):
        self.headers = {"User-Agent": DEFAULT_UA, "Accept": _DEFAULT_ACCEPT, **(headers or {})}
        self.timeout = timeout
        self.attempts = max(1, attempts)
        self.backoff = backoff
        self.verify = verify

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
        raise SourceError(describe_network_error(last, host_of(url)))

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
