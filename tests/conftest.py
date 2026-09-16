"""测试共享夹具：无头 Qt 平台 + 隔离的数据目录（避免写用户 APPDATA）+ 公共 QApplication。

约定（见 `docs/TESTING.md`）：
- 所有 UI 测试用 `qapp` 夹具（session 级单例），不要各自 new QApplication；
- 数据目录默认指向临时目录，测试之间互不污染；
- 需要真实应用数据目录的测试请显式设置 `MODU_DATA_DIR`。
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MODU_DATA_DIR", tempfile.mkdtemp(prefix="modu-test-"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """全局唯一的 QApplication（offscreen）。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app
