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
    assert "music" in keys
    assert get_board("book") is not None
    assert get_board("convert") is not None
    assert get_board("music") is not None
    assert get_board("nope") is None


def test_home_cards_do_not_overlap(qapp: QApplication) -> None:
    """首页每张卡片必须独占一个格子（此前幽灵卡与第三张板块卡重叠导致无法点击）。"""
    from PySide6.QtWidgets import QGridLayout

    page = HomePage()
    page.resize(1200, 800)
    page.show()
    qapp.processEvents()

    layout = page.findChild(QGridLayout)
    assert layout is not None

    occupied: dict[tuple[int, int], object] = {}
    widgets = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget() if item is not None else None
        if widget is None:
            continue
        position = layout.getItemPosition(index)[:2]
        assert position not in occupied, f"格子 {position} 有重叠控件"
        occupied[position] = widget
        widgets.append(widget)

    # 板块卡 + 幽灵卡都在，且实际几何矩形互不相交（重叠会让卡片点不动）
    assert len(widgets) >= len(ACTIVE_BOARDS) + 1
    for i, first in enumerate(widgets):
        for second in widgets[i + 1:]:
            assert not first.geometry().intersects(second.geometry()), "卡片矩形重叠"
    page.close()


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

    # 标题栏使用「墨软」品牌名
    assert "墨软·工作台" in shell.windowTitle()

    # 跳转到两个板块页
    for spec in ACTIVE_BOARDS:
        shell.go_page(spec.key)
        assert shell.current_key == spec.key
        assert isinstance(shell.current_page(), spec.page)

    # 返回首页
    shell.go_page(HOME_KEY)
    assert shell.current_key == HOME_KEY
    shell.close()


def test_board_titles_use_moruan_branding() -> None:
    """板块显示名统一为墨软系列（防止重命名回退）。"""
    titles = {spec.key: spec.title for spec in ACTIVE_BOARDS}
    assert titles["book"] == "墨软书库"
    assert titles["convert"] == "墨软转换"
    assert titles["music"] == "墨软乐库"
    assert "墨读" not in "".join(titles.values())
