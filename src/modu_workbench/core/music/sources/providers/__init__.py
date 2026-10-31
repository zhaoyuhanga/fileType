"""内置音源集合：默认优先级顺序即“跨源兜底”的尝试顺序。"""
from __future__ import annotations

from .archive_org import ArchiveOrgSource
from .audius import AudiusSource
from .ccmixter import CcmixterSource
from .direct import DirectUrlSource
from .itunes import ItunesSource
from .jamendo import JamendoSource
from .kuwo import KuwoSource
from .netease import NeteaseSource

# 默认顺序：国内可下载源 → 国际/自由授权完整曲目 → 试听兜底 → 直链
DEFAULT_PROVIDER_ORDER = (
    "netease", "kuwo", "audius", "archive", "ccmixter", "itunes", "jamendo", "url",
)

_PROVIDER_CLASSES = {
    "netease": NeteaseSource,
    "kuwo": KuwoSource,
    "audius": AudiusSource,
    "archive": ArchiveOrgSource,
    "ccmixter": CcmixterSource,
    "itunes": ItunesSource,
    "jamendo": JamendoSource,
    "url": DirectUrlSource,
}


def build_default_providers() -> list:
    """按默认顺序实例化所有内置音源。"""
    return [_PROVIDER_CLASSES[key]() for key in DEFAULT_PROVIDER_ORDER]


def provider_class(key: str):  # noqa: ANN201
    return _PROVIDER_CLASSES.get(key)


__all__ = [
    "ArchiveOrgSource",
    "AudiusSource",
    "CcmixterSource",
    "DEFAULT_PROVIDER_ORDER",
    "DirectUrlSource",
    "ItunesSource",
    "JamendoSource",
    "KuwoSource",
    "NeteaseSource",
    "build_default_providers",
    "provider_class",
]
