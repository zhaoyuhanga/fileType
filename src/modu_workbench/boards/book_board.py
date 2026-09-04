"""墨读书库板块：书架 / 在线书库 / 阅读视图。"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from modu_workbench.core.reader import Library
from modu_workbench.services import app_context
from modu_workbench.ui_kit.toast import Toaster
from modu_workbench.ui_kit.widgets import make_nav_button, set_nav_active

from .book_online import OnlineDownloadPage
from .book_reader import ReaderView
from .book_shelf import ShelfView

SHELF_KEY = "shelf"
ONLINE_KEY = "online"


class BookBoardPage(QWidget):
    """书库板块页面：书架为主视图，阅读时全屏阅读器。

    数据层使用应用级共享书库（services.app_context），与"转换→加入书库"联动。
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._library = app_context.library()
        self._toaster = Toaster(self)

        self._shelf = ShelfView(self._library, self._toaster)
        self._shelf.open_book.connect(self._open_reader)
        self._online = OnlineDownloadPage(self._library, self._toaster)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._mode_bar = QWidget()
        mode_row = QHBoxLayout(self._mode_bar)
        mode_row.setContentsMargins(16, 8, 16, 4)
        mode_row.setSpacing(8)
        self._mode_buttons: dict[str, QWidget] = {}
        for key, label in ((SHELF_KEY, "📚 书架"), (ONLINE_KEY, "🌐 在线书库")):
            button = make_nav_button(label)
            button.clicked.connect(lambda _=False, k=key: self._show_mode(k))
            self._mode_buttons[key] = button
            mode_row.addWidget(button)
        mode_row.addStretch(1)
        layout.addWidget(self._mode_bar)

        self._stack = QStackedWidget()
        self._shelf_index = self._stack.addWidget(self._shelf)
        self._online_index = self._stack.addWidget(self._online)
        self._reader: ReaderView | None = None
        layout.addWidget(self._stack, 1)

        self._show_mode(SHELF_KEY)

    # ---------- 模式切换 ----------

    def _show_mode(self, key: str) -> None:
        index = self._shelf_index if key == SHELF_KEY else self._online_index
        self._stack.setCurrentIndex(index)
        for mode_key, button in self._mode_buttons.items():
            set_nav_active(button, mode_key == key)
        if key == SHELF_KEY:
            self._shelf.reload()

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
        self._mode_bar.hide()
        self._stack.addWidget(reader)
        self._stack.setCurrentWidget(reader)
        reader.setFocus()

    def _close_reader(self) -> None:
        reader = self._reader
        self._reader = None
        if reader is None:
            return
        self._stack.removeWidget(reader)
        reader.deleteLater()
        self._mode_bar.show()
        self._show_mode(SHELF_KEY)

    @property
    def library(self) -> Library:
        return self._library
