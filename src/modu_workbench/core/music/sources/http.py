"""音源 HTTP 客户端：统一 UA/Referer、超时、重试与可读的网络错误说明。"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class SourceError(RuntimeError):
    """音源不可用、抓取失败或曲目受限。"""


def host_of(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def describe_network_error(error: Exception | None, host: str = "") -> str:
    """把网络异常翻译成用户看得懂的说明（DNS/连接/超时/被拦截）。"""
    text = str(error or "")
    lowered = text.lower()
    name = host or "目标站点"
    if "getaddrinfo" in lowered or "name or service not known" in lowered or "nodename" in lowered:
        return (
            f"无法解析 {name} 的域名（DNS/网络问题）。"
            "可改用其他音源（酷我 / iTunes / Audius / Internet Archive / 直链）。"
        )
    if "certificate" in lowered or "ssl" in lowered:
        return f"{name} 的 HTTPS 证书校验失败（可能被网络中间设备拦截），可改用其他音源。"
    if "timed out" in lowered or "timeout" in lowered:
        return f"连接 {name} 超时，请稍后重试或改用其他音源。"
    if "connection" in lowered or "max retries" in lowered:
        return f"无法连接 {name}（连接被重置/拒绝），请检查网络或改用其他音源。"
    return f"{name} 请求失败：{text[:200]}"


class HttpClient:
    """带退避重试的轻量 HTTP 客户端（每个音源一份，便于注入测试替身）。"""

    def __init__(self, *, headers: dict | None = None, timeout: float = 12.0,
                 attempts: int = 3, backoff: float = 0.8, verify: bool = True):
        self.headers = {"User-Agent": DEFAULT_UA, **(headers or {})}
        self.timeout = timeout
        self.attempts = max(1, attempts)
        self.backoff = backoff
        self.verify = verify

    # ---------- 基础请求 ----------

    def request(self, method: str, url: str, *, headers: dict | None = None,
                timeout: float | None = None, **kwargs) -> requests.Response:
        merged = {**self.headers, **(headers or {})}
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
            raise SourceError(f"{host_of(url)} 返回内容不是有效 JSON") from error

    def get_text(self, url: str, **kwargs) -> str:
        return self.get(url, **kwargs).text
