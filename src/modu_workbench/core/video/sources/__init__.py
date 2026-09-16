"""影视源子系统：可插拔的数据源 + 注册表（聚合 / 熔断 / 重试 / 换源）。

对外便捷 API（与 `core.music.sources` 保持一致的使用手感）：
- `registry()`              应用级单例注册表
- `get_source(key)`         取某个源
- `list_sources()`          取全部源元信息
- `search_all(keyword, …)`  跨源聚合搜索
"""
from __future__ import annotations

from typing import List

from ..models import RemoteVideo
from .base import (
    KIND_CC,
    KIND_CUSTOM,
    KIND_DIRECT,
    KIND_EXPERIMENTAL,
    KIND_FULL,
    KIND_LABELS,
    KIND_PREVIEW,
    ResolvedPlay,
    SourceHealth,
    SourceInfo,
    VideoSource,
)
from .http import (
    DEFAULT_UA,
    MEDIA_URL_SUFFIXES,
    HttpClient,
    SourceError,
    describe_network_error,
    guess_referer,
    host_of,
    looks_like_media_url,
    origin_of,
)
from .matcher import best_match, match_score
from .providers import (
    CMS_SITES,
    DEFAULT_PROVIDER_ORDER,
    ArchiveOrgVideoSource,
    CmsVodSource,
    CustomVodSource,
    DirectUrlVideoSource,
    WikimediaVideoSource,
    build_default_providers,
    extract_media_url,
    is_play_page,
)
from .registry import (
    SETTING_ENABLED,
    SETTING_ORDER,
    SETTING_SOURCE_URLS,
    SETTING_VERSION,
    SOURCES_VERSION,
    VideoRegistry,
)

_default_registry: VideoRegistry | None = None


def registry() -> VideoRegistry:
    """应用级视频源注册表（单例）。"""
    global _default_registry
    if _default_registry is None:
        _default_registry = VideoRegistry()
    return _default_registry


def get_source(key: str) -> VideoSource | None:
    """按 key 取源；`url` / `custom` 等未注册 key 会构造临时实例。"""
    found = registry().get(key)
    if found is not None:
        return found
    return None


def require_source(key: str) -> VideoSource:
    return registry().require(key)


def list_sources(*, enabled_only: bool = False) -> List[SourceInfo]:
    return registry().infos(enabled_only=enabled_only)


def search_all(keyword: str, kind: str = "all", limit: int = 20,
               sources=None, include_degraded: bool = False,
               page: int = 1) -> tuple[List[RemoteVideo], List[str]]:  # noqa: ANN001
    """跨源聚合搜索（自动跳过被熔断的源）。返回 (结果, 错误说明)。"""
    return registry().search_all(
        keyword, kind=kind, limit=limit, sources=sources,
        include_degraded=include_degraded, page=page,
    )


__all__ = [
    "CMS_SITES",
    "DEFAULT_PROVIDER_ORDER",
    "DEFAULT_UA",
    "KIND_CC",
    "KIND_CUSTOM",
    "KIND_DIRECT",
    "KIND_EXPERIMENTAL",
    "KIND_FULL",
    "KIND_LABELS",
    "KIND_PREVIEW",
    "MEDIA_URL_SUFFIXES",
    "SETTING_ENABLED",
    "SETTING_ORDER",
    "SETTING_SOURCE_URLS",
    "SETTING_VERSION",
    "SOURCES_VERSION",
    "ArchiveOrgVideoSource",
    "CmsVodSource",
    "CustomVodSource",
    "DirectUrlVideoSource",
    "HttpClient",
    "ResolvedPlay",
    "SourceError",
    "SourceHealth",
    "SourceInfo",
    "VideoRegistry",
    "VideoSource",
    "WikimediaVideoSource",
    "best_match",
    "build_default_providers",
    "describe_network_error",
    "extract_media_url",
    "get_source",
    "guess_referer",
    "host_of",
    "is_play_page",
    "list_sources",
    "looks_like_media_url",
    "match_score",
    "origin_of",
    "registry",
    "require_source",
    "search_all",
]
