"""M0 冒烟测试：板块注册表 / 主题 / 主壳导航（无头 offscreen 运行）。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from modu_workbench.app_shell import HOME_KEY, AppShell
from modu_workbench.boards.home_board import HomePage
from modu_workbench.boards.registry import ACTIVE_BOARDS, get_board
from modu_workbench.ui_kit.theme import ThemeTokens, app_qss


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_board_registry_contains_book_and_convert() -> None:
    keys = [spec.key for spec in ACTIVE_BOARDS]
    assert "book" in keys
    assert "convert" in keys
    assert get_board("book") is not None
    assert get_board("convert") is not None
    assert get_board("nope") is None


def test_theme_tokens_and_qss() -> None:
    tokens = ThemeTokens()
    assert tokens.shell_bg.startswith("#")
    qss = app_qss(tokens)
    assert "navButton" in qss
    assert "boardCard" in qss


def test_shell_pages_and_navigation(qapp: QApplication) -> None:
    qapp.setStyleSheet(app_qss(ThemeTokens()))
    shell = AppShell()

    # 首页为当前页，且含板块卡片
    assert shell.current_key == HOME_KEY
    assert isinstance(shell.current_page(), HomePage)

    # 跳转到两个板块页
    for spec in ACTIVE_BOARDS:
        shell.go_page(spec.key)
        assert shell.current_key == spec.key
        assert isinstance(shell.current_page(), spec.page)

    # 返回首页
    shell.go_page(HOME_KEY)
    assert shell.current_key == HOME_KEY
    shell.close()
