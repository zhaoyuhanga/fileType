"""设置页基类：各板块的设置页实现同一套接口，由设置对话框统一装配。

约定：
- `title` / `icon` 用于左侧列表；
- `load()` 从存储读取当前值填入控件；
- `save()` 把控件值写回存储（QSettings 或板块数据库），返回是否有改动；
- 设置页只负责"本板块"的配置，通用项放 `general.py`。
"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget

SETTINGS_ORG = "ModuWorkbench"
SETTINGS_APP = "modu-workbench"


def app_settings():  # noqa: ANN201
    """统一的应用级 QSettings 工厂（各板块设置页共用）。"""
    from PySide6.QtCore import QSettings

    return QSettings(SETTINGS_ORG, SETTINGS_APP)


class SettingsPage(QWidget):
    """板块设置页基类。"""

    title = "设置"
    icon = "⚙"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._dirty = False

    # ---------- 子类实现 ----------

    def load(self) -> None:
        """从存储载入当前配置到控件。"""
        return None

    def save(self) -> bool:
        """保存控件值；返回 True 表示发生了改动。"""
        return False

    # ---------- 辅助 ----------

    def mark_dirty(self) -> None:
        self._dirty = True

    @property
    def dirty(self) -> bool:
        return self._dirty

    def hint(self) -> str:
        """页脚提示（可选覆盖）。"""
        return ""


__all__ = ["SETTINGS_APP", "SETTINGS_ORG", "SettingsPage", "app_settings"]
