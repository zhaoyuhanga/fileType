"""墨读音乐音源层：多音源注册表 + 重试 + 跨源兜底。

对外保持稳定 API（`get_source` / `list_sources` / `search_all` / `SourceError` …），
内部按音源拆分到 `providers/`，由 `registry.MusicRegistry` 统一编排。
"""
from __future__ import annotations

from .base import (
    KIND_CC,
    KIND_DIRECT,
    KIND_EXPERIMENTAL,
    KIND_FULL,
    KIND_LABELS,
    KIND_PREVIEW,
    MusicSource,
    ResolvedAudio,
    SourceHealth,
    SourceInfo,
)
from .http import DEFAULT_UA, HttpClient, SourceError, describe_network_error, host_of
from .matcher import artist_score, best_match, duration_score, match_score, title_score
from .providers import DEFAULT_PROVIDER_ORDER, build_default_providers, provider_class
from .providers.archive_org import ArchiveOrgSource
from .providers.audius import AudiusSource
from .providers.ccmixter import CcmixterSource
from .providers.direct import DirectUrlSource
from .providers.itunes import ItunesSource
from .providers.jamendo import JamendoSource
from .providers.kuwo import KuwoSource
from .providers.netease import NeteaseSource
from .registry import DEGRADE_AFTER, DEGRADE_SECONDS, MusicRegistry, clean_lyrics

SEARCH_KINDS = ("song", "artist", "album", "genre")
SEARCH_KIND_LABELS = {
    "song": "歌曲",
    "artist": "歌手",
    "album": "专辑",
    "genre": "类型/风格",
}

# 应用级默认注册表（UI 通过 services.app_context.music_registry() 使用）
_registry = MusicRegistry()


def registry() -> MusicRegistry:
    return _registry


def get_source(key: str) -> MusicSource:
    """兼容旧接口：取单个音源（未知则抛 SourceError）。"""
    return _registry.require(key)


def list_sources() -> list[SourceInfo]:
    """兼容旧接口：列出全部音源元信息。"""
    return _registry.infos()


def search_all(keyword: str, kind: str = "song", limit: int = 20,
               sources=None) -> tuple[list, list[str]]:  # noqa: ANN001
    """兼容旧接口：跨音源搜索。"""
    return _registry.search_all(keyword, kind=kind, limit=limit, sources=sources)


__all__ = [
    "DEFAULT_PROVIDER_ORDER",
    "DEFAULT_UA",
    "DEGRADE_AFTER",
    "DEGRADE_SECONDS",
    "ArchiveOrgSource",
    "AudiusSource",
    "CcmixterSource",
    "DirectUrlSource",
    "HttpClient",
    "ItunesSource",
    "JamendoSource",
    "KIND_CC",
    "KIND_DIRECT",
    "KIND_EXPERIMENTAL",
    "KIND_FULL",
    "KIND_LABELS",
    "KIND_PREVIEW",
    "KuwoSource",
    "MusicRegistry",
    "MusicSource",
    "NeteaseSource",
    "ResolvedAudio",
    "SEARCH_KIND_LABELS",
    "SEARCH_KINDS",
    "SourceError",
    "SourceHealth",
    "SourceInfo",
    "artist_score",
    "best_match",
    "build_default_providers",
    "clean_lyrics",
    "describe_network_error",
    "duration_score",
    "get_source",
    "host_of",
    "list_sources",
    "match_score",
    "provider_class",
    "registry",
    "search_all",
    "title_score",
]
