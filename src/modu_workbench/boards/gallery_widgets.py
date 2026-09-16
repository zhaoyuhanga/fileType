"""墨软图库界面共享组件：缩略图网格、大图查看器、对话框与后台线程。

线程模型与其它板块一致：所有扫描/解码/算法/AI 调用都在 QThread 里，
通过信号回主线程更新界面，绝不阻塞 UI。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterable, List

from PySide6.QtCore import QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
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
    QStyle,
    QStyledItemDelegate,
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
FAVORITE_ROLE = Qt.ItemDataRole.UserRole + 1
PLACEHOLDER_COLOR = QColor(226, 232, 240)

# 缩略图卡片几何：图片区恒为 thumb×thumb，文件名单独占一条，绝不压在图上
CARD_PAD = 4          # 卡片与格子边缘的间距
INNER_PAD = 4         # 卡片内图片四周留白
TITLE_HEIGHT = 22     # 底部文件名条高度


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


# --------------------------------------------------------------------------- 卡片绘制


class ThumbnailDelegate(QStyledItemDelegate):
    """把每个缩略图画成一张「卡片」：图片在上、文件名在下。

    为什么不用默认绘制：QListView 图标模式下文件名会压在缩略图底部（用户反馈
    「名字在图片重叠」），而且默认样式没有卡片间距、选中态也不明显。
    这里把几何算死 —— 图片区恒为 thumb×thumb，文件名单独占 TITLE_HEIGHT 高的一条，
    两者永不重叠；文件名过长用中间省略（保留扩展名）。
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._cell = QSize(THUMB_MEDIUM + 16, THUMB_MEDIUM + 16 + TITLE_HEIGHT)

    def set_cell(self, cell: QSize) -> None:
        self._cell = QSize(cell)

    def sizeHint(self, option, index) -> QSize:  # noqa: ANN001, N802
        return QSize(self._cell)

    def paint(self, painter, option, index) -> None:  # noqa: ANN001, N802
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        card = QRectF(option.rect).adjusted(CARD_PAD, CARD_PAD, -CARD_PAD, -CARD_PAD)
        if card.width() <= 4 or card.height() <= 4:
            painter.restore()
            return

        background = QColor("#ffffff")
        if selected:
            background = QColor("#e8f0ff")
        elif hovered:
            background = QColor("#f5f9ff")
        painter.setBrush(QBrush(background))
        painter.setPen(QPen(QColor("#5b7ff0") if selected else QColor("#e3e8f2"),
                            2 if selected else 1))
        painter.drawRoundedRect(card, 10, 10)

        image_rect = QRectF(
            card.left() + INNER_PAD, card.top() + INNER_PAD,
            card.width() - 2 * INNER_PAD,
            card.height() - 2 * INNER_PAD - TITLE_HEIGHT,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#dde8ff") if selected else QColor("#f1f4fa")))
        painter.drawRoundedRect(image_rect, 7, 7)

        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            side = int(min(image_rect.width(), image_rect.height()))
            pixmap = icon.pixmap(QSize(side, side))
            if not pixmap.isNull():
                painter.drawPixmap(
                    int(image_rect.left() + (image_rect.width() - pixmap.width()) / 2),
                    int(image_rect.top() + (image_rect.height() - pixmap.height()) / 2),
                    pixmap,
                )

        if index.data(FAVORITE_ROLE):
            badge = QRectF(image_rect.right() - 24, image_rect.top() + 6, 18, 18)
            painter.setBrush(QBrush(QColor(255, 249, 224, 240)))
            painter.setPen(QPen(QColor("#e0a800"), 1))
            painter.drawEllipse(badge)
            star_font = QFont(option.font)
            star_font.setPointSizeF(max(8.0, option.font.pointSizeF()))
            painter.setFont(star_font)
            painter.setPen(QColor("#c98a00"))
            painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), "★")

        title_rect = QRectF(
            card.left() + INNER_PAD, image_rect.bottom() + 1,
            card.width() - 2 * INNER_PAD, TITLE_HEIGHT - 2,
        )
        painter.setFont(option.font)
        painter.setPen(QColor("#1b3fa8") if selected else QColor("#2b3445"))
        title = painter.fontMetrics().elidedText(
            str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
            Qt.TextElideMode.ElideMiddle,
            int(max(10, title_rect.width())),
        )
        painter.drawText(title_rect, int(Qt.AlignmentFlag.AlignCenter), title)
        painter.restore()


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
        self.setSpacing(2)
        self.setWordWrap(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        # 浅灰底 + 白色卡片：没有底色时卡片与空白糊在一起，看起来"很丑"
        self.setStyleSheet(
            "QListWidget { background: #eef1f7; border: 1px solid #e3e8f2;"
            " border-radius: 10px; padding: 6px; }"
        )
        self._delegate = ThumbnailDelegate(self)
        self.setItemDelegate(self._delegate)

        self.verticalScrollBar().valueChanged.connect(self._load_visible)
        self.itemDoubleClicked.connect(self._emit_activated)
        self.itemSelectionChanged.connect(
            lambda: self.selectionChangedItems.emit(self.selected_items())
        )
        self._apply_metrics()

    # ---------- 数据 ----------

    def set_items(self, items: Iterable[ImageItem], *, keep_selection: bool = True) -> None:
        """替换整个列表。

        `keep_selection=True` 时会尽量保留原来的当前项与选中项：
        否则每次搜索/刷新后 currentRow 变成 -1，"下一张"之类的操作就没反应。
        """
        previous_id = 0
        current = self.current_item()
        if keep_selection and current is not None:
            previous_id = current.id

        self._items = list(items)
        self._loaded.clear()
        self._fill_cursor = 0
        self.clear()
        cell = self._cell_size()
        for item in self._items:
            entry = QListWidgetItem(item.display())
            entry.setData(ITEM_ROLE, item.id)
            entry.setData(FAVORITE_ROLE, bool(item.favorited))
            entry.setToolTip(self._tooltip(item))
            entry.setIcon(icon_from_file(None, self._thumb_size))
            entry.setSizeHint(cell)
            self.addItem(entry)

        # 恢复选中：优先原来的项，否则默认选中第一项（大图查看/快捷键都依赖它）
        target = 0
        if previous_id:
            for row, item in enumerate(self._items):
                if item.id == previous_id:
                    target = row
                    break
        if self._items:
            self.setCurrentRow(target)
        self._load_visible()
        # 视口之外的部分稍后分批补全：避免用户滚动前一直看到灰色占位
        QTimer.singleShot(120, self.fill_thumbnails_lazily)

    def ensure_current(self) -> ImageItem | None:
        """确保有当前项（供"下一张/编辑"等操作使用），返回它。"""
        current = self.current_item()
        if current is not None:
            return current
        if not self._items:
            return None
        self.setCurrentRow(0)
        return self.current_item()

    def set_thumb_size(self, size: int, columns: int = 0) -> None:
        self._thumb_size = max(96, int(size))
        if columns:
            self._columns = columns
        self._apply_metrics()
        self._loaded.clear()
        for row in range(self.count()):
            entry = self.item(row)
            entry.setSizeHint(self._cell_size())
            entry.setIcon(icon_from_file(None, self._thumb_size))
        self._load_visible()

    def _cell_size(self) -> QSize:
        side = self._thumb_size + 2 * CARD_PAD + 2 * INNER_PAD
        return QSize(side, side + TITLE_HEIGHT)

    def _apply_metrics(self) -> None:
        self.setIconSize(QSize(self._thumb_size, self._thumb_size))
        cell = self._cell_size()
        self.setGridSize(cell)
        self._delegate.set_cell(cell)

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

    def item_at_pos(self, pos) -> ImageItem | None:  # noqa: ANN001
        entry = self.itemAt(pos)
        if entry is None:
            return None
        return self.item_at_row(self.row(entry))

    def focus_at(self, pos) -> ImageItem | None:  # noqa: ANN001
        """右键落点即目标：命中哪张就把哪张设为当前项并选中。

        默认行为里右键**不会**改变选中项，于是菜单会作用在"上一次选中的图片"上，
        甚至在没有选中项时直接返回 —— 用户看到的就是「右键点了没反应」。
        """
        item = self.item_at_pos(pos)
        if item is None:
            return None
        entry = self.itemAt(pos)
        if entry is not None and not entry.isSelected():
            self.clearSelection()
            entry.setSelected(True)
            self.setCurrentItem(entry)
        return item

    # ---------- 懒加载 ----------

    def _load_visible(self) -> None:
        """加载当前视口附近项目的缩略图。

        这里不能用 `indexAt(视口左下角)` 推断首行：当视口上方/下方是空白时
        `indexAt` 返回 -1，首行会被误判成 0，导致只有前十几项加载缩略图、
        其余长期显示灰色占位（表现就是"图片显示很丑"）。改用网格几何直接算行号。
        """
        if not self._items:
            return
        count = self.count()
        grid = self.gridSize()
        cell_h = max(1, grid.height())
        # 一屏大约能放几行
        per_row = max(1, self._per_row())
        rows_on_screen = max(1, self.viewport().height() // cell_h + 1)

        # 内容坐标下的可见区间（用滚动条位置换算，避免依赖 indexAt 的空白判定）
        scroll_y = self.verticalScrollBar().value()
        first_row = max(0, scroll_y // cell_h - 1)
        last_row = min((count - 1) // per_row, first_row + rows_on_screen + 1)

        for row in range(first_row * per_row, min(count, (last_row + 1) * per_row)):
            self._load_row(row)
        self._loaded_range = (first_row, last_row)

    def _per_row(self) -> int:
        """当前每行能放几个（由控件宽度与格子宽度决定）。"""
        width = max(1, self.viewport().width())
        cell_w = max(1, self.gridSize().width())
        return max(1, width // cell_w)

    def load_all_thumbnails(self, on_progress=None) -> int:  # noqa: ANN001
        """把全部缩略图加载出来（用于"生成全部缩略图"或导出前预热）。"""
        produced = 0
        total = self.count()
        for row in range(total):
            self._load_row(row)
            produced += 1
            if on_progress and row % 20 == 0:
                on_progress(row, total, f"生成缩略图 {row}/{total}")
        return produced

    def fill_thumbnails_lazily(self, chunk: int = 40) -> None:
        """分批把剩余缩略图补齐，保持界面响应。

        为什么需要它：懒加载只覆盖可见区，用户还没滚动时其余项是灰色占位图 ——
        观感很差（"图片显示好丑"）。缩略图本身很小（WEBP 几十 KB），
        分批补齐的成本远低于一直显示占位的体验损失。
        """
        total = self.count()
        if not total:
            return
        # 优先补齐当前可见位置之后的，用户往下滚时立刻有图
        start = max(0, getattr(self, "_fill_cursor", 0))
        end = min(total, start + max(1, chunk))
        for row in range(start, end):
            self._load_row(row)
        self._fill_cursor = end
        if end < total:
            QTimer.singleShot(30, self.fill_thumbnails_lazily)

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
        else:
            # 生成失败（格式不支持/文件丢失）也标记为已处理，避免反复重试
            entry.setIcon(icon_from_file(None, self._thumb_size))
            entry.setToolTip(entry.toolTip() + "\n（无法生成缩略图：格式不支持或文件已丢失）")

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

    def showEvent(self, event) -> None:  # noqa: N802
        # 控件首次显示时布局才确定，此时再补一次可见区加载，
        # 否则从隐藏页切过来会满屏灰色占位
        super().showEvent(event)
        self._load_visible()


# --------------------------------------------------------------------------- 大图查看器


class ImageViewer(QScrollArea):
    """大图查看：滚轮缩放（Ctrl/直接）、拖拽平移、双击复位、适应窗口。

    用 QLabel 承载 QPixmap 并放进 QScrollArea —— 比自绘简单且滚动条天然可用。

    缩放模型（用户反馈「缩放和下一张后的缩放不一致」）：
    - `_mode == "fit"`：按窗口自适应，百分比随图片比例变化；
    - `_mode == "manual"`：**记住百分比**，翻到上一张/下一张仍保持同一个缩放比例；
    - 从「适应窗口」按 +/- 时，先把当前的适应百分比换算成手动百分比再乘系数，
      所以画面是连续变化的（此前会从 39% 直接跳到 125%，看起来"缩放不一致"）。
    """

    zoomChanged = Signal(int, bool)      # (百分比, 是否适应窗口)
    CANVAS_COLOR = QColor("#eef1f6")     # 画布底色：让留白看起来是"相框"而不是空白

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(f"background:{self.CANVAS_COLOR.name()};")
        self.setWidget(self._label)
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self._source = QPixmap()
        self._mode = "fit"
        self._percent = 100
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
        # 保留当前缩放模式：手动缩放过就继续用手动百分比，否则依旧自适应
        if self._mode != "manual":
            self._mode = "fit"
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
        self._label.setPixmap(self._framed(scaled))
        self._label.resize(scaled.width() + 2, scaled.height() + 2)
        self.zoomChanged.emit(self._display_percent(scaled.width()), self._mode == "fit")

    @staticmethod
    def _framed(pixmap: QPixmap) -> QPixmap:
        """给显示中的图片描一圈 1px 细边 —— 白底照片在浅色画布上才看得出边界。"""
        framed = QPixmap(pixmap.width() + 2, pixmap.height() + 2)
        framed.fill(QColor("#ccd4e2"))
        painter = QPainter(framed)
        painter.drawPixmap(1, 1, pixmap)
        painter.end()
        return framed

    def _display_percent(self, shown_width: int) -> int:
        if self._source.isNull() or not shown_width:
            return 100
        return max(1, int(round(shown_width * 100 / max(1, self._source.width()))))

    def _target_size(self) -> QSize:
        if self._mode == "fit":
            available = self.viewport().size()
            return QSize(max(1, available.width() - 8), max(1, available.height() - 8))
        return QSize(max(1, int(self._source.width() * self._percent / 100)),
                     max(1, int(self._source.height() * self._percent / 100)))

    def zoom_in(self) -> None:
        self._zoom(1.25)

    def zoom_out(self) -> None:
        self._zoom(0.8)

    def _zoom(self, factor: float) -> None:
        if self._source.isNull():
            return
        # 从「适应窗口」开始缩放时，先把当前适应比例接上，画面才不会突然跳变
        base = self.zoom_percent if self._mode == "fit" else self._percent
        self._percent = int(max(5, min(1200, round(max(1, base) * factor))))
        self._mode = "manual"
        self._render()

    def reset_zoom(self) -> None:
        self._mode = "fit"
        self._render()

    def actual_size(self) -> None:
        if self._source.isNull():
            return
        self._mode = "manual"
        self._percent = 100
        self._render()

    @property
    def zoom_percent(self) -> int:
        if self._source.isNull():
            return 0
        pixmap = self._label.pixmap()
        # _framed() 每边多 1px，扣除后再算百分比
        return self._display_percent(max(0, pixmap.width() - 2) if pixmap else 0)

    @property
    def is_fitted(self) -> bool:
        return self._mode == "fit"

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
        if self._mode == "fit":
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
