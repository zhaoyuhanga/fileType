"""板块无关的公共能力层（唯一允许被各板块依赖的 core 子包）。

分层约定见 `docs/REFACTOR_PLAN.md` 第 2 节：

    boards/<板块>  →  core/<同板块>  →  core/platform
    core/<板块>    ✗→ core/<其他板块>（core.convert 作为共享转换引擎例外，且它不得反向依赖板块）

本包只放"与业务无关、被多个板块共用"的东西：

- `paths`     数据目录与数据库/媒体文件布局（含旧数据迁移）
- `http`      统一 HTTP 客户端（UA/超时/退避重试/错误翻译）
- `media`     ffmpeg / ffprobe 定位与媒体时长探测
- `files`     本地文件扫描（导入与预览共用）
"""
from __future__ import annotations

from .files import SUPPORTED_IMPORT_EXTENSIONS, scan_paths
from .http import (
    DEFAULT_UA,
    HttpClient,
    SourceError,
    describe_network_error,
    guess_referer,
    host_of,
    looks_like_media_url,
    origin_of,
)
from .media import bundled_dir, find_ffmpeg, find_ffprobe, probe_duration_ms
from .paths import (
    APP_DIR_NAME,
    LEGACY_APP_DIR_NAME,
    app_data_dir,
    ensure_legacy_migration,
    gallery_cache_dir,
    gallery_db_path,
    gallery_dir,
    gallery_import_dir,
    library_db_path,
    llm_db_path,
    music_db_path,
    music_dir,
    music_download_dir,
    video_db_path,
    video_dir,
    video_download_dir,
)

__all__ = [
    "APP_DIR_NAME",
    "DEFAULT_UA",
    "LEGACY_APP_DIR_NAME",
    "SUPPORTED_IMPORT_EXTENSIONS",
    "HttpClient",
    "SourceError",
    "app_data_dir",
    "bundled_dir",
    "describe_network_error",
    "ensure_legacy_migration",
    "find_ffmpeg",
    "find_ffprobe",
    "gallery_cache_dir",
    "gallery_db_path",
    "gallery_dir",
    "gallery_import_dir",
    "guess_referer",
    "host_of",
    "library_db_path",
    "llm_db_path",
    "looks_like_media_url",
    "music_db_path",
    "music_dir",
    "music_download_dir",
    "origin_of",
    "probe_duration_ms",
    "scan_paths",
    "video_db_path",
    "video_dir",
    "video_download_dir",
]
