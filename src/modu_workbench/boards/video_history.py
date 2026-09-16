"""墨软影视 · 播放历史：看过什么、看到哪一集、用的哪个源，并可一键续播。

覆盖诉求 5（历史播放记录）与「继续观看」体验：
- 历史按时间倒序，记录集数 / 画质 / 来源；
- 双击即可按记录重新解析并继续播放（直链会过期，因此每次播放都重新解析）；
- 支持按「仅播放 / 仅下载」过滤与清空。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.video import VideoLibrary, VideoStorage
from modu_workbench.ui_kit.toast import Toaster

from .video_widgets import VIDEO_ID_ROLE, attach_context_menu, configure_table, select_all

COLUMNS = ["片名", "集", "画质", "动作", "来源", "时间"]

_ACTION_LABELS = {"play": "播放", "download": "下载"}


def _format_time(stamp: int) -> str:
    import time

    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(stamp)))
    except (ValueError, OSError):
        return "-"


class VideoHistoryPage(QWidget):
    """播放 / 下载历史。"""

    playRequested = Signal(list, int)   # List[Video], start_index
    downloadRequested = Signal(list)    # List[Video]：对在线条目重新下载
    libraryChanged = Signal()

    def __init__(self, library: VideoLibrary, storage: VideoStorage, toaster: Toaster,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._toaster = toaster
        self._entries: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 12)
        layout.setSpacing(10)

        title = QLabel("播放历史")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "记录每一次播放与下载（含集数、画质与实际使用的源）。"
            "双击一条记录即可重新解析地址继续播放 —— 在线直链会过期，所以每次都会重新取。"
        )
        subtitle.setObjectName("pageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("筛选"))
        self._action = QComboBox()
        self._action.addItem("全部记录", "")
        self._action.addItem("仅播放", "play")
        self._action.addItem("仅下载", "download")
        self._action.currentIndexChanged.connect(lambda _index: self.reload())
        filter_row.addWidget(self._action)
        filter_row.addStretch(1)
        self._count = QLabel("")
        self._count.setObjectName("readerStatus")
        filter_row.addWidget(self._count)
        layout.addLayout(filter_row)

        self._table = configure_table(QTableWidget(), COLUMNS)
        self._table.doubleClicked.connect(lambda _index: self._play_action())
        attach_context_menu(self._table, self._row_menu)
        layout.addWidget(self._table, 1)

        actions = QHBoxLayout()
        self._play_button = QPushButton("▶ 继续播放")
        self._play_button.setObjectName("primaryButton")
        self._play_button.clicked.connect(self._play_action)
        actions.addWidget(self._play_button)

        self._download_button = QPushButton("⬇ 下载这一集")
        self._download_button.clicked.connect(self._download_action)
        actions.addWidget(self._download_button)

        self._favorite_button = QPushButton("★ 收藏 / 取消")
        self._favorite_button.clicked.connect(self._toggle_favorite)
        actions.addWidget(self._favorite_button)

        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.reload)
        actions.addWidget(refresh)

        clear = QPushButton("清空历史")
        clear.setObjectName("dangerButton")
        clear.clicked.connect(self._clear)
        actions.addWidget(clear)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._status = QLabel("就绪。")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self.reload()

    # ------------------------------------------------------------------ 数据

    def reload(self) -> None:
        action = self._action.currentData() or ""
        self._entries = self._storage.list_history(limit=400, action=action)
        self._table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            values = [
                entry.title,
                entry.episode_label or "-",
                entry.quality or "-",
                _ACTION_LABELS.get(entry.action, entry.action),
                entry.source or "-",
                _format_time(entry.played_at),
            ]
            for index, text in enumerate(values):
                item = QTableWidgetItem(text)
                if index == 0:
                    item.setData(VIDEO_ID_ROLE, entry.video_id or None)
                    item.setToolTip(entry.path or entry.title)
                self._table.setItem(row, index, item)
        select_all(self._table, False)
        self._count.setText(f"共 {len(self._entries)} 条")
        if not self._entries:
            self._status.setText("还没有记录。去「搜索下载」找一部片子看看？")
        self._update_buttons()

    def _selected_entry(self):  # noqa: ANN201
        row = self._table.currentRow()
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    # ------------------------------------------------------------------ 动作

    def _row_menu(self, row: int) -> list[tuple[str, object]]:
        if not (0 <= row < len(self._entries)):
            return []
        entry = self._entries[row]
        return [
            ("▶ 继续播放", lambda: self._play_action(row)),
            ("⬇ 下载这一集", lambda: self._download_action(row)),
            ("★ 收藏 / 取消", lambda: self._toggle_favorite(row)),
        ]

    def _play_action(self, row: int | None = None) -> None:
        entry = self._entry_at(row)
        if entry is None:
            self._toaster.info("请先选中一条记录")
            return
        video = self._library.storage.get_video(entry.video_id)
        if video is None:
            self._toaster.error("该条目已从库中删除，无法继续播放")
            return
        # 本地文件还在 → 直接播本地；否则按记录重新解析在线地址
        self.playRequested.emit([video], 0)
        self._status.setText(f"继续播放：{video.display()}")

    def _download_action(self, row: int | None = None) -> None:
        entry = self._entry_at(row)
        if entry is None:
            self._toaster.info("请先选中一条记录")
            return
        video = self._library.storage.get_video(entry.video_id)
        if video is None:
            self._toaster.error("该条目已从库中删除")
            return
        if not video.online_only:
            self._toaster.info("该条目已经是本地文件，无需重新下载")
            return
        self.downloadRequested.emit([video])

    def _toggle_favorite(self, row: int | None = None) -> None:
        entry = self._entry_at(row)
        if entry is None:
            self._toaster.info("请先选中一条记录")
            return
        favorited = self._library.toggle_favorite(entry.video_id)
        self._toaster.success("已收藏" if favorited else "已取消收藏")
        self.libraryChanged.emit()

    def _entry_at(self, row: int | None):  # noqa: ANN201
        if row is None or isinstance(row, bool):
            return self._selected_entry()
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def _clear(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self, "清空历史", "确定清空全部播放/下载历史吗？（不影响已入库的视频）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._storage.clear_history()
        self._toaster.success("已清空历史")
        self.reload()

    def _update_buttons(self) -> None:
        has_rows = bool(self._entries)
        for button in (self._play_button, self._download_button, self._favorite_button):
            button.setEnabled(has_rows)

    def on_shown(self) -> None:
        self.reload()

    def shutdown(self) -> None:
        return
