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


@pytest.fixture(scope="session", autouse=True)
def _qt_shutdown_cleanup():
    """退出测试前把残留的 Qt 对象销毁干净。

    ## 为什么需要它

    PySide6 6.12 上出现过一种很难查的现象：**所有用例都通过**，但 pytest
    进程退出时崩溃（Windows 退出码 0xC0000409 / 0xC0000005），CI 因此判失败，
    而本地旧版本 PySide6 上一切正常。

    典型来源是测试里创建的顶层窗口、`QMediaPlayer` 等 Qt 对象没有在解释器
    销毁前释放。这里在会话结束时按"关闭 → deleteLater → 处理事件"的顺序收尾，
    让 Qt 自己走正常的析构路径。
    """
    yield
    app = QApplication.instance()
    if app is None:
        return
    try:
        for widget in list(app.topLevelWidgets()):
            widget.close()
            widget.deleteLater()
        app.processEvents()
        for widget in list(app.allWidgets()):
            widget.deleteLater()
        app.processEvents()
        app.quit()
        app.processEvents()
    except Exception:  # noqa: BLE001  清理失败不应该让测试结果变红
        pass

