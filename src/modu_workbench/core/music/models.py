"""墨读音乐：数据模型（本地曲目 / 在线结果 / 歌单 / 历史）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 支持的音频扩展名（本地库与导入共用）
AUDIO_EXTENSIONS = (
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma",
)

# 转换目标（与 core/convert/media_io.AUDIO_TARGETS 对齐）
MUSIC_TARGETS = ("mp3", "m4a", "wav", "flac", "aac", "ogg", "opus", "wma")

PLAY_MODES = ("order", "loop-all", "loop-one", "shuffle")
PLAY_MODE_LABELS = {
    "order": "顺序播放",
    "loop-all": "列表循环",
    "loop-one": "单曲循环",
    "shuffle": "随机播放",
}

_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')


def safe_filename(name: str, fallback: str = "track") -> str:
    """把曲目名转换为合法文件名（去非法字符、合并连续下划线、限长）。"""
    cleaned = _ILLEGAL.sub("_", (name or "").strip())
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return (cleaned or fallback)[:120]


@dataclass
class Track:
    """本地曲目（tracks 表一行）。"""

    id: int = 0
    path: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: int = 0
    size_bytes: int = 0
    format: str = ""
    source: str = ""
    remote_id: str = ""
    cover_path: str = ""
    category: str = ""
    favorited: bool = False
    play_count: int = 0
    added_at: int = 0
    last_played_at: int | None = None

    @property
    def exists(self) -> bool:
        return bool(self.path) and Path(self.path).is_file()

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration_ms)

    def display(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


@dataclass
class RemoteTrack:
    """在线搜索结果（尚未下载）。"""

    source: str
    remote_id: str
    title: str
    artist: str = ""
    album: str = ""
    duration_ms: int = 0
    url: str = ""
    cover_url: str = ""
    category: str = ""
    extra: dict = field(default_factory=dict)

    def display(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title

    def to_json(self) -> dict:
        return {
            "source": self.source,
            "remoteId": self.remote_id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "durationMs": self.duration_ms,
            "url": self.url,
            "coverUrl": self.cover_url,
            "category": self.category,
        }

    @classmethod
    def from_json(cls, data: dict) -> "RemoteTrack":
        return cls(
            source=str(data.get("source", "")),
            remote_id=str(data.get("remoteId", data.get("remote_id", ""))),
            title=str(data.get("title", "")),
            artist=str(data.get("artist", "")),
            album=str(data.get("album", "")),
            duration_ms=int(data.get("durationMs") or 0),
            url=str(data.get("url", "")),
            cover_url=str(data.get("coverUrl", data.get("cover_url", ""))),
            category=str(data.get("category", "")),
        )


@dataclass
class Playlist:
    id: int = 0
    name: str = ""
    kind: str = "playlist"   # playlist / favorite / category
    created_at: int = 0
    track_count: int = 0
    pending_count: int = 0

    @property
    def is_favorite(self) -> bool:
        return self.kind == "favorite"


@dataclass
class HistoryEntry:
    id: int
    track_id: int
    title: str
    artist: str
    action: str          # play / download
    played_at: int
    path: str = ""


def format_duration(ms: int) -> str:
    if not ms or ms < 0:
        return "--:--"
    total = int(ms // 1000)
    return f"{total // 60:02d}:{total % 60:02d}"


def parse_track_name(stem: str) -> tuple[str, str]:
    """从文件名推断 (歌手, 曲名)：支持 "歌手 - 曲名" / "歌手-曲名"。"""
    for sep in (" - ", " – ", "-", "—"):
        if sep in stem:
            left, _, right = stem.partition(sep)
            left, right = left.strip(), right.strip()
            if left and right:
                return left, right
    return "", stem.strip()
