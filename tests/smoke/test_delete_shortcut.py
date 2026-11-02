"""Delete 键删除选中项 + 转换板块「移除选中」的测试。

## 背景

桌面应用里 `Delete` 删选中项是肌肉记忆，但打磨前**没有一处绑它**。
这轮补上，同时守住两个关键约束：

1. **绝不能误删**：Delete 必须只在列表控件有焦点时生效
   （`WidgetWithChildrenShortcut`），否则用户在搜索框里删字会删掉数据；
2. **危险操作要有确认**：影视/图库的删除本来就有确认对话框，Delete 走同一入口；
   乐库的移除**没有确认**，所以刻意**不绑** Delete（见 `test_music_library_has_no_delete_key`）。

另外补了一个功能缺口：转换板块此前只有「清空列表」，加错一个文件只能全清。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QTableWidget, QWidget

from modu_workbench.ui_kit.shortcuts import DELETE_KEYS, install_delete_shortcut


def _keys(shortcuts) -> list[str]:  # noqa: ANN001
    return sorted(shortcut.key().toString() for shortcut in shortcuts)


def _expected(keys) -> list[str]:  # noqa: ANN001
    """把键位转成 Qt 的显示形式再比较。

    `QKeySequence("Delete").toString()` 返回 **"Del"**（Windows 上的标准写法），
    直接跟字面量 "Delete" 比较会误判成"没绑定"。
    """
    return sorted(QKeySequence(key).toString() for key in keys)


# --------------------------------------------------------------------------- 助手层


def test_install_delete_shortcut_binds_delete_and_backspace(qapp: QApplication) -> None:
    """Delete 与 Backspace 都绑上（笔记本键盘常常没有独立 Delete）。"""
    table = QTableWidget(2, 2)
    calls: list[int] = []
    shortcuts = install_delete_shortcut(table, lambda: calls.append(1))

    assert _keys(shortcuts) == _expected(DELETE_KEYS), f"键位不对：{_keys(shortcuts)}"
    for shortcut in shortcuts:
        shortcut.activated.emit()
    assert calls == [1, 1], "Delete/Backspace 没触发处理函数"


def test_delete_shortcut_is_widget_scoped(qapp: QApplication) -> None:
    """**关键约束**：Delete 必须是控件级（有焦点才触发），不能是窗口级。

    窗口级的话，用户在搜索框里按 Delete 删字，会连带把选中的数据删掉。
    """
    table = QTableWidget(2, 2)
    shortcuts = install_delete_shortcut(table, lambda: None)
    for shortcut in shortcuts:
        assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut, (
            "Delete 用了窗口级快捷键，会在搜索框里误删数据"
        )
        assert shortcut.parent() is table, "快捷键没有挂在列表控件上"


def test_delete_handler_exception_is_swallowed(qapp: QApplication) -> None:
    """删除函数抛异常时不能把界面搞崩。"""
    def boom() -> None:
        raise RuntimeError("故意失败")

    table = QTableWidget(1, 1)
    shortcuts = install_delete_shortcut(table, boom)
    shortcuts[0].activated.emit()        # 不抛即通过


# --------------------------------------------------------------------------- 转换板块


def _convert_page(tmp_path: Path, qapp: QApplication, count: int = 3):  # noqa: ANN001, ANN202
    from modu_workbench.boards.convert.board import ConvertBoardPage

    page = ConvertBoardPage(output_dir=str(tmp_path / "out"))
    files = []
    for index in range(count):
        path = tmp_path / f"a{index}.png"
        path.write_bytes(b"x")
        files.append(str(path))
    page._append_paths(files)            # noqa: SLF001
    return page


def test_convert_removes_only_selected_rows(qapp: QApplication, tmp_path: Path) -> None:
    """「移除选中」只删选中的那行，其它保留，且不动磁盘文件。"""
    page = _convert_page(tmp_path, qapp, count=3)
    try:
        before = [Path(row["path"]).name for row in page._rows]      # noqa: SLF001
        page._table.selectRow(1)                                     # noqa: SLF001
        page._remove_selected_rows()                                 # noqa: SLF001

        after = [Path(row["path"]).name for row in page._rows]       # noqa: SLF001
        assert before[1] not in after, "选中的行没有被移除"
        assert len(after) == 2, f"移除数量不对：{after}"
        assert page._table.rowCount() == 2, "表格没有跟着刷新"
        assert (tmp_path / before[1]).is_file(), "磁盘文件不该被删"
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


def test_convert_remove_without_selection_is_noop(qapp: QApplication, tmp_path: Path) -> None:
    """没选中时什么都不删（而不是错误地删掉全部）。"""
    page = _convert_page(tmp_path, qapp, count=2)
    try:
        page._table.clearSelection()                                 # noqa: SLF001
        page._remove_selected_rows()                                 # noqa: SLF001
        assert len(page._rows) == 2, "没选中却删了东西"               # noqa: SLF001
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


def test_convert_table_has_delete_shortcut(qapp: QApplication, tmp_path: Path) -> None:
    """转换表格挂上了 Delete 快捷键。"""
    page = _convert_page(tmp_path, qapp, count=1)
    try:
        shortcuts = page._delete_shortcuts                            # noqa: SLF001
        assert QKeySequence("Delete").toString() in _keys(shortcuts), (
            f"转换表格没有 Delete：{_keys(shortcuts)}"
        )
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- 不该绑的地方


def test_music_library_has_no_delete_key(qapp: QApplication, tmp_path: Path) -> None:
    """**乐库刻意不绑 Delete**：它的"从曲库移除"没有确认对话框，误按就是丢记录。

    这条用例是"故意不做事"的证据：将来如果有人顺手给乐库加上 Delete，
    这里会失败，逼他先想清楚要不要加确认。
    """
    from modu_workbench.boards.music.library_page import MusicLibraryPage
    from modu_workbench.core.music import MusicLibrary, MusicStorage
    from modu_workbench.ui_kit.toast import Toaster

    host = QWidget()
    host.resize(120, 80)
    storage = MusicStorage(str(tmp_path / "m.db"))
    page = None
    try:
        from modu_workbench.core.music import MusicPlayer

        library = MusicLibrary(storage, tmp_path / "music")
        player = MusicPlayer()
        page = MusicLibraryPage(library, storage, player, Toaster(host))
        bound = [
            shortcut.key().toString()
            for shortcut in page._table.findChildren(QShortcut)      # noqa: SLF001
        ]
        assert QKeySequence("Delete").toString() not in bound, (
            "乐库给表格绑了 Delete，但它的移除没有确认对话框 —— 误按会丢记录"
        )
    finally:
        if page is not None:
            page.close()
            page.deleteLater()
        storage.close()
        host.close()
        qapp.processEvents()
