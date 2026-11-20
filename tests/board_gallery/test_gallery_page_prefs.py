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


def test_duplicate_dialog_keeps_first_of_each_group(qapp: QApplication) -> None:
    """重复分组默认只勾「每组第一张以外」的图，避免一键把整组删光。"""
    from modu_workbench.boards.gallery.widgets import DuplicateGroupsDialog
    from modu_workbench.core.gallery import ImageItem

    groups = [
        [ImageItem(id=1, path="a.jpg", title="A"), ImageItem(id=2, path="a2.jpg", title="A2"),
         ImageItem(id=3, path="a3.jpg", title="A3")],
        [ImageItem(id=4, path="b.jpg", title="B"), ImageItem(id=5, path="b2.jpg", title="B2")],
    ]
    dialog = DuplicateGroupsDialog(groups)
    try:
        assert dialog.selected_ids() == [2, 3, 5]
    finally:
        dialog.close()


def test_find_duplicates_can_be_cancelled(library: ImageLibrary) -> None:
    from modu_workbench.core.gallery import ImageItem

    library.storage.list_images = lambda **kwargs: [  # type: ignore[method-assign]
        ImageItem(id=index, path=f"{index}.jpg", dhash="0000000000000000")
        for index in range(1, 7)
    ]
    seen = {"n": 0}

    def cancel() -> bool:
        seen["n"] += 1
        return seen["n"] > 1

    groups = library.find_duplicates(cancel=cancel)
    assert len(groups) < 2, "取消后不该继续把所有分组都算完"


def test_album_can_be_renamed_and_deleted(
    qapp: QApplication, library: ImageLibrary, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相册重命名/删除以前只能改 SQLite；界面补上右键入口。"""
    from PIL import Image as PILImage
    from PySide6.QtWidgets import QMessageBox

    from modu_workbench.boards.gallery import board as gallery_board

    source = tmp_path / "相册图.jpg"
    PILImage.new("RGB", (40, 40), (10, 20, 30)).save(source)
    library.import_paths([str(source)])
    album_id = library.create_album("旧相册")
    assert album_id

    page = _open(library)
    try:
        monkeypatch.setattr(gallery_board, "ask_text", lambda *args, **kwargs: "新相册")
        page._rename_album(album_id)  # noqa: SLF001
        assert library.storage.get_album(album_id).name == "新相册"

        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )
        page._delete_album(album_id)  # noqa: SLF001
        assert library.storage.get_album(album_id) is None
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


def test_tag_can_be_renamed_and_removed_from_images(
    qapp: QApplication, library: ImageLibrary, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image as PILImage

    from modu_workbench.boards.gallery import board as gallery_board

    source = tmp_path / "标签图.jpg"
    PILImage.new("RGB", (40, 40), (90, 90, 90)).save(source)
    library.import_paths([str(source)])
    item = library.list_images()[0]
    library.tag_images([item.id], "旧标签")

    page = _open(library)
    try:
        monkeypatch.setattr(gallery_board, "ask_text", lambda *args, **kwargs: "新标签")
        page._rename_tag("旧标签")  # noqa: SLF001
        assert any(tag.name == "新标签" for tag in library.list_tags())

        page._active_filter = {"tag": "新标签"}  # noqa: SLF001
        page._untag_current([item])  # noqa: SLF001
        counts = {tag.name: tag.use_count for tag in library.list_tags()}
        assert counts.get("新标签", 0) == 0, "标签应已从图片上移除"
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()
