"""内置视频源清单：新增数据源只改这一个文件。

设计目标（用户诉求第 6 条）：
- **单独处理、不影响其他业务** —— 每个源是一个独立类，互不引用；
- **后续加源容易** —— 在 `CMS_SITES` 里加一行，或直接往「自定义采集源」填地址。

源分成三类：
1. 采集源（苹果CMS 协议）：片源丰富，覆盖电影/电视剧/动漫/综艺；
   接口地址属于第三方站点，可能变更，因此都支持在「源设置」里改地址。
2. 自由授权源：Internet Archive / Wikimedia Commons，可自由下载与再分发。
3. 直链 / 自定义源：用户自备地址。
"""
from __future__ import annotations

from ..base import VideoSource
from .cms_vod import CmsVodSource, build_source, extract_media_url, is_play_page
from .public import (
    ArchiveOrgVideoSource,
    CustomVodSource,
    DirectUrlVideoSource,
    WikimediaVideoSource,
)

# 默认启用的源顺序（磁盘上的配置会覆盖它）
#
# 顺序 = 优先级：跨源聚合按「标题+年份」去重、保留先出现的源（见 registry.search_all），
# 因此把「实测码率高、线路全」的采集站排在前面，避免模糊线路抢占同名结果。
# 实测依据（同集分片大小 ÷ 分片时长，2026-09 实测）：
#   爱坤 5000kb > 电影天堂 3000k > 非凡/最大/如意/U酷 2000k > 光速 ≈900kbps > 360 706kb。
DEFAULT_PROVIDER_ORDER: tuple[str, ...] = (
    "cms_ffzy", "cms_dyttzy", "cms_ikunzy", "cms_zuidazy", "cms_rycjapi",
    "cms_ukuapi", "cms_guangsu", "cms_360", "cms_mdzy",
    "archive", "wikimedia", "url", "custom",
)

# ---- 苹果CMS 采集站清单（接口地址可在「源设置」里修改） ----
# 说明：这些是公开的采集接口（同类开源项目 LibreTV 等亦使用），
# 仅用于个人学习与技术研究；请遵守相应站点条款与版权要求。
#
# 维护约定：采集站域名寿命普遍很短（改版/换域名/挂 Cloudflare 是常态），
# 因此清单里的每一项都必须是「实测能返回 JSON 列表」的地址；
# 其中返回分享页（`/share/<hash>`）的站点由 CmsVodSource.play_url 自动解析，
# 不需要单独处理。失效的源请直接替换，不要留在默认启用列表里。
#
# 排序约定：列表顺序即聚合优先级（去重保留先出现的源），按实测码率从高到低排；
# 同一上游换域名的站点（如 光速/红牛/豪华/虎牙/速播 共用一套 CDN 与同名分片）
# 只保留一个，避免搜索时重复请求同名结果。
#
# 最近一次全量实测（2026-09-18，关键词 流浪地球 / 庆余年 / 甄嬛传）：
# - 新增：爱坤（1080P 主清单，分片 5000kb）、最大（2000k_1080，12 万条资源）、
#   光速（接口快、线路多、库大）、魔都（部分片源 4K）；
# - 下线：量子资源（cj.lziapi.com）—— 搜索接口仍返回 JSON，
#   但所有播放地址（含 /share/ 页与直链 m3u8）均 404，直连与代理都一样。
CMS_SITES: tuple[dict, ...] = (
    {
        "key": "cms_ffzy",
        "label": "非凡资源",
        "base_url": "https://api.ffzyapi.com",
        "note": "非凡资源 · 苹果CMS 采集接口（分享页地址已自动解析，2000k 线路）",
    },
    {
        "key": "cms_dyttzy",
        "label": "电影天堂",
        "base_url": "https://caiji.dyttzyapi.com",
        "note": "电影天堂 · 苹果CMS 采集接口（分享页地址已自动解析，3000k 线路）",
    },
    {
        "key": "cms_ikunzy",
        "label": "爱坤资源",
        "base_url": "https://ikunzyapi.com",
        "note": "爱坤资源 · 苹果CMS 采集接口（直链 m3u8，1080P 主清单）",
    },
    {
        "key": "cms_zuidazy",
        "label": "最大资源",
        "base_url": "https://zuidazy.me",
        "note": "最大资源 · 苹果CMS 采集接口（直链 m3u8，2000k_1080 线路）",
    },
    {
        "key": "cms_rycjapi",
        "label": "如意资源",
        "base_url": "https://cj.rycjapi.com",
        "note": "如意资源 · 苹果CMS 采集接口（多线路，分享页地址已自动解析）",
    },
    {
        "key": "cms_ukuapi",
        "label": "U酷资源",
        "base_url": "https://api.ukuapi.com",
        "note": "U酷资源 · 苹果CMS 采集接口（分享页地址已自动解析）",
    },
    {
        "key": "cms_guangsu",
        "label": "光速资源",
        "base_url": "https://api.guangsuapi.com",
        "note": "光速资源 · 苹果CMS 采集接口（接口快、线路多，码率中等）",
    },
    {
        "key": "cms_360",
        "label": "360资源",
        "base_url": "https://www.360zy.com",
        "note": "360资源 · 苹果CMS 采集接口（电影/剧集/动漫/综艺）",
    },
    {
        "key": "cms_mdzy",
        "label": "魔都资源",
        "base_url": "https://www.mdzyapi.com",
        "note": "魔都资源 · 苹果CMS 采集接口（直链 m3u8，部分片源可达 4K）",
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
    "extract_media_url",
    "is_play_page",
]
