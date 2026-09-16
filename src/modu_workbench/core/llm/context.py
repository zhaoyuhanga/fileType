"""大模型能力中心的应用级单例（跨板块共用）。

放在 core/llm 内而不是 app 层：图库、设置页等都可以依赖 `core.llm`，
而 `core.platform` / `core.llm` 是唯一允许被所有板块依赖的公共子包。
"""
from __future__ import annotations

from modu_workbench.core.llm import LlmStorage, ModelRouter
from modu_workbench.core.platform.paths import llm_db_path

_llm_storage: LlmStorage | None = None
_llm_router: ModelRouter | None = None


def llm_storage() -> LlmStorage:
    global _llm_storage
    if _llm_storage is None:
        _llm_storage = LlmStorage(llm_db_path())
    return _llm_storage


def llm_router() -> ModelRouter:
    """应用级大模型路由器（多类型 / 多配置 / 优先级降级）。"""
    global _llm_router
    if _llm_router is None:
        _llm_router = ModelRouter(llm_storage())
    return _llm_router
