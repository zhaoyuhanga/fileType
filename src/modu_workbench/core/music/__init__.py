"""墨读音乐核心：曲库存储、在线音源、下载、转换与播放。"""
from __future__ import annotations

from .downloader import DownloadResult, download_many, download_track, unique_path
from .library import MusicLibrary, is_audio_file, scan_audio_files
from .models import (
    AUDIO_EXTENSIONS,
    MUSIC_TARGETS,
    PLAY_MODE_LABELS,
    PLAY_MODES,
    HistoryEntry,
    Playlist,
    RemoteTrack,
    Track,
    format_duration,
    parse_track_name,
    safe_filename,
)
from .player import MULTIMEDIA_AVAILABLE, MusicPlayer
from .sources import (
    SEARCH_KIND_LABELS,
    SEARCH_KINDS,
    MusicSource,
    SourceError,
    clean_lyrics,
    describe_network_error,
    get_source,
    list_sources,
    search_all,
)
from .storage import FAVORITE_NAME, MusicStorage

__all__ = [
    "AUDIO_EXTENSIONS",
    "DownloadResult",
    "FAVORITE_NAME",
    "HistoryEntry",
    "MULTIMEDIA_AVAILABLE",
    "MUSIC_TARGETS",
    "MusicLibrary",
    "MusicPlayer",
    "MusicSource",
    "MusicStorage",
    "PLAY_MODE_LABELS",
    "PLAY_MODES",
    "Playlist",
    "RemoteTrack",
    "SEARCH_KIND_LABELS",
    "SEARCH_KINDS",
    "SourceError",
    "Track",
    "clean_lyrics",
    "describe_network_error",
    "download_many",
    "download_track",
    "format_duration",
    "get_source",
    "is_audio_file",
    "list_sources",
    "parse_track_name",
    "safe_filename",
    "scan_audio_files",
    "search_all",
    "unique_path",
]
