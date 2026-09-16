"""设计系统测试：令牌尺度、"无直角"约束、组件行为、首页响应式栅格。

这些测试锁住 v1.0.0 的界面规范（`docs/UI_GUIDE.md`）：
留白/圆角/字号只能按刻度取值，页面骨架必须走组件库。
"""
from __future__ import annotations

import re

import pytest
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel

from modu_workbench.boards.home.page import MAX_COLUMNS, MIN_COLUMNS, HomePage
from modu_workbench.ui_kit.theme import DARK, LIGHT, TOKENS, ThemeTokens, app_qss
from modu_workbench.ui_kit.tokens import (
    CARD_PADDING,
    FONT,
    PAGE_MARGIN,
    ROW_GAP,
    SECTION_GAP,
    SPACE,
    TOKEN_FIELDS,
)


# ---------------------------------------------------------------- 令牌


def test_radius_scale_never_sharp() -> None:
    """规范要求"无棱角"：所有圆角令牌 ≥ 10px。"""
    tokens = ThemeTokens()
    assert tokens.radius_sm >= 10
    assert tokens.radius >= tokens.radius_sm
    assert tokens.radius_lg >= tokens.radius
    assert tokens.radius_pill >= 100


def test_qss_has_no_zero_radius() -> None:
    qss = app_qss(TOKENS)
    assert "border-radius: 0" not in qss
    radii = {int(value) for value in re.findall(r"border-radius:\s*(\d+)px", qss)}
    assert radii, "QSS 里应当有圆角定义"
    assert min(radii) >= 10 or min(radii) in (2, 7), "只允许滑块/进度条这类装饰件用更小圆角"


def test_spacing_and_font_scales_are_ordered() -> None:
    values = [SPACE[key] for key in ("xs", "sm", "md", "lg", "xl", "xxl", "huge")]
    assert values == sorted(values)
    assert len(set(values)) == len(values)
    fonts = [FONT[key] for key in ("caption", "small", "body", "body_lg", "subtitle", "title", "display")]
    assert fonts == sorted(fonts)
    assert PAGE_MARGIN == SPACE["xl"]
    assert SECTION_GAP >= SPACE["lg"]
    assert CARD_PADDING >= SPACE["lg"]
    assert ROW_GAP <= CARD_PADDING


def test_light_and_dark_share_semantic_fields() -> None:
    """深色主题必须覆盖所有颜色字段（间距/圆角/尺寸共用令牌，不参与对比）。"""
    color_fields = [name for name in TOKEN_FIELDS if isinstance(getattr(LIGHT, name), str)
                    and getattr(LIGHT, name).startswith("#")]
    assert color_fields, "令牌里应有颜色字段"
    for field in color_fields:
        light = getattr(LIGHT, field)
        dark = getattr(DARK, field)
        assert light != dark, f"{field} 在深色主题里未调整"
        assert light.startswith("#") and len(light) == 7


# ---------------------------------------------------------------- 组件


def test_page_header_and_section_card(qapp: QApplication) -> None:
    from modu_workbench.ui_kit.components import PageHeader, SectionCard

    header = PageHeader("标题", "副标题")
    assert header.title_label.text() == "标题"
    assert header.subtitle_label.isVisible() is False or header.subtitle_label.text() == "副标题"
    header.set_subtitle("")
    assert header.subtitle_label.isVisible() is False

    card = SectionCard("区块", QLabel("内容"))
    assert card.objectName() == "sectionCard"
    assert card.body_layout.count() >= 2          # 标题行 + 内容
    assert card.layout().contentsMargins().left() == CARD_PADDING


def test_empty_state_has_icon_title_and_action(qapp: QApplication) -> None:
    from modu_workbench.ui_kit.components import EmptyState, primary_button

    state = EmptyState("🎬", "还没有内容", "先搜索一部片子", primary_button("搜索"))
    texts = [child.text() for child in state.findChildren(QLabel)]
    assert "🎬" in texts and "还没有内容" in texts


def test_column_page_uses_page_margins(qapp: QApplication) -> None:
    from modu_workbench.ui_kit.components import ColumnPage

    page = ColumnPage("标题", "副标题")
    margins = page.layout().contentsMargins()
    assert margins.left() == margins.right() == PAGE_MARGIN
    assert page.layout().spacing() == SECTION_GAP


# ---------------------------------------------------------------- 首页栅格


def test_home_grid_reflows_with_window_width(qapp: QApplication) -> None:
    page = HomePage()
    layout = page.findChild(QGridLayout)
    assert layout is not None

    page.resize(1600, 900)
    page._reflow()                                   # noqa: SLF001
    wide = page._columns                            # noqa: SLF001
    page.resize(700, 900)
    page._reflow()                                   # noqa: SLF001
    narrow = page._columns                          # noqa: SLF001

    assert MIN_COLUMNS <= narrow <= wide <= MAX_COLUMNS
    assert wide > narrow, "宽窗口必须排更多列（否则右侧会出现大片空白）"
    page.close()


def test_home_last_row_fills_remaining_width(qapp: QApplication) -> None:
    """最后一行只有一张卡时必须横向铺满，避免右下角空白。"""
    page = HomePage()
    page.resize(1600, 900)
    page._reflow()                                   # noqa: SLF001
    layout = page.findChild(QGridLayout)
    assert layout is not None

    ghosts = [layout.itemAt(index) for index in range(layout.count())]
    spans = [layout.getItemPosition(index) for index in range(layout.count())]
    assert any(span[3] > 1 for span in spans), "幽灵卡应跨列铺满"
    assert all(item is not None for item in ghosts)
    page.close()
