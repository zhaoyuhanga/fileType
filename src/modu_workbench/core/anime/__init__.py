"""第七大板块「墨软动漫」：规则驱动的番剧采集 + 追番 + 弹幕。

与第六板块「墨软影视」**定位不重叠**：

| 维度 | 墨软影视 | 墨软动漫 |
|---|---|---|
| 内容来源 | 苹果 CMS / 公开源（结构化接口） | **规则驱动的番剧站采集**（XPath / JSON API 规则） |
| 核心资产 | 多源聚合 + 下载入库 | **规则库 + 追番 + 弹幕**（+ Bangumi 元数据） |
| 换源语义 | 同一作品在多个 CMS 源之间换 | 同一番剧在**多条规则/多个线路**之间换 |
| 播放器/代理/下载 | 自己的 | **复用共享层**（`ui_kit/media/`、`services/media_proxy/`、`core/video/segment_downloader.py`） |

模块划分（见 `docs/VIDEO_ANIME_PLAN.md` 4.2.3）：

    models.py     规则/番剧/线路/剧集/进度/弹幕 数据模型
    selectors.py  XPath / CSS / JSONPath 三种求值器（统一 first()/all()/text()/attr()）
    rules.py      规则解析、校验、变量替换、导入导出、订阅校验和
    sniffer.py    播放页静态嗅探（苹果 CMS player_aaaa / JSON / iframe 递归 / JS 变量）
    engine.py     规则执行：搜索 → 详情 → 线路/剧集 → 播放地址
    providers.py  把规则包装成"源"（接口与 core/video 的 VideoSource 对齐）
    storage.py    anime_* 表 CRUD
    library.py    业务编排（搜索/追番/进度/下载/换源/更新检查）
    danmaku.py    弹弹play 客户端 + XML 解析/生成 + 匹配
    bangumi.py    Bangumi 元数据（只读 + 缓存）
    update_check.py  追番更新检查（并发 2、可取消、写回 total/latest）
    tasks.py      后台任务封装（纯 Python，UI 侧用 QThread 包一层）

**为什么门面是「懒加载」**：本包直接被 `core/`、`services/`、`boards/` 多处引用
（比如影视板块的弹幕接线只需要 `models.normalize_title`）。如果 `__init__` 一上来
就把引擎/存储/库全部 import，会形成不必要的重依赖链，也让单测难以只加载一小块。
所以这里用 PEP 562 的模块级 `__getattr__` 按需导入（与 `app/context.py` 同一套路）。

**能力边界（写死在文档里，不要承诺做不到的事）**：不内置浏览器内核、不执行页面 JS
→ 纯 JS 渲染型站点无法支持；实时 Anime4K 超分不做（改为离线 ffmpeg 增强）；
不发送弹幕（受限接口未开放）；WebDAV 只做 JSON 导出/导入。
"""
from __future__ import annotations

import importlib

#: 名字 → 提供该名字的子模块（首次访问时才 import）
_LAZY: dict[str, str] = {
    # 数据模型与规则（最轻，很多地方只要这些）
    "AnimeEpisode": "models",
    "AnimeRule": "models",
    "AnimeSeries": "models",
    "Road": "models",
    "SeriesDetail": "models",
    "SniffedMedia": "models",
    "WatchProgress": "models",
    "normalize_title": "models",
    "parse_episode_number": "models",
    "SUPPORTED_API_LEVEL": "models",
    "RuleError": "rules",
    "RuleManager": "rules",
    "load_rule": "rules",
    "parse_rule": "rules",
    "rule_from_dict": "rules",
    "rule_to_dict": "rules",
    "validate_rule": "rules",
    "Selector": "selectors",
    "SelectorError": "selectors",
    # 执行链路
    "AnimeEngine": "engine",
    "SearchHit": "engine",
    "SearchGroup": "engine",
    "Sniffer": "sniffer",
    "RuleProvider": "providers",
    "RuleRegistry": "providers",
    # 存储与业务
    "AnimeStorage": "storage",
    "AnimeLibrary": "library",
    "ResolvedAnime": "library",
    "UpdateReport": "update_check",
    "SeriesUpdate": "update_check",
    "check_updates": "update_check",
    # 外部数据源
    "DANDANPLAY_BASE": "danmaku",
    "DanmakuComment": "danmaku",
    "DanmakuMatch": "danmaku",
    "DandanplayClient": "danmaku",
    "parse_danmaku_xml": "danmaku",
    "to_danmaku_xml": "danmaku",
    "BANGUMI_BASE": "bangumi",
    "BangumiClient": "bangumi",
    "BangumiSubject": "bangumi",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str):  # noqa: ANN202  PEP 562 懒加载
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    globals()[name] = value          # 之后直接命中，不再走 __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(list(globals()) + __all__))
