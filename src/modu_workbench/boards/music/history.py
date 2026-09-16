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
from modu_workbench.ui_kit.components import EmptyState
from modu_workbench.ui_kit.toast import Toaster

from .widgets import TRACK_ID_ROLE, configure_table


class MusicHistoryPage(QWidget):
    """播放/下载历史：双击可重新播放。"""

    playRequested = Signal(list, int)   # (tracks, start_index)

    # ------------------------------------------------------------------ 状态

    @property
    def _status(self) -> QLabel:
        """兼容旧引用：状态文字统一显示在板块任务条上。"""
        return self._task._label if self._task is not None else self._fallback_status   # noqa: SLF001

    def _report(self, message: str, done: int = 0, total: int = 0) -> None:
        self._fallback_status.setText(message)
        if self._task is not None:
            if done or total:
                self._task.report(message, done, total)
            else:
                self._task.note(message)      # 纯信息：不显示进度条/取消

    def _busy(self, message: str) -> None:
        """进行中提示（搜索/导入/解析这类未知时长）。"""
        self._fallback_status.setText(message)
        if self._task is not None:
            self._task.busy(message)

    def _idle(self, message: str = "") -> None:
        if message:
            self._fallback_status.setText(message)
        if self._task is not None:
            self._task.idle(message)

    def _set_progress(self, value: int) -> None:
        """兼容旧写法：只更新进度数值；是否显示进度条由 report/busy/idle 决定。"""
        bar = self._task_bar._progress if hasattr(self, "_task_bar") else (
            self._task._progress if self._task is not None else None)     # noqa: SLF001
        if bar is not None:
            bar.setRange(0, 100)
            bar.setValue(int(value))

    def __init__(self, library: MusicLibrary, storage: MusicStorage, player: MusicPlayer,
                 toaster: Toaster, parent: QWidget | None = None, *, task_bar=None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._task = task_bar
        self._fallback_status = QLabel("就绪。")
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
        self._empty = EmptyState(
            "🕘", "还没有播放记录",
            "播放或下载过的曲目会自动记录在这里",
        )
        self._empty.setMaximumHeight(240)
        layout.addWidget(self._empty, 1 if 'layout' == 'layout' else 2)
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

        self._status_label = QLabel("就绪。")
        self._status_label.setObjectName("readerStatus")
        self._status_label.setVisible(False)            # 状态统一显示在板块任务条

        self.reload()

    def reload(self) -> None:
        self._entries = self._storage.list_history(300)
        self._table.setRowCount(len(self._entries))
        self._empty.setVisible(not self._entries)
        self._table.setVisible(bool(self._entries))
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
        self._report(f"共 {len(self._entries)} 条记录")

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
