"""墨读书库板块（M0 占位 → M1 迁入 win-e-book 完整能力）。"""
from __future__ import annotations

from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from ..ui_kit.widgets import placeholder_page

_BOOK_LINES = [
    "本地书库：拖拽 / 批量导入 TXT、EPUB，自动章节解析与编码识别",
    "阅读记忆：SQLite 保存进度，续读自动回到上次章节与位置",
    "阅读主题与历史记录；内置在线书库下载（00shu，默认开启）",
    "键盘快捷键：上一章 / 下一章、字号缩放、主题切换",
]


class BookBoardPage(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        inner = placeholder_page(
            icon="📚",
            title="墨读书库",
            subtitle="电子书阅读板块 · 建设中",
            phase="M1",
            lines=_BOOK_LINES,
        )
        inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(inner)
