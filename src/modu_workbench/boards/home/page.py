"""首页：产品介绍 + 板块入口卡片墙（响应式栅格，可扩展）。

排版规范（v1.0.0）：
- 采用 `ColumnPage` 统一页头与 24px 页边距；
- 板块卡按窗口宽度自动 2/3/4 列重排（每列最小 240px），卡片等高，右侧不留空洞；
- 空状态、区块卡片都走组件库，不再手写边距。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ... import APP_SLOGAN, APP_NAME, __version__
from ...app.registry import ACTIVE_BOARDS
from ...ui_kit.components import ColumnPage, SectionCard, chip, hint_label
from ...ui_kit.tokens import SPACE
from ...ui_kit.widgets import BoardCard, make_ghost_card

#: 每列目标宽度（低于它就减少列数，避免卡片被压得过窄）
COLUMN_WIDTH = 260
MIN_COLUMNS = 2
MAX_COLUMNS = 4


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

        self._page = ColumnPage(
            APP_NAME,
            f"{APP_SLOGAN} · 板块化工作台，能力可插拔 · v{__version__}",
        )
        scroll.setWidget(self._page)

        # 板块入口
        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(SPACE["lg"])
        self._grid.setVerticalSpacing(SPACE["lg"])
        self._cards: list[QWidget] = []
        self._columns = 0

        for spec in ACTIVE_BOARDS:
            card = BoardCard(
                key=spec.key,
                icon=spec.icon,
                title=spec.title,
                tagline=spec.tagline,
                description=spec.description,
                phase=spec.phase,
            )
            card.clicked.connect(self.open_board)
            self._cards.append(card)
        self._cards.append(make_ghost_card(
            "已预留注册机制：后续板块（如 PDF 批注、素材管理）加入注册表即自动出现在这里。"
        ))

        entry = SectionCard("选择板块开始", actions=[chip(f"共 {len(ACTIVE_BOARDS)} 个板块")])
        entry.add_layout(self._grid)
        self._page.add(entry)

        # 使用提示（把"空白"换成有用的信息，避免首页下方出现大片留白）
        tips = SectionCard("使用提示")
        tips.add(hint_label(
            "· 每个板块各自独立：数据、设置、数据源互不影响，可单独升级。\n"
            "· 转换/影视/乐库/图库支持批量操作与后台任务，可随时取消。\n"
            "· 全部数据保存在本机（单库 modu.db + 媒体目录），可整体备份或迁移。"
        ))
        self._page.add(tips)

        footer = QLabel("墨软·工作台 © 2026 · 本地离线 · 仅供学习使用")
        footer.setObjectName("footerText")
        self._page.add(footer)

        self._reflow()

    # ------------------------------------------------------------------ 响应式栅格

    def _target_columns(self) -> int:
        width = max(self.width(), 720) - 2 * SPACE["xl"]
        columns = max(MIN_COLUMNS, min(MAX_COLUMNS, width // COLUMN_WIDTH))
        return max(1, min(columns, len(self._cards)))

    def _reflow(self) -> None:
        columns = self._target_columns()
        if columns == self._columns:
            return
        self._columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        for index, card in enumerate(self._cards):
            row, column = divmod(index, columns)
            # 幽灵卡（最后一张）横向铺满本行剩余列：首页不允许出现大片空白
            span = max(1, columns - column) if index == len(self._cards) - 1 else 1
            self._grid.addWidget(card, row, column, 1, span)
        for column in range(MAX_COLUMNS):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._reflow()
        super().resizeEvent(event)
