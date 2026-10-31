"""板块无关的公共能力层（唯一允许被各板块依赖的 core 子包）。

分层约定见 `docs/ARCHITECTURE.md` 的「依赖规则」一节（由
`tests/architecture/test_architecture.py` 强制）：

    boards/<板块>  →  core/<同板块>  →  core/platform
    core/<板块>    ✗→ core/<其他板块>（core.convert 作为共享转换引擎例外，且它不得反向依赖板块）

本包只放"与业务无关、被多个板块共用"的东西：

- `paths`     数据目录与数据库/媒体文件布局（含旧数据迁移）
- `http`      统一 HTTP 客户端（UA/超时/退避重试/错误翻译）
- `media`     ffmpeg / ffprobe 定位与媒体时长探测
- `files`     本地文件扫描（导入与预览共用）
- `textutil`  标题/集数归一化（弹幕缓存键依赖它的语义，改动要慎重）
- `logging`   忽略失败留痕（`swallow` / `debug`）

## 为什么这里是 PEP 562 懒转发，而不是一堆模块级 import

以前本文件在模块级 `from .http import ...`，而 `http` 又 `import requests`。
后果：**只看首页也要先付 `requests` 的 95ms**（urllib3 63ms + ssl 5ms），
启动时全仓库 205 个模块被加载了 181 个 —— 而 `AppShell()` 自己只花 4ms。

改成懒转发后：
- `import core.platform` 只付"路径/数据库"这类真正每个板块都要的东西；
- `requests` 推迟到「第一次真的联网」（进乐库/影视/图库/大模型板块，或在线搜索）；
- 对外接口**一个名字都没少**，`from modu_workbench.core.platform import HttpClient`
  照旧可用（首次访问时才解析）。

新增成员时：在这里加进 `_LAZY` 或直接 import，并把名字补进 `__all__`；
`tests/architecture/test_module_contracts.py` 会校验 `__all__` 里每个名字都能取到。
"""
from __future__ import annotations

import importlib
from typing import Any

# 便宜的（不拖 requests/Qt）—— 保持模块级导入，各板块高频使用
from .db import SqliteStore, app_db, run_migrations
from .files import SUPPORTED_IMPORT_EXTENSIONS, scan_paths
from .logging import debug, swallow
from .media import bundled_dir, find_ffmpeg, find_ffprobe, probe_duration_ms
from .paths import (
    APP_DIR_NAME,
    LEGACY_APP_DIR_NAME,
    app_data_dir,
    app_db_path,
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

#: 贵的/低频的 → 懒解析。左侧是名字，右侧是提供它的子模块。
_LAZY = {
    # http（会拖 requests：实测 95ms）
    "DEFAULT_UA": ".http",
    "HttpClient": ".http",
    "SourceError": ".http",
    "describe_network_error": ".http",
    "guess_referer": ".http",
    "host_of": ".http",
    "looks_like_media_url": ".http",
    "origin_of": ".http",
}

__all__ = sorted(
    [
        "APP_DIR_NAME",
        "LEGACY_APP_DIR_NAME",
        "SUPPORTED_IMPORT_EXTENSIONS",
        "SqliteStore",
        "app_data_dir",
        "app_db",
        "app_db_path",
        "bundled_dir",
        "debug",
        "ensure_legacy_migration",
        "find_ffmpeg",
        "find_ffprobe",
        "gallery_cache_dir",
        "gallery_db_path",
        "gallery_dir",
        "gallery_import_dir",
        "library_db_path",
        "llm_db_path",
        "music_db_path",
        "music_dir",
        "music_download_dir",
        "probe_duration_ms",
        "run_migrations",
        "scan_paths",
        "swallow",
        "video_db_path",
        "video_dir",
        "video_download_dir",
        *_LAZY,
    ]
)


def __getattr__(name: str) -> Any:  # noqa: ANN401  PEP 562 懒加载
    """按需解析 http 客户端相关的名字（避免首屏付 requests 的导入成本）。"""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name, __package__)
    value = getattr(module, name)
    globals()[name] = value          # 之后直接命中，不再走 __getattr__
    return value
