"""墨软图库 · 编辑美化对话框回归测试。

重点覆盖：曾经漏 import PIL.Image 导致 `Image.Resampling.LANCZOS` 抛 NameError，
打包后（无控制台）表现就是「右键 → 编辑美化 → 没反应」。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from modu_workbench.boards.gallery_editor import PREVIEW_MAX, ImageEditorDialog
from modu_workbench.core.image import ImageLibrary, ImageStorage


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture()
def library(tmp_path: Path) -> ImageLibrary:
    store = ImageStorage(str(tmp_path / "gallery.db"))
    lib = ImageLibrary(store, str(tmp_path / "cache"), image_dir=str(tmp_path / "images"))
    root = tmp_path / "photos"
    root.mkdir()
    rng = np.random.default_rng(11)
    # 尺寸刻意大于 PREVIEW_MAX，强制走「缩放预览」分支（就是崩溃的那一行）
    for index in range(2):
        side = PREVIEW_MAX + 400 + index * 50
        Image.fromarray(rng.integers(0, 255, (side, side - 200, 3), dtype="uint8")).save(
            root / f"大图{index}.jpg")
    lib.import_paths([str(root)])
    return lib


@pytest.fixture()
def item(library: ImageLibrary):  # noqa: ANN201
    items = library.list_images()
    assert items
    return items[0]


def button(dialog: ImageEditorDialog, text: str) -> QPushButton:
    for widget in dialog.findChildren(QPushButton):
        if widget.text() == text:
            return widget
    raise AssertionError(f"对话框里没有按钮：{text}")


def test_editor_opens_with_large_image(qapp: QApplication, library: ImageLibrary, item) -> None:  # noqa: ANN001, ARG001
    """大图必须能打开并生成缩放预览（PIL.Image 缺失时的回归点）。"""
    dialog = ImageEditorDialog(library, item)
    dialog.show()
    qapp.processEvents()
    try:
        assert dialog.isVisible()
        assert dialog._source is not None, "原图没读进来"
        assert dialog._preview is not None, "预览没生成"
        assert dialog._preview_scale < 1.0, "大图应当走缩放预览分支"
        assert max(dialog._preview.size) <= PREVIEW_MAX + 1
    finally:
        dialog.close()


def test_editor_operations_chain(qapp: QApplication, library: ImageLibrary, item) -> None:  # noqa: ANN001, ARG001
    """旋转/翻转/滤镜/调节/边框/文字/马赛克整条链路都不能抛异常。"""
    dialog = ImageEditorDialog(library, item)
    dialog.show()
    qapp.processEvents()
    try:
        for label in ("↺ 左转90°", "⇋ 水平翻转", "应用滤镜", "应用调节"):
            button(dialog, label).click()
            qapp.processEvents()
        dialog._push_ratio_crop(1.0)
        dialog._push_step("border", {"width": 8, "color": [255, 255, 255]})
        dialog._push_step("text", {"text": "测试", "x": 10, "y": 10, "size": 24,
                                   "color": [255, 255, 255], "shadow": True})
        dialog._push_step("mosaic", {"box": [10, 10, 60, 60], "block": 8, "mode": "mosaic"})
        qapp.processEvents()
        assert len(dialog._steps) >= 7

        dialog._undo()
        dialog._redo_step()
        dialog._reset()
        qapp.processEvents()
        assert dialog._steps == []
        assert "原图" in dialog._status.text()
    finally:
        dialog.close()


def test_editor_region_selection_modes(qapp: QApplication, library: ImageLibrary, item) -> None:  # noqa: ANN001, ARG001
    """画布框选的三条分支（裁剪 / 马赛克 / 涂鸦）都要能落到步骤栈里。"""
    from PySide6.QtCore import QRectF

    dialog = ImageEditorDialog(library, item)
    dialog.show()
    qapp.processEvents()
    try:
        box = QRectF(20, 30, 200, 160)
        for mode, expect in (("crop", "crop"), ("mosaic", "mosaic"), ("draw", "draw")):
            dialog._begin_select(mode)
            dialog._on_region_selected(box)
            qapp.processEvents()
            assert dialog._steps and dialog._steps[-1].step == expect, f"{mode} 没记录步骤"
        assert len(dialog._steps) == 3
    finally:
        dialog.close()


def test_editor_save_as_new_creates_file(qapp: QApplication, library: ImageLibrary,
                                         item, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001, ARG001
    """另存为新图：写出文件、发出 saved 信号、原图不动。"""
    dialog = ImageEditorDialog(library, item)
    dialog.show()
    qapp.processEvents()
    target = tmp_path / "导出" / "结果.jpg"
    target.parent.mkdir()
    monkeypatch.setattr(
        "modu_workbench.boards.gallery_editor.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(target), "")),
    )
    saved: list[str] = []
    dialog.saved.connect(saved.append)
    try:
        dialog._push_step("rotate", {"angle": 90})
        button(dialog, "另存为新图").click()
        qapp.processEvents()
    finally:
        dialog.close()
    assert saved == [str(target)]
    assert target.is_file() and target.stat().st_size > 0
    with Image.open(target) as exported:
        assert list(exported.size) == [item.height, item.width]      # 旋转后宽高互换


def test_editor_overwrite_needs_confirmation(qapp: QApplication, library: ImageLibrary,
                                             item, monkeypatch) -> None:  # noqa: ANN001, ARG001
    """覆盖原图必须二次确认：选「否」时原文件一个字节都不许变，也不发 saved。"""
    before = Path(item.path).read_bytes()
    dialog = ImageEditorDialog(library, item)
    dialog.show()
    qapp.processEvents()
    monkeypatch.setattr(
        "modu_workbench.boards.gallery_editor.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    saved: list[str] = []
    dialog.saved.connect(saved.append)
    try:
        dialog._push_step("rotate", {"angle": 90})
        button(dialog, "覆盖原图").click()
        qapp.processEvents()
    finally:
        dialog.close()
    assert saved == []
    assert Path(item.path).read_bytes() == before
