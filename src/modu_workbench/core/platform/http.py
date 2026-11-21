"""统一 HTTP 客户端：UA、超时、退避重试与可读的网络错误说明。

各板块（音源 / 视频源 / 图库 AI / 大模型）此前各自实现了一份几乎相同的客户端，
这里合并为唯一实现，板块只通过 `hint` 定制错误提示里的"下一步建议"。

约定：
- 失败统一抛 `SourceError`（RuntimeError 子类），调用方按"可换源/可重试"处理；
- 重试只针对 `requests.RequestException`（连接重置、超时、5xx 等），
  非 2xx 会经 `raise_for_status()` 进入重试路径，最终以可读中文抛出。
"""
from __future__ import annotations

import os
import ssl
import time
from typing import Any
from urllib.parse import urlsplit

import requests

from .logging import debug

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


def ca_bundle() -> str | bool:
    """HTTPS 校验用的 CA 路径：优先 certifi，缺失时回退系统证书。

    ## 为什么要在运行时找一遍

    打包漏掉 `certifi/cacert.pem` 时，`requests` 会抛
    `Could not find a suitable TLS CA certificate bundle, invalid path: ...`，
    结果是**所有 https 源一起报"不可用"**（用户看到的是"全部数据源失效"，
    很难联想到是安装包缺文件）。这里先找一份能用的 bundle；实在没有才交给
    requests 报错（错误文案在 `describe_network_error` 里单独翻译成"安装包不完整"）。
    """
    candidates: list[str] = []
    try:
        import certifi

        candidates.append(certifi.where())
    except Exception as error:  # noqa: BLE001  certifi 不在（极简环境）时继续找系统证书
        # 高频路径（每次建 HttpClient 都会走），所以用 debug 而不是 swallow：
        # 默认日志级别下不该因为"没装 certifi"刷警告。
        debug("加载 certifi 证书", error)
    try:
        paths = ssl.get_default_verify_paths()
        candidates.extend([paths.cafile or "", paths.openssl_cafile or ""])
    except Exception as error:  # noqa: BLE001  某些 OpenSSL 构建不提供默认路径
        debug("查询系统证书路径", error)
    candidates.append(os.environ.get("REQUESTS_CA_BUNDLE", ""))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return True


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
    if "ca certificate bundle" in lowered or "tls ca" in lowered:
        return (
            f"HTTPS 证书链缺失（本机安装包不完整，缺 certifi/cacert.pem）——"
            f"所有源都会一起失败，请重新安装完整安装包{tail}"
        )
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


def parse_proxy_server(server: str) -> dict[str, str] | None:
    """把 Windows 的 `ProxyServer` 串解析成 requests 的 `proxies` 字典。

    两种格式都要支持：

    - `127.0.0.1:7890`（http/https 共用一个端口）；
    - `http=127.0.0.1:7890;https=127.0.0.1:7890`（分协议）。
    """
    text = (server or "").strip()
    if not text:
        return None

    def _with_scheme(value: str, scheme: str = "http") -> str:
        value = value.strip()
        if "://" in value:
            return value
        return f"{scheme}://{value}"

    if "=" not in text:
        url = _with_scheme(text)
        return {"http": url, "https": url}
    mapping: dict[str, str] = {}
    for part in text.split(";"):
        if "=" not in part:
            continue
        scheme, value = part.split("=", 1)
        scheme = scheme.strip().lower()
        # `socks=` / `socks5=` 要保留协议，否则 requests 会把 socks 代理当 http 用
        default = "socks5" if scheme.startswith("socks") else "http"
        mapping[scheme] = _with_scheme(value, default)
    return mapping or None


def system_proxy() -> dict[str, str] | None:
    """读 Windows「系统代理」（设置 → 网络 → 代理）。

    ## 为什么必须读它

    `requests` 只认 `HTTP_PROXY/HTTPS_PROXY` 环境变量，**不读 Windows 系统代理**。
    而国内用户几乎都是用 Clash/v2ray 开「系统代理」，于是应用里所有源都连不上，
    表现就是"全部数据源不可用"——但浏览器却一切正常。这里在没设环境变量时
    回退到系统代理，并尊重 `ProxyEnable=0`（用户明确关了就不代理）。
    """
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0] or 0)
            if not enabled:
                return None
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "")
    except (OSError, ValueError):
        return None
    return parse_proxy_server(server)


def env_proxy_configured() -> bool:
    """环境变量里有没有代理配置（有的话交给 requests 自己处理）。"""
    return any(
        os.environ.get(name)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    )


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
        # 系统代理只在"没设环境变量代理"时兜底，避免盖掉用户显式配置
        self.proxies = None if env_proxy_configured() else system_proxy()

    # ---------- 基础请求 ----------

    def _proxies_for(self, url: str) -> dict[str, str] | None:
        """本机地址不走代理；其余按环境变量/系统代理处理。"""
        if host_of(url) in ("127.0.0.1", "localhost", "::1"):
            return None
        return self.proxies

    def request(self, method: str, url: str, *, headers: dict | None = None,
                timeout: float | None = None, referer: str = "", **kwargs) -> requests.Response:
        merged = {**self.headers, **(headers or {})}
        if referer:
            merged.setdefault("Referer", referer)
        last: Exception | None = None
        verify = ca_bundle() if self.verify is True else self.verify
        # 本机地址绝不走代理：媒体代理、测试用的本地 HTTP 服务都在 127.0.0.1 上，
        # 走系统代理会把它们也转发出去（等于自己把自己绕死）。
        proxies = self._proxies_for(url)
        for attempt in range(1, self.attempts + 1):
            try:
                response = requests.request(
                    method, url, headers=merged, timeout=timeout or self.timeout,
                    verify=verify, proxies=proxies, **kwargs,
                )
                response.raise_for_status()
                return response
            except (requests.RequestException, OSError) as error:
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
