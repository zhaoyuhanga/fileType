"""墨软乐库：播放历史页。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.music import MusicLibrary, MusicPlayer, MusicStorage
from modu_workbench.ui_kit.toast import Toaster

from .widgets import TRACK_ID_ROLE, configure_table


class MusicHistoryPage(QWidget):
    """播放/下载历史：双击可重新播放。"""

    playRequested = Signal(list, int)   # (tracks, start_index)

    def __init__(self, library: MusicLibrary, storage: MusicStorage, player: MusicPlayer,
                 toaster: Toaster, parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._player = player
        self._toaster = toaster
        self._entries: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 12)
        layout.setSpacing(10)

        title = QLabel("播放历史")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel("记录播放与下载行为，双击可重新播放（文件已删除的记录会跳过）。")
        subtitle.setObjectName("pageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._table = configure_table(QTableWidget(), ["曲名", "歌手", "行为", "时间", "文件"])
        self._table.doubleClicked.connect(lambda _index: self.play_selected())
        layout.addWidget(self._table, 1)

        actions = QHBoxLayout()
        play = QPushButton("播放选中")
        play.setObjectName("primaryButton")
        play.clicked.connect(self.play_selected)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.reload)
        clear = QPushButton("清空历史")
        clear.setObjectName("dangerButton")
        clear.clicked.connect(self._clear)
        for widget in (play, refresh, clear):
            actions.addWidget(widget)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._status = QLabel("就绪。")
        self._status.setObjectName("readerStatus")
        layout.addWidget(self._status)

        self.reload()

    def reload(self) -> None:
        self._entries = self._storage.list_history(300)
        self._table.setRowCount(len(self._entries))
        action_labels = {"play": "播放", "download": "下载"}
        for row, entry in enumerate(self._entries):
            title_item = QTableWidgetItem(entry.title)
            title_item.setData(TRACK_ID_ROLE, entry.track_id)
            self._table.setItem(row, 0, title_item)
            self._table.setItem(row, 1, QTableWidgetItem(entry.artist))
            self._table.setItem(row, 2, QTableWidgetItem(action_labels.get(entry.action, entry.action)))
            stamp = datetime.fromtimestamp(entry.played_at).strftime("%Y-%m-%d %H:%M")
            self._table.setItem(row, 3, QTableWidgetItem(stamp))
            self._table.setItem(row, 4, QTableWidgetItem(entry.path or "-"))
        self._status.setText(f"共 {len(self._entries)} 条记录")

    def selected_track_ids(self) -> list[int]:
        ids: list[int] = []
        model = self._table.selectionModel()
        if model is None:
            return ids
        for index in model.selectedRows():
            item = self._table.item(index.row(), 0)
            if item is not None and item.data(TRACK_ID_ROLE) is not None:
                ids.append(int(item.data(TRACK_ID_ROLE)))
        return ids

    def play_selected(self) -> None:
        ids = self.selected_track_ids()
        if not ids:
            self._toaster.info("请先选择一条历史记录")
            return
        tracks = []
        for track_id in dict.fromkeys(ids):
            track = self._storage.get_track(track_id)
            if track is not None and track.exists:
                tracks.append(track)
        if not tracks:
            self._toaster.error("对应文件已不存在")
            return
        self.playRequested.emit(tracks, 0)

    def _clear(self) -> None:
        self._storage.clear_history()
        self.reload()
        self._toaster.success("已清空播放历史")
