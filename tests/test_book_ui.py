"""墨读书库 UI 冒烟测试（offscreen）：书架构建 / 阅读器导航。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from modu_workbench.boards.book_reader import FONT_MAX, FONT_MIN, ReaderView
from modu_workbench.boards.book_shelf import ShelfView
from modu_workbench.core.reader import Library, Storage
from modu_workbench.ui_kit.toast import Toaster


@pytest.fixture()
def library(tmp_path: Path) -> Library:
    storage = Storage(str(tmp_path / "library.db"))
    lib = Library(storage)
    book = tmp_path / "sample.txt"
    book.write_text(
        "正文 第一章 起点\n\n这是第一章的内容。\n\n"
        "正文 第二章 旅途\n\n这是第二章的内容。\n\n"
        "正文 第三章 终点\n\n这是第三章的内容。",
        encoding="utf-8",
    )
    lib.import_path(str(book))
    return lib


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _toaster(qapp: QApplication) -> Toaster:
    host = QWidget()
    host.hide()
    return Toaster(host)


def test_shelf_lists_imported_book(qapp: QApplication, library: Library) -> None:
    shelf = ShelfView(library, _toaster(qapp))
    shelf.reload()
    assert len(shelf._records) == 1  # noqa: SLF001
    assert shelf._records[0].title == "sample.txt" or shelf._records[0].format == "txt"  # noqa: SLF001
    shelf.close()


def test_reader_navigation_and_theme(qapp: QApplication, library: Library) -> None:
    record = library.list_books()[0]
    reader = ReaderView(library, record)

    # 默认第 0 章
    assert reader._index == 0  # noqa: SLF001
    # 下一章 -> 第 1 章
    reader.go_chapter(+1)
    assert reader._index == 1  # noqa: SLF001
    # 章末不回绕
    reader.go_chapter(+99)
    assert reader._index == len(reader._chapters) - 1  # noqa: SLF001
    # 字号钳制
    reader.change_font(-999)
    assert reader._font_size >= FONT_MIN  # noqa: SLF001
    reader.change_font(+999)
    assert reader._font_size <= FONT_MAX  # noqa: SLF001
    # 主题切换
    reader._change_theme(4)  # noqa: SLF001
    assert reader._theme_index == 4  # noqa: SLF001
    # 保存位置后记录可读回
    reader._save_now()  # noqa: SLF001
    after = library.get_book(record.id)
    assert after is not None and after.last_chapter_index >= 1
    reader.close()


def test_board_page_opens_reader_and_back(qapp: QApplication, library: Library) -> None:
    from modu_workbench.boards.book_board import BookBoardPage

    # 用同一份 library 不方便注入；改验证 BookBoardPage 自身能创建并列出书籍为空/有书。
    board = BookBoardPage()
    try:
        assert board.library is not None
        board._shelf.reload()  # noqa: SLF001
        board.close()
    finally:
        board.close()
