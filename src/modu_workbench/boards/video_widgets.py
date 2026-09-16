"""墨软影视界面共享组件：表格、对话框与后台工作线程。

线程模型与墨软乐库一致：所有联网/磁盘操作都放在 QThread 里，
通过信号回到 UI 线程更新界面，绝不阻塞主线程。
"""
from __future__ import annotations

import threading
from typing import Iterable, List

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from modu_workbench.core.video import (
    MEDIA_KIND_LABELS,
    Episode,
    Quality,
    RemoteVideo,
    Video,
    VideoLibrary,
    VideoRegistry,
    VideoStorage,
)
from modu_workbench.core.video import hls
from modu_workbench.services import app_context

REMOTE_ROLE = Qt.ItemDataRole.UserRole + 1
VIDEO_ID_ROLE = Qt.ItemDataRole.UserRole
QUALITY_ROLE = Qt.ItemDataRole.UserRole + 2

SETTINGS_ORG = "ModuWorkbench"
SETTINGS_APP = "modu-workbench"


def video_settings():  # noqa: ANN201
    """应用设置（与设置对话框共用）。"""
    from PySide6.QtCore import QSettings

    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def video_download_dir_pref() -> str:
    """影视下载目录：优先用户设置，否则默认 ~/Videos/墨软影视。"""
    from modu_workbench.services.config import video_download_dir

    stored = str(video_settings().value("video/download_dir", "", type=str) or "").strip()
    return stored or str(video_download_dir())


def video_auto_switch_pref() -> bool:
    value = video_settings().value("video/auto_switch", True)
    if isinstance(value, bool):
        return value
    return str(value).lower() not in ("false", "0", "no", "")


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


def remote_columns(remote: RemoteVideo) -> list[str]:
    """搜索结果的列内容：片名 / 类型 / 年份 / 地区 / 集数 / 备注 / 源。"""
    episodes = remote.episode_count
    return [
        remote.title,
        MEDIA_KIND_LABELS.get(remote.kind, remote.kind),
        remote.year or "-",
        remote.region or "-",
        f"{episodes} 集" if episodes else "待解析",
        remote.remarks or remote.category or "-",
        remote.source,
    ]


def fill_remote_row(table: QTableWidget, row: int, remote: RemoteVideo) -> None:
    values = remote_columns(remote)
    for index, text in enumerate(values):
        if index == 0:
            item = make_item(text, checkable=True)
            item.setData(REMOTE_ROLE, remote.to_json())
            item.setToolTip(remote.description or remote.title)
        else:
            item = make_item(text)
        table.setItem(row, index, item)


def fill_video_row(table: QTableWidget, row: int, video: Video, columns: list[str] | None = None) -> None:
    """本地/已入库条目：片名 / 类型 / 集 / 画质 / 时长 / 大小 / 来源。"""
    keys = columns or ["title", "kind", "episode", "quality", "duration", "size", "source"]
    for index, key in enumerate(keys):
        if key == "title":
            item = make_item(video.title, checkable=True)
            item.setData(VIDEO_ID_ROLE, video.id)
            item.setToolTip(video.description or video.title)
        elif key == "kind":
            item = make_item(video.kind_label)
        elif key == "episode":
            item = make_item(video.episode_label or "-")
        elif key == "quality":
            item = make_item(video.quality or "-")
        elif key == "duration":
            item = make_item(video.duration_text)
        elif key == "size":
            item = make_item(f"{video.size_bytes / 1048576:.1f} MB" if video.size_bytes else "-")
        elif key == "source":
            item = make_item(video.source or "-")
        elif key == "category":
            item = make_item(video.category or "未分类")
        elif key == "favorite":
            item = make_item("★" if video.favorited else "☆")
        elif key == "local":
            item = make_item("本地" if video.exists else ("在线" if video.online_only else "文件缺失"))
        elif key == "year":
            item = make_item(video.year or "-")
        elif key == "played":
            item = make_item(str(video.play_count))
        else:
            item = make_item("")
        table.setItem(row, index, item)


def row_remote(table: QTableWidget, row: int) -> RemoteVideo | None:
    item = table.item(row, 0)
    if item is None or item.data(REMOTE_ROLE) is None:
        return None
    return RemoteVideo.from_json(item.data(REMOTE_ROLE))


def row_video_id(table: QTableWidget, row: int) -> int | None:
    item = table.item(row, 0)
    if item is None or item.data(VIDEO_ID_ROLE) is None:
        return None
    return int(item.data(VIDEO_ID_ROLE))


def checked_rows(table: QTableWidget, role: int) -> List[int]:
    rows: list[int] = []
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is None or item.data(role) is None:
            continue
        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable and item.checkState() == Qt.CheckState.Checked:
            rows.append(row)
    return rows


def selected_rows(table: QTableWidget, role: int) -> List[int]:
    model = table.selectionModel()
    if model is None:
        return []
    rows: list[int] = []
    for index in sorted(model.selectedIndexes(), key=lambda item: item.row()):
        item = table.item(index.row(), 0)
        if item is not None and item.data(role) is not None and index.row() not in rows:
            rows.append(index.row())
    return rows


def action_rows(table: QTableWidget, role: int) -> List[int]:
    """操作目标：优先勾选，其次选中行。"""
    rows = checked_rows(table, role)
    return rows or selected_rows(table, role)


def action_remotes(table: QTableWidget) -> List[RemoteVideo]:
    remotes: list[RemoteVideo] = []
    for row in action_rows(table, REMOTE_ROLE):
        remote = row_remote(table, row)
        if remote is not None and remote not in remotes:
            remotes.append(remote)
    return remotes


def action_video_ids(table: QTableWidget) -> List[int]:
    ids: list[int] = []
    for row in action_rows(table, VIDEO_ID_ROLE):
        video_id = row_video_id(table, row)
        if video_id is not None and video_id not in ids:
            ids.append(video_id)
    return ids


def as_remote_batch(value) -> List[RemoteVideo] | None:  # noqa: ANN001
    """归一化槽函数参数：按钮 clicked 会传 bool，必须忽略。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, RemoteVideo)]
    return [value] if isinstance(value, RemoteVideo) else None


def select_all(table: QTableWidget, checked: bool = True) -> None:
    state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            item.setCheckState(state)


def attach_context_menu(table: QTableWidget, provider) -> None:  # noqa: ANN001
    """挂右键菜单：provider(row) 返回 [(菜单项, 处理函数), ...]。"""
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


def make_kind_combo(include_all: bool = True) -> QComboBox:
    """类型下拉：全部 / 电影 / 电视剧 / 动漫 / 综艺 / 纪录片 / 其他。"""
    combo = QComboBox()
    if include_all:
        combo.addItem("全部类型", "all")
    for key, label in MEDIA_KIND_LABELS.items():
        combo.addItem(label, key)
    return combo


# --------------------------------------------------------------------------- 对话框


def ask_text(parent: QWidget | None, title: str, label: str, default: str = "") -> str | None:
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Normal, default)
    if not ok:
        return None
    text = (text or "").strip()
    return text or None


class CollectionPicker(QDialog):
    """选择目标分类/收藏集合（可现场新建）。"""

    def __init__(self, storage: VideoStorage, parent: QWidget | None = None,
                 title: str = "加入分类"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(340)
        self._storage = storage

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(QLabel("选择分类："))
        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        create = QPushButton("新建分类…")
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
            label = f"{playlist.name}（{playlist.video_count} 个）"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, playlist.id)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _create(self) -> None:
        name = ask_text(self, "新建分类", "分类名称：")
        if not name:
            return
        playlist_id = self._storage.create_playlist(name, kind="category")
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
    """跨源聚合搜索。"""

    finishedResults = Signal(object, object)   # (List[RemoteVideo], List[str] errors)
    failed = Signal(str)

    def __init__(self, keyword: str, kind: str = "all", source_key: str = "all",
                 limit: int = 30, page: int = 1, parent=None, library=None):
        super().__init__(parent)
        self._keyword = keyword
        self._kind = kind
        self._source_key = source_key
        self._limit = limit
        self._page = page
        self._library = library

    def _registry(self):  # noqa: ANN202
        """优先用注入的库（便于测试隔离与将来的多库扩展），否则回退到应用级单例。"""
        if self._library is not None:
            return self._library.registry
        return app_context.video_registry()

    def run(self) -> None:  # noqa: D102
        try:
            registry = self._registry()
            if self._source_key == "all":
                videos, errors = registry.search_all(
                    self._keyword, kind=self._kind, limit=self._limit, page=self._page
                )
            else:
                videos = registry.search_one(
                    self._source_key, self._keyword, kind=self._kind,
                    limit=self._limit, page=self._page,
                )
                errors = []
            self.finishedResults.emit(videos, errors)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class DetailWorker(QThread):
    """拉取条目详情（分集 + 画质）。"""

    finishedDetail = Signal(object)
    failed = Signal(str)

    def __init__(self, remote: RemoteVideo, parent=None):
        super().__init__(parent)
        self._remote = remote

    def run(self) -> None:  # noqa: D102
        try:
            self.finishedDetail.emit(app_context.video_registry().detail(self._remote))
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class ResolveWorker(QThread):
    """解析播放地址（含跨源兜底）；成功后条目自动入库。"""

    finishedResolve = Signal(object, object)   # (ResolvedPlay, Video)
    failed = Signal(str)

    def __init__(self, remote: RemoteVideo, episode: Episode | None = None,
                 quality: Quality | None = None, *, allow_cross_source: bool = True,
                 exclude: Iterable[str] = (), parent=None, library=None):
        super().__init__(parent)
        self._remote = remote
        self._episode = episode
        self._quality = quality
        self._allow_cross = allow_cross_source
        self._exclude = list(exclude)
        self._library = library

    def run(self) -> None:  # noqa: D102
        try:
            library = self._library or app_context.video_library()
            resolved, video = library.resolve_playback(
                self._remote, self._episode, self._quality,
                allow_cross_source=self._allow_cross, exclude=self._exclude,
            )
            self.finishedResolve.emit(resolved, video)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class QualityWorker(QThread):
    """探测 m3u8 主清单，得到可选清晰度列表。"""

    finishedQualities = Signal(object)   # List[HlsVariant]
    failed = Signal(str)

    # 探测清晰度是「锦上添花」，超时要短：否则用户关掉对话框时它还在跑
    TIMEOUT = 4.0
    ATTEMPTS = 1

    def __init__(self, url: str, referer: str = "", parent=None):
        super().__init__(parent)
        self._url = url
        self._referer = referer

    def run(self) -> None:  # noqa: D102
        try:
            from modu_workbench.core.video.sources import HttpClient

            client = HttpClient(timeout=self.TIMEOUT, attempts=self.ATTEMPTS)
            self.finishedQualities.emit(hls.list_qualities(self._url, http=client, referer=self._referer))
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class DownloadWorker(QThread):
    """批量下载（可选跨源兜底与取消）。"""

    progressed = Signal(int, int, str)
    taskDone = Signal(object)        # DownloadResult
    finishedAll = Signal(object)     # List[DownloadResult]
    failed = Signal(str)

    def __init__(self, tasks: Iterable[tuple], dest_dir: str, *, allow_cross_source: bool = True,
                 parent=None, library=None):
        super().__init__(parent)
        self._tasks = list(tasks)
        self._dest = dest_dir
        self._allow_cross = allow_cross_source
        self._library = library
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102
        try:
            library = self._library or app_context.video_library()
            results = library.download_many(
                self._tasks, dest_dir=self._dest,
                on_progress=lambda done, total, message: self.progressed.emit(done, total, message),
                on_task_done=lambda result: self.taskDone.emit(result),
                cancel=self._cancel, allow_cross_source=self._allow_cross,
            )
            self.finishedAll.emit(results)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class ConvertWorker(QThread):
    """视频格式转换（逐个条目，支持取消）。"""

    progressed = Signal(int, int, str)
    finishedAll = Signal(object, object)   # (ok paths, errors)
    failed = Signal(str)

    def __init__(self, video_ids: Iterable[int], target_format: str, output_dir: str, parent=None):
        super().__init__(parent)
        self._ids = list(video_ids)
        self._target = target_format
        self._out = output_dir
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102
        outputs: list[str] = []
        errors: list[str] = []
        total = len(self._ids)
        library: VideoLibrary = app_context.video_library()
        try:
            for index, video_id in enumerate(self._ids, start=1):
                if self._cancel.is_set():
                    errors.append("已取消")
                    break
                video = library.storage.get_video(video_id)
                name = video.display() if video else str(video_id)
                self.progressed.emit(index - 1, total, f"[{index}/{total}] {name}")
                try:
                    path = library.convert(video_id, self._target, self._out, cancel=self._cancel)
                    outputs.append(str(path))
                except Exception as error:  # noqa: BLE001
                    errors.append(f"{name}：{error}")
            self.progressed.emit(len(outputs), total, f"完成 {len(outputs)}/{total}")
            self.finishedAll.emit(outputs, errors)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


__all__ = [
    "CollectionPicker",
    "ConvertWorker",
    "DetailWorker",
    "DownloadWorker",
    "QualityWorker",
    "REMOTE_ROLE",
    "ResolveWorker",
    "SearchWorker",
    "VIDEO_ID_ROLE",
    "action_remotes",
    "action_video_ids",
    "as_remote_batch",
    "ask_text",
    "attach_context_menu",
    "configure_table",
    "fill_remote_row",
    "fill_video_row",
    "make_item",
    "make_kind_combo",
    "remote_columns",
    "row_remote",
    "row_video_id",
    "select_all",
    "video_auto_switch_pref",
    "video_download_dir_pref",
    "video_settings",
]
