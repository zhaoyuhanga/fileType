"""AI 优化（本地增强）对话框测试：风格卡片、参数区显隐、批量执行与进度。

用户反馈「风格下拉框很丑」——现在改成可视化卡片，这里锁住它的行为：
互斥选中、说明文字同步、参数真的传进算法。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QPushButton

from modu_workbench.boards.gallery.enhance import EnhanceDialog
from modu_workbench.core.gallery import SPECS, STYLE_PRESETS, ImageLibrary, ImageStorage


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def library(tmp_path: Path) -> ImageLibrary:
    store = ImageStorage(str(tmp_path / "gallery.db"))
    lib = ImageLibrary(store, str(tmp_path / "cache"), image_dir=str(tmp_path / "images"))
    root = tmp_path / "photos"
    root.mkdir()
    rng = np.random.default_rng(5)
    for index in range(3):
        Image.fromarray(rng.integers(0, 255, (120, 90, 3), dtype="uint8")).save(
            root / f"照片{index}.jpg")
    lib.import_paths([str(root)])
    return lib


@pytest.fixture()
def items(library: ImageLibrary):  # noqa: ANN201
    return library.list_images()


@pytest.fixture()
def dialog(qapp: QApplication, library: ImageLibrary, items):  # noqa: ANN001, ARG001
    widget = EnhanceDialog(library, items)
    widget.resize(1100, 720)
    widget.show()
    qapp.processEvents()
    yield widget
    if widget._worker is not None and widget._worker.isRunning():
        widget._worker.cancel()
        widget._worker.wait(5000)
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def select_spec(dialog: EnhanceDialog, key: str) -> None:
    for row in range(dialog._list.count()):
        entry = dialog._list.item(row)
        if entry.data(33) == key or entry.text().startswith(
                next(spec.label for spec in SPECS if spec.key == key)):
            dialog._list.setCurrentRow(row)
            return
    raise AssertionError(f"能力列表里没有 {key}")


def test_style_chips_exist_and_are_exclusive(dialog: EnhanceDialog, qapp: QApplication) -> None:
    """每种风格一个卡片按钮；点一个只亮一个，说明文字跟着变。"""
    assert set(dialog._style_buttons) == set(STYLE_PRESETS)
    select_spec(dialog, "stylize")
    qapp.processEvents()
    assert dialog._style_box.isVisible()

    for key, (label, tip) in STYLE_PRESETS.items():
        dialog._style_buttons[key].click()
        qapp.processEvents()
        checked = [k for k, button in dialog._style_buttons.items() if button.isChecked()]
        assert checked == [key], f"{key} 没有做到互斥选中"
        assert dialog._style.currentData() == key
        assert label in dialog._style_hint.text() and tip in dialog._style_hint.text()


def test_selected_style_reaches_params(dialog: EnhanceDialog, qapp: QApplication) -> None:
    select_spec(dialog, "stylize")
    qapp.processEvents()
    dialog._style_buttons["mono"].click()
    qapp.processEvents()
    params = dialog._params()
    assert params["style"] == "mono"


def test_params_panel_hides_when_spec_needs_nothing(dialog: EnhanceDialog,
                                                    qapp: QApplication) -> None:
    """一键增强不需要参数 → 参数区整体收起；超分只需要倍数。"""
    select_spec(dialog, "auto_enhance")
    qapp.processEvents()
    assert not dialog._params_card.isVisible()

    select_spec(dialog, "upscale")
    qapp.processEvents()
    assert dialog._params_card.isVisible()
    assert dialog._scale.isVisible()
    assert not dialog._strength.isVisible()
    assert not dialog._style_box.isVisible()


def test_apply_all_runs_every_item(dialog: EnhanceDialog, qapp: QApplication) -> None:
    """「应用到全部」要有真实进度与产出（不是点了没反应）。"""
    select_spec(dialog, "auto_enhance")
    qapp.processEvents()
    dialog._apply_all.click()
    worker = dialog._worker
    assert worker is not None
    seen: list[tuple[int, int, str]] = []
    worker.progressed.connect(lambda d, t, m: seen.append((d, t, m)))
    worker.wait(120_000)
    qapp.processEvents()

    assert seen and seen[-1][0] == seen[-1][1] == 3      # 3 张全部完成
    assert dialog._progress.value() == 100
    assert "成功 3" in dialog._status.text()
    assert not dialog._cancel_button.isEnabled()


def button(dialog: EnhanceDialog, text: str):
    for widget in dialog.findChildren(QPushButton):
        if widget.text() == text:
            return widget
    raise AssertionError(f"对话框里没有按钮：{text}")


def test_preview_reports_quality_metrics(dialog: EnhanceDialog, qapp: QApplication) -> None:
    button(dialog, "预览效果").click()
    qapp.processEvents()
    assert "清晰度" in dialog._metrics.text()
    assert dialog._after.pixmap() is not None and not dialog._after.pixmap().isNull()
