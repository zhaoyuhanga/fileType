"""图库打磨：每页张数记忆、设置里的导入目录生效、导入结束后任务条收起。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from modu_workbench.boards.gallery.board import GalleryBoardPage
from modu_workbench.core.gallery import ImageLibrary, ImageStorage

ORG, APP = "ModuWorkbench", "modu-workbench"


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_gallery_settings():
    settings = QSettings(ORG, APP)
    for key in ("gallery/page_size", "gallery/import_dir"):
        settings.remove(key)
    settings.sync()
    yield
    for key in ("gallery/page_size", "gallery/import_dir"):
        settings.remove(key)
    settings.sync()


@pytest.fixture()
def library(tmp_path: Path) -> ImageLibrary:
    store = ImageStorage(str(tmp_path / "gallery.db"))
    return ImageLibrary(store, str(tmp_path / "cache"), image_dir=str(tmp_path / "images"))


def _open(library: ImageLibrary) -> GalleryBoardPage:
    page = GalleryBoardPage(library=library)
    page.resize(1000, 700)
    page.show()
    return page


def test_page_size_from_settings_is_selected_and_visible(
    qapp: QApplication, library: ImageLibrary
) -> None:
    """设置里填 300 张/页：下拉必须有这一项，否则显示与实际分页对不上。"""
    settings = QSettings(ORG, APP)
    settings.setValue("gallery/page_size", 300)
    settings.sync()

    page = _open(library)
    try:
        assert page.page_size == 300
        assert page._page_size_combo.findData(300) >= 0  # noqa: SLF001
        assert page._page_size_combo.currentData() == 300  # noqa: SLF001
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_changing_page_size_is_remembered(qapp: QApplication, library: ImageLibrary) -> None:
    page = _open(library)
    try:
        index = page._page_size_combo.findData(60)  # noqa: SLF001
        page._page_size_combo.setCurrentIndex(index)  # noqa: SLF001
        assert page.page_size == 60
        assert int(QSettings(ORG, APP).value("gallery/page_size")) == 60
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_import_dialogs_start_in_configured_dir(
    qapp: QApplication, library: ImageLibrary, tmp_path: Path
) -> None:
    """设置 → 图库「默认导入目录」以前没人读，导入对话框永远从空/图片目录开始。"""
    target = tmp_path / "我的图片"
    target.mkdir()
    settings = QSettings(ORG, APP)
    settings.setValue("gallery/import_dir", str(target))
    settings.sync()

    page = _open(library)
    try:
        assert Path(page._import_start_dir()) == target  # noqa: SLF001
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_editor_keeps_unsaved_steps_as_draft(
    qapp: QApplication, library: ImageLibrary, tmp_path: Path
) -> None:
    """编辑十几步后点「关闭」不再全部丢失：步骤栈作为草稿落库，下次打开还在。"""
    from PIL import Image as PILImage

    from modu_workbench.boards.gallery.editor import ImageEditorDialog
    from modu_workbench.core.gallery import EditStep

    source = tmp_path / "草稿.jpg"
    PILImage.new("RGB", (80, 60), (120, 140, 160)).save(source)
    library.import_paths([str(source)])
    item = library.list_images()[0]

    dialog = ImageEditorDialog(library, item)
    try:
        dialog._steps.append(EditStep("adjust", {"brightness": 1.2}))  # noqa: SLF001
    finally:
        dialog.close()
    qapp.processEvents()

    steps = library.load_steps(item.id)
    assert steps and steps[-1].step == "adjust"
