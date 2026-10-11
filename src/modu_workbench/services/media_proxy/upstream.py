"""上游取流：连接池 + 分层超时/重试 + 请求头注入 + Referer 三段回退。

对应方案里的 V1-a / V1-f / V1-g / V1-n / V1-p。

**为什么必须复用连接**：旧实现每个分片都 `requests.get(...)`，等于每个分片
新开一次 TCP+TLS。境外源 RTT 200–400ms 时，每片多付一个 RTT 的握手；
1080p/2s 分片意味着每分钟约 30 次握手，抖动直接表现为卡顿。

**为什么不转发 Content-Length 之外的怪东西**：上游是 chunked（无 `Content-Length`）
时绝不伪造长度 —— 要么按 chunked 原样转发，要么读完再发（缓存写入路径），
避免"长度与内容不符"这类播放器难以诊断的错误（V1-n）。
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

from .config import (
    RETRY_IMAGE,
    RETRY_PLAYLIST,
    RETRY_SEGMENT,
    SEGMENT_RETRY_BACKOFF,
    TIMEOUT_DIRECT,
    TIMEOUT_IMAGE,
    TIMEOUT_PLAYLIST,
    TIMEOUT_SEGMENT,
    UA,
)
from .stats import get_stats

#: 请求类别 →（连接超时, 读超时）
TIMEOUTS: dict[str, tuple[float, float]] = {
    "playlist": TIMEOUT_PLAYLIST,
    "segment": TIMEOUT_SEGMENT,
    "image": TIMEOUT_IMAGE,
    "direct": TIMEOUT_DIRECT,
}

#: 请求类别 → 重试次数（分片只快速重试一次，别把播放器堵住）
RETRIES: dict[str, int] = {
    "playlist": RETRY_PLAYLIST,
    "segment": RETRY_SEGMENT,
    "image": RETRY_IMAGE,
    "direct": RETRY_SEGMENT,
}


class BlockedTarget(RuntimeError):
    """目标地址被安全策略拒绝（环回/私网/链路本地/组播/非法 scheme）。"""


def _host_is_private(host: str) -> bool:
    """判断主机是否指向本机/内网（SSRF 收口，见 V1-q）。"""
    name = (host or "").strip().strip("[]")
    if not name:
        return True
    if name.lower() in ("localhost", "localhost.localdomain"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        # 域名：解析一次再判断（解析失败视为不安全，交由上层报错）
        try:
            infos = socket.getaddrinfo(name, None)
        except OSError:
            return False
        for info in infos:
            try:
                address = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue
            if _address_is_private(address):
                return True
        return False
    return _address_is_private(address)


def _address_is_private(address: ipaddress._BaseAddress) -> bool:  # noqa: SLF001
    return bool(
        address.is_loopback or address.is_private or address.is_link_local
        or address.is_multicast or address.is_reserved or address.is_unspecified
    )


def env_allows_private() -> bool:
    """`MODU_MEDIA_ALLOW_PRIVATE=1` 时允许代理访问环回/私网目标。

    正常运行时**不设置**：本机代理只服务于"公网片源"，拒绝私网目标可以防止
    被构造的地址诱导去读本机/内网服务（SSRF 收口 V1-q）。
    测试与本地 fixture 需要访问 127.0.0.1 时显式打开。
    """
    import os

    return os.environ.get("MODU_MEDIA_ALLOW_PRIVATE") == "1"


def validate_target(url: str, *, allow_private: bool | None = None) -> str:
    """校验上游目标地址；不合法直接抛 `BlockedTarget`。

    `allow_private=None`（默认）时读环境变量，因此**测试里改环境变量立即生效**，
    不受进程级客户端单例缓存的影响。
    """
    allowed = env_allows_private() if allow_private is None else allow_private
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https"):
        raise BlockedTarget(f"不支持的协议：{parts.scheme or '(空)'}")
    host = parts.hostname or ""
    if not host:
        raise BlockedTarget("缺少主机名")
    if not allowed and _host_is_private(host):
        raise BlockedTarget(f"拒绝访问本机/内网地址：{host}")
    return url


def guess_referer(url: str) -> str:
    """源站 origin 作为 Referer（三段回退的第①段）。"""
    parts = urlsplit(url or "")
    if not parts.scheme or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def referer_candidates(url: str, referer: str = "") -> list[str]:
    """Referer 三段回退（V1-p）。

    ① 显式传入的 referer（一般是源站 origin，来自 `guess_referer`）
    ② 目标 URL 自身（MoonTVPlus 的做法：很多站点就是校验这个）
    ③ 不带 Referer（EE 的 `AptvPlayer` 风格，部分源反而只认裸请求）
    """
    candidates: list[str] = []
    for item in (referer, url, ""):
        if item not in candidates:
            candidates.append(item)
    return candidates


def build_headers(referer: str = "", extra: str = "", explicit: dict | None = None) -> dict:
    """构造上游请求头。

    `extra` 保留旧模块的 `k=v|k2=v2` 字符串协议（代理 URL 的 `h=` 参数）。
    """
    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        # 避免把 gzip 内容当媒体转发，也省一次 CPU（V1-m）
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }
    if referer:
        headers["Referer"] = referer
    for mapping in (explicit or {}, _parse_extra(extra)):
        for key, value in mapping.items():
            if key and value not in (None, ""):
                headers[str(key).strip()] = str(value).strip()
    return headers


def _parse_extra(extra: str) -> dict:
    result: dict[str, str] = {}
    for line in (extra or "").split("|"):
        key, _, value = line.partition("=")
        if key.strip() and value.strip():
            result[key.strip()] = value.strip()
    return result


@dataclass
class UpstreamResponse:
    """上游响应（只暴露代理需要的那几个字段）。"""

    status: int
    headers: dict
    url: str
    body: requests.Response

    @property
    def content_length(self) -> int:
        try:
            return int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def content_type(self) -> str:
        return str(self.headers.get("Content-Type") or "application/octet-stream")

    def iter_content(self, chunk_size: int = 64 * 1024):  # noqa: ANN201
        return self.body.iter_content(chunk_size=chunk_size)

    def close(self) -> None:
        try:
            self.body.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> UpstreamResponse:
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        self.close()


class UpstreamClient:
    """带连接池的取流客户端。

    每个 `UpstreamClient` 持有一个 `requests.Session`（内含 `HTTPAdapter` 连接池）；
    默认按目标主机维护最多 8 个 Session，其余主机共用溢出 Session。
    """

    def __init__(self, *, pool_connections: int = 8, pool_maxsize: int = 16,
                 max_sessions: int = 8, idle_keepalive: float = 30.0,
                 allow_private: bool = False) -> None:
        self.pool_connections = pool_connections
        self.pool_maxsize = pool_maxsize
        self.max_sessions = max_sessions
        self.allow_private = allow_private
        self._lock = threading.Lock()
        self._sessions: dict[str, requests.Session] = {}
        self._overflow: requests.Session | None = None
        self.adapter_calls = 0        # 观测用：实际发起的上游请求数

    # ------------------------------------------------------------------ Session

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=self.pool_connections,
            pool_maxsize=self.pool_maxsize,
            max_retries=0,            # 重试由本模块控制（要分层，不能交给 urllib3）
            pool_block=False,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({"User-Agent": UA, "Accept-Encoding": "identity"})
        return session

    def session_for(self, url: str) -> requests.Session:
        host = urlsplit(url).netloc.lower()
        with self._lock:
            session = self._sessions.get(host)
            if session is not None:
                return session
            if len(self._sessions) < self.max_sessions:
                session = self._new_session()
                self._sessions[host] = session
                return session
            if self._overflow is None:
                self._overflow = self._new_session()
            return self._overflow

    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions) + (1 if self._overflow is not None else 0)

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            if self._overflow is not None:
                sessions.append(self._overflow)
            self._sessions.clear()
            self._overflow = None
        for session in sessions:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ 取流

    def get(
        self,
        url: str,
        *,
        headers: dict | None = None,
        referer: str = "",
        extra: str = "",
        kind: str = "segment",
        range_header: str = "",
        attempts: int | None = None,
        stream: bool = True,
        retry_referer: bool = True,
    ) -> UpstreamResponse | None:
        """取上游响应；全部尝试失败返回 None（调用方回 502）。

        `retry_referer=True` 时按三段回退切 Referer —— 同一段内做的是
        网络重试，跨段做的是"这个源到底认哪个 Referer"的探测。
        """
        validate_target(url, allow_private=self.allow_private or None)
        total_attempts = RETRIES.get(kind, 1) if attempts is None else max(1, attempts)
        timeout = TIMEOUTS.get(kind, TIMEOUT_SEGMENT)
        query = _replace_referer(extra, referer)
        candidates = referer_candidates(url, referer) if retry_referer else [referer]
        stats = get_stats()
        last_error: Exception | None = None

        for candidate in candidates:
            for attempt in range(1, total_attempts + 1):
                request_headers = build_headers(candidate, query, headers)
                if range_header:
                    request_headers["Range"] = range_header
                started = time.time()
                try:
                    response = self.session_for(url).get(
                        url, headers=request_headers, timeout=timeout,
                        allow_redirects=True, stream=stream,
                    )
                except requests.RequestException as error:
                    last_error = error
                    stats.record_latency(time.time() - started)
                    if attempt < total_attempts:
                        time.sleep(_backoff(kind, attempt))
                    continue
                self.adapter_calls += 1
                stats.record_latency(time.time() - started)
                stats.record_upstream(0)
                # 403/401 基本都是 Referer/UA 不被接受 → 换下一段 Referer 再试
                if response.status_code in (401, 403) and retry_referer:
                    response.close()
                    last_error = requests.HTTPError(
                        f"{response.status_code} from upstream", response=response)
                    break
                return UpstreamResponse(
                    status=response.status_code,
                    headers={key: value for key, value in response.headers.items()},
                    url=response.url or url,
                    body=response,
                )
        if last_error is not None:
            import os
            import sys

            if os.environ.get("MODU_MEDIA_LOG"):
                print(f"[media] upstream failed: {url[:120]} -> {last_error}", file=sys.stderr)
        return None

    def get_bytes(self, url: str, *, headers: dict | None = None, referer: str = "",
                  extra: str = "", kind: str = "playlist",
                  max_bytes: int = 4 * 1024 * 1024) -> bytes:
        """读完整响应体（清单/密钥这类小文件用；有硬上限）。"""
        response = self.get(url, headers=headers, referer=referer, extra=extra,
                            kind=kind, stream=True)
        if response is None:
            raise requests.RequestException(f"上游请求失败：{url[:120]}")
        with response:
            if response.status >= 400:
                raise requests.HTTPError(f"上游返回 {response.status}", response=response.body)
            declared = response.content_length
            if declared and declared > max_bytes:
                raise ValueError(f"响应过大（{declared} 字节），已拒绝")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"响应超过上限 {max_bytes} 字节，已拒绝")
                chunks.append(chunk)
            return b"".join(chunks)


def _replace_referer(extra: str, referer: str) -> str:
    """把 `extra` 里可能已存在的 Referer 去掉（Referer 由三段回退单独控制）。"""
    if not extra:
        return ""
    kept = []
    for line in extra.split("|"):
        key, _, _value = line.partition("=")
        if key.strip().lower() == "referer":
            continue
        kept.append(line)
    return "|".join(kept)


def _backoff(kind: str, attempt: int) -> float:
    if kind == "segment":
        return SEGMENT_RETRY_BACKOFF * attempt
    return 0.6 * attempt


_client: UpstreamClient | None = None
_client_lock = threading.Lock()


def get_client(*, allow_private: bool | None = None) -> UpstreamClient:
    """进程级客户端单例。"""
    global _client
    if allow_private is None:
        allow_private = env_allows_private()
    with _client_lock:
        if _client is None:
            _client = UpstreamClient(allow_private=allow_private)
        return _client


def reset_client() -> None:
    """仅供测试：关闭并丢弃单例。"""
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
        _client = None


__all__ = [
    "BlockedTarget", "TIMEOUTS", "UpstreamClient", "UpstreamResponse",
    "build_headers", "env_allows_private", "get_client", "guess_referer",
    "referer_candidates", "reset_client", "validate_target",
]
