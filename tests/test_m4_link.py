"""M4 联动/设置测试：共享书库上下文与设置对话框。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from modu_workbench.boards.book_online import OnlineDownloadPage
from modu_workbench.core.reader import Library
from modu_workbench.services import app_context
from modu_workbench.ui_kit.settings import PAGE_ROLE, SettingsDialog
from modu_workbench.ui_kit.toast import Toaster


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_shared_library_singleton() -> None:
    assert app_context.library() is app_context.library()
    assert isinstance(app_context.library(), Library)


def test_settings_dialog_builds(qapp: QApplication) -> None:
    dialog = SettingsDialog()
    dialog.show()
    try:
        # 默认停在「通用」页；书库页默认开启合规声明
        dialog._select("book")                                   # noqa: SLF001
        book_page = dialog._ensure_page("book")                  # noqa: SLF001
        assert book_page is not None
        assert book_page._compliance.isChecked()                 # noqa: SLF001
    finally:
        dialog.close()


def test_settings_dialog_has_all_board_pages(qapp: QApplication) -> None:
    """设置必须按板块分区，且能直接定位到指定板块。"""
    from modu_workbench.ui_kit.settings import PAGE_FACTORIES

    keys = [key for key, _title, _icon, _factory in PAGE_FACTORIES]
    assert keys == ["general", "book", "convert", "music", "video", "gallery"]

    dialog = SettingsDialog(initial="gallery")
    try:
        entry = dialog._nav.currentItem()                        # noqa: SLF001
        assert entry is not None and entry.data(PAGE_ROLE) == "gallery"
    finally:
        dialog.close()


def test_settings_dialog_survives_broken_page(qapp: QApplication, monkeypatch) -> None:
    """某个板块设置页构造失败时，不应把整个设置窗口带崩。"""
    import modu_workbench.ui_kit.settings as settings_pkg

    class Boom(settings_pkg.SettingsPage):
        def __init__(self, parent=None):  # noqa: ANN001
            raise RuntimeError("模拟板块初始化失败")

    patched = tuple(
        (key, title, icon, Boom if key == "music" else factory)
        for key, title, icon, factory in settings_pkg.PAGE_FACTORIES
    )
    monkeypatch.setattr(settings_pkg, "PAGE_FACTORIES", patched)

    dialog = settings_pkg.SettingsDialog(initial="music")
    try:
        page = dialog._ensure_page("music")                      # noqa: SLF001
        assert page is not None and "失败" in page.hint()
    finally:
        dialog.close()


def test_online_page_respects_settings_default(qapp: QApplication) -> None:
    host = QApplication.instance()
    assert host is not None
    page = OnlineDownloadPage(app_context.library(), Toaster(host))
    try:
        assert page._compliance.isChecked()  # noqa: SLF001  # 默认合规开启
    finally:
        page.close()
