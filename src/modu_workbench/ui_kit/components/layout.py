"""页面骨架与通用组件（统一排版，避免各板块各写一套留白）。

用法（推荐写法）：

    from modu_workbench.ui_kit.components import PageHeader, SectionCard, EmptyState

    page = ColumnPage(title="墨软影视", subtitle="在线搜索 · 本地播放", actions=[download_btn])
    page.add(SectionCard("最近播放", body_widget))
    page.set_empty(EmptyState("🎬", "还没有内容", "先搜索一部片子试试", button))

约定：
- 页面外边距一律 24（`tokens.PAGE_MARGIN`），区块间距 16（`SECTION_GAP`）；
- 卡片内边距 16（`CARD_PADDING`），控件间距 8（`ROW_GAP`）；
- 列表为空时必须显示 `EmptyState`（图标 + 说明 + 主操作），不留大片空白。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..tokens import CARD_PADDING, FONT, PAGE_MARGIN, ROW_GAP, SECTION_GAP, SPACE

__all__ = [
    "ColumnPage",
    "EmptyState",
    "PageHeader",
    "SectionCard",
    "Toolbar",
    "chip",
    "divider",
    "ghost_button",
    "hint_label",
    "primary_button",
    "row",
    "spacer",
]


# ---------------------------------------------------------------- 基础零件


def row(*widgets: QWidget, spacing: int = ROW_GAP, stretch_last: bool = False) -> QWidget:
    """水平排布一组控件（统一的 8px 间距）。"""
    holder = QWidget()
    holder.setObjectName("toolbarRow")
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        if widget is not None:
            layout.addWidget(widget)
    if stretch_last:
        layout.addStretch(1)
    return holder


def spacer() -> QWidget:
    """占位弹簧（把后续控件推到右侧）。"""
    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return widget


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("pageDivider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


def chip(text: str) -> QLabel:
    """小徽章（胶囊形）：用于状态/计数，不抢视线。"""
    label = QLabel(text)
    label.setObjectName("statChip")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def hint_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("pageHint")
    label.setWordWrap(True)
    return label


def primary_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("primaryButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def ghost_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("ghostButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def link_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("linkButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


# ---------------------------------------------------------------- 页面骨架


class PageHeader(QWidget):
    """页面标题区：标题 + 副标题 + 右侧操作（所有板块统一，不再各写各的）。"""

    def __init__(self, title: str, subtitle: str = "", actions: list[QWidget] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("pageHeader")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["md"])

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(SPACE["xs"])
        self.title_label = QLabel(title)
        self.title_label.setObjectName("pageTitle")
        texts.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("pageSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        texts.addWidget(self.subtitle_label)
        layout.addLayout(texts, 1)

        for widget in actions or []:
            layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignBottom)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))


class SectionCard(QFrame):
    """区块卡片：白色圆角容器 + 可选标题行（留白由卡片统一负责）。"""

    def __init__(self, title: str = "", body: QWidget | None = None,
                 actions: list[QWidget] | None = None, *, hint: str = "",
                 flat: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("sectionCard")
        if flat:
            self.setProperty("flat", True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
        layout.setSpacing(SPACE["md"])

        if title:
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(SPACE["sm"])
            title_label = QLabel(title)
            title_label.setObjectName("sectionCardTitle")
            head.addWidget(title_label)
            if hint:
                hint_widget = QLabel(hint)
                hint_widget.setObjectName("sectionCardHint")
                head.addWidget(hint_widget)
            head.addStretch(1)
            for widget in actions or []:
                head.addWidget(widget)
            layout.addLayout(head)

        self.body_layout = layout
        if body is not None:
            layout.addWidget(body)

    def add(self, widget: QWidget) -> QWidget:
        self.body_layout.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:  # noqa: ANN001
        self.body_layout.addLayout(layout)


class EmptyState(QFrame):
    """空状态：图标 + 标题 + 说明 +（可选）主操作按钮。列表页为空时必须用它。"""

    def __init__(self, icon: str, title: str, hint: str = "", action: QWidget | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("emptyState")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xl"], SPACE["xxl"], SPACE["xl"], SPACE["xxl"])
        layout.setSpacing(SPACE["sm"])
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_label = QLabel(icon)
        icon_label.setObjectName("emptyIcon")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setObjectName("emptyTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        if hint:
            hint_widget = QLabel(hint)
            hint_widget.setObjectName("emptyHint")
            hint_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint_widget.setWordWrap(True)
            layout.addWidget(hint_widget)

        if action is not None:
            layout.addSpacing(SPACE["sm"])
            layout.addWidget(action, 0, Qt.AlignmentFlag.AlignCenter)


class Toolbar(QWidget):
    """列表页工具条：搜索框 + 筛选 + 主操作（统一 8px 间距与底部分隔线）。"""

    def __init__(self, widgets: list[QWidget], *, extra: QWidget | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("toolbarRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(ROW_GAP)
        for widget in widgets:
            if widget is not None:
                layout.addWidget(widget)
        layout.addStretch(1)
        if extra is not None:
            layout.addWidget(extra)


class ColumnPage(QWidget):
    """标准页面：页头 + 内容区（外边距 24、区块间距 16）。

    板块页面直接把已有控件 `add()` 进来即可逐步迁移，避免一次性重写。
    """

    def __init__(self, title: str, subtitle: str = "", actions: list[QWidget] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("pageRoot")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        outer.setSpacing(SECTION_GAP)

        self.header = PageHeader(title, subtitle, actions)
        outer.addWidget(self.header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(SECTION_GAP)
        outer.addLayout(self.body, 1)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:  # noqa: ANN001
        self.body.addLayout(layout)

    def set_subtitle(self, text: str) -> None:
        self.header.set_subtitle(text)

    @property
    def title_label(self) -> QLabel:
        return self.header.title_label
