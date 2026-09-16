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
    QStackedWidget,
)

from modu_workbench.core.video import VideoLibrary, VideoStorage
from modu_workbench.ui_kit.components import (
    ColumnPage,
    EmptyState,
    SectionCard,
    chip,
    ghost_button,
    primary_button,
    row,
)
from modu_workbench.ui_kit.toast import Toaster

from .widgets import VIDEO_ID_ROLE, attach_context_menu, configure_table, select_all

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
                 parent: QWidget | None = None, *, task_bar=None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._toaster = toaster
        self._task = task_bar
        self._entries: list = []
        self._tail_stretch = False
        self._fallback_status = QLabel("就绪。")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._page = ColumnPage(
            "播放历史",
            "记录每一次播放与下载（含集数、画质与实际使用的源）。双击一条记录即可重新解析地址继续播放"
            "—— 在线直链会过期，所以每次都会重新取。",
            actions=[ghost_button("刷新")],
        )
        refresh = self._page.header.findChildren(QPushButton)[0]
        refresh.clicked.connect(self.reload)
        outer.addWidget(self._page)

        self._count_chip = chip("0 条")
        self._history_card = SectionCard("历史记录", actions=[self._count_chip])
        self._action = QComboBox()
        self._action.addItem("全部记录", "")
        self._action.addItem("仅播放", "play")
        self._action.addItem("仅下载", "download")
        self._action.currentIndexChanged.connect(lambda _index: self.reload())
        self._history_card.add(row(QLabel("筛选"), self._action, stretch_last=True))

        self._table = configure_table(QTableWidget(), COLUMNS)
        self._table.doubleClicked.connect(lambda _index: self._play_action())
        attach_context_menu(self._table, self._row_menu)
        self._empty = EmptyState(
            "🕘", "还没有播放记录",
            "播放过的内容会自动记录在这里（在线直链会过期，双击会重新解析）",
        )
        self._history_card.add(self._empty)
        self._history_card.add(self._table)
        self._table.setVisible(False)          # 无数据时显示空状态，有数据时切回表格

        self._play_button = primary_button("▶ 继续播放")
        self._play_button.clicked.connect(self._play_action)
        self._download_button = ghost_button("⬇ 下载这一集")
        self._download_button.clicked.connect(self._download_action)
        self._favorite_button = ghost_button("★ 收藏 / 取消")
        self._favorite_button.clicked.connect(self._toggle_favorite)
        clear = QPushButton("清空历史")
        clear.setObjectName("dangerButton")
        clear.clicked.connect(self._clear)
        self._history_card.add(row(self._play_button, self._download_button,
                                   self._favorite_button, clear, stretch_last=True))
        self._page.add(self._history_card)
        self._set_tail_stretch(True)
        self._count = self._count_chip      # 兼容旧引用（筛选行右侧计数）


    def _set_tail_stretch(self, enabled: bool) -> None:
        """页面末尾弹簧：列表为空时插入，让卡片保持自然高度（避免行与行被拉开）。"""
        layout = self._page.body
        if enabled and not self._tail_stretch:
            layout.addStretch(1)
            self._tail_stretch = True
        elif not enabled and self._tail_stretch and layout.count():
            layout.takeAt(layout.count() - 1)
            self._tail_stretch = False

    # ------------------------------------------------------------------ 状态

    @property
    def _status(self) -> QLabel:
        """兼容旧引用：状态文字统一显示在板块任务条上。"""
        return self._task._label if self._task is not None else self._fallback_status   # noqa: SLF001

    def _report(self, message: str) -> None:
        self._fallback_status.setText(message)
        if self._task is not None:
            self._task.idle(message)

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
        # 有记录给表格，无记录给空状态（不留一张空表格）
        self._empty.setVisible(not self._entries)
        self._table.setVisible(bool(self._entries))
        self._set_tail_stretch(not self._entries)
        if not self._entries:
            self._fallback_status.setText("还没有记录。去「搜索下载」找一部片子看看？")
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
        self._report(f"继续播放：{video.display()}")

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
