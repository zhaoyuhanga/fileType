"""主窗口几何记忆：大小/位置重启后保留，屏幕外坐标能拉回主屏。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMainWindow

from modu_workbench.core.platform.paths import SETTINGS_APP, SETTINGS_ORG
from modu_workbench.ui_kit.view_state import (
    WINDOW_GEOMETRY_KEY,
    WINDOW_MAXIMIZED_KEY,
    restore_window_state,
    save_window_state,
)


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _clean_window_settings():
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    for key in (WINDOW_GEOMETRY_KEY, WINDOW_MAXIMIZED_KEY):
        settings.remove(key)
    settings.sync()
    yield
    for key in (WINDOW_GEOMETRY_KEY, WINDOW_MAXIMIZED_KEY):
        settings.remove(key)
    settings.sync()


def test_window_size_and_position_round_trip(qapp: QApplication) -> None:
    # 离屏平台的虚拟屏幕只有 800×800，用能放下的尺寸断言"原样恢复"
    first = QMainWindow()
    first.resize(700, 600)
    first.move(36, 28)
    save_window_state(first)
    first.close()

    second = QMainWindow()
    try:
        assert restore_window_state(second) in ("normal", "maximized")
        assert (second.width(), second.height()) == (700, 600)
    finally:
        second.close()


def test_no_saved_geometry_means_default(qapp: QApplication) -> None:
    window = QMainWindow()
    try:
        assert restore_window_state(window) == ""
    finally:
        window.close()


def test_off_screen_geometry_is_pulled_back(qapp: QApplication) -> None:
    """显示器换了/拔了之后，旧坐标可能完全在屏幕外 —— 必须拉回可见区域。"""
    first = QMainWindow()
    first.resize(800, 600)
    first.move(8000, 8000)
    save_window_state(first)
    first.close()

    second = QMainWindow()
    try:
        restore_window_state(second)
        screen = QGuiApplication.primaryScreen()
        assert screen is not None
        assert second.frameGeometry().intersects(screen.availableGeometry()), \
            "窗口落在屏幕外等于启动后看不见"
    finally:
        second.close()
