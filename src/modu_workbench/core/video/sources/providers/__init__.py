"""内置视频源清单：新增数据源只改这一个文件。

设计目标（用户诉求第 6 条）：
- **单独处理、不影响其他业务** —— 每个源是一个独立类，互不引用；
- **后续加源容易** —— 在 `DEFAULT_SOURCES` 里加一行，或直接往「自定义采集源」填地址。

源分成三类：
1. 采集源（苹果CMS 协议）：片源丰富，覆盖电影/电视剧/动漫/综艺；
   接口地址属于第三方站点，可能变更，因此都支持在「源设置」里改地址。
2. 自由授权源：Internet Archive / Wikimedia Commons，可自由下载与再分发。
3. 直链 / 自定义源：用户自备地址。
"""
from __future__ import annotations

from ..base import VideoSource
from .cms_vod import CmsVodSource, build_source
from .public import (
    ArchiveOrgVideoSource,
    CustomVodSource,
    DirectUrlVideoSource,
    WikimediaVideoSource,
)

# 默认启用的源顺序（磁盘上的配置会覆盖它）
DEFAULT_PROVIDER_ORDER: tuple[str, ...] = (
    "cms_360", "cms_heimuer", "cms_ffzy", "cms_wolong", "cms_tyyszy",
    "archive", "wikimedia", "url", "custom",
)

# ---- 苹果CMS 采集站清单（接口地址可在「源设置」里修改） ----
# 说明：这些是公开的采集接口（同类开源项目 LibreTV 等亦使用），
# 仅用于个人学习与技术研究；请遵守相应站点条款与版权要求。
CMS_SITES: tuple[dict, ...] = (
    {
        "key": "cms_360",
        "label": "360资源",
        "base_url": "https://360zy.com",
        "note": "360资源 · 苹果CMS 采集接口（电影/剧集/动漫/综艺）",
    },
    {
        "key": "cms_heimuer",
        "label": "黑木耳",
        "base_url": "https://json.heimuer.xyz",
        "note": "黑木耳资源 · 苹果CMS 采集接口",
    },
    {
        "key": "cms_ffzy",
        "label": "非凡影视",
        "base_url": "http://ffzy5.tv",
        "note": "非凡影视 · 苹果CMS 采集接口",
    },
    {
        "key": "cms_wolong",
        "label": "卧龙资源",
        "base_url": "https://wolongzyw.com",
        "note": "卧龙资源 · 苹果CMS 采集接口",
    },
    {
        "key": "cms_tyyszy",
        "label": "天涯资源",
        "base_url": "https://tyyszy.com",
        "note": "天涯资源 · 苹果CMS 采集接口",
    },
)


def build_cms_providers() -> list[VideoSource]:
    return [
        build_source(site["key"], site["label"], site["base_url"], site.get("note", ""))
        for site in CMS_SITES
    ]


def build_default_providers() -> list[VideoSource]:
    """构造全部内置视频源（顺序即默认搜索顺序）。"""
    providers: list[VideoSource] = []
    providers.extend(build_cms_providers())
    providers.append(ArchiveOrgVideoSource())
    providers.append(WikimediaVideoSource())
    providers.append(DirectUrlVideoSource())
    providers.append(CustomVodSource())
    return providers


# 源地址覆盖（用户在设置里改过地址时生效）
CMS_SITE_BY_KEY = {site["key"]: site for site in CMS_SITES}

__all__ = [
    "CMS_SITES",
    "CMS_SITE_BY_KEY",
    "DEFAULT_PROVIDER_ORDER",
    "ArchiveOrgVideoSource",
    "CmsVodSource",
    "CustomVodSource",
    "DirectUrlVideoSource",
    "WikimediaVideoSource",
    "build_cms_providers",
    "build_default_providers",
    "build_source",
]
