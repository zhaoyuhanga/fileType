"""音源基类与元信息：所有音乐来源都实现同一套接口。

新增音源只需：
1. 继承 `MusicSource`，实现 `search()`（返回 `RemoteTrack` 列表）与 `download_url()`；
2. 在 `providers/__init__.py` 的 `build_default_providers()` 里注册。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ..models import RemoteTrack
from .http import HttpClient, SourceError

# 音源能力类型
KIND_FULL = "full"        # 完整曲目（免费可下载）
KIND_PREVIEW = "preview"  # 试听片段
KIND_CC = "cc"            # 自由授权（CC / 公共领域）完整曲目
KIND_DIRECT = "direct"    # 直链
KIND_EXPERIMENTAL = "experimental"

KIND_LABELS = {
    KIND_FULL: "完整曲目",
    KIND_PREVIEW: "试听片段",
    KIND_CC: "自由授权",
    KIND_DIRECT: "直链",
    KIND_EXPERIMENTAL: "实验性",
}


@dataclass(frozen=True)
class SourceInfo:
    key: str
    label: str
    note: str
    kind: str = KIND_FULL
    needs_key: bool = False
    homepage: str = ""

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


@dataclass
class SourceHealth:
    """音源健康度：连续失败自动降级（熔断），成功即恢复。"""

    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    degraded_until: float = 0.0
    last_error: str = ""
    last_ok_at: float = 0.0

    def is_degraded(self, now: float) -> bool:
        return self.degraded_until > now

    def as_text(self, now: float) -> str:
        if self.is_degraded(now):
            return "临时降级"
        if self.consecutive_failures:
            return "不稳定"
        return "可用"


@dataclass
class ResolvedAudio:
    """下载地址解析结果（可能来自跨源兜底）。"""

    url: str
    source: str
    track: RemoteTrack
    switched_from: str = ""
    note: str = ""
    lyrics: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def switched(self) -> bool:
        return bool(self.switched_from)


class MusicSource:
    """音源基类：默认实现直链下载，子类按需覆盖。"""

    info: SourceInfo

    def __init__(self, *, http: HttpClient | None = None, key: str = ""):
        self.http = http or HttpClient(headers=self.default_headers())
        self._key = key or ""

    # ---------- 元信息 ----------

    @property
    def key(self) -> str:
        return self.info.key

    @property
    def label(self) -> str:
        return self.info.label

    def default_headers(self) -> dict:
        return {}

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def set_credential(self, value: str) -> None:
        """可选：需要密钥的音源在此接收设置里的值。"""
        self._key = (value or "").strip()

    @property
    def credential(self) -> str:
        return self._key

    # ---------- 能力 ----------

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        raise NotImplementedError

    def download_url(self, track: RemoteTrack) -> str:
        """返回可直接下载的音频地址。"""
        if not track.url:
            raise SourceError("该结果没有可用的下载地址")
        return track.url

    def lyrics(self, track: RemoteTrack) -> str:
        return ""

    # ---------- 工具 ----------

    def require_available(self) -> None:
        if not self.available():
            raise SourceError(self.unavailable_reason() or f"{self.label} 当前不可用")
