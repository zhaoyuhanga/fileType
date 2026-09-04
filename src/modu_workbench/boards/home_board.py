"""首页：产品介绍 + 板块入口卡片墙（可扩展）。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import APP_SLOGAN, __version__
from ..ui_kit.widgets import BoardCard, make_chip, make_ghost_card
from .registry import ACTIVE_BOARDS


class HomePage(QWidget):
    """欢迎页：介绍 + 板块卡片，点击发出 open_board(key)。"""

    open_board = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("homePage")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(48, 36, 48, 36)
        layout.setSpacing(10)
        scroll.setWidget(body)

        intro = QLabel(f"欢迎使用墨读·工作台")
        intro.setObjectName("introTitle")
        layout.addWidget(intro)

        sub = QLabel(f"{APP_SLOGAN} · 板块化工作台，可随时扩展新能力 · v{__version__}")
        sub.setObjectName("introSub")
        layout.addWidget(sub)

        layout.addSpacing(20)

        section = QLabel("选择板块开始")
        section.setObjectName("sectionTitle")
        layout.addWidget(section)
        layout.addSpacing(4)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(18)
        layout.addLayout(grid)

        for index, spec in enumerate(ACTIVE_BOARDS):
            card = BoardCard(
                key=spec.key,
                icon=spec.icon,
                title=spec.title,
                tagline=spec.tagline,
                description=spec.description,
            )
            card.clicked.connect(self.open_board)
            card_header = card.layout()
            card_header.addWidget(make_chip(spec.phase, ready=False))
            grid.addWidget(card, index // 2, index % 2)

        grid.addWidget(make_ghost_card("已预留注册机制：后续板块（如 PDF 批注、素材管理）可直接加入注册表自动出现在此。"), (len(ACTIVE_BOARDS)) // 2, 0)

        layout.addStretch(1)

        footer_row = QHBoxLayout()
        footer_row.addStretch(1)
        footer = QLabel("墨读·工作台 © 2026 · 本地离线 · 仅供学习使用")
        footer.setObjectName("footerText")
        footer_row.addWidget(footer)
        layout.addLayout(footer_row)
