"""墨软图库 · AI 优化：本地增强算法的选择、参数、前后对比与批量执行。

界面明确区分能力边界：
- 「本地算法」= 真正能做好；
- 「近似效果」= 只能做到近似（背景移除/消除/老照片/人像柔化）；
不做成"点了没反应"的按钮 —— 每项都给出说明与保真度标注。

DeepSeek 的文本能力不在这里（它不能改像素），见图库的「AI 描述/标签」。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.image import (
    SPECS,
    ImageItem,
    ImageLibrary,
    STYLE_PRESETS,
    apply_enhancement,
    compute_quality_metrics,
    open_oriented,
)
from modu_workbench.ui_kit.toast import Toaster

from .gallery_widgets import EnhanceWorker, ITEM_ROLE

PREVIEW_MAX = 420


def _pil_to_pixmap(image) -> QPixmap:  # noqa: ANN001
    rgb = image.convert("RGBA")
    data = rgb.tobytes("raw", "RGBA")
    qimage = QImage(data, rgb.width, rgb.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimage.copy())


class EnhanceDialog(QDialog):
    """选择优化方式 → 预览前后对比 → 批量执行（不覆盖原图）。"""

    finishedAll = Signal(object, object)   # (ok 列表, 错误列表)

    def __init__(self, library: ImageLibrary, items: list[ImageItem],
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("AI 图片优化（本地算法）")
        self.resize(1080, 720)
        self._library = library
        self._items = list(items)
        self._toaster = Toaster(self)
        self._worker: EnhanceWorker | None = None
        self._source = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("AI 图片优化")
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)
        header.addWidget(QLabel(f"待处理 {len(self._items)} 张"))
        layout.addLayout(header)

        note = QLabel(
            "说明：以下为**本地算法**（numpy + Pillow），不联网、不上传图片、不依赖模型。"
            "DeepSeek 只处理文本（描述/标签/参数建议），无法编辑像素 —— 真正的图像优化在这里。"
        )
        note.setObjectName("readerStatus")
        note.setWordWrap(True)
        layout.addWidget(note)

        body = QHBoxLayout()
        body.setSpacing(10)

        self._list = QListWidget()
        self._list.setFixedWidth(300)
        for spec in SPECS:
            entry = QListWidgetItem(f"{spec.label}　[{spec.fidelity_label}]")
            entry.setData(ITEM_ROLE, spec.key)
            entry.setToolTip(f"{spec.description}\n耗时：{spec.cost}")
            self._list.addItem(entry)
        self._list.setCurrentRow(0)
        self._list.currentItemChanged.connect(lambda *_: self._on_spec_changed())
        body.addWidget(self._list)

        right = QVBoxLayout()
        self._desc = QLabel("")
        self._desc.setObjectName("readerStatus")
        self._desc.setWordWrap(True)
        right.addWidget(self._desc)

        # 参数区（按选中能力显示/隐藏）
        self._params_row = QHBoxLayout()
        self._scale_label = QLabel("放大倍数")
        self._scale = QComboBox()
        for factor in (2, 3, 4):
            self._scale.addItem(f"{factor}x", factor)
        self._params_row.addWidget(self._scale_label)
        self._params_row.addWidget(self._scale)

        self._strength_label = QLabel("强度")
        self._strength = QSpinBox()
        self._strength.setRange(1, 20)
        self._strength.setValue(10)
        self._params_row.addWidget(self._strength_label)
        self._params_row.addWidget(self._strength)

        self._style_label = QLabel("风格")
        self._style = QComboBox()
        for key, (label, tip) in STYLE_PRESETS.items():
            self._style.addItem(label, key)
            self._style.setItemData(self._style.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self._params_row.addWidget(self._style_label)
        self._params_row.addWidget(self._style)

        self._bg_label = QLabel("背景")
        self._background = QComboBox()
        self._background.addItem("透明（PNG）", "transparent")
        self._background.addItem("白色", "white")
        self._background.addItem("黑色", "black")
        self._background.addItem("浅灰", "gray")
        self._params_row.addWidget(self._bg_label)
        self._params_row.addWidget(self._background)
        self._params_row.addStretch(1)
        right.addLayout(self._params_row)

        # 前后对比
        compare = QHBoxLayout()
        before_box = QVBoxLayout()
        before_box.addWidget(QLabel("处理前"))
        self._before = QLabel("（选择图片后显示）")
        self._before.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._before.setMinimumSize(PREVIEW_MAX, PREVIEW_MAX)
        self._before.setStyleSheet("background:#f1f3f7;border:1px solid #e3e8f2;")
        before_box.addWidget(self._before)
        compare.addLayout(before_box)

        after_box = QVBoxLayout()
        after_box.addWidget(QLabel("处理后（预览）"))
        self._after = QLabel("点「预览效果」查看")
        self._after.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._after.setMinimumSize(PREVIEW_MAX, PREVIEW_MAX)
        self._after.setStyleSheet("background:#f1f3f7;border:1px solid #e3e8f2;")
        after_box.addWidget(self._after)
        compare.addLayout(after_box)
        right.addLayout(compare)

        self._metrics = QLabel("")
        self._metrics.setObjectName("readerStatus")
        self._metrics.setWordWrap(True)
        right.addWidget(self._metrics)

        preview_row = QHBoxLayout()
        preview_button = QPushButton("预览效果")
        preview_button.clicked.connect(self._preview)
        preview_row.addWidget(preview_button)
        self._save_preview = QCheckBox("把预览结果另存为一张新图")
        self._save_preview.setToolTip("用于先看效果再决定是否处理整批")
        preview_row.addWidget(self._save_preview)
        preview_row.addStretch(1)
        right.addLayout(preview_row)
        body.addLayout(right, 1)
        layout.addLayout(body, 1)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._status = QLabel(
            "选择一种优化方式 → 预览 → 应用到当前图片或全部选中图片。"
            "处理结果始终另存为新图，原图不会被修改。"
        )
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        actions = QHBoxLayout()
        self._apply_one = QPushButton("应用到当前图片")
        self._apply_one.clicked.connect(lambda: self._start(only_first=True))
        actions.addWidget(self._apply_one)
        self._apply_all = QPushButton(f"应用到全部 {len(self._items)} 张")
        self._apply_all.setObjectName("primaryButton")
        self._apply_all.clicked.connect(lambda: self._start(only_first=False))
        actions.addWidget(self._apply_all)
        self._cancel_button = QPushButton("取消任务")
        self._cancel_button.setObjectName("dangerButton")
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self._cancel)
        actions.addWidget(self._cancel_button)
        actions.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        self._on_spec_changed()
        self._load_source_preview()

    # ------------------------------------------------------------------ 参数

    def _current_spec(self):  # noqa: ANN202
        entry = self._list.currentItem()
        if entry is None:
            return SPECS[0]
        key = entry.data(ITEM_ROLE)
        return next((spec for spec in SPECS if spec.key == key), SPECS[0])

    def _on_spec_changed(self) -> None:
        spec = self._current_spec()
        self._desc.setText(f"【{spec.label}】{spec.description}"
                           f"　保真度：{spec.fidelity_label}　耗时：{spec.cost}")
        accepts = set(spec.accepts)
        self._scale_label.setVisible("scale" in accepts)
        self._scale.setVisible("scale" in accepts)
        self._strength_label.setVisible("strength" in accepts or "amount" in accepts)
        self._strength.setVisible("strength" in accepts or "amount" in accepts)
        self._style_label.setVisible("style" in accepts)
        self._style.setVisible("style" in accepts)
        self._bg_label.setVisible("background" in accepts)
        self._background.setVisible("background" in accepts)
        if spec.fidelity == "approximate":
            self._status.setText(
                f"注意：「{spec.label}」是**近似效果**（本地算法近似），复杂画面可能不理想；"
                "满意后再保存。"
            )

    def _params(self) -> dict:
        spec = self._current_spec()
        params: dict = {}
        if "scale" in spec.accepts:
            params["scale"] = int(self._scale.currentData() or 2)
        if "strength" in spec.accepts:
            params["strength"] = self._strength.value() / 10
        if "amount" in spec.accepts:
            params["amount"] = self._strength.value() / 10
        if "style" in spec.accepts:
            params["style"] = str(self._style.currentData() or "vivid")
        if "background" in spec.accepts:
            params["background"] = str(self._background.currentData() or "transparent")
        return params

    # ------------------------------------------------------------------ 预览

    def _current_item(self) -> ImageItem | None:
        return self._items[0] if self._items else None

    def _load_source_preview(self) -> None:
        item = self._current_item()
        if item is None or not item.path:
            self._before.setText("没有可用的本地图片")
            return
        try:
            self._source = open_oriented(item.path)
        except Exception as error:  # noqa: BLE001
            self._before.setText(f"无法读取：{error}")
            return
        self._before.setPixmap(self._scaled(self._source))

    def _scaled(self, image) -> QPixmap:  # noqa: ANN001
        copy = image.copy()
        copy.thumbnail((PREVIEW_MAX, PREVIEW_MAX))
        pixmap = _pil_to_pixmap(copy)
        copy.close()
        return pixmap

    def _preview(self) -> None:
        if self._source is None:
            self._toaster.info("没有可预览的图片")
            return
        spec = self._current_spec()
        if "mask" in spec.accepts:
            self._toaster.info("「AI 消除」需要在编辑美化里用框选指定区域，暂不能整图预览")
            return
        self._status.setText(f"正在预览「{spec.label}」…")
        try:
            processed = apply_enhancement(spec.key, self._source, self._params())
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"预览失败：{error}")
            return
        try:
            self._after.setPixmap(self._scaled(processed))
            before = compute_quality_metrics(self._source)
            after = compute_quality_metrics(processed)
            self._metrics.setText(
                f"清晰度 {before['sharpness']} → {after['sharpness']}　"
                f"噪点 {before['noise']} → {after['noise']}　"
                f"对比 {before['contrast']} → {after['contrast']}　"
                f"亮度 {before['brightness']} → {after['brightness']}"
                "（清晰度/对比越大越好，噪点越小越好）"
            )
        finally:
            try:
                processed.close()
            except Exception:  # noqa: BLE001
                pass
        self._status.setText(f"已预览「{spec.label}」；满意后应用到图片（结果另存为新图）。")
        if self._save_preview.isChecked():
            self._save_item_preview(spec)

    def _save_item_preview(self, spec) -> None:  # noqa: ANN001
        item = self._current_item()
        if item is None:
            return
        try:
            path = self._library.run_enhancement(item, spec.key, self._params())
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"另存失败：{error}")
            return
        self._toaster.success(f"已另存预览：{Path(path).name}")

    # ------------------------------------------------------------------ 执行

    def _start(self, *, only_first: bool) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._toaster.info("已有任务在进行")
            return
        spec = self._current_spec()
        if "mask" in spec.accepts:
            self._toaster.info("「AI 消除」请在「编辑美化」里框选区域后应用")
            return
        targets = self._items[:1] if only_first else self._items
        if not targets:
            return
        self._cancel_button.setEnabled(True)
        self._progress.setValue(0)
        self._worker = EnhanceWorker(self._library, targets, spec.key, self._params(), parent=self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.finishedAll.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(lambda: self._cancel_button.setEnabled(False))
        self._worker.start()
        self._status.setText(f"开始处理 {len(targets)} 张：{spec.label}")

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._status.setText("正在取消…")

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self._progress.setValue(int(done * 100 / total) if total else 0)
        self._status.setText(message)

    def _on_finished(self, outputs: list, errors: list) -> None:
        self._progress.setValue(100)
        message = f"处理完成：成功 {len(outputs)}，失败 {len(errors)}"
        if errors:
            message += "；" + "；".join(errors[:3])
        self._status.setText(message)
        if outputs:
            self._toaster.success(f"已生成 {len(outputs)} 张新图（原图未改动）")
        else:
            self._toaster.error("处理失败，请查看状态栏原因")
        self.finishedAll.emit(outputs, errors)

    def _on_failed(self, message: str) -> None:
        self._status.setText(f"任务失败：{message}")
        self._toaster.error(message)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        if self._source is not None:
            try:
                self._source.close()
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)


__all__ = ["EnhanceDialog"]
