"""墨软影视：数据模型（画质 / 剧集 / 本地视频 / 在线结果 / 分类 / 历史）。

命名与字段语义刻意与 `core.music.models` 对齐（Track/RemoteTrack/Playlist/HistoryEntry），
便于两个板块共用交互惯例，同时保留影视特有的「剧集 / 画质 / 线路」概念。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 支持的本地视频扩展名（本地库与导入共用）
VIDEO_EXTENSIONS = (
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".m4v", ".ts", ".mpg", ".mpeg", ".rmvb",
)

# 视频转换目标（与 core/convert/media_io.VIDEO_TARGETS 对齐）
VIDEO_TARGETS = ("mp4", "mkv", "mov", "avi", "webm", "flv", "ts", "mp3", "m4a", "wav")

# 内容类型（影视板块的一级分类维度）
MEDIA_KINDS = ("movie", "tv", "anime", "variety", "doc", "other")
MEDIA_KIND_LABELS = {
    "movie": "电影",
    "tv": "电视剧",
    "anime": "动漫",
    "variety": "综艺",
    "doc": "纪录片",
    "other": "其他",
}

# 播放模式（与乐库保持一致）
PLAY_MODES = ("order", "loop-all", "loop-one", "shuffle")
PLAY_MODE_LABELS = {
    "order": "顺序播放",
    "loop-all": "列表循环",
    "loop-one": "单集循环",
    "shuffle": "随机播放",
}

# 站点内容类型关键词 → 统一类型。顺序敏感：动漫/纪录片要先于「剧」判断。
_KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("doc", ("纪录片", "记录片", "纪实", "documentary")),
    ("anime", ("动漫", "动画", "番剧", "国漫", "日漫", "anime", "cartoon")),
    ("variety", ("综艺", "真人秀", "脱口秀", "variety")),
    ("tv", ("电视剧", "连续剧", "国产剧", "港剧", "台剧", "日剧", "韩剧", "美剧", "欧美剧",
            "海外剧", "泰剧", "短剧", "电视", "series", "tv")),
    ("movie", ("电影", "影片", "片", "movie", "film")),
)

_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')
_TAG_SPLIT = re.compile(r"[,，/|、;；]+")
_HTML_TAG = re.compile(r"<[^>]+>")

# 收藏集合名（与 core.music.storage.FAVORITE_NAME 保持一致）
FAVORITE_NAME = "我的收藏"


def safe_filename(name: str, fallback: str = "video") -> str:
    """把片名转换为合法文件名（去非法字符、合并连续下划线、限长）。"""
    cleaned = _ILLEGAL.sub("_", (name or "").strip())
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return (cleaned or fallback)[:120]


def strip_html(text: str) -> str:
    """站点简介里常带 HTML 标签，展示前先清洗。"""
    return _HTML_TAG.sub("", (text or "").replace("&nbsp;", " ")).strip()


def split_tags(value: str) -> list[str]:
    """把「科幻,冒险/灾难」这类多分隔符字段切成标签列表。"""
    if not value:
        return []
    parts = [item.strip() for item in _TAG_SPLIT.split(value)]
    return [item for item in parts if item]


def classify_kind(*texts: str) -> str:
    """按站点给定的类型名/分类文本推断统一内容类型（电影/电视剧/动漫…）。"""
    haystack = " ".join(text for text in texts if text).lower()
    if not haystack:
        return "other"
    for kind, keywords in _KIND_RULES:
        if any(keyword.lower() in haystack for keyword in keywords):
            return kind
    return "other"


def normalize_kind(kind: str, *fallback_texts: str) -> str:
    """规整类型值：已是合法枚举则直接用，否则按文本推断。"""
    value = (kind or "").strip().lower()
    if value in MEDIA_KINDS:
        return value
    return classify_kind(value, *fallback_texts)


def kind_label(kind: str) -> str:
    return MEDIA_KIND_LABELS.get(kind, MEDIA_KIND_LABELS["other"])


def quality_rank(label: str) -> int:
    """画质排序权重：数值越大越清晰（4K > 1080P > 720P …）。"""
    text = (label or "").lower()
    for keyword, rank in (
        ("4k", 400), ("2160", 400), ("2k", 300), ("1440", 300),
        ("1080", 200), ("720", 150), ("540", 120), ("480", 90), ("360", 60),
    ):
        if keyword in text:
            return rank
    if "高清" in text or "hd" in text:
        return 200
    if "标清" in text or "sd" in text:
        return 90
    if "超清" in text or "蓝光" in text or "bluray" in text:
        return 300
    return 10


@dataclass
class Quality:
    """一个可选清晰度：label 给用户看，url 是实际播放地址。"""

    label: str
    url: str = ""
    height: int = 0
    bandwidth: int = 0
    source: str = ""

    @property
    def rank(self) -> int:
        return self.height or quality_rank(self.label)

    def to_json(self) -> dict:
        return {
            "label": self.label, "url": self.url, "height": self.height,
            "bandwidth": self.bandwidth, "source": self.source,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Quality":
        return cls(
            label=str(data.get("label", "")),
            url=str(data.get("url", "")),
            height=int(data.get("height") or 0),
            bandwidth=int(data.get("bandwidth") or 0),
            source=str(data.get("source", "")),
        )


@dataclass
class Episode:
    """一集（或一个播放线路内的一个分集）。

    name 形如「第01集」/「正片」；route 是所属线路（苹果 CMS 的播放源名称）。
    """

    name: str
    url: str
    index: int = 0
    route: str = ""
    qualities: list[Quality] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "name": self.name, "url": self.url, "index": self.index, "route": self.route,
            "qualities": [q.to_json() for q in self.qualities],
        }

    @classmethod
    def from_json(cls, data: dict) -> "Episode":
        return cls(
            name=str(data.get("name", "")),
            url=str(data.get("url", "")),
            index=int(data.get("index") or 0),
            route=str(data.get("route", "")),
            qualities=[Quality.from_json(item) for item in (data.get("qualities") or [])],
        )


@dataclass
class Video:
    """本地视频（videos 表一行）；在线条目入库时 file_path 为空。"""

    id: int = 0
    file_path: str = ""
    title: str = ""
    kind: str = "other"
    series_key: str = ""
    season: int = 0
    episode_index: int = 0
    episode_label: str = ""
    year: str = ""
    region: str = ""
    category: str = ""
    tags: str = ""
    actors: str = ""
    director: str = ""
    description: str = ""
    cover_path: str = ""
    duration_ms: int = 0
    size_bytes: int = 0
    format: str = ""
    source: str = ""
    remote_id: str = ""
    quality: str = ""
    # 最近一次解析到的在线直链（仅内存态，不入库；用于「未下载也能播」）
    remote_url: str = ""
    favorited: bool = False
    play_count: int = 0
    added_at: int = 0
    last_played_at: int | None = None

    @property
    def exists(self) -> bool:
        return bool(self.file_path) and Path(self.file_path).is_file()

    @property
    def online_only(self) -> bool:
        """尚未下载/落地的在线条目（可直接在线播放）。"""
        return not self.file_path

    @property
    def playable(self) -> bool:
        """本地文件存在，或是在线条目 —— 两者都可播放。"""
        return self.exists or self.online_only

    @property
    def kind_label(self) -> str:
        return kind_label(self.kind)

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration_ms)

    @property
    def playback_target(self) -> str:
        """可播放目标：本地优先，否则回退在线直链。"""
        return self.file_path if self.exists else self.remote_url

    def display(self) -> str:
        if self.episode_label:
            return f"{self.title} · {self.episode_label}"
        return self.title

    def to_remote(self) -> "RemoteVideo":
        """还原为在线条目（在线播放 / 重新下载用）。"""
        return RemoteVideo(
            source=self.source,
            remote_id=self.remote_id,
            title=self.title,
            kind=self.kind,
            year=self.year,
            region=self.region,
            category=self.category,
            tags=self.tags,
            actors=self.actors,
            director=self.director,
            description=self.description,
            cover_url=getattr(self, "cover_url", ""),
            episodes=[Episode(name=self.episode_label or "正片", url=self.remote_url,
                              index=self.episode_index)],
            series_key=self.series_key,
        )


@dataclass
class RemoteVideo:
    """在线搜索结果（尚未下载）。episodes 为空表示还没拉详情。"""

    source: str
    remote_id: str
    title: str
    kind: str = "other"
    year: str = ""
    region: str = ""
    category: str = ""
    tags: str = ""
    actors: str = ""
    director: str = ""
    description: str = ""
    cover_url: str = ""
    remarks: str = ""
    score: str = ""
    duration_ms: int = 0
    episodes: list[Episode] = field(default_factory=list)
    series_key: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def kind_label(self) -> str:
        return kind_label(self.kind)

    @property
    def episode_count(self) -> int:
        return len(self.episodes)

    def display(self) -> str:
        suffix = f"（{self.year}）" if self.year else ""
        return f"{self.title}{suffix}"

    def to_json(self) -> dict:
        return {
            "source": self.source,
            "remoteId": self.remote_id,
            "title": self.title,
            "kind": self.kind,
            "year": self.year,
            "region": self.region,
            "category": self.category,
            "tags": self.tags,
            "actors": self.actors,
            "director": self.director,
            "description": self.description,
            "coverUrl": self.cover_url,
            "remarks": self.remarks,
            "score": self.score,
            "durationMs": self.duration_ms,
            "seriesKey": self.series_key,
            "episodes": [e.to_json() for e in self.episodes],
        }

    @classmethod
    def from_json(cls, data: dict) -> "RemoteVideo":
        return cls(
            source=str(data.get("source", "")),
            remote_id=str(data.get("remoteId", data.get("remote_id", ""))),
            title=str(data.get("title", "")),
            kind=normalize_kind(str(data.get("kind", "")), str(data.get("category", ""))),
            year=str(data.get("year", "")),
            region=str(data.get("region", "")),
            category=str(data.get("category", "")),
            tags=str(data.get("tags", "")),
            actors=str(data.get("actors", "")),
            director=str(data.get("director", "")),
            description=str(data.get("description", "")),
            cover_url=str(data.get("coverUrl", data.get("cover_url", ""))),
            remarks=str(data.get("remarks", "")),
            score=str(data.get("score", "")),
            duration_ms=int(data.get("durationMs") or 0),
            series_key=str(data.get("seriesKey", data.get("series_key", ""))),
            episodes=[Episode.from_json(item) for item in (data.get("episodes") or [])],
        )


@dataclass
class Playlist:
    """分类 / 收藏（playlists 表）。kind: category / favorite。"""

    id: int = 0
    name: str = ""
    kind: str = "category"
    created_at: int = 0
    video_count: int = 0

    @property
    def is_favorite(self) -> bool:
        return self.kind == "favorite"


@dataclass
class HistoryEntry:
    id: int
    video_id: int
    title: str
    kind: str
    action: str          # play / download
    played_at: int
    episode_label: str = ""
    quality: str = ""
    source: str = ""
    path: str = ""


@dataclass
class PlayRecord:
    """在线播放记录：记住某集最近一次可用的直链与画质，便于快速续播。"""

    video_id: int
    episode_label: str
    quality: str
    url: str
    source: str
    updated_at: int


def format_duration(ms: int) -> str:
    if not ms or ms < 0:
        return "--:--"
    total = int(ms // 1000)
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def parse_video_name(stem: str) -> tuple[str, str, int]:
    """从文件名推断 (剧名, 集标签, 集序号)。

    支持 "庆余年 S01E03" / "庆余年 第03集" / "庆余年 EP03" 等常见命名。
    """
    text = (stem or "").strip()
    patterns = (
        (re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})"), True),
        (re.compile(r"第\s*(\d{1,4})\s*[集话話期]"), False),
        (re.compile(r"[Ee][Pp]?\s*(\d{1,4})"), False),
        (re.compile(r"[\[\(](\d{1,4})[\]\)]"), False),
    )
    for pattern, with_season in patterns:
        match = pattern.search(text)
        if not match:
            continue
        if with_season:
            season = int(match.group(1))
            number = int(match.group(2))
            label = f"S{season:02d}E{number:02d}"
        else:
            season = 0
            number = int(match.group(1))
            label = f"第{number:02d}集"
        title = (text[:match.start()] + text[match.end():]).strip(" .-_")
        return (title or text), label, number
    return text, "", 0
