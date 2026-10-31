"""可复用基础控件（组件库的历史入口，新代码优先用 `ui_kit/components`）。

这里保留：导航按钮（顶栏用）、板块卡片（首页用）、幽灵占位卡。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .tokens import CARD_PADDING, ROW_GAP, SPACE


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
    """首页板块卡片：图标 + 标题 + 一句话定位 + 简介 + 状态徽章。

    统一样式（圆角/留白/悬停）来自 `ui_kit/theme.py`，尺寸满足"最小 240×150、
    等高对齐"的排版要求，避免首页出现大片空白或卡片高低不齐。
    """

    clicked = Signal(str)

    def __init__(self, key: str, icon: str, title: str, tagline: str, description: str,
                 phase: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._key = key
        self.setObjectName("boardCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(240, 150)
        # 竖向 Expanding：全屏时卡片跟着变高，避免首页"顶部一条、下面全空"
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
        layout.setSpacing(SPACE["sm"])

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(ROW_GAP)
        icon_label = QLabel(icon)
        icon_label.setObjectName("cardIcon")
        head.addWidget(icon_label)
        head.addStretch(1)
        if phase:
            head.addWidget(make_chip(phase, ready=phase in ("可用", "定版")))
        layout.addLayout(head)

        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        layout.addWidget(title_label)

        tagline_label = QLabel(tagline)
        tagline_label.setObjectName("cardTagline")
        tagline_label.setWordWrap(True)
        layout.addWidget(tagline_label)

        desc_label = QLabel(description)
        desc_label.setObjectName("cardDesc")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        layout.addStretch(1)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self._key)
        super().mouseReleaseEvent(event)


def make_ghost_card(text: str) -> QFrame:
    """「更多板块筹备中」占位卡（与板块卡等高，避免首页右侧出现空白洞）。"""
    frame = QFrame()
    frame.setObjectName("boardCardGhost")
    frame.setMinimumSize(240, 150)
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
    layout.setSpacing(SPACE["sm"])
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
