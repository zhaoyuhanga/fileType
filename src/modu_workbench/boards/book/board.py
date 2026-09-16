"""墨软书库板块：书架 / 在线书库 / 阅读视图。"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from modu_workbench.core.book import Library
from . import context as app_context
from modu_workbench.ui_kit.components import TaskBar
from modu_workbench.ui_kit.tokens import PAGE_MARGIN, ROW_GAP, SPACE
from modu_workbench.ui_kit.toast import Toaster
from modu_workbench.ui_kit.widgets import make_nav_button, set_nav_active

from .online import OnlineDownloadPage
from .reader import ReaderView
from .shelf import ShelfView

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

        # 统一任务条：在线书库的下载进度与状态汇总到这里（空闲自动收起）
        self._task_bar = TaskBar("就绪。可导入本地 TXT / EPUB，或从在线书库下载。")
        self._shelf = ShelfView(self._library, self._toaster)
        self._shelf.open_book.connect(self._open_reader)
        self._online = OnlineDownloadPage(self._library, self._toaster, task_bar=self._task_bar)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._mode_bar = QWidget()
        mode_row = QHBoxLayout(self._mode_bar)
        mode_row.setContentsMargins(PAGE_MARGIN, SPACE["md"], PAGE_MARGIN, SPACE["sm"])
        mode_row.setSpacing(ROW_GAP)
        self._mode_buttons: dict[str, QWidget] = {}
        for key, label in ((SHELF_KEY, "📚 书架"), (ONLINE_KEY, "🌐 在线书库")):
            button = make_nav_button(label)
            button.clicked.connect(lambda _=False, k=key: self._show_mode(k))
            self._mode_buttons[key] = button
            mode_row.addWidget(button)

        settings_button = make_nav_button("⚙ 板块设置")
        settings_button.setToolTip("打开「设置 → 书库」：合规开关、字号、行距、进度记忆")
        settings_button.clicked.connect(self._open_board_settings)
        mode_row.addWidget(settings_button)
        mode_row.addStretch(1)
        layout.addWidget(self._mode_bar)

        self._stack = QStackedWidget()
        self._shelf_index = self._stack.addWidget(self._shelf)
        self._online_index = self._stack.addWidget(self._online)
        self._reader: ReaderView | None = None
        layout.addWidget(self._stack, 1)
        layout.addWidget(self._task_bar)

        self._show_mode(SHELF_KEY)

    # ---------- 模式切换 ----------

    def show_page(self, key: str) -> None:
        """与其他板块一致的子页切换入口（shelf / online），便于截图与自动化。"""
        if key in (SHELF_KEY, ONLINE_KEY):
            self._show_mode(key)

    def _show_mode(self, key: str) -> None:
        index = self._shelf_index if key == SHELF_KEY else self._online_index
        self._stack.setCurrentIndex(index)
        for mode_key, button in self._mode_buttons.items():
            set_nav_active(button, mode_key == key)
        if key == SHELF_KEY:
            self._shelf.reload()

    def _open_board_settings(self) -> None:
        """打开统一设置对话框的「书库」页（设置按板块区分）。"""
        from modu_workbench.ui_kit.settings import SettingsDialog

        SettingsDialog(self, initial="book").exec()
        self._show_mode(SHELF_KEY)

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
