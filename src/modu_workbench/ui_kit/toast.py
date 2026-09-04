"""轻量 Toast 提示（统一规范）：成功 / 信息 / 错误，自动消失。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget

from .theme import ThemeTokens

_T = ThemeTokens()

_STYLES = {
    "success": (
        "QLabel {{ background: #17301f; color: {text}; border: 1px solid {accent};"
        " border-radius: 8px; padding: 9px 16px; font-size: 13px; }}"
    ),
    "info": (
        "QLabel {{ background: #1a2740; color: {text}; border: 1px solid {border};"
        " border-radius: 8px; padding: 9px 16px; font-size: 13px; }}"
    ),
    "error": (
        "QLabel {{ background: #3a1d1d; color: #f3cfcf; border: 1px solid {danger};"
        " border-radius: 8px; padding: 9px 16px; font-size: 13px; }}"
    ),
}


def _style_for(kind: str) -> str:
    template = _STYLES.get(kind, _STYLES["info"])
    return template.format(text=_T.text, accent=_T.accent, border=_T.border, danger=_T.danger)


class Toaster:
    """在宿主顶层窗口底部居中弹出提示；多条时向上堆叠。"""

    def __init__(self, host: QWidget):
        self._host = host
        self._labels: list[QLabel] = []

    def show(self, message: str, kind: str = "info", duration_ms: int = 2600) -> None:
        window = self._host.window()
        label = QLabel(message, window)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setStyleSheet(_style_for(kind))
        label.adjustSize()
        label.show()
        label.raise_()
        self._labels.append(label)
        self._reflow()

        QTimer.singleShot(duration_ms, lambda: self._dismiss(label))

    def _dismiss(self, label: QLabel) -> None:
        label.hide()
        label.deleteLater()
        if label in self._labels:
            self._labels.remove(label)
        self._reflow()

    def _reflow(self) -> None:
        window = self._host.window()
        if window is None:
            return
        bottom = window.height() - 36
        for label in reversed(self._labels):
            label.adjustSize()
            label.move(max(8, (window.width() - label.width()) // 2), max(8, bottom - label.height()))
            bottom -= label.height() + 8

    def success(self, message: str) -> None:
        self.show(message, "success")

    def error(self, message: str) -> None:
        self.show(message, "error", duration_ms=4200)

    def info(self, message: str) -> None:
        self.show(message, "info")
