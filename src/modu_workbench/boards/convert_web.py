"""墨读转换 · Web 前端宿主：加载 main 分支 React 构建并桥接 Python。

仅真实桌面环境使用（offscreen/无资源时由 ConvertBoardPage 回退 Qt 界面）。
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from modu_workbench.services.web_bridge import WebBridge


class WebConvertPage(QWidget):
    """内嵌 main 分支前端的转换板块页。"""

    def __init__(self, front_dir: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self._front = front_dir
        self.bridge = WebBridge(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._view = QWebEngineView(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject("bridge", self.bridge)
        self._view.page().setWebChannel(self._channel)
        layout.addWidget(self._view)

        index_url = QUrl.fromLocalFile(str(front_dir / "index.html"))
        self._view.load(index_url)

    def reload_front(self) -> None:
        self._view.reload()


def web_convert_available() -> bool:
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return False
    from modu_workbench.services.webfront import webfront_dir

    return webfront_dir() is not None
