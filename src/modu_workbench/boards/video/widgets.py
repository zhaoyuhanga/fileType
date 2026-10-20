"""墨软影视界面共享组件：表格、对话框与后台工作线程。

线程模型与墨软乐库一致：所有联网/磁盘操作都放在 QThread 里，
通过信号回到 UI 线程更新界面，绝不阻塞主线程。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Iterable, List

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
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
    VideoStorage,
)
from modu_workbench.core.video import hls
from . import context as app_context

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
    from modu_workbench.core.platform.paths import video_download_dir

    stored = str(video_settings().value("video/download_dir", "", type=str) or "").strip()
    return stored or str(video_download_dir())


def video_auto_switch_pref() -> bool:
    value = video_settings().value("video/auto_switch", True)
    if isinstance(value, bool):
        return value
    return str(value).lower() not in ("false", "0", "no", "")


# --------------------------------------------------------------------------- 图片代理（V3-f）


def proxied_image(url: str, referer: str = "") -> str:
    """把远程海报地址改写成**本机图片代理**地址（治采集站的防盗链 403）。

    采集站的海报往往校验 Referer，直接交给 Qt 去下就是一张破图；走
    `services.media_proxy.image_url()` 后由本机带 Referer/UA 取回并缓存
    （`Cache-Control: max-age=86400`，见计划 3.3.6）。

    约定：
    - `""` / `None` → 返回 `""`（调用方据此走占位图）；
    - 已经是本机代理地址 → 原样返回（`image_url` 自带幂等，这里不重复包一层）；
    - **任何异常都退回原地址**：代理起不来时"直连"顶多是破图，总好过整个界面报错。

    代理模块是惰性导入的：`boards.video` 的纯逻辑（表格、匹配）不该因为
    "导入界面代码"就顺带把本机 HTTP 代理服务拉起来。
    """
    text = (url or "").strip()
    if not text:
        return ""
    try:
        from modu_workbench.services.media_proxy import image_url

        return image_url(text, referer) or text
    except Exception:  # noqa: BLE001  代理不可用时退回直连，绝不打断渲染
        return text


class _ImageFetchWorker(QThread):
    """后台取图：**只做网络 IO**，解码与 setPixmap 回到 UI 线程做。

    为什么不在工作线程里直接 `QPixmap.loadFromData`：QPixmap 依赖 GUI 后端，
    在非 GUI 线程上构造属于未定义行为（X11 下会直接崩）。所以这里只把原始字节
    带回来，由 `load_pixmap` 在主线程解码。
    """

    fetched = Signal(str, object)     # url, bytes（失败时是 None）

    TIMEOUT = 8.0

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self) -> None:  # noqa: D102
        import requests

        payload = None
        try:
            response = requests.get(self._url, timeout=self.TIMEOUT)
            if response.ok and response.content:
                payload = response.content
        except Exception:  # noqa: BLE001  取图失败就是"没有封面"，不是错误
            payload = None
        self.fetched.emit(self._url, payload)


def load_pixmap(url: str, referer: str = "", size: tuple[int, int] | None = None,
                *, on_loaded=None, parent=None):  # noqa: ANN001, ANN201
    """异步加载远程封面，`on_loaded(QPixmap|None)` 回调；返回 QThread（调用方要留着引用）。

    与源设置里那些 Worker 同样的用法：拿到返回值后必须持有它 ——
    丢掉仍在运行的 QThread 引用会让进程直接崩（见
    `detail.py::VideoDetailDialog.shutdown` 里的同一条教训）。

    行为约定（计划 3.3.6 的"失败回退"）：
    - 空地址 → 同步回调 `None`（不起线程），调用方显示既定占位图；
    - 下载/解码失败 → 回调 `None`，**不弹任何错误框**（顶多由调用方写个 tooltip）；
    - `size` 给了就等比缩放到该尺寸，避免原图直接贴上去。
    """
    from PySide6.QtGui import QPixmap

    target = proxied_image(url, referer)
    if not target:
        if on_loaded is not None:
            on_loaded(None)
        return None

    worker = _ImageFetchWorker(target, parent)

    def _on_fetched(_url: str, payload) -> None:  # noqa: ANN001
        pixmap = None
        if payload:
            candidate = QPixmap()
            if candidate.loadFromData(payload) and not candidate.isNull():
                if size:
                    candidate = candidate.scaled(
                        int(size[0]), int(size[1]),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                pixmap = candidate
        if on_loaded is not None:
            on_loaded(pixmap)

    worker.fetched.connect(_on_fetched)
    worker.start()
    return worker


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
    # 片名（第 0 列）吃掉剩余宽度：反馈里「左侧片名太窄」就是因为以前第 0 列用默认宽度，
    # 而富余宽度全给了最后一列。其余列按内容自适应，最小宽度兜住片名不被挤没。
    header = table.horizontalHeader()
    header.setMinimumSectionSize(96)
    if len(headers) > 1:
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index in range(1, len(headers)):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
    else:
        header.setStretchLastSection(True)
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
    """跨源聚合搜索（并发 + 单源时间预算 + 进度回报）。

    三条信号对应三种可见状态，别让用户对着"搜索中…"干等：

    - `progressed(done, total, count)`：每完成一个源回报一次（"已查 4/11 个源，
      已找到 12 条…"）。实测最慢的源会拖到 9 秒预算上限，而**第一个源 0.7 秒就有结果**，
      没有这条信号用户就以为卡死了。
    - `finishedResults(videos, errors)`：全部结束（或预算用尽）后的最终结果。
    - `failed(message)`：整体异常（单个源失败走 `errors`，不算整体失败）。
    """

    progressed = Signal(int, int, int)         # (已完成源数, 参与源数, 累计结果数)
    partialResults = Signal(object, object)    # (List[RemoteVideo], List[str]) 边搜边给
    finishedResults = Signal(object, object)   # (List[RemoteVideo], List[str] errors)
    failed = Signal(str)

    def __init__(self, keyword: str, kind: str = "all", source_key: str = "all",
                 limit: int = 30, page: int = 1, parent=None, library=None,
                 per_source_timeout: float | None = None):
        super().__init__(parent)
        self._keyword = keyword
        self._kind = kind
        self._source_key = source_key
        self._limit = limit
        self._page = page
        self._library = library
        self._per_source_timeout = per_source_timeout

    def _registry(self):  # noqa: ANN202
        """优先用注入的库（便于测试隔离与将来的多库扩展），否则回退到应用级单例。"""
        if self._library is not None:
            return self._library.registry
        return app_context.video_registry()

    def run(self) -> None:  # noqa: D102
        try:
            registry = self._registry()
            if self._source_key == "all":
                kwargs = {}
                if self._per_source_timeout is not None:
                    kwargs["per_source_timeout"] = self._per_source_timeout
                videos, errors = registry.search_all(
                    self._keyword, kind=self._kind, limit=self._limit, page=self._page,
                    on_partial=self._on_partial, **kwargs,
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

    def _on_partial(self, done: int, total: int, videos: list, errors: list) -> None:
        """从工作线程回调；信号由 Qt 排队到 UI 线程，这里不碰界面。"""
        self.progressed.emit(int(done), int(total), len(videos))
        self.partialResults.emit(list(videos), list(errors))


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


class HdSearchWorker(QThread):
    """跨源找更高清晰度（后台线程）。

    采集站常常只给单条线路，画质由站点决定；同一个片名在别的源上可能是 1080P。
    这里逐个源搜索 + 探测 m3u8 主清单，返回分辨率最高的候选。
    """

    progressed = Signal(str)
    found = Signal(object)        # HdCandidate | None
    failed = Signal(str)

    def __init__(self, remote, episode=None, *, current_height: int = 0,
                 library=None, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self._remote = remote
        self._episode = episode
        self._current_height = current_height
        self._library = library

    def run(self) -> None:  # noqa: D102
        try:
            library = self._library or app_context.video_library()
            candidate = library.find_higher_quality(
                self._remote, self._episode,
                current_height=self._current_height,
                on_progress=lambda message: self.progressed.emit(message),
            )
            self.found.emit(candidate)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class DownloadWorker(QThread):
    """批量下载（可选跨源兜底、可取消、可暂停/继续）。

    暂停/继续的实现要点：**暂停不是"杀掉线程"** —— 它只是一个 `threading.Event`，
    分片下载器在四个检查点等它（取清单后 / 每片开始前 / 每片写盘后 / 拼接前），
    已下载的分片留在 `.parts/` 里。因此"暂停后继续"是**真续传**，不会从头再来。
    """

    progressed = Signal(int, int, str)
    taskDone = Signal(object)        # DownloadResult
    finishedAll = Signal(object)     # List[DownloadResult]
    failed = Signal(str)
    pausedChanged = Signal(bool)

    def __init__(self, tasks: Iterable[tuple], dest_dir: str, *, allow_cross_source: bool = True,
                 parent=None, library=None):
        super().__init__(parent)
        self._tasks = list(tasks)
        self._dest = dest_dir
        self._allow_cross = allow_cross_source
        self._library = library
        self._cancel = threading.Event()
        self._pause = threading.Event()

    # ------------------------------------------------------------------ 控制

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.clear()

    def pause(self) -> None:
        """暂停：分片下载器在下一个检查点停下，已下载分片保留。"""
        if self._cancel.is_set():
            return
        self._pause.set()
        self.pausedChanged.emit(True)

    def resume(self) -> None:
        self._pause.clear()
        self.pausedChanged.emit(False)

    @property
    def is_paused(self) -> bool:
        return self._pause.is_set()

    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    # ------------------------------------------------------------------ 执行

    def run(self) -> None:  # noqa: D102
        try:
            library = self._library or app_context.video_library()
            results = library.download_many(
                self._tasks, dest_dir=self._dest,
                on_progress=lambda done, total, message: self.progressed.emit(done, total, message),
                on_task_done=lambda result: self.taskDone.emit(result),
                cancel=self._cancel, allow_cross_source=self._allow_cross,
                pause_event=self._pause,
            )
            self.finishedAll.emit(results)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class DanmakuWorker(QThread):
    """后台取弹幕（弹弹play）：匹配 + 下载 + 落盘缓存，绝不阻塞 UI。

    弹幕是**可选增强**：任何失败都只回报一句可读原因，不影响正在播放的视频。
    """

    fetched = Signal(object)          # DanmakuResult

    def __init__(self, title: str, episode_label: str = "", *, episode_number: int = 0,
                 year: str = "", parent=None):
        super().__init__(parent)
        self._title = title
        self._episode = episode_label
        self._number = episode_number
        self._year = year
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102
        from modu_workbench.ui_kit.media.danmaku_bridge import DanmakuFetcher

        try:
            result = DanmakuFetcher().fetch(
                self._title, self._episode, episode_number=self._number,
                year=self._year, cancel=self._cancel)
        except Exception as error:  # noqa: BLE001  弹幕失败不能影响播放
            from modu_workbench.ui_kit.media.danmaku_bridge import DanmakuResult

            result = DanmakuResult(note=f"弹幕获取失败：{error}")
        self.fetched.emit(result)


class EnhanceWorker(QThread):
    """离线画质增强（逐个条目，支持取消）。

    **这是离线重编码，不是 Anime4K 实时超分**（见 `core/video/enhance.py` 的说明）：
    只处理已经下载到本地的文件，耗时以分钟计，所以进度与取消都要有。
    """

    progressed = Signal(int, int, str)
    finishedAll = Signal(object, object)   # (ok paths, errors)
    failed = Signal(str)

    def __init__(self, video_ids: Iterable[int], mode: str = "upscale", parent=None):
        super().__init__(parent)
        self._ids = list(video_ids)
        self._mode = mode
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102
        from modu_workbench.core.video.enhance import enhance_video

        outputs: list[str] = []
        errors: list[str] = []
        total = len(self._ids)
        library: VideoLibrary = app_context.video_library()
        for index, video_id in enumerate(self._ids, start=1):
            if self._cancel.is_set():
                errors.append("已取消")
                break
            video = library.storage.get_video(video_id)
            name = video.display() if video else str(video_id)
            self.progressed.emit(index - 1, total, f"[{index}/{total}] {name}")
            if video is None:
                errors.append(f"{name}：条目不存在")
                continue
            if not video.file_path or not Path(video.file_path).is_file():
                errors.append(f"{name}：只有下载到本地的文件才能增强")
                continue
            source = Path(video.file_path)
            suffix = source.suffix or ".mp4"
            target = source.with_name(f"{source.stem}（增强）{suffix}")
            if target.exists():
                target = source.with_name(
                    f"{source.stem}（增强 {int(time.time())}）{suffix}")
            try:
                result = enhance_video(
                    source, target, mode=self._mode, cancel=self._cancel,
                    on_progress=lambda done, count, message,
                    i=index: self.progressed.emit(i - 1, total, message),
                )
            except Exception as error:  # noqa: BLE001
                errors.append(f"{name}：{error}")
                continue
            if result.ok and result.path is not None:
                outputs.append(str(result.path))
                # 入库（与下载入库同一条路径，便于「我的视频」直接看到）
                try:
                    library.import_download_result(_enhanced_result(video, result.path))
                except Exception:  # noqa: BLE001  入库失败不影响文件已产出
                    pass
            else:
                errors.append(f"{name}：{result.message}")
        self.progressed.emit(len(outputs), total, f"完成 {len(outputs)}/{total}")
        self.finishedAll.emit(outputs, errors)


def _enhanced_result(video, path):  # noqa: ANN001, ANN202
    """把增强产物包成 `DownloadResult`，复用既有的入库逻辑。"""
    from modu_workbench.core.video.downloader import DownloadResult

    return DownloadResult(
        video=video.to_remote() if hasattr(video, "to_remote") else video,
        episode=None, quality=None, path=str(path), ok=True,
        message="离线增强", source="local",
    )


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
    "HdSearchWorker",
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
    "load_pixmap",
    "proxied_image",
    "remote_columns",
    "row_remote",
    "row_video_id",
    "select_all",
    "video_auto_switch_pref",
    "video_download_dir_pref",
    "video_settings",
]
