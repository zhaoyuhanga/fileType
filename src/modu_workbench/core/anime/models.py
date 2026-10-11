"""墨软动漫：数据模型（规则 / 番剧 / 线路 / 剧集 / 进度 / 弹幕）。

命名与字段语义刻意与 `core.video.models` 对齐（RemoteVideo/Episode/Quality），
因此播放器、下载器、代理这些共享层不需要区分"这是影视还是动漫"。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

#: 规则模式（与 Kazumi 一一对应）
SEARCH_MODES = ("xpath", "api")
CHAPTER_MODES = ("xpath", "api")
#: 规则类型（本期只实现 anime）
RULE_TYPES = ("anime", "comic", "novel")

#: 内置规则的 API 等级（Kazumi 客户端当前也是 8）
SUPPORTED_API_LEVEL = 8

#: 线路/剧集名里提取集数的多套正则（对齐 Kazumi 的容错做法）
EPISODE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"第\s*(\d{1,4})\s*[集话話期]"),
    re.compile(r"\bEP?\s*(\d{1,4})\b", re.IGNORECASE),
    re.compile(r"\bE\s*(\d{1,4})\b", re.IGNORECASE),
    re.compile(r"【\s*(\d{1,4})\s*】"),
    re.compile(r"^\s*(\d{1,4})\s*[\.、,，]"),
    re.compile(r"\[\s*(\d{1,4})\s*\]"),
)

_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[\s\-_·・:：,，.。!！?？~～/\\|()（）\[\]【】\"'`]+")
_REPEAT = re.compile(r"(.)\1{2,}")


def strip_html(text: str) -> str:
    """站点简介/标题里常带 HTML 标签，展示与匹配前先清洗。"""
    return _HTML_TAG.sub("", (text or "").replace("&nbsp;", " ")).strip()


def normalize_title(title: str) -> str:
    """标题归一化（用于跨规则合并与弹幕匹配）。

    规则：全角转半角 → 去掉标点与空白 → 连续 3 个以上相同字符合并为 3 个 → 小写。
    与 Kazumi 的"归一化去重"思路一致，但保守（不做翻译/别名映射）。
    """
    text = unicodedata.normalize("NFKC", strip_html(title or ""))
    text = _PUNCT.sub("", text)
    text = _REPEAT.sub(lambda match: match.group(1) * 3, text)
    return text.casefold()


def parse_episode_number(name: str, *extra_patterns: str) -> int:
    """从剧集名里提取集数；提不到返回 0（调用方用序号兜底）。

    `extra_patterns` 来自规则的 `episodeParse` —— 站点自定义格式优先。
    """
    text = strip_html(name or "")
    if not text:
        return 0
    for pattern in extra_patterns:
        if not pattern:
            continue
        try:
            match = re.search(pattern, text)
        except re.error:
            continue
        if match and match.groups():
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue
    for pattern in EPISODE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue
    return 0


@dataclass
class AnimeRule:
    """一条采集规则（字段与 Kazumi 规则一一对应 + 我们的扩展）。

    只保留"解析后的关键字段"，原始 JSON 存在 `raw` 里 —— 这样既能跑引擎，
    也能原样导出（用户规则文件是唯一真相）。
    """

    name: str = ""
    version: str = ""
    api: str = str(SUPPORTED_API_LEVEL)
    type: str = "anime"
    base_url: str = ""
    key: str = ""                       # 稳定标识（用户规则=文件名，内置=builtin:name）
    path: str = ""                       # 规则文件路径（仅用于展示）
    builtin: bool = False
    source_url: str = ""                 # 订阅来源
    deprecated: bool = False
    raw: dict = field(default_factory=dict)

    # 播放/请求相关
    referer: str = ""
    user_agent: str = ""
    search_mode: str = "xpath"
    chapter_mode: str = "xpath"
    use_post: bool = False
    use_legacy_parser: bool = False
    ad_blocker: bool = False
    use_webview: bool = False            # 只读兼容：为 True 时提示"可能解析失败"
    timeout: float = 20.0
    retries: int = 2
    qps: float = 2.0
    episode_parse: str = ""

    # XPath 模式
    search_url: str = ""
    search_list: str = ""
    search_name: str = ""
    search_result: str = ""
    chapter_roads: str = ""
    chapter_result: str = ""

    # API 模式
    search_api: dict = field(default_factory=dict)
    chapter_api: dict = field(default_factory=dict)

    # 扩展
    catalogue: dict = field(default_factory=dict)
    schedule: dict = field(default_factory=dict)

    @property
    def api_level(self) -> int:
        try:
            return int(str(self.api).strip() or SUPPORTED_API_LEVEL)
        except (TypeError, ValueError):
            return SUPPORTED_API_LEVEL

    @property
    def supported(self) -> bool:
        """API 等级是否被本客户端支持（更高的规则**加载但灰显**，不静默失败）。"""
        return self.api_level <= SUPPORTED_API_LEVEL

    @property
    def kind(self) -> str:
        return self.search_mode if self.search_mode in SEARCH_MODES else "xpath"

    @property
    def label(self) -> str:
        return self.name or self.key or "未命名规则"

    def describe(self) -> str:
        parts = [self.label, f"v{self.version or '-'}", self.kind.upper()]
        if self.builtin:
            parts.append("内置")
        if self.deprecated:
            parts.append("已退役")
        if not self.supported:
            parts.append(f"需要更新的客户端（api {self.api_level}）")
        if self.use_webview:
            parts.append("依赖网页渲染，可能解析失败")
        return " · ".join(parts)


@dataclass
class Road:
    """一条线路（同一作品的不同播放源/播放方式）。"""

    index: int = 0
    name: str = ""
    episodes: list[AnimeEpisode] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.name or f"线路 {self.index + 1}"

    @property
    def count(self) -> int:
        return len(self.episodes)


@dataclass
class AnimeEpisode:
    """一集。"""

    index: int = 0
    name: str = ""
    url: str = ""                        # 播放页或直接媒体地址
    road_index: int = 0
    road_name: str = ""
    episode_id: int = 0                  # 弹弹play 的 episodeId（匹配到才有）
    number: int = 0                      # 从名字里解析出的集数（0 = 未解析出）

    @property
    def key(self) -> str:
        """稳定标识（进度/弹幕缓存用）：线路 + 集数 + 名字。"""
        return f"{self.road_index}:{self.number or self.index}:{normalize_title(self.name)}"

    @property
    def label(self) -> str:
        return self.name or f"第 {self.number or self.index + 1} 集"


@dataclass
class AnimeSeries:
    """一部番剧（某条规则下的一份来源记录）。"""

    id: int = 0
    rule_key: str = ""
    remote_id: str = ""
    title: str = ""
    cover: str = ""
    year: str = ""
    summary: str = ""
    bangumi_id: int = 0
    total_episodes: int = 0
    latest_episode: str = ""
    air_weekday: int = -1
    score: float = 0.0
    created_at: int = 0
    updated_at: int = 0

    @property
    def title_norm(self) -> str:
        return normalize_title(self.title)

    @property
    def key(self) -> str:
        return f"{self.rule_key}:{self.remote_id}"


@dataclass
class SeriesDetail:
    """详情页拉取结果：元数据 + 线路/剧集。"""

    series: AnimeSeries
    roads: list[Road] = field(default_factory=list)
    note: str = ""

    @property
    def episode_count(self) -> int:
        return sum(road.count for road in self.roads)

    def road(self, index: int) -> Road | None:
        for item in self.roads:
            if item.index == index:
                return item
        return None


@dataclass
class WatchProgress:
    """观看进度（按集）。"""

    series_id: int = 0
    episode_key: str = ""
    episode_name: str = ""
    episode_index: int = 0
    position_ms: int = 0
    duration_ms: int = 0
    watched: bool = False
    updated_at: int = 0

    @property
    def percent(self) -> int:
        if self.duration_ms <= 0:
            return 0
        return max(0, min(100, int(self.position_ms * 100 / self.duration_ms)))

    @property
    def finished(self) -> bool:
        return self.watched or (self.duration_ms > 0
                                and self.position_ms >= self.duration_ms * 0.95)


@dataclass
class SniffedMedia:
    """嗅探结果：真实媒体地址 + 命中策略（试跑面板要显示策略）。"""

    url: str = ""
    strategy: str = ""
    referer: str = ""
    note: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.url)


__all__ = [
    "CHAPTER_MODES", "EPISODE_PATTERNS", "RULE_TYPES", "SEARCH_MODES",
    "SUPPORTED_API_LEVEL", "AnimeEpisode", "AnimeRule", "AnimeSeries", "Road",
    "SeriesDetail", "SniffedMedia", "WatchProgress", "normalize_title",
    "parse_episode_number", "strip_html",
]
