"""墨软乐库界面共享组件：表格、对话框与后台工作线程。"""
from __future__ import annotations

import threading
from typing import Iterable, List

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.music import (
    MusicLibrary,
    MusicStorage,
    RemoteTrack,
    Track,
    download_many,
    format_duration,
    search_all,
)

TRACK_ID_ROLE = Qt.ItemDataRole.UserRole
REMOTE_ROLE = Qt.ItemDataRole.UserRole + 1

SETTINGS_ORG = "ModuWorkbench"
SETTINGS_APP = "modu-workbench"


def music_settings():  # noqa: ANN201
    """应用设置（与设置对话框共用）。"""
    from PySide6.QtCore import QSettings

    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def music_download_dir_pref() -> str:
    """音乐下载目录：优先用户设置，否则默认 ~/Music/墨软乐库。"""
    from modu_workbench.services.config import music_download_dir

    stored = str(music_settings().value("music/download_dir", "", type=str) or "").strip()
    return stored or str(music_download_dir())


def jamendo_client_id_pref() -> str:
    import os

    stored = str(music_settings().value("music/jamendo_client_id", "", type=str) or "").strip()
    return stored or os.environ.get("MODU_JAMENDO_CLIENT_ID", "")


# --------------------------------------------------------------------------- 表格

def configure_table(table: QTableWidget, headers: list[str]) -> QTableWidget:
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setAlternatingRowColors(False)
    table.setShowGrid(False)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def make_item(text: str, checkable: bool = False, checked: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if checkable:
        item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    return item


def fill_track_row(table: QTableWidget, row: int, track: Track, columns: list[str] | None = None) -> None:
    """按「曲名 / 歌手 / 专辑 / 时长 / 格式」顺序填充一行（id 存在首列）。"""
    values = columns or ["title", "artist", "album", "duration", "format"]
    for index, key in enumerate(values):
        if key == "title":
            item = make_item(track.title)
            item.setData(TRACK_ID_ROLE, track.id)
        elif key == "artist":
            item = make_item(track.artist)
        elif key == "album":
            item = make_item(track.album)
        elif key == "duration":
            item = make_item(format_duration(track.duration_ms))
        elif key == "format":
            item = make_item(track.format.upper())
        elif key == "favorite":
            item = make_item("★" if track.favorited else "☆")
        elif key == "category":
            item = make_item(track.category)
        elif key == "size":
            item = make_item(f"{track.size_bytes / 1048576:.1f} MB" if track.size_bytes else "-")
        elif key == "played":
            item = make_item(str(track.play_count))
        else:
            item = make_item("")
        table.setItem(row, index, item)


def fill_remote_row(table: QTableWidget, row: int, remote: RemoteTrack, columns: list[str] | None = None) -> None:
    values = columns or ["title", "artist", "album", "duration", "source"]
    for index, key in enumerate(values):
        if key == "title":
            item = make_item(remote.title, checkable=True)
            item.setData(REMOTE_ROLE, remote.to_json())
        elif key == "artist":
            item = make_item(remote.artist)
        elif key == "album":
            item = make_item(remote.album)
        elif key == "duration":
            item = make_item(format_duration(remote.duration_ms))
        elif key == "source":
            item = make_item(remote.source)
        elif key == "category":
            item = make_item(remote.category)
        else:
            item = make_item("")
        table.setItem(row, index, item)


def checked_track_ids(table: QTableWidget) -> List[int]:
    """只返回「勾选」的曲目 id（非勾选列一律忽略，避免误伤整表）。"""
    ids: list[int] = []
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is None or item.data(TRACK_ID_ROLE) is None:
            continue
        if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            continue
        if item.checkState() != Qt.CheckState.Checked:
            continue
        ids.append(int(item.data(TRACK_ID_ROLE)))
    return ids


def all_track_ids(table: QTableWidget) -> List[int]:
    """当前表格内的全部曲目 id（用于“全部”类操作）。"""
    ids: list[int] = []
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is not None and item.data(TRACK_ID_ROLE) is not None:
            ids.append(int(item.data(TRACK_ID_ROLE)))
    return ids


def row_track_id(table: QTableWidget, row: int) -> int | None:
    item = table.item(row, 0)
    if item is None or item.data(TRACK_ID_ROLE) is None:
        return None
    return int(item.data(TRACK_ID_ROLE))


def row_remote(table: QTableWidget, row: int) -> RemoteTrack | None:
    item = table.item(row, 0)
    if item is None or item.data(REMOTE_ROLE) is None:
        return None
    return RemoteTrack.from_json(item.data(REMOTE_ROLE))


def selected_track_ids(table: QTableWidget) -> List[int]:
    ids: list[int] = []
    for index in table.selectionModel().selectedRows() if table.selectionModel() else []:
        item = table.item(index.row(), 0)
        if item is not None and item.data(TRACK_ID_ROLE) is not None:
            ids.append(int(item.data(TRACK_ID_ROLE)))
    return ids


def action_ids(table: QTableWidget) -> List[int]:
    """操作目标：优先勾选项，其次选中行（单条操作与批量操作共用）。"""
    return checked_track_ids(table) or selected_track_ids(table)


def action_remotes(table: QTableWidget) -> List[RemoteTrack]:
    """在线结果的操作目标：优先勾选项，其次选中行。"""
    remotes = checked_remotes(table)
    if remotes:
        return remotes
    model = table.selectionModel()
    if model is None:
        return []
    remotes = []
    for index in sorted(model.selectedIndexes(), key=lambda item: item.row()):
        remote = row_remote(table, index.row())
        if remote is not None and remote not in remotes:
            remotes.append(remote)
    return remotes


def as_remote_batch(value) -> List[RemoteTrack] | None:  # noqa: ANN001
    """把槽函数收到的参数归一化：按钮 clicked 会传 bool，需忽略。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, RemoteTrack)]
    return [value] if isinstance(value, RemoteTrack) else None


def checked_remotes(table: QTableWidget) -> List[RemoteTrack]:
    remotes: list[RemoteTrack] = []
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is None or item.data(REMOTE_ROLE) is None:
            continue
        if item.checkState() == Qt.CheckState.Checked:
            remotes.append(RemoteTrack.from_json(item.data(REMOTE_ROLE)))
    return remotes


def select_all(table: QTableWidget, checked: bool = True) -> None:
    state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            item.setCheckState(state)


def attach_context_menu(table: QTableWidget, provider) -> None:  # noqa: ANN001
    """给表格挂右键菜单：provider(row) 返回 [(菜单项, 处理函数), ...]。"""
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def show_menu(pos) -> None:  # noqa: ANN001
        row = table.indexAt(pos).row()
        if row < 0:
            return
        entries = provider(row) or []
        if not entries:
            return
        menu = QMenu(table)
        for label, handler in entries:
            action = menu.addAction(label)
            action.triggered.connect(lambda _checked=False, fn=handler: fn())
        menu.exec(table.viewport().mapToGlobal(pos))

    table.customContextMenuRequested.connect(show_menu)


# --------------------------------------------------------------------------- 对话框

def ask_text(parent: QWidget | None, title: str, label: str, default: str = "") -> str | None:
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Normal, default)
    if not ok:
        return None
    text = (text or "").strip()
    return text or None


class PlaylistPicker(QDialog):
    """选择目标歌单（可现场新建）。"""

    def __init__(self, storage: MusicStorage, parent: QWidget | None = None, title: str = "加入歌单"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(320)
        self._storage = storage

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(QLabel("选择歌单："))
        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        create = QPushButton("新建歌单…")
        create.clicked.connect(self._create)
        row.addWidget(create)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        layout.addLayout(row)

        self._reload()

    def _reload(self) -> None:
        self._list.clear()
        for playlist in self._storage.list_playlists():
            label = f"{playlist.name}（{playlist.track_count} 首"
            if playlist.pending_count:
                label += f" + {playlist.pending_count} 待下载"
            label += "）"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, playlist.id)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _create(self) -> None:
        name = ask_text(self, "新建歌单", "歌单名称：")
        if not name:
            return
        playlist_id = self._storage.create_playlist(name)
        self._reload()
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == playlist_id:
                self._list.setCurrentRow(row)
                break

    def selected_playlist_id(self) -> int | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return int(item.data(Qt.ItemDataRole.UserRole))


# --------------------------------------------------------------------------- 后台线程

class SearchWorker(QThread):
    """在线搜索（可跨音源）。"""

    finishedResults = Signal(object, object)  # (List[RemoteTrack], List[str] errors)
    failed = Signal(str)

    def __init__(self, keyword: str, kind: str, source_key: str = "all", limit: int = 30, parent=None):
        super().__init__(parent)
        self._keyword = keyword
        self._kind = kind
        self._source_key = source_key
        self._limit = limit

    def run(self) -> None:  # noqa: D102
        try:
            if self._source_key == "all":
                tracks, errors = search_all(self._keyword, kind=self._kind, limit=self._limit)
            else:
                from modu_workbench.core.music import get_source

                tracks = get_source(self._source_key).search(self._keyword, kind=self._kind, limit=self._limit)
                errors = []
            self.finishedResults.emit(tracks, errors)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class DownloadWorker(QThread):
    """批量下载在线曲目（支持取消；可选跨源兜底）。"""

    progressed = Signal(int, int, str)
    trackDone = Signal(object)      # DownloadResult
    finishedAll = Signal(object)    # List[DownloadResult]
    failed = Signal(str)

    def __init__(self, remotes: Iterable[RemoteTrack], dest_dir: str, save_cover: bool = True,
                 save_lyrics: bool = True, registry=None, allow_cross_source: bool = True, parent=None):
        super().__init__(parent)
        self._remotes = list(remotes)
        self._dest = dest_dir
        self._save_cover = save_cover
        self._save_lyrics = save_lyrics
        self._registry = registry
        self._cross = allow_cross_source
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102
        try:
            results = download_many(
                self._remotes,
                self._dest,
                on_progress=lambda done, total, message: self.progressed.emit(done, total, message),
                on_track_done=lambda result: self.trackDone.emit(result),
                cancel=self._cancel,
                save_cover=self._save_cover,
                save_lyrics=self._save_lyrics,
                registry=self._registry,
                allow_cross_source=self._cross,
            )
            self.finishedAll.emit(results)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class ConvertWorker(QThread):
    """音乐格式转换（逐个曲目，支持取消）。"""

    progressed = Signal(int, int, str)
    finishedAll = Signal(object, object)  # (ok paths, errors)
    failed = Signal(str)

    def __init__(self, library: MusicLibrary, track_ids: Iterable[int], target_format: str,
                 output_dir: str, parent=None):
        super().__init__(parent)
        self._library = library
        self._ids = list(track_ids)
        self._target = target_format
        self._out = output_dir
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102
        outputs: list[str] = []
        errors: list[str] = []
        total = len(self._ids)
        try:
            for index, track_id in enumerate(self._ids, start=1):
                if self._cancel.is_set():
                    errors.append("已取消")
                    break
                track = self._library.storage.get_track(track_id)
                name = track.display() if track else str(track_id)
                self.progressed.emit(index - 1, total, f"[{index}/{total}] {name}")
                try:
                    path = self._library.convert(track_id, self._target, self._out, cancel=self._cancel)
                    outputs.append(str(path))
                except Exception as error:  # noqa: BLE001
                    errors.append(f"{name}：{error}")
            self.progressed.emit(len(outputs), total, f"完成 {len(outputs)}/{total}")
            self.finishedAll.emit(outputs, errors)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))
