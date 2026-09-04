"""墨读书库板块：书架 / 阅读 / （在线书库随后接入）。"""
from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from modu_workbench.core.reader import Library, Storage
from modu_workbench.services.config import ensure_legacy_migration
from modu_workbench.ui_kit.toast import Toaster

from .book_reader import ReaderView
from .book_shelf import ShelfView


class BookBoardPage(QWidget):
    """书库板块页面：书架为主页，打开书籍时内嵌阅读视图。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        # 数据层：旧墨读库自动迁移，随后打开读写
        db_path = ensure_legacy_migration()
        self._storage = Storage(db_path)
        self._library = Library(self._storage)
        self._toaster = Toaster(self)

        self._shelf = ShelfView(self._library, self._toaster)
        self._shelf.open_book.connect(self._open_reader)

        self._stack = QStackedWidget()
        self._shelf_index = self._stack.addWidget(self._shelf)
        self._reader: ReaderView | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

    # ---------- 阅读 ----------

    def _open_reader(self, book_id: int) -> None:
        record = self._library.get_book(book_id)
        if record is None:
            self._toaster.error("找不到这本书（可能已被移除）")
            return
        try:
            reader = ReaderView(self._library, record)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"无法打开这本书：{error}")
            return

        reader.back_requested.connect(self._close_reader)
        self._reader = reader
        self._stack.addWidget(reader)
        self._stack.setCurrentWidget(reader)
        reader.setFocus()

    def _close_reader(self) -> None:
        reader = self._reader
        self._reader = None
        if reader is None:
            return
        self._stack.setCurrentIndex(self._shelf_index)
        self._stack.removeWidget(reader)
        reader.deleteLater()
        self._shelf.reload()

    # 供测试 / 复用

    @property
    def library(self) -> Library:
        return self._library

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._reader is not None:
            self._close_reader()
        try:
            self._storage.close()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)
