"""**转发壳**：规则源适配器的实现已上移到 `ui_kit/media/rule_sources.py`。

为什么必须搬走：适配器要继承影视板块的 `VideoSource`、用 `core.video.models` 的
`RemoteVideo`/`Episode` —— 而架构闸门（`tests/architecture/test_architecture.py`）
禁止 `core/<板块>` 引用 `core/<别的板块>`。这与播放器、弹幕桥搬家的理由是同一个。

这里保留同名转发，旧引用（含既有测试）不需要改动即可继续用。
新代码请 `from modu_workbench.ui_kit.media.rule_sources import RuleProvider`。
"""
from __future__ import annotations

from modu_workbench.ui_kit.media.rule_sources import (  # noqa: F401
    KEY_PREFIX,
    RuleProvider,
    RuleRegistry,
    provider_key,
    series_remote_id,
)

__all__ = ["KEY_PREFIX", "RuleProvider", "RuleRegistry", "provider_key", "series_remote_id"]
