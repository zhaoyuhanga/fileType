"""墨软图库界面共享组件：缩略图网格、大图查看器、对话框与后台线程。

线程模型与其它板块一致：所有扫描/解码/算法/AI 调用都在 QThread 里，
通过信号回主线程更新界面，绝不阻塞 UI。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterable, List

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
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
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.image import (
    VIEW_MODE_LABELS,
    ImageItem,
    ImageLibrary,
    ImageStorage,
    THUMB_MEDIUM,
    THUMB_SMALL,
    EditStep,
    apply_enhancement,
    enhance_labels,
    open_oriented,
)
from modu_workbench.services import app_context
from modu_workbench.ui_kit.toast import Toaster

ITEM_ROLE = Qt.ItemDataRole.UserRole
PLACEHOLDER_COLOR = QColor(226, 232, 240)


# --------------------------------------------------------------------------- 缩略图


def pixmap_from_file(path: str | None, size: int) -> QPixmap:
    """把磁盘缩略图读成 QPixmap（保持比例，居中放进 size×size 的透明画布）。"""
    if not path or not Path(path).is_file():
        placeholder = QPixmap(size, size)
        placeholder.fill(PLACEHOLDER_COLOR)
        return placeholder
    pixmap = QPixmap(path)
    if pixmap.isNull():
        pixmap = QPixmap(size, size)
        pixmap.fill(PLACEHOLDER_COLOR)
        return pixmap
    scaled = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    painter.end()
    return canvas


def icon_from_file(path: str | None, size: int) -> QIcon:
    return QIcon(pixmap_from_file(path, size))


# --------------------------------------------------------------------------- 网格


class ThumbnailGrid(QListWidget):
    """缩略图网格/瀑布流/时间轴共用的基础控件。

    - 只对「可见区域附近」的项创建 QPixmap（懒加载），避免万张图一次性解码；
    - 滚动时按需补载，切换视图只改尺寸与图标大小。
    """

    itemActivated = Signal(object)      # ImageItem（双击/回车）
    selectionChangedItems = Signal(list)

    def __init__(self, library: ImageLibrary, parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._items: list[ImageItem] = []
        self._loaded: set[int] = set()
        self._thumb_size = THUMB_MEDIUM
        self._columns = 4

        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setUniformItemSizes(True)
        self.setSpacing(6)
        self.setWordWrap(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self.verticalScrollBar().valueChanged.connect(self._load_visible)
        self.itemDoubleClicked.connect(self._emit_activated)
        self.itemSelectionChanged.connect(
            lambda: self.selectionChangedItems.emit(self.selected_items())
        )
        self._apply_metrics()

    # ---------- 数据 ----------

    def set_items(self, items: Iterable[ImageItem]) -> None:
        self._items = list(items)
        self._loaded.clear()
        self.clear()
        for item in self._items:
            entry = QListWidgetItem(item.display())
            entry.setData(ITEM_ROLE, item.id)
            entry.setToolTip(self._tooltip(item))
            entry.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            entry.setIcon(icon_from_file(None, self._thumb_size))
            entry.setSizeHint(QSize(self._thumb_size, self._thumb_size + 26))
            if item.favorited:
                entry.setForeground(QColor("#b9790a"))
            self.addItem(entry)
        self._load_visible()

    def set_thumb_size(self, size: int, columns: int = 0) -> None:
        self._thumb_size = max(96, int(size))
        if columns:
            self._columns = columns
        self._apply_metrics()
        self._loaded.clear()
        for row in range(self.count()):
            entry = self.item(row)
            entry.setSizeHint(QSize(self._thumb_size, self._thumb_size + 26))
            entry.setIcon(icon_from_file(None, self._thumb_size))
        self._load_visible()

    def _apply_metrics(self) -> None:
        self.setIconSize(QSize(self._thumb_size, self._thumb_size))
        self.setGridSize(QSize(self._thumb_size + 14, self._thumb_size + 34))

    def _tooltip(self, item: ImageItem) -> str:
        parts = [item.display(), f"{item.resolution} · {item.size_text}"]
        meta = [f"拍摄：{item.taken_text}", f"来源：{item.source_text}"]
        if item.camera:
            meta.insert(0, f"相机：{item.camera}")
        if item.ai_caption:
            meta.append(f"描述：{item.ai_caption}")
        if item.tag_list:
            meta.append("标签：" + "、".join(item.tag_list))
        if item.path:
            meta.append(item.path)
        return "\n".join(parts + meta)

    def items(self) -> list[ImageItem]:
        return list(self._items)

    def item_at_row(self, row: int) -> ImageItem | None:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def selected_items(self) -> list[ImageItem]:
        result: list[ImageItem] = []
        for entry in self.selectedItems():
            image_id = entry.data(ITEM_ROLE)
            for item in self._items:
                if item.id == image_id:
                    result.append(item)
                    break
        return result

    def current_item(self) -> ImageItem | None:
        return self.item_at_row(self.currentRow())

    def action_items(self) -> list[ImageItem]:
        """操作目标：优先选中项，其次当前项。"""
        selected = self.selected_items()
        if selected:
            return selected
        current = self.current_item()
        return [current] if current else []

    # ---------- 懒加载 ----------

    def _load_visible(self) -> None:
        """只加载视口附近的项目缩略图（前后各留一屏余量）。"""
        if not self._items:
            return
        viewport_rect = self.viewport().rect()
        margin = viewport_rect.height()
        near = viewport_rect.adjusted(0, -margin, 0, margin)

        first = self.indexAt(near.topLeft()).row()
        last = self.indexAt(near.bottomRight()).row()
        if first < 0:
            first = 0
        if last < 0:
            last = min(self.count() - 1, first + self._columns * 4)

        for row in range(max(0, first), min(self.count(), last + 1)):
            self._load_row(row)

    def _load_row(self, row: int) -> None:
        if row in self._loaded:
            return
        item = self.item_at_row(row)
        entry = self.item(row)
        if item is None or entry is None:
            return
        self._loaded.add(row)
        thumb = self._library.thumbnail(item, self._thumb_size)
        if thumb:
            entry.setIcon(icon_from_file(thumb, self._thumb_size))

    def refresh_row(self, image_id: int) -> None:
        """单张图改动后刷新它的缩略图（覆盖原图/编辑保存时用）。"""
        for row, item in enumerate(self._items):
            if item.id == image_id:
                self._loaded.discard(row)
                self._load_row(row)
                break

    def _emit_activated(self, entry: QListWidgetItem) -> None:
        for item in self._items:
            if item.id == entry.data(ITEM_ROLE):
                self.itemActivated.emit(item)
                return

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._load_visible()


# --------------------------------------------------------------------------- 大图查看器


class ImageViewer(QScrollArea):
    """大图查看：滚轮缩放（Ctrl/直接）、拖拽平移、双击复位、适应窗口。

    用 QLabel 承载 QPixmap 并放进 QScrollArea —— 比自绘简单且滚动条天然可用。
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setBackgroundRole(self.backgroundRole())
        self.setWidget(self._label)
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._source = QPixmap()
        self._scale = 1.0
        self._fit = True
        self._drag_origin = None
        self.setMinimumSize(320, 240)

    def load(self, path: str | None) -> bool:
        """载入图片文件；失败返回 False（界面显示提示）。"""
        if not path or not Path(path).is_file():
            self._source = QPixmap()
            self._label.setText("无法显示该图片（文件不存在或格式不支持）")
            return False
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._source = QPixmap()
            self._label.setText("无法解码该图片（可能是 HEIC/RAW 等需要额外解码器的格式）")
            return False
        self._source = pixmap
        self._fit = True
        self._render()
        return True

    def load_item(self, library: ImageLibrary, item: ImageItem, *, large: bool = True) -> bool:
        """优先用大缩略图（加载快），失败再退回原图。"""
        from modu_workbench.core.image import THUMB_LARGE

        if large:
            preview = library.thumbnail(item, THUMB_LARGE)
            if preview and self.load(preview):
                return True
        return self.load(item.path)

    # ---------- 缩放/平移 ----------

    def _render(self) -> None:
        if self._source.isNull():
            return
        target = self._target_size()
        scaled = self._source.scaled(target, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        self._label.setPixmap(scaled)
        self._label.resize(scaled.size())

    def _target_size(self) -> QSize:
        if self._fit:
            available = self.viewport().size()
            return QSize(max(1, available.width() - 8), max(1, available.height() - 8))
        return QSize(max(1, int(self._source.width() * self._scale)),
                     max(1, int(self._source.height() * self._scale)))

    def zoom_in(self) -> None:
        self._zoom(1.25)

    def zoom_out(self) -> None:
        self._zoom(0.8)

    def _zoom(self, factor: float) -> None:
        if self._source.isNull():
            return
        self._fit = False
        self._scale = max(0.05, min(12.0, self._scale * factor))
        self._render()

    def reset_zoom(self) -> None:
        self._fit = True
        self._scale = 1.0
        self._render()

    def actual_size(self) -> None:
        if self._source.isNull():
            return
        self._fit = False
        self._scale = 1.0
        self._render()

    @property
    def zoom_percent(self) -> int:
        if self._source.isNull():
            return 0
        shown = self._label.pixmap().width() if self._label.pixmap() else 0
        if not shown:
            return 100
        return int(shown * 100 / max(1, self._source.width()))

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._source.isNull():
            return
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_in()
        elif delta < 0:
            self.zoom_out()
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self._source.isNull():
            self._drag_origin = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is not None:
            delta = event.position().toPoint() - self._drag_origin
            self._drag_origin = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_origin = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.reset_zoom()
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit:
            self._render()


def pixmap_to_qimage(pixmap: QPixmap) -> QImage:
    return pixmap.toImage()


# --------------------------------------------------------------------------- 后台线程


class ImportWorker(QThread):
    """批量导入/扫描（可取消）。"""

    progressed = Signal(int, int, str)
    finishedImport = Signal(object)      # ScanResult
    failed = Signal(str)

    def __init__(self, library: ImageLibrary, paths: Iterable[str], *, source: str = "local",
                 album_id: int | None = None, skip_duplicates: bool = True,
                 copy_into_library: bool = False, parent=None):
        super().__init__(parent)
        self._library = library
        self._paths = list(paths)
        self._source = source
        self._album_id = album_id
        self._skip_duplicates = skip_duplicates
        self._copy = copy_into_library
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102
        try:
            result = self._library.import_paths(
                self._paths, source=self._source, album_id=self._album_id,
                skip_duplicates=self._skip_duplicates,
                on_progress=lambda d, t, m: self.progressed.emit(d, t, m),
                cancel=self._cancel, copy_into_library=self._copy,
            )
            self.finishedImport.emit(result)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class UrlCollectWorker(QThread):
    """从网址收集图片。"""

    collected = Signal(object)           # ImageItem
    failed = Signal(str)

    def __init__(self, library: ImageLibrary, url: str, *, album_id: int | None = None, parent=None):
        super().__init__(parent)
        self._library = library
        self._url = url
        self._album_id = album_id

    def run(self) -> None:  # noqa: D102
        try:
            self.collected.emit(self._library.collect_from_url(self._url, album_id=self._album_id))
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class EnhanceWorker(QThread):
    """本地增强（一键增强/超分/降噪/...），可取消。"""

    progressed = Signal(int, int, str)
    finishedOne = Signal(str, str)       # (image_id, 产出路径)
    finishedAll = Signal(object, object)  # (ok 列表, 错误列表)
    failed = Signal(str)

    def __init__(self, library: ImageLibrary, items: list[ImageItem], kind: str,
                 params: dict | None = None, parent=None):
        super().__init__(parent)
        self._library = library
        self._items = list(items)
        self._kind = kind
        self._params = dict(params or {})
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102
        outputs: list[str] = []
        errors: list[str] = []
        total = len(self._items)
        label = enhance_labels().get(self._kind, self._kind)
        try:
            for index, item in enumerate(self._items, start=1):
                if self._cancel.is_set():
                    errors.append("已取消")
                    break
                self.progressed.emit(index - 1, total, f"[{index}/{total}] {label}：{item.display()}")
                try:
                    path = self._library.run_enhancement(item, self._kind, self._params)
                    outputs.append(str(path))
                    self.finishedOne.emit(str(item.id), str(path))
                except Exception as error:  # noqa: BLE001
                    errors.append(f"{item.display()}：{error}")
            self.progressed.emit(len(outputs), total, f"完成 {len(outputs)}/{total}")
            self.finishedAll.emit(outputs, errors)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class AnalyzeWorker(QThread):
    """DeepSeek 文本分析（生成描述/标签），逐张执行。"""

    progressed = Signal(int, int, str)
    finishedOne = Signal(int, str, str)   # (image_id, caption, tags)
    finishedAll = Signal(object, object)  # (ok 数, 错误列表)
    failed = Signal(str)

    def __init__(self, library: ImageLibrary, items: list[ImageItem], *,
                 use_vision: bool = True, parent=None):
        super().__init__(parent)
        self._library = library
        self._items = list(items)
        self._use_vision = use_vision
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102
        errors: list[str] = []
        ok = 0
        total = len(self._items)
        try:
            for index, item in enumerate(self._items, start=1):
                if self._cancel.is_set():
                    errors.append("已取消")
                    break
                self.progressed.emit(index - 1, total, f"[{index}/{total}] 分析：{item.display()}")
                try:
                    result = self._library.ai_analyze(item, use_vision=self._use_vision)
                    ok += 1
                    self.finishedOne.emit(item.id, str(result.get("caption") or ""),
                                          ",".join(result.get("tags") or []))
                except Exception as error:  # noqa: BLE001
                    errors.append(f"{item.display()}：{error}")
            self.progressed.emit(ok, total, f"完成 {ok}/{total}")
            self.finishedAll.emit(ok, errors)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class DuplicateWorker(QThread):
    """相似/重复图片检测（感知哈希聚类，图多时需要时间）。"""

    finishedGroups = Signal(object)
    failed = Signal(str)

    def __init__(self, library: ImageLibrary, *, max_distance: int = 4, parent=None):
        super().__init__(parent)
        self._library = library
        self._max_distance = max_distance

    def run(self) -> None:  # noqa: D102
        try:
            self.finishedGroups.emit(self._library.find_duplicates(max_distance=self._max_distance))
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


# --------------------------------------------------------------------------- 对话框


def ask_text(parent: QWidget | None, title: str, label: str, default: str = "") -> str | None:
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Normal, default)
    if not ok:
        return None
    text = (text or "").strip()
    return text or None


class AlbumPicker(QDialog):
    """选择目标相册（可现场新建）。"""

    def __init__(self, library: ImageLibrary, parent: QWidget | None = None,
                 title: str = "加入相册"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(340)
        self._library = library

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(QLabel("选择相册（删除相册不会删除原图）："))
        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        create = QPushButton("新建相册…")
        create.clicked.connect(self._create)
        row.addWidget(create)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        layout.addLayout(row)
        self._reload()

    def _reload(self) -> None:
        self._list.clear()
        for album in self._library.list_albums(include_favorite=False):
            entry = QListWidgetItem(f"{album.name}（{album.image_count} 张）")
            entry.setData(ITEM_ROLE, album.id)
            self._list.addItem(entry)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _create(self) -> None:
        name = ask_text(self, "新建相册", "相册名称：")
        if not name:
            return
        album_id = self._library.create_album(name)
        self._reload()
        for row in range(self._list.count()):
            entry = self._list.item(row)
            if entry.data(ITEM_ROLE) == album_id:
                self._list.setCurrentRow(row)
                break

    def selected_album_id(self) -> int | None:
        entry = self._list.currentItem()
        return int(entry.data(ITEM_ROLE)) if entry else None


class TagInputDialog(QDialog):
    """给选中图片批量打标签：可从已有标签里选，也可输入新标签。"""

    def __init__(self, library: ImageLibrary, count: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("添加标签")
        self.setMinimumWidth(360)
        self._library = library

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(QLabel(f"为选中的 {count} 张图片添加标签："))

        self._input = QLineEdit()
        self._input.setPlaceholderText("输入标签名（可多个，用逗号分隔）")
        layout.addWidget(self._input)

        layout.addWidget(QLabel("或点击已有标签："))
        self._list = QListWidget()
        self._list.setMaximumHeight(150)
        for tag in self._library.list_tags():
            entry = QListWidgetItem(f"{tag.name}（{tag.use_count}）")
            entry.setData(ITEM_ROLE, tag.name)
            self._list.addItem(entry)
        self._list.itemDoubleClicked.connect(self._pick_existing)
        layout.addWidget(self._list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _pick_existing(self, entry: QListWidgetItem) -> None:
        existing = self._input.text().strip()
        name = str(entry.data(ITEM_ROLE))
        self._input.setText(f"{existing},{name}" if existing else name)

    def tag_names(self) -> list[str]:
        raw = self._input.text().replace("，", ",")
        return [part.strip() for part in raw.split(",") if part.strip()]


__all__ = [
    "ITEM_ROLE",
    "AlbumPicker",
    "AnalyzeWorker",
    "DuplicateWorker",
    "EnhanceWorker",
    "ImageViewer",
    "ImportWorker",
    "TagInputDialog",
    "ThumbnailGrid",
    "UrlCollectWorker",
    "ask_text",
    "icon_from_file",
    "pixmap_from_file",
]
