"""「上次状态」的记忆测试：列表列宽。

## 为什么需要

两件用户每天都会碰到的小事：

列表每次启动都回默认列宽 —— 手工拉宽过的列，下次又变窄。
（"记住上次板块"被启动预算闸门否掉了，见 view_state 模块说明。）

本文件钉住：存得进、读得出、坏数据不炸、以及**不碰真实注册表**
（用假 settings 替换 `app_settings`，否则测试会污染开发机的设置）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QApplication, QTableWidget

from modu_workbench.ui_kit import view_state


class _FakeSettings:
    """内存版 QSettings（只实现 view_state 用到的三个方法）。"""

    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})
        self.written: list[tuple[str, object]] = []

    def value(self, key: str, default=None, **_kwargs):  # noqa: ANN001, ANN003
        return self.values.get(key, default)

    def setValue(self, key: str, value) -> None:  # noqa: N802, ANN001
        self.values[key] = value
        self.written.append((key, value))

    def sync(self) -> None:
        return None


@pytest.fixture()
def fake_settings(monkeypatch):
    """把 view_state 的 QSettings 工厂换成内存版（不碰真实注册表）。"""
    settings = _FakeSettings()
    monkeypatch.setattr(
        "modu_workbench.ui_kit.view_state._settings", lambda: settings, raising=True
    )
    return settings


# --------------------------------------------------------------------------- 列宽


def test_table_layout_roundtrip(qapp: QApplication, fake_settings) -> None:  # noqa: ANN001
    """列宽存得进、恢复得出。"""
    first = QTableWidget(3, 3)
    first.setHorizontalHeaderLabels(["A", "B", "C"])
    first.setColumnWidth(0, 240)
    view_state.save_table_layout(first, "music/library_columns")

    stored = fake_settings.values["music/library_columns"]
    assert isinstance(stored, QByteArray) and not stored.isEmpty(), "没有存成 QByteArray"

    second = QTableWidget(3, 3)
    second.setHorizontalHeaderLabels(["A", "B", "C"])
    second.setColumnWidth(0, 80)                 # 默认窄
    view_state.remember_table_layout(second, "music/library_columns")
    assert second.columnWidth(0) == 240, "恢复后列宽不是保存时的值"


def test_remember_layout_without_saved_state_is_noop(qapp: QApplication, fake_settings) -> None:  # noqa: ANN001
    """从没存过时保持默认列宽（不能报错）。"""
    table = QTableWidget(2, 2)
    table.setHorizontalHeaderLabels(["A", "B"])
    view_state.remember_table_layout(table, "video/library_columns")
    assert table.columnWidth(0) > 0


def test_corrupt_state_is_ignored(qapp: QApplication, fake_settings) -> None:  # noqa: ANN001
    """存了坏数据（不是合法状态）时保持默认，不崩。"""
    fake_settings.values["video/library_columns"] = QByteArray(b"garbage")
    table = QTableWidget(2, 2)
    table.setHorizontalTable = None  # 无关属性，仅确保对象未被替换
    table.setHorizontalHeaderLabels(["A", "B"])
    view_state.remember_table_layout(table, "video/library_columns")   # 不抛即通过
    assert table.columnWidth(0) > 0


def test_state_helpers_never_raise(monkeypatch, qapp: QApplication) -> None:  # noqa: ANN001
    """settings 抛异常时，两个 helper 都要静默降级。"""
    def boom() -> None:
        raise RuntimeError("注册表不可用")

    monkeypatch.setattr("modu_workbench.ui_kit.view_state._settings", boom)
    table = QTableWidget(1, 1)
    view_state.remember_table_layout(table, "x/columns")
    view_state.save_table_layout(table, "x/columns")
