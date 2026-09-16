"""墨软图库 · 编辑美化：裁剪、旋转翻转、滤镜、调节、文字、马赛克、边框 + 撤销重做。

交互设计：
- 中间是可缩放的画布（QGraphicsView），裁剪/马赛克直接在图上框选；
- 右侧是工具面板，所有参数实时预览（在缩略尺寸上预览，保存时才全尺寸渲染）；
- 编辑是**非破坏性**的：所有操作记成步骤栈，可撤销/重做，原图不动；
- 保存默认另存为新图并入库，也可（二次确认后）覆盖原图。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.gallery import (
    ADJUST_LABELS,
    DEFAULT_ADJUST,
    EXPORT_FORMATS,
    FILTER_LABELS,
    EditStep,
    ImageItem,
    ImageLibrary,
    apply_steps,
    open_oriented,
)
from modu_workbench.ui_kit.toast import Toaster

# 预览用最长边（全尺寸只在保存时渲染，避免大图卡顿）
PREVIEW_MAX = 1400


class CropRectItem(QGraphicsRectItem):
    """可拖拽/缩放的裁剪框。"""

    def __init__(self) -> None:
        super().__init__()
        self.setPen(QPen(QColor("#4f5bd5"), 2, Qt.PenStyle.DashLine))
        self.setBrush(QBrush(QColor(79, 91, 213, 40)))
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, True)


class EditCanvas(QGraphicsView):
    """编辑画布：显示预览图，支持框选（裁剪/马赛克混合模式）。"""

    regionSelected = Signal(QRectF)      # 场景坐标下的选区

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._pixmap_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pixmap_item)
        self._rect = CropRectItem()
        self._rect.setVisible(False)
        self._scene.addItem(self._rect)
        self._select_mode = ""
        self._origin: QPointF | None = None
        self.setMinimumSize(420, 320)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap_item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def begin_select(self, mode: str) -> None:
        """进入框选模式（crop / mosaic）。"""
        self._select_mode = mode
        self._rect.setVisible(False)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def end_select(self) -> None:
        self._select_mode = ""
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.unsetCursor()

    def clear_rect(self) -> None:
        self._rect.setVisible(False)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._select_mode and event.button() == Qt.MouseButton.LeftButton:
            self._origin = self.mapToScene(event.position().toPoint())
            self._rect.setRect(QRectF(self._origin, self._origin))
            self._rect.setVisible(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._select_mode and self._origin is not None:
            current = self.mapToScene(event.position().toPoint())
            self._rect.setRect(QRectF(self._origin, current).normalized()
                               .intersected(QRectF(self._pixmap_item.pixmap().rect())))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._select_mode and self._origin is not None:
            self._origin = None
            rect = self._rect.rect()
            if rect.width() > 6 and rect.height() > 6:
                self.regionSelected.emit(rect)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)


class ImageEditorDialog(QDialog):
    """图片编辑对话框（非破坏性步骤栈 + 实时预览）。"""

    saved = Signal(str)      # 保存后的文件路径

    def __init__(self, library: ImageLibrary, item: ImageItem, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"编辑：{item.display()}")
        self.resize(1180, 760)
        self._library = library
        self._item = item
        self._toaster = Toaster(self)
        self._steps: list[EditStep] = library.load_steps(item.id)
        self._redo: list[EditStep] = []
        self._source = None
        self._preview = None
        self._preview_scale = 1.0
        self._text_color = [255, 255, 255]
        self._draw_points: list[tuple[int, int]] = []

        self._build_ui()
        self._load_source()
        self._refresh_preview()

    # ------------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel(self._item.display())
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)
        self._history_label = QLabel("")
        self._history_label.setObjectName("readerStatus")
        header.addWidget(self._history_label)
        layout.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(10)

        self._canvas = EditCanvas()
        self._canvas.regionSelected.connect(self._on_region_selected)
        body.addWidget(self._canvas, 1)

        panel = QTabWidget()
        panel.setFixedWidth(320)
        panel.addTab(self._tab_transform(), "基础")
        panel.addTab(self._tab_filter(), "滤镜调节")
        panel.addTab(self._tab_overlay(), "文字/马赛克")
        body.addWidget(panel)
        layout.addLayout(body, 1)

        actions = QHBoxLayout()
        self._undo_button = QPushButton("↶ 撤销")
        self._undo_button.clicked.connect(self._undo)
        self._redo_button = QPushButton("↷ 重做")
        self._redo_button.clicked.connect(self._redo_step)
        self._reset_button = QPushButton("重置")
        self._reset_button.clicked.connect(self._reset)
        preview_button = QPushButton("👁 按住看原图")
        preview_button.pressed.connect(self._show_original)
        preview_button.released.connect(self._refresh_preview)
        for widget in (self._undo_button, self._redo_button, self._reset_button, preview_button):
            actions.addWidget(widget)
        actions.addStretch(1)

        actions.addWidget(QLabel("保存格式"))
        self._format_combo = QComboBox()
        for fmt in EXPORT_FORMATS:
            self._format_combo.addItem(fmt.upper(), fmt)
        index = self._format_combo.findData((self._item.ext or "jpg").lower())
        if index >= 0:
            self._format_combo.setCurrentIndex(index)
        actions.addWidget(self._format_combo)

        save_button = QPushButton("另存为新图")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self._save_as_new)
        actions.addWidget(save_button)

        overwrite_button = QPushButton("覆盖原图")
        overwrite_button.setObjectName("dangerButton")
        overwrite_button.setToolTip("会修改原文件，需二次确认；建议先另存")
        overwrite_button.clicked.connect(self._overwrite)
        actions.addWidget(overwrite_button)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        self._status = QLabel("提示：裁剪/马赛克先点「开始框选」，在图上拖出区域。")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

    def _tab_transform(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        row = QHBoxLayout()
        for label, step in (("↺ 左转90°", ("rotate", {"angle": -90})),
                            ("↻ 右转90°", ("rotate", {"angle": 90})),
                            ("⇋ 水平翻转", ("flip", {"axis": "horizontal"})),
                            ("⇅ 垂直翻转", ("flip", {"axis": "vertical"}))):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, s=step: self._push_step(s[0], s[1]))
            row.addWidget(button)
        layout.addLayout(row)

        angle_row = QHBoxLayout()
        angle_row.addWidget(QLabel("任意角度"))
        self._angle = QSpinBox()
        self._angle.setRange(-180, 180)
        self._angle.setValue(0)
        angle_row.addWidget(self._angle, 1)
        apply_angle = QPushButton("应用")
        apply_angle.clicked.connect(
            lambda: self._push_step("rotate", {"angle": self._angle.value(), "expand": True}))
        angle_row.addWidget(apply_angle)
        layout.addLayout(angle_row)

        crop_box = QWidget()
        crop_layout = QVBoxLayout(crop_box)
        crop_layout.addWidget(QLabel("裁剪：点下面按钮后在图上拖出区域"))
        crop_row = QHBoxLayout()
        crop_button = QPushButton("✂ 开始框选")
        crop_button.clicked.connect(lambda: self._begin_select("crop"))
        crop_row.addWidget(crop_button)
        for label, ratio in (("1:1", 1.0), ("4:3", 4 / 3), ("16:9", 16 / 9), ("3:4", 3 / 4)):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, r=ratio: self._push_ratio_crop(r))
            crop_row.addWidget(button)
        crop_layout.addLayout(crop_row)
        layout.addWidget(crop_box)

        border_row = QHBoxLayout()
        border_row.addWidget(QLabel("边框宽度"))
        self._border_width = QSpinBox()
        self._border_width.setRange(0, 200)
        self._border_width.setValue(12)
        border_row.addWidget(self._border_width)
        border_button = QPushButton("加边框")
        border_button.clicked.connect(
            lambda: self._push_step("border", {"width": self._border_width.value(),
                                               "color": [250, 250, 250]}))
        border_row.addWidget(border_button)
        layout.addLayout(border_row)

        layout.addStretch(1)
        return page

    def _tab_filter(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("滤镜"))
        self._filter_combo = QComboBox()
        for key, label in FILTER_LABELS.items():
            self._filter_combo.addItem(label, key)
        filter_row.addWidget(self._filter_combo, 1)
        layout.addLayout(filter_row)

        self._filter_amount = QSlider(Qt.Orientation.Horizontal)
        self._filter_amount.setRange(0, 100)
        self._filter_amount.setValue(100)
        layout.addWidget(QLabel("滤镜强度"))
        layout.addWidget(self._filter_amount)

        apply_filter = QPushButton("应用滤镜")
        apply_filter.clicked.connect(
            lambda: self._push_step("filter", {"name": self._filter_combo.currentData(),
                                               "amount": self._filter_amount.value() / 100}))
        layout.addWidget(apply_filter)

        form = QFormLayout()
        self._adjust_sliders: dict[str, QSlider] = {}
        specs = (
            ("brightness", 50, 180, 100),
            ("contrast", 50, 180, 100),
            ("saturation", 0, 200, 100),
            ("sharpness", 0, 200, 100),
            ("temperature", -100, 100, 0),
        )
        for key, low, high, default in specs:
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(low, high)
            slider.setValue(default)
            slider.valueChanged.connect(self._on_adjust_changed)
            self._adjust_sliders[key] = slider
            form.addRow(ADJUST_LABELS[key], slider)
        layout.addLayout(form)

        reset_adjust = QPushButton("重置调节")
        reset_adjust.clicked.connect(self._reset_adjust_sliders)
        apply_adjust = QPushButton("应用调节")
        apply_adjust.setObjectName("primaryButton")
        apply_adjust.clicked.connect(self._apply_adjust)
        layout.addWidget(apply_adjust)
        layout.addWidget(reset_adjust)
        layout.addStretch(1)
        return page

    def _tab_overlay(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel("文字（叠加在左上角）"))
        self._text_input = QLineEdit()
        self._text_input.setPlaceholderText("要添加的文字")
        layout.addWidget(self._text_input)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("字号"))
        self._text_size = QSpinBox()
        self._text_size.setRange(8, 400)
        self._text_size.setValue(48)
        size_row.addWidget(self._text_size)
        color_button = QPushButton("颜色…")
        color_button.clicked.connect(self._pick_text_color)
        size_row.addWidget(color_button)
        layout.addLayout(size_row)
        add_text = QPushButton("添加文字")
        add_text.clicked.connect(self._add_text)
        layout.addWidget(add_text)

        layout.addSpacing(12)
        layout.addWidget(QLabel("马赛克 / 模糊：先框选再选模式"))
        mosaic_row = QHBoxLayout()
        self._mosaic_block = QSpinBox()
        self._mosaic_block.setRange(3, 80)
        self._mosaic_block.setValue(14)
        mosaic_row.addWidget(QLabel("块大小"))
        mosaic_row.addWidget(self._mosaic_block)
        self._mosaic_mode = QComboBox()
        self._mosaic_mode.addItem("马赛克", "mosaic")
        self._mosaic_mode.addItem("模糊", "blur")
        mosaic_row.addWidget(self._mosaic_mode)
        layout.addLayout(mosaic_row)
        mosaic_button = QPushButton("▦ 开始框选马赛克")
        mosaic_button.clicked.connect(lambda: self._begin_select("mosaic"))
        layout.addWidget(mosaic_button)

        layout.addSpacing(12)
        layout.addWidget(QLabel("涂鸦：在图上拖动画出线条（点「开始涂鸦」后生效）"))
        draw_row = QHBoxLayout()
        self._draw_width = QSpinBox()
        self._draw_width.setRange(1, 60)
        self._draw_width.setValue(8)
        draw_row.addWidget(QLabel("粗细"))
        draw_row.addWidget(self._draw_width)
        self._draw_erase = QCheckBox("橡皮")
        draw_row.addWidget(self._draw_erase)
        layout.addLayout(draw_row)
        self._draw_button = QPushButton("✎ 开始涂鸦")
        self._draw_button.setCheckable(True)
        self._draw_button.toggled.connect(self._toggle_draw)
        layout.addWidget(self._draw_button)

        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------ 数据

    def _load_source(self) -> None:
        try:
            self._source = open_oriented(self._item.path)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"无法读取图片：{error}")
            self._source = None
            return
        width, height = self._source.size
        scale = min(1.0, PREVIEW_MAX / max(1, max(width, height)))
        self._preview_scale = scale
        if scale < 1.0:
            self._preview = self._source.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.LANCZOS)
        else:
            self._preview = self._source.copy()

    def _refresh_preview(self) -> None:
        if self._preview is None:
            return
        rendered = apply_steps(self._preview, self._steps)
        self._canvas.set_pixmap(self._pil_to_pixmap(rendered))
        labels = [step.label for step in self._steps]
        self._history_label.setText(
            f"步骤 {len(self._steps)}" + (f"：{' → '.join(labels[-5:])}" if labels else "")
        )
        self._undo_button.setEnabled(bool(self._steps))
        self._redo_button.setEnabled(bool(self._redo))
        if self._source is not None:
            self._status.setText(
                f"原图 {self._source.width}×{self._source.height}；"
                f"预览按 {self._preview_scale:.0%} 渲染，保存时用全尺寸。"
            )

    @staticmethod
    def _pil_to_pixmap(image) -> QPixmap:  # noqa: ANN001
        rgb = image.convert("RGBA")
        data = rgb.tobytes("raw", "RGBA")
        qimage = QImage(data, rgb.width, rgb.height, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qimage.copy())

    def _push_step(self, step: str, params: dict) -> None:
        self._steps.append(EditStep(step, params))
        self._redo.clear()
        self._refresh_preview()

    def _undo(self) -> None:
        if not self._steps:
            return
        self._redo.append(self._steps.pop())
        self._refresh_preview()

    def _redo_step(self) -> None:
        if not self._redo:
            return
        self._steps.append(self._redo.pop())
        self._refresh_preview()

    def _reset(self) -> None:
        self._steps.clear()
        self._redo.clear()
        self._refresh_preview()

    def _show_original(self) -> None:
        if self._preview is not None:
            self._canvas.set_pixmap(self._pil_to_pixmap(self._preview))

    # ------------------------------------------------------------------ 交互

    def _begin_select(self, mode: str) -> None:
        if mode == "draw":
            self._draw_points = []
        self._canvas.begin_select(mode)
        self._status.setText(
            "在图上拖出区域；松开后自动应用（裁剪/马赛克）。涂鸦模式下拖动即为画笔。"
        )

    def _on_region_selected(self, rect: QRectF) -> None:
        # 画布坐标 → 原图坐标（先把预览坐标还原到原图尺度）
        scale = self._preview_scale or 1.0
        box = [
            int(rect.left() / scale), int(rect.top() / scale),
            int(rect.width() / scale), int(rect.height() / scale),
        ]
        mode = getattr(self._canvas, "_select_mode", "")
        if mode == "crop":
            self._canvas.end_select()
            self._canvas.clear_rect()
            self._push_step("crop", {"box": box})
            self._status.setText(f"已裁剪：{box[2]}×{box[3]}")
        elif mode == "mosaic":
            self._canvas.end_select()
            self._canvas.clear_rect()
            self._push_step("mosaic", {
                "box": box,
                "block": self._mosaic_block.value(),
                "mode": self._mosaic_mode.currentData(),
            })
            self._status.setText("已应用马赛克/模糊")
        elif mode == "draw":
            # 涂鸦：把选区当作一笔线段记录，保持涂鸦模式继续画
            self._push_step("draw", {
                "points": [
                    [box[0], box[1]],
                    [box[0] + max(1, box[2]), box[1] + max(1, box[3])],
                ],
                "color": [230, 60, 60],
                "width": self._draw_width.value(),
                "erase": self._draw_erase.isChecked(),
            })
        else:
            self._canvas.clear_rect()

    def _push_ratio_crop(self, ratio: float) -> None:
        if self._source is None:
            return
        width, height = self._source.size
        if width / height > ratio:
            new_height = height
            new_width = int(height * ratio)
        else:
            new_width = width
            new_height = int(width / ratio)
        left = (width - new_width) // 2
        top = (height - new_height) // 2
        self._push_step("crop", {"box": [left, top, new_width, new_height]})
        self._status.setText(f"已按比例裁剪（{ratio:.2f}:1）")

    def _pick_text_color(self) -> None:
        color = QColorDialog.getColor(QColor(*self._text_color), self, "选择文字颜色")
        if color.isValid():
            self._text_color = [color.red(), color.green(), color.blue()]

    def _add_text(self) -> None:
        text = self._text_input.text().strip()
        if not text:
            self._toaster.info("请输入文字内容")
            return
        self._push_step("text", {
            "text": text, "x": 16, "y": 16,
            "size": self._text_size.value(), "color": list(self._text_color), "shadow": True,
        })
        self._status.setText(f"已添加文字：{text}")

    def _on_adjust_changed(self) -> None:
        """调节滑块变化时实时预览（不写入步骤栈，点「应用调节」才记录）。"""
        params = self._current_adjust()
        preview_steps = self._steps + [EditStep("adjust", params)]
        if self._preview is not None:
            rendered = apply_steps(self._preview, preview_steps)
            self._canvas.set_pixmap(self._pil_to_pixmap(rendered))

    def _current_adjust(self) -> dict:
        values = {key: slider.value() for key, slider in self._adjust_sliders.items()}
        return {
            "brightness": values["brightness"] / 100,
            "contrast": values["contrast"] / 100,
            "saturation": values["saturation"] / 100,
            "sharpness": values["sharpness"] / 100,
            "temperature": values["temperature"] / 100,
        }

    def _reset_adjust_sliders(self) -> None:
        defaults = {"brightness": 100, "contrast": 100, "saturation": 100,
                    "sharpness": 100, "temperature": 0}
        for key, slider in self._adjust_sliders.items():
            slider.blockSignals(True)
            slider.setValue(defaults[key])
            slider.blockSignals(False)
        self._refresh_preview()

    def _apply_adjust(self) -> None:
        self._push_step("adjust", self._current_adjust())
        self._reset_adjust_sliders()
        self._status.setText("已应用调节")

    def _toggle_draw(self, enabled: bool) -> None:
        if enabled:
            self._canvas.begin_select("draw")
            self._status.setText("在图上拖动画线；再次点击按钮结束涂鸦")
        else:
            self._canvas.end_select()
            self._status.setText("已结束涂鸦")

    # ------------------------------------------------------------------ 保存

    def _save_as_new(self) -> None:
        fmt = self._format_combo.currentData() or "jpg"
        default = self._library.export_default_path(self._item, fmt=fmt, tag="edited")
        target, _ = QFileDialog.getSaveFileName(self, "另存为新图", str(default),
                                                f"{fmt.upper()} (*.{fmt})")
        if not target:
            return
        try:
            path = self._library.export_edited(self._item, target, fmt=fmt, steps=self._steps)
            self._library.save_steps(self._item.id, self._steps)   # 记住编辑栈，便于再改
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"保存失败：{error}")
            return
        self._toaster.success(f"已保存：{Path(path).name}")
        self.saved.emit(str(path))
        self.accept()

    def _overwrite(self) -> None:
        if not self._item.path:
            self._toaster.error("该条目没有本地文件")
            return
        answer = QMessageBox.question(
            self, "覆盖原图",
            f"将直接修改原文件：\n{self._item.path}\n\n此操作不可撤销，确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        fmt = self._format_combo.currentData() or (self._item.ext or "jpg")
        try:
            path = self._library.overwrite_original(self._item, fmt=fmt, steps=self._steps)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"覆盖失败：{error}")
            return
        self._toaster.success(f"已覆盖：{Path(path).name}")
        self.saved.emit(str(path))
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        for image in (self._preview, self._source):
            try:
                if image is not None:
                    image.close()
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)


__all__ = ["ImageEditorDialog"]
