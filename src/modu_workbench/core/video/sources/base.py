"""影视源基类与元信息：所有视频来源都实现同一套接口。

新增一个源只需三步（不动任何既有业务代码）：
1. 继承 `VideoSource`，实现 `search()`；按需实现 `detail()` / `play_url()`；
2. 在 `providers/__init__.py` 的 `build_default_providers()` 里注册一个实例；
3. 完成 —— 注册表自动获得「聚合搜索 / 熔断降级 / 换源重试 / 配置持久化」能力。

约定：
- `search()` 返回 `RemoteVideo` 列表（episodes 可以为空，先给结果，详情再拉）；
- `detail()` 补齐 `episodes`（分集 + 画质）；
- `play_url()` 返回最终可播放地址（默认直接取剧集 url）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ..models import Episode, Quality, RemoteVideo
from .http import HttpClient, SourceError

# 视频源能力类型
KIND_FULL = "full"                # 完整内容（免费可直接播放/下载）
KIND_PREVIEW = "preview"          # 仅预告/试看片段
KIND_CC = "cc"                    # 自由授权（公共领域 / CC，可自由下载）
KIND_DIRECT = "direct"            # 直链（用户自己提供的地址）
KIND_CUSTOM = "custom"            # 用户自定义（自行添加的采集接口）
KIND_EXPERIMENTAL = "experimental"

KIND_LABELS = {
    KIND_FULL: "完整内容",
    KIND_PREVIEW: "预览片段",
    KIND_CC: "自由授权",
    KIND_DIRECT: "直链",
    KIND_CUSTOM: "自定义",
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
    # 是否支持把内容下载到本地（预览片段源/部分受限源为 False）
    downloadable: bool = True
    # 是否可在设置里增删改（用户自定义源）
    editable: bool = False

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


@dataclass
class SourceHealth:
    """视频源健康度：连续失败自动降级（熔断），成功即恢复。"""

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
class ResolvedPlay:
    """播放地址解析结果（可能来自跨源兜底）。"""

    url: str
    source: str
    video: RemoteVideo
    episode: Episode | None = None
    quality: Quality | None = None
    switched_from: str = ""
    note: str = ""
    is_hls: bool = False
    referer: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def switched(self) -> bool:
        return bool(self.switched_from)

    @property
    def quality_label(self) -> str:
        return self.quality.label if self.quality else ""

    @property
    def episode_label(self) -> str:
        return self.episode.name if self.episode else ""


class VideoSource:
    """视频源基类：默认实现直链播放，子类按需覆盖。"""

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
        """可选：需要密钥/自定义地址的源在此接收设置里的值。"""
        self._key = (value or "").strip()

    def reset_credential(self) -> None:
        """恢复默认凭据/接口地址（内置源在自己的类里重写）。"""
        self.set_credential("")

    @property
    def credential(self) -> str:
        return self._key

    # ---------- 能力 ----------

    def search(self, keyword: str, kind: str = "all", limit: int = 30,
               page: int = 1) -> List[RemoteVideo]:
        raise NotImplementedError

    def detail(self, video: RemoteVideo) -> RemoteVideo:
        """补齐分集与画质；默认原样返回（源本身已在搜索结果里带上剧集）。"""
        return video

    def play_url(self, video: RemoteVideo, episode: Episode,
                 quality: Quality | None = None) -> str:
        """返回最终播放/下载地址。"""
        if quality is not None and quality.url:
            return quality.url
        if episode is not None and episode.url:
            return episode.url
        raise SourceError("该条目没有可用的播放地址")

    def supports_download(self) -> bool:
        return bool(self.info.downloadable)

    def supports_keyword_search(self) -> bool:
        """是否参与「关键词聚合搜索」。

        直链类源只能凭一个地址检索，参与关键词聚合搜索只会每次都报一个
        「请输入 http(s):// 开头的地址」，因此由子类声明为 False 并在聚合搜索里跳过
        （用户直接粘贴地址时仍会走它，见 `VideoRegistry.search_all`）。
        """
        return True

    def download_headers(self, url: str = "") -> dict:
        """下载分片时需要附带的请求头（Referer 等）。"""
        from .http import guess_referer

        referer = guess_referer(url) if url else ""
        return {"Referer": referer} if referer else {}

    # ---------- 工具 ----------

    def require_available(self) -> None:
        if not self.available():
            raise SourceError(self.unavailable_reason() or f"{self.label} 当前不可用")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} key={self.key!r}>"
