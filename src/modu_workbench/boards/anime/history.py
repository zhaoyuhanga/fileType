"""墨软动漫 · 播放历史：看过什么、看到哪一集、用的哪条规则，并可一键续看。

与影视板块的历史页同构（`boards/video/history.py`），差别只在数据源：
这里读 `anime_history` 表（**快照式**存 title/episode_name/rule_key ——
番剧行被删掉后历史仍然可读，这是 `v3_anime` 迁移里刻意的设计）。

在线播放地址会过期，所以"继续播放"永远是**按记录重新解析**，不缓存直链。

后端 `AnimeStorage` 的方法名由另一个模块并行开发，这里全部**鸭子类型**调用：
`list_history()` / `history()` / `clear_history()` 任一存在即可用，
都不存在时页面显示空状态并写明原因，而不是抛异常。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.ui_kit.components import (
    ColumnPage,
    EmptyState,
    SectionCard,
    TaskBar,
    chip,
    ghost_button,
    primary_button,
    row,
)
from modu_workbench.ui_kit.toast import Toaster

COLUMNS = ["番剧", "剧集", "规则", "动作", "时间"]
SERIES_ROLE = Qt.ItemDataRole.UserRole


def _format_time(stamp: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(stamp)))
    except (ValueError, OSError, TypeError):
        return "-"


class AnimeHistoryPage(QWidget):
    """播放历史（含"继续观看"入口与清空）。"""

    playRequested = Signal(object, object)   # series/history entry, episode | None
    libraryChanged = Signal()

    def __init__(self, library=None, storage=None, toaster: Toaster | None = None,  # noqa: ANN001
                 parent: QWidget | None = None, *, task_bar: TaskBar | None = None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._toaster = toaster or Toaster(self)
        self._task = task_bar
        self._entries: list = []
        self._storage_error = ""
        self._tail_stretch = False
        self._fallback_status = QLabel("就绪。")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._page = ColumnPage(
            "历史",
            "记录每一次播放（含剧集与使用的规则）。双击一条即可按记录重新解析地址续看 ——"
            "在线直链会过期，所以每次都会重新取。",
        )
        outer.addWidget(self._page)

        self._count_chip = chip("0 条")
        refresh = ghost_button("刷新")
        refresh.clicked.connect(self.reload)
        self._card = SectionCard("播放历史", actions=[self._count_chip, refresh])
        self._action = QComboBox()
        self._action.addItem("全部记录", "")
        self._action.addItem("只看有进度的", "progress")
        self._action.currentIndexChanged.connect(lambda _index: self.reload())
        self._card.add(row(QLabel("筛选"), self._action))

        self._table = QTableWidget()
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.doubleClicked.connect(lambda _index: self._continue_watching())
        self._empty = EmptyState(
            "🕘", "还没有播放记录",
            "播放过的番剧会自动记录在这里；追番后也可以在「番剧库」的继续观看里找到")
        self._card.add(self._empty)
        self._card.add(self._table)
        self._table.setVisible(False)

        self._play_button = primary_button("▶ 继续观看")
        self._play_button.clicked.connect(self._continue_watching)
        self._favorite_button = ghost_button("去「番剧库」")
        self._favorite_button.clicked.connect(self._goto_library)
        clear = QPushButton("清空历史")
        clear.setObjectName("dangerButton")
        clear.clicked.connect(self._clear)
        self._card.add(row(self._play_button, self._favorite_button, clear, stretch_last=True))
        self._page.add(self._card)
        self._set_tail_stretch(True)
        self.reload()

    # ------------------------------------------------------------------ 状态

    @property
    def _status(self) -> QLabel:
        """兼容旧引用：状态文字统一显示在板块任务条上。"""
        return self._task._label if self._task is not None else self._fallback_status  # noqa: SLF001

    def _note_fallback(self, message: str) -> None:
        label = getattr(self, "_fallback_status", None)
        if label is not None:
            label.setText(message)

    def _report_status(self, message: str) -> None:
        self._note_fallback(message)
        if self._task is not None:
            self._task.note(message)

    def _set_tail_stretch(self, enabled: bool) -> None:
        layout = self._page.body
        if enabled and not self._tail_stretch:
            layout.addStretch(1)
            self._tail_stretch = True
        elif not enabled and self._tail_stretch and layout.count():
            layout.takeAt(layout.count() - 1)
            self._tail_stretch = False

    # ------------------------------------------------------------------ 存储

    def storage(self):  # noqa: ANN201
        """动漫存储：构造时没给就按需取（取不到返回 None 并记下原因）。"""
        if self._storage is None and not self._storage_error:
            try:
                from . import context as app_context

                self._storage = app_context.anime_storage()
            except Exception as error:  # noqa: BLE001
                self._storage_error = str(error) or error.__class__.__name__
        return self._storage

    # ------------------------------------------------------------------ 数据

    def reload(self) -> None:
        storage = self.storage()
        if storage is None:
            self._entries = []
            self._note_fallback(f"动漫后端未就绪：{self._storage_error or '核心模块还没装上'}")
        else:
            self._entries = self._read_history(storage)
        self._fill_table()
        self._count_chip.setText(f"{len(self._entries)} 条")
        self._empty.setVisible(not self._entries)
        self._table.setVisible(bool(self._entries))
        self._set_tail_stretch(not self._entries)
        self._play_button.setEnabled(bool(self._entries))
        if not self._entries and storage is not None:
            self._note_fallback("还没有播放记录。去「搜索」页找一部番剧看看？")

    def _read_history(self, storage) -> list:  # noqa: ANN001
        """读历史：兼容 `list_history()` / `history()` 两种命名，失败给空列表。"""
        for name in ("list_history", "history", "recent_history"):
            getter = getattr(storage, name, None)
            if not callable(getter):
                continue
            try:
                entries = list(getter(limit=400) or [])
            except TypeError:
                try:
                    entries = list(getter() or [])
                except Exception as error:  # noqa: BLE001
                    self._note_fallback(f"读取历史失败：{error}")
                    return []
            except Exception as error:  # noqa: BLE001
                self._note_fallback(f"读取历史失败：{error}")
                return []
            if self._action.currentData() == "progress":
                entries = [item for item in entries
                           if int(getattr(item, "position_ms", 0) or 0) > 0]
            return entries
        self._storage_error = "存储没有可用的历史接口（list_history/history）"
        self._note_fallback(f"动漫后端未就绪：{self._storage_error}")
        return []

    def _fill_table(self) -> None:
        self._table.setRowCount(len(self._entries))
        for index, entry in enumerate(self._entries):
            values = [
                str(getattr(entry, "title", "") or "-"),
                str(getattr(entry, "episode_name", "") or getattr(entry, "episode_label", "") or "-"),
                str(getattr(entry, "rule_key", "") or getattr(entry, "source", "") or "-"),
                "播放",
                _format_time(getattr(entry, "played_at", 0) or 0),
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(SERIES_ROLE, entry)
                self._table.setItem(index, column, item)
        self._table.resizeColumnsToContents()

    def current_entry(self):  # noqa: ANN201
        row = self._table.currentRow()
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    # ------------------------------------------------------------------ 动作

    def _continue_watching(self) -> None:
        entry = self.current_entry()
        if entry is None:
            self._toaster.info("请先选中一条记录")
            return
        series = getattr(entry, "series", None)
        episode = getattr(entry, "episode", None)
        if series is None and self._library is not None:
            series = self._find_series(entry)
        if series is None:
            # 番剧行可能已被删除：用历史快照也能重新搜索，但这里只诚实提示
            self._toaster.error("找不到对应的番剧记录（可能已从番剧库移除），请重新搜索")
            return
        self.playRequested.emit(series, episode)
        self._report_status(f"继续观看：{getattr(entry, 'title', '')}")

    def _find_series(self, entry):  # noqa: ANN001
        """按历史里的 series_id 在追番/库里找番剧对象。"""
        series_id = int(getattr(entry, "series_id", 0) or 0)
        if not series_id:
            return None
        for name in ("series", "get_series", "series_by_id"):
            getter = getattr(self._library, name, None)
            if not callable(getter):
                continue
            try:
                found = getter(series_id)
            except Exception:  # noqa: BLE001
                return None
            if found is not None:
                return found
        return None

    def _goto_library(self) -> None:
        self._report_status("在左侧导航点「📺 番剧库」查看追番与继续观看")

    def _clear(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self, "清空历史", "确定清空全部播放历史吗？（不影响追番与进度）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        storage = self.storage()
        if storage is None or not hasattr(storage, "clear_history"):
            self._toaster.error("动漫后端未就绪：无法清空历史")
            return
        try:
            storage.clear_history()
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"清空失败：{error}")
            return
        self._toaster.success("已清空历史")
        self.reload()
        self.libraryChanged.emit()

    def on_shown(self) -> None:
        self.reload()

    def shutdown(self) -> None:
        return


__all__ = ["AnimeHistoryPage", "COLUMNS", "SERIES_ROLE"]
