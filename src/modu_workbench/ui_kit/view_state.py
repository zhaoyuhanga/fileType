"""记住界面上的"上次状态"：列表列宽。

> 曾尝试"记住上次所在板块，启动时自动回去"，被架构闸门的**启动预算**否掉：
> 恢复板块意味着启动那一刻就要**构建**该板块 —— 实测本项目模块数
> 24 → **110**，还把 `requests`（约 95ms）拖进启动路径。
> 懒加载是这个项目的刻意设计（见 `tests/architecture/test_startup_budget.py`），
> 这个功能与它正面冲突，因此放弃；只保留列宽记忆（板块已构建后才用到，零额外成本）。

## 为什么需要

乐库/影视的列表每次启动都回到默认列宽 —— 用户手工把「剧名」拉宽，
下次打开又变窄，得重新拉一遍。

## 设计取舍

- **存 `header().saveState()`**，而不是逐个 `setColumnWidth`：一次就把
  "顺序 + 宽度 + 是否隐藏"全存下来，列数变化时 `restoreState` 自带容错，
  对不上就保持默认。
- 直接用 `QSettings(SETTINGS_ORG, SETTINGS_APP)`，**刻意不走
  `ui_kit.settings.app_settings()`**：`ui_kit/settings` 是个包，导入其子模块
  会先执行包 `__init__`，把八个设置页的工厂（以及 core.document /
  core.convert / requests）全拉进启动路径 —— 实测启动模块数 46 → 110。
  这里只读一个键，不值得付那个代价。
- 任何失败都**静默忽略**：记住列宽是锦上添花，绝不能因为它让程序起不来。
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QTableWidget

from modu_workbench.core.platform.logging import swallow
from modu_workbench.core.platform.paths import SETTINGS_APP, SETTINGS_ORG


def _settings() -> QSettings:
    """与应用其它设置同一个 QSettings 作用域。"""
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def remember_table_layout(table: QTableWidget, key: str) -> None:
    """恢复表格的列布局（顺序/宽度/隐藏）。

    在表格**创建后**调用一次；从没存过时什么都不做（保持默认列宽）。
    """
    try:
        saved = _settings().value(key, None, type=QByteArray)
        if saved:
            table.horizontalHeader().restoreState(saved)
    except Exception as error:  # noqa: BLE001  恢复失败就用默认列宽
        swallow(f"恢复列宽（{key}）", error)


def save_table_layout(table: QTableWidget, key: str) -> None:
    """保存表格列布局（板块 `shutdown()` 或关窗时调用）。"""
    try:
        _settings().setValue(key, table.horizontalHeader().saveState())
    except Exception as error:  # noqa: BLE001
        swallow(f"保存列宽（{key}）", error)


__all__ = ["remember_table_layout", "save_table_layout"]
