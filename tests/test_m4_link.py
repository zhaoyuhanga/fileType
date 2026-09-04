"""M4 联动/设置测试：共享书库上下文与设置对话框。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from modu_workbench.boards.book_online import OnlineDownloadPage
from modu_workbench.core.reader import Library
from modu_workbench.services import app_context
from modu_workbench.ui_kit.settings_dialog import SettingsDialog
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
        assert dialog._compliance.isChecked()  # noqa: SLF001  # 默认开启
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
