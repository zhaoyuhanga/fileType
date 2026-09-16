"""大模型能力中心（跨板块共用）。

- `models.py`：模型类型、服务商预设、配置模型、SQLite 存储；
- `router.py`：OpenAI 兼容客户端 + 按优先级降级的路由器。

典型用法（图库/影视/任何需要大模型的板块）::

    from modu_workbench.services import app_context

    router = app_context.llm_router()
    text, profile = router.chat("text", [{"role": "user", "content": "你好"}])
"""
from __future__ import annotations

from .models import (
    KIND_DESCRIPTIONS,
    KIND_IMAGE,
    KIND_LABELS,
    KIND_ROLE_HINTS,
    KIND_TEXT,
    KIND_VIDEO,
    PROVIDER_KEYS,
    PROVIDER_PRESETS,
    LlmConfigError,
    LlmError,
    LlmRequestError,
    LlmStorage,
    ModelProfile,
    ProviderPreset,
    kind_label,
    provider_label,
    provider_preset,
)
from .router import ModelRouter, OpenAiCompatClient, describe_http_error

__all__ = [
    "KIND_DESCRIPTIONS",
    "KIND_IMAGE",
    "KIND_LABELS",
    "KIND_ROLE_HINTS",
    "KIND_TEXT",
    "KIND_VIDEO",
    "PROVIDER_KEYS",
    "PROVIDER_PRESETS",
    "LlmConfigError",
    "LlmError",
    "LlmRequestError",
    "LlmStorage",
    "ModelProfile",
    "ModelRouter",
    "OpenAiCompatClient",
    "ProviderPreset",
    "describe_http_error",
    "kind_label",
    "provider_label",
    "provider_preset",
]
