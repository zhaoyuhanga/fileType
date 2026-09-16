"""墨软影视核心：影视库存储、多源搜索、下载、播放与转换。

分层（与 core.music 对齐，便于维护者举一反三）：
- models.py     数据模型（画质 / 剧集 / 本地条目 / 在线结果 / 历史）
- storage.py    SQLite（视频 / 分类收藏 / 历史 / 播放记录 / 配置）
- library.py    业务动作（导入 / 在线播放解析 / 下载入库 / 转换）
- hls.py        m3u8 清单解析（多清晰度 + 分片）
- downloader.py 下载（HLS 合流 / 直链）
- sources/      可插拔视频源 + 注册表（聚合 / 熔断 / 重试 / 换源）
"""
from __future__ import annotations

from .downloader import (
    DownloadResult,
    build_filename,
    copy_local,
    download_episode,
    download_many,
    find_ffmpeg,
    guess_extension,
    looks_like_video,
    unique_path,
)
from .hls import HlsPlaylist, HlsVariant, list_qualities, parse_m3u8, segment_iv
from .library import VideoLibrary, is_video_file, probe_duration_ms, scan_video_files
from .models import (
    FAVORITE_NAME,
    MEDIA_KIND_LABELS,
    MEDIA_KINDS,
    PLAY_MODE_LABELS,
    PLAY_MODES,
    VIDEO_EXTENSIONS,
    VIDEO_TARGETS,
    Episode,
    HistoryEntry,
    Playlist,
    PlayRecord,
    Quality,
    RemoteVideo,
    Video,
    classify_kind,
    format_duration,
    kind_label,
    normalize_kind,
    parse_video_name,
    quality_rank,
    safe_filename,
    split_tags,
    strip_html,
)
from .sources import (
    DEFAULT_PROVIDER_ORDER,
    KIND_CC,
    KIND_CUSTOM,
    KIND_DIRECT,
    KIND_EXPERIMENTAL,
    KIND_FULL,
    KIND_LABELS,
    KIND_PREVIEW,
    ResolvedPlay,
    SourceError,
    SourceHealth,
    SourceInfo,
    VideoRegistry,
    VideoSource,
    best_match,
    build_default_providers,
    describe_network_error,
    get_source,
    list_sources,
    match_score,
    registry,
    search_all,
)
from .storage import VideoStorage, remote_to_video


def match_quality(qualities, label: str):  # noqa: ANN001
    """在一组画质里按标签选出最合适的一个（界面下拉 → Quality 对象的唯一入口）。

    匹配策略：精确标签 → 只有一个候选 → 按清晰度等级取最接近的。
    """
    candidates = [item for item in (qualities or []) if item is not None]
    if not candidates:
        return None
    wanted = (label or "").strip()
    if wanted:
        for quality in candidates:
            if quality.label == wanted:
                return quality
    if len(candidates) == 1:
        return candidates[0]

    from .models import quality_rank

    target = quality_rank(wanted)
    return min(candidates, key=lambda item: abs(item.rank - target))


def apply_quality_choice(episode, label: str):  # noqa: ANN001
    """把界面上的清晰度标签映射回该集具体的 `Quality` 对象。"""
    if episode is None:
        return None
    return match_quality(getattr(episode, "qualities", None) or [], label)


__all__ = [
    "DEFAULT_PROVIDER_ORDER",
    "FAVORITE_NAME",
    "KIND_CC",
    "KIND_CUSTOM",
    "KIND_DIRECT",
    "KIND_EXPERIMENTAL",
    "KIND_FULL",
    "KIND_LABELS",
    "KIND_PREVIEW",
    "MEDIA_KIND_LABELS",
    "MEDIA_KINDS",
    "PLAY_MODE_LABELS",
    "PLAY_MODES",
    "VIDEO_EXTENSIONS",
    "VIDEO_TARGETS",
    "DownloadResult",
    "Episode",
    "HistoryEntry",
    "HlsPlaylist",
    "HlsVariant",
    "PlayRecord",
    "Playlist",
    "Quality",
    "RemoteVideo",
    "ResolvedPlay",
    "SourceError",
    "SourceHealth",
    "SourceInfo",
    "Video",
    "VideoLibrary",
    "VideoRegistry",
    "VideoSource",
    "VideoStorage",
    "apply_quality_choice",
    "best_match",
    "build_default_providers",
    "build_filename",
    "classify_kind",
    "copy_local",
    "describe_network_error",
    "download_episode",
    "download_many",
    "find_ffmpeg",
    "format_duration",
    "get_source",
    "guess_extension",
    "is_video_file",
    "kind_label",
    "list_qualities",
    "list_sources",
    "looks_like_video",
    "match_quality",
    "match_score",
    "normalize_kind",
    "parse_m3u8",
    "parse_video_name",
    "probe_duration_ms",
    "quality_rank",
    "registry",
    "remote_to_video",
    "safe_filename",
    "scan_video_files",
    "search_all",
    "split_tags",
    "strip_html",
    "unique_path",
]
