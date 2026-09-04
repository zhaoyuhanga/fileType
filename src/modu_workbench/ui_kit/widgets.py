"""可复用基础控件（统一设计规范组件库）。

M0 提供：导航按钮、板块卡片（BoardCard）、状态徽章（chip）。
后续里程碑将在此补充 Button 变体 / Toast / ConfirmDialog / Progress 等。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget


def make_nav_button(text: str, active: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    set_nav_active(button, active)
    return button


def set_nav_active(button: QPushButton, active: bool) -> None:
    button.setObjectName("navButtonActive" if active else "navButton")
    style = button.style()
    if style:
        style.unpolish(button)
        style.polish(button)
    button.update()


def make_chip(text: str, ready: bool = True) -> QLabel:
    label = QLabel(text)
    label.setObjectName("chipReady" if ready else "chipPlanned")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


class BoardCard(QFrame):
    """首页板块卡片：展示图标/标题/简介，点击后携带板块 key 发出信号。"""

    clicked = Signal(str)

    def __init__(self, key: str, icon: str, title: str, tagline: str, description: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._key = key
        self.setObjectName("boardCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(320, 176)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(6)

        top = QLabel(icon)
        top.setObjectName("cardIcon")

        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")

        tagline_label = QLabel(tagline)
        tagline_label.setObjectName("cardTagline")
        tagline_label.setWordWrap(True)

        desc_label = QLabel(description)
        desc_label.setObjectName("cardDesc")
        desc_label.setWordWrap(True)

        layout.addWidget(top)
        layout.addWidget(title_label)
        layout.addWidget(tagline_label)
        layout.addWidget(desc_label)
        layout.addStretch(1)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self._key)
        super().mouseReleaseEvent(event)


def make_ghost_card(text: str) -> QFrame:
    """“更多板块筹备中”占位卡。"""
    frame = QFrame()
    frame.setObjectName("boardCardGhost")
    frame.setMinimumSize(320, 176)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(6)
    title = QLabel("＋ 更多板块筹备中")
    title.setObjectName("ghostTitle")
    desc = QLabel(text)
    desc.setObjectName("ghostDesc")
    desc.setWordWrap(True)
    layout.addStretch(1)
    layout.addWidget(title)
    layout.addWidget(desc)
    layout.addStretch(1)
    return frame
