"""墨读转换板块（M0 占位 → M2/M3 迁入 fileType 转换能力）。"""
from __future__ import annotations

from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from ..ui_kit.widgets import placeholder_page

_CONVERT_LINES = [
    "文本互转 / PDF(Qt) / 图片 / 归档，附 txt/md/json/mp4 查看编辑",
    "Word(docx)、Excel(xlsx/xls) 与音视频(ffmpeg) 能力迁移中",
    "批量任务队列：实时进度 / 取消 / 输出防覆盖 / 解压防穿越",
    "统一提示（Toast）与本地化状态，行为对齐旧版 fileType",
]


class ConvertBoardPage(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        inner = placeholder_page(
            icon="🔄",
            title="墨读转换",
            subtitle="文档转换板块 · 建设中",
            phase="M2/M3",
            lines=_CONVERT_LINES,
        )
        inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(inner)
