"""板块通用快捷键与空状态一键行动的测试。

## 背景

打磨前：列表页**一个快捷键都没有**（只有阅读器/文档/图库有零散几个），
想搜索得先用鼠标点进搜索框，想刷新得去找按钮；空状态则全是纯文字，
`EmptyState` 明明支持 `action` 参数，12 处调用却**一处都没用**。

这轮补上 `Ctrl+F`（聚焦搜索）/ `F5`（刷新）与三处空状态主按钮。本文件钉住：

- 快捷键确实注册到了板块上，且键位一致（Ctrl+F / Ctrl+K / F5 / Ctrl+R）；
- 按 Ctrl+F 真的把焦点移到搜索框并全选；
- 按 F5 真的调用了刷新（且是**晚绑定**：替换实例方法也生效）；
- 空状态里确实有按钮，点它会调到对应的导入动作。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from modu_workbench.ui_kit.shortcuts import REFRESH_KEYS, SEARCH_KEYS, install_board_shortcuts

_HOSTS: list[QWidget] = []


def _host() -> QWidget:
    host = QWidget()
    host.resize(120, 80)
    _HOSTS.append(host)
    return host


@pytest.fixture(autouse=True)
def _cleanup(qapp: QApplication):
    yield
    for host in list(_HOSTS):
        host.close()
        host.deleteLater()
    _HOSTS.clear()
    qapp.processEvents()


# --------------------------------------------------------------------------- 工具层


def test_installs_search_and_refresh_shortcuts(qapp: QApplication) -> None:
    """快捷键键位固定：找是 Ctrl+F/Ctrl+K，刷新是 F5/Ctrl+R。"""
    from PySide6.QtWidgets import QLineEdit

    widget = _host()
    search = QLineEdit(widget)
    calls: list[int] = []
    shortcuts = install_board_shortcuts(widget, search=search, refresh=lambda: calls.append(1))

    keys = sorted(shortcut.key().toString() for shortcut in shortcuts)
    for sequence in (*SEARCH_KEYS, *REFRESH_KEYS):
        assert sequence in keys, f"缺少快捷键 {sequence}"


def test_ctrl_f_focuses_and_selects_search(qapp: QApplication) -> None:
    """按 Ctrl+F：焦点进搜索框，且已有内容被全选（再敲就是替换）。"""
    from PySide6.QtWidgets import QLineEdit

    widget = _host()
    widget.show()
    search = QLineEdit(widget)
    search.setText("旧关键词")
    other = QLineEdit(widget)
    other.setFocus()
    qapp.processEvents()
    assert not search.hasFocus(), "前置条件：焦点不在搜索框"

    shortcuts = install_board_shortcuts(widget, search=search)
    focus_shortcut = next(s for s in shortcuts if s.key().toString() == "Ctrl+F")
    focus_shortcut.activated.emit()
    qapp.processEvents()

    # 说明：offscreen 平台不给窗口系统焦点，`hasFocus()` / `focusWidget()`
    # 在这里恒为 False/None，**不能**用来判断"焦点是否移过去了"。
    # 但 `selectAll()` 与焦点无关，一定生效 —— 它足以证明
    # "Ctrl+F 的槽真的跑完整了"（顺带守住"全选"这个真实需求：
    # 不全选的话，用户接着敲键盘就变成往旧关键词后面追加）。
    assert search.selectedText() == "旧关键词", (
        "Ctrl+F 没有全选搜索框内容（槽可能没执行完）"
    )


def test_refresh_is_late_bound(qapp: QApplication) -> None:
    """F5 走「方法名晚绑定」：替换实例方法后按 F5 调用的是新方法。

    这条同时守着一个坑——如果快捷键直接持有绑定方法的强引用，
    既形成引用环（退出阶段易崩），测试也替换不了。
    """
    class _Counter(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.count = 0

        def reload(self) -> None:
            self.count += 1

    widget = _Counter()
    _HOSTS.append(widget)
    install_board_shortcuts(widget, refresh=widget.reload)

    widget.reload = lambda: setattr(widget, "count", widget.count + 100)   # 替换实例方法
    f5 = next(s for s in widget.children() if hasattr(s, "key") and
              getattr(s, "key", lambda: None)().toString() == "F5")
    f5.activated.emit()
    qapp.processEvents()

    assert widget.count == 100, f"F5 调到的不是替换后的方法（count={widget.count}）"


def test_refresh_exception_is_swallowed(qapp: QApplication) -> None:
    """刷新函数抛异常时必须被吞掉，不能让快捷键把界面搞崩。"""
    def boom() -> None:
        raise RuntimeError("故意失败")

    widget = _host()
    shortcuts = install_board_shortcuts(widget, refresh=boom)
    f5 = next(s for s in shortcuts if s.key().toString() == "F5")
    f5.activated.emit()          # 不抛即通过
    qapp.processEvents()


# --------------------------------------------------------------------------- 板块接线


def _shelf(tmp_path: Path, qapp: QApplication):  # noqa: ANN001, ANN202
    from modu_workbench.boards.book.shelf import ShelfView
    from modu_workbench.core.book import Library, Storage
    from modu_workbench.ui_kit.toast import Toaster

    return ShelfView(Library(Storage(str(tmp_path / "b.db"))), Toaster(_host()))


def test_book_shelf_has_shortcuts_and_empty_action(qapp: QApplication, tmp_path: Path) -> None:
    """书库：有 Ctrl+F/F5，空书架里有一个「导入书籍」按钮。"""
    shelf = _shelf(tmp_path, qapp)
    try:
        keys = sorted(s.key().toString() for s in shelf._shortcuts)      # noqa: SLF001
        assert "Ctrl+F" in keys and "F5" in keys, f"书库快捷键不全：{keys}"

        buttons = [b.text() for b in shelf.findChildren(QPushButton)]
        assert "导入书籍" in buttons, f"空状态没有主操作按钮：{buttons}"
    finally:
        shelf.close()
        shelf.deleteLater()
        qapp.processEvents()


def test_empty_action_button_calls_import(qapp: QApplication, tmp_path: Path) -> None:
    """点空状态里的按钮，确实会调到导入动作。"""
    shelf = _shelf(tmp_path, qapp)
    try:
        called: list[int] = []
        shelf._pick_files = lambda: called.append(1)      # noqa: SLF001  拦截文件对话框
        button = next(b for b in shelf.findChildren(QPushButton) if b.text() == "导入书籍")
        button.click()
        assert called == [1], "点空状态按钮没有触发导入"
    finally:
        shelf.close()
        shelf.deleteLater()
        qapp.processEvents()


def test_convert_empty_state_has_action(qapp: QApplication, tmp_path: Path) -> None:
    """转换：空列表里有「添加文件」按钮。"""
    from modu_workbench.boards.convert.board import ConvertBoardPage

    page = ConvertBoardPage(output_dir=str(tmp_path / "out"))
    try:
        buttons = [b.text() for b in page.findChildren(QPushButton)]
        assert "添加文件" in buttons, f"转换空状态没有主操作按钮：{buttons}"
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()
