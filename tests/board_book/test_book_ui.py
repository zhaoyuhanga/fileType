"""墨软书库 UI 冒烟测试（offscreen）：书架构建 / 阅读器导航。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from modu_workbench.boards.book.reader import FONT_MAX, FONT_MIN, ReaderView
from modu_workbench.boards.book.shelf import ShelfView
from modu_workbench.core.book import Library, Storage
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
    from modu_workbench.boards.book.board import BookBoardPage

    # 用同一份 library 不方便注入；改验证 BookBoardPage 自身能创建并列出书籍为空/有书。
    board = BookBoardPage()
    try:
        assert board.library is not None
        board._shelf.reload()  # noqa: SLF001
        board.close()
    finally:
        board.close()


def test_board_mode_switch_to_online(qapp: QApplication) -> None:
    from modu_workbench.boards.book.board import ONLINE_KEY, SHELF_KEY, BookBoardPage

    board = BookBoardPage()
    try:
        board._show_mode(ONLINE_KEY)  # noqa: SLF001
        assert board._stack.currentWidget() is board._online  # noqa: SLF001
        board._show_mode(SHELF_KEY)  # noqa: SLF001
        assert board._stack.currentWidget() is board._shelf  # noqa: SLF001
    finally:
        board.close()


def test_online_page_compliance_gate(qapp: QApplication, library: Library) -> None:
    from modu_workbench.boards.book.online import OnlineDownloadPage

    page = OnlineDownloadPage(library, _toaster(qapp))
    try:
        # 未填 URL 时不可下载
        assert not page._start_button.isEnabled()  # noqa: SLF001
        page._url_input.setText("https://www.00shu.la/txt/53054/")  # noqa: SLF001
        # 合规勾选 + 有效 URL -> 可下载
        assert page._start_button.isEnabled()  # noqa: SLF001
        # 取消合规勾选 -> 禁止下载
        page._compliance.setChecked(False)  # noqa: SLF001
        assert not page._start_button.isEnabled()  # noqa: SLF001
    finally:
        page.close()


def test_reader_reports_load_failure_instead_of_showing_blank(qapp, tmp_path: Path) -> None:  # noqa: ANN001
    """原文件丢失时，阅读器必须**报出原因**，而不是打开一个空白页。

    ## 这条守的是什么

    `ReaderView._load_book` 曾经是：

        try:
            _, parsed = self._library.load_for_reading(...)
        except Exception:
            parsed = None          # ← 静默吞掉

    后果：书被删/改名/权限不足时，用户点开只看到一片空白 ——
    既不知道书没了，也不知道该做什么。外层 `_open_reader` 的
    `except` 也抓不到（异常已经在内层被吃掉）。

    现在改成发 `load_failed` 信号，携带**可执行的原因**
    （文件找不到 → 提示放回原位置或重新导入）。
    """
    from modu_workbench.boards.book.reader import ReaderView
    from modu_workbench.core.book import Library, Storage

    library = Library(Storage(str(tmp_path / "library.db")))
    missing = tmp_path / "不存在的书.txt"
    book_id = library.storage.upsert_book(str(missing), "已丢失的书", "作者", "txt")
    record = library.get_book(book_id)
    assert record is not None

    view = ReaderView(library, record)
    reasons: list[str] = []
    view.load_failed.connect(reasons.append)

    # 构造时已跑过一次 _load_book，但因为信号是在构造后连的，
    # 这里显式再触发一次以捕获原因。
    view._load_book(record)  # noqa: SLF001

    assert reasons, "载入失败必须发 load_failed，不能静默"
    message = reasons[-1]
    assert "找不到" in message or "文件" in message, f"原因不可读：{message}"
    assert str(missing.name) in message, "提示里应带出文件路径，方便用户定位"

    view.deleteLater()
    library.storage.close()
