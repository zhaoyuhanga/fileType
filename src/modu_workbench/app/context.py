"""应用级上下文（兼容入口）：把各板块自己的 context 聚合起来。

设计变更（v1.0.0 重构）：
- 各板块的单例**住在自己的包里**（`boards/<板块>/context.py`），板块内部通过
  `from . import context as app_context` 使用，互不牵动；
- 大模型单例住在 `core.llm.context`（跨板块共用）；
- 本模块只做"按名字懒加载"的聚合，供设置页与测试等跨板块调用，
  不会在 import 时把所有板块一起拉起来（每个名字首次访问才 import 对应板块）。
"""
from __future__ import annotations

import importlib

# 名字 → 提供该名字的模块（首次访问时才 import）
_LAZY: dict[str, str] = {
    "library": "modu_workbench.boards.book.context",
    "storage": "modu_workbench.boards.book.context",
    "document_storage": "modu_workbench.boards.document.context",
    "document_library": "modu_workbench.boards.document.context",
    "document_ai": "modu_workbench.boards.document.context",
    "music_storage": "modu_workbench.boards.music.context",
    "music_library": "modu_workbench.boards.music.context",
    "music_registry": "modu_workbench.boards.music.context",
    "music_player": "modu_workbench.boards.music.context",
    "video_storage": "modu_workbench.boards.video.context",
    "video_registry": "modu_workbench.boards.video.context",
    "video_library": "modu_workbench.boards.video.context",
    "image_storage": "modu_workbench.boards.gallery.context",
    "image_library": "modu_workbench.boards.gallery.context",
    "llm_storage": "modu_workbench.core.llm.context",
    "llm_router": "modu_workbench.core.llm.context",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str):  # noqa: ANN201  PEP 562 懒加载
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value          # 之后直接命中，不再走 __getattr__
    return value
