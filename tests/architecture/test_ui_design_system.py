"""设计系统测试：令牌尺度、"无直角"约束、组件行为、首页响应式栅格。

这些测试锁住 v1.0.0 的界面规范（`docs/UI_GUIDE.md`）：
留白/圆角/字号只能按刻度取值，页面骨架必须走组件库。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtGui import QColor
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


def test_qss_covers_popup_controls() -> None:
    """弹出类控件必须逐个写规则 —— 漏掉的子控件会退回 Fusion 默认外观（丑的根源）。

    QSS 只影响"写到的控件 + 子控件"：下拉框的箭头、数字框的上下按钮、
    勾选指示器、菜单项、树的分支箭头、选项卡，少一个就会出现两种视觉混搭。
    选择器写法按 Qt 6.11 实测结果锁定（见下一条测试）。
    """
    qss = app_qss(TOKENS)
    for selector in (
        "QComboBox::drop-down",                 # 右侧箭头区（不写会画灰按钮+竖线）
        "QComboBox::down-arrow",                # 箭头本身
        "QComboBox QAbstractItemView",          # 弹出列表（背景/边距/去虚线框）
        "QComboBox::item:selected",             # 列表选中态（6.11 只有这种写法生效）
        "QComboBox::item:hover",
        "QSpinBox::up-button",
        "QSpinBox::down-button",
        "QSpinBox::up-arrow",
        "QCheckBox::indicator:checked",
        "QRadioButton::indicator:checked",
        "QMenu::item:selected",
        "QTreeView::branch:closed:has-children",
        "QTabBar::tab:selected",
    ):
        assert selector in qss, f"QSS 缺少弹出控件规则：{selector}"


def test_combo_popup_uses_working_selectors() -> None:
    """下拉列表的写法有坑，这里把实测结论钉死（改错会又丑又卡）：

    1. `QComboBox QAbstractItemView::item`（后代 + 子控件）在 Qt 6.11 **不匹配** ——
       写了等于没写，列表项会退回 Fusion 默认外观；
    2. `QComboBox::item` 上**不能加 padding** —— 实测触发行高 1900px、
       弹出层暴涨到 792px 的几何爆炸（点一下就像卡死）；
    3. view 上的 `selection-background-color` 不生效，选中态必须靠 `QComboBox::item:selected`；
    4. 也不该再用"显示时改 window flags/半透明"那套：会重建原生弹出窗口（点一下卡一下），
       而且全局事件过滤器会给每个事件加一层 Python 回调。
    """
    from modu_workbench.ui_kit import theme

    qss = app_qss(TOKENS)
    rules = re.sub(r"/\*.*?\*/", "", qss, flags=re.S)     # 只看真正的规则，不看注释
    assert "QComboBox QAbstractItemView::item" not in rules, "该写法在 Qt 6.11 不匹配，别再写"
    view_rule = rules.split("QComboBox QAbstractItemView {", 1)[1].split("}", 1)[0]
    assert "selection-background-color" not in view_rule, \
        "view 级 selection-background-color 对下拉列表无效"
    combo_rule = rules.split("QComboBox::item {", 1)[1].split("}", 1)[0]
    assert "padding" not in combo_rule and "min-height" not in combo_rule, \
        "给 QComboBox::item 加 padding/min-height 会触发几何爆炸"

    assert not hasattr(theme, "install_popup_polisher"), "弹出层处理器已移除，别再加回来"
    assert not hasattr(theme, "polish_popup")
    source = Path(theme.__file__).read_text(encoding="utf-8")
    assert "setWindowFlag" not in source, "主题不该去改窗口标志（会重建原生弹出窗口、点一下卡一下）"
    assert "installEventFilter" not in source, "主题不该装全局事件过滤器（每个事件都回调进 Python）"


def test_runtime_icons_are_generated(qapp: QApplication) -> None:  # noqa: ARG001
    """箭头/勾选图标是运行时画出来的 PNG（QSS 的 image: 只认文件路径）。"""
    from modu_workbench.ui_kit.theme import _icon_urls

    icons = _icon_urls(TOKENS)
    assert {"chevron_down", "chevron_up", "chevron_right", "check", "dot"} <= set(icons)
    for name, path in icons.items():
        assert path.endswith(".png") and "/" in path, f"{name} 不是可用的 QSS 路径：{path}"
        assert Path(path).is_file() and Path(path).stat().st_size > 0, f"{name} 图标没生成"
    qss = app_qss(TOKENS)
    assert icons["chevron_down"] in qss            # 图标真的被写进 QSS
    assert "border-top: 5px solid" not in qss      # 不再用 border 拼三角形（会变方块）


# ---------------------------------------------------------------- 树（图库左侧分类导航）


def _tree_section() -> str:
    """取出 QSS 里「树」那一段（去掉注释，只看真正的规则）。"""
    qss = app_qss(TOKENS)
    section = qss.split("/* ---------- 树", 1)[1].split("/* ---------- 选项卡", 1)[0]
    return re.sub(r"/\*.*?\*/", "", section, flags=re.S)


def _nearest_distance(color: QColor, target: QColor) -> int:
    """两个颜色的最大通道差（0 = 完全一致，255 = 差到天边）。"""
    return max(abs(color.red() - target.red()),
               abs(color.green() - target.green()),
               abs(color.blue() - target.blue()))


def test_tree_rules_are_token_driven_and_kill_system_blue() -> None:
    """树的规则锁死（用户反馈「点击会有蓝色标记」）：

    1. `show-decoration-selected` 必须是 0 —— 设成 1 时分支（缩进）列会被 Fusion
       按系统高亮色画成方块，与内容列的圆角块拼成「左蓝右紫」的接缝；
       （`QTreeView::branch:selected { background: transparent; }` 实测**不生效**，
       QSS 把 transparent 当成没写，所以只能从根上不画）
    2. 选中/悬停底色必须来自令牌，不能写死色值；
    3. `outline: none` —— 不要 Fusion 的虚线焦点框（焦点由控件自己画描边）。
    """
    section = _tree_section()
    panel = section.split("QTreeView, QTreeWidget {", 1)[1].split("}", 1)[0]
    assert "show-decoration-selected: 0" in panel
    assert "outline: none" in panel, "树的虚线焦点框必须关掉"
    assert "border-radius" in panel, "无直角：面板也要圆角"

    item = section.split("QTreeView::item, QTreeWidget::item {", 1)[1].split("}", 1)[0]
    assert "min-height" in item and "border-radius" in item

    hover = section.split("QTreeView::item:hover, QTreeWidget::item:hover {", 1)[1].split("}", 1)[0]
    selected = section.split("QTreeView::item:selected, QTreeWidget::item:selected {", 1)[1]
    selected = selected.split("}", 1)[0]
    assert TOKENS.surface_hover in hover, "悬停底色必须用令牌"
    assert TOKENS.accent_soft in selected and TOKENS.accent_strong in selected, \
        "选中态必须是「主题浅紫底 + 强调色文字」，不是 Fusion 的系统蓝"

    # 树这一段里除了令牌色值，不允许出现别的写死颜色
    allowed = {
        getattr(TOKENS, name).lower()
        for name in TOKEN_FIELDS
        if isinstance(getattr(TOKENS, name), str) and getattr(TOKENS, name).startswith("#")
    }
    literals = {value.lower() for value in re.findall(r"#[0-9a-fA-F]{6}", section)}
    assert literals <= allowed, f"树规则里出现了非令牌色值：{sorted(literals - allowed)}"


def test_nav_tree_is_compact_and_paints_its_own_rows(qapp: QApplication) -> None:
    """分类树的行高/缩进必须收敛，且整行底由控件自己画。

    缩进**只能代码设置**：QSS 里没有 `indentation` 属性（实测报
    "Unknown property indentation"），写了等于没写 —— 所以这里直接量控件的值。
    """
    from modu_workbench.boards.gallery.nav_tree import (
        BADGE_ROLE,
        CategoryNavDelegate,
        CategoryNavTree,
    )

    tree = CategoryNavTree()
    tree.setStyleSheet(app_qss(TOKENS))
    root = tree.add_node(None, "全部图片", badge=16, payload={"all": True})
    tree.add_node(root, "我的收藏", badge=0, payload={"favorite": True})
    tree.add_node(None, "相册", group=True)
    tree.resize(240, 200)
    tree.show()
    qapp.processEvents()

    assert tree.indentation() == SPACE["lg"], "缩进要按 SPACE 令牌收紧（默认 20 太散）"
    assert isinstance(tree.itemDelegate(), CategoryNavDelegate)
    # 默认（不写 QSS）行高 36：这里必须明显更紧凑，但不能挤到看不清
    height = tree.visualItemRect(root).height()
    assert 24 <= height <= 28, f"分类树行高应紧凑（实测 {height}px）"
    assert root.text(0) == "全部图片", "标题里不再拼计数，计数走右侧徽章"
    assert root.data(0, BADGE_ROLE) == "16"
    # 整行底是自己画的：drawRow 被覆写，否则分支列会退回 Fusion 方块
    from PySide6.QtWidgets import QTreeWidget

    assert CategoryNavTree.drawRow is not QTreeWidget.drawRow
    tree.close()


def test_tree_selection_is_tinted_not_system_blue(qapp: QApplication) -> None:
    """抓像素验证（QSS 字符串锁不住这条：`branch:selected{transparent}` 看着对、实测无效）。

    选中行的**分支/缩进列**在修好之前是一整块系统高亮色方块：
    这里要求它是「面板底色 + 主题浅紫胶囊」，胶囊还必须盖住缩进列（没有接缝）。
    """
    from PySide6.QtCore import QPoint

    from modu_workbench.boards.gallery.nav_tree import ROW_INSET, CategoryNavTree

    tree = CategoryNavTree()
    tree.setStyleSheet(app_qss(TOKENS))
    root = tree.add_node(None, "全部图片", badge=16, payload={"all": True})
    child = tree.add_node(root, "我的收藏", badge=0, payload={"favorite": True})
    tree.resize(240, 180)
    tree.show()
    qapp.processEvents()
    tree.setCurrentItem(child)
    qapp.processEvents()

    offset = tree.viewport().mapTo(tree, QPoint(0, 0))
    row = tree.visualItemRect(child)
    middle = offset.y() + row.center().y()
    image = tree.grab().toImage()

    def sample(viewport_x: int) -> QColor:
        return image.pixelColor(offset.x() + viewport_x, middle)

    system_blue = qapp.palette().highlight().color()
    accent_soft = QColor(TOKENS.accent_soft)
    accent = QColor(TOKENS.accent)

    # 缩进列的最左边（胶囊之外）是面板底色，绝不能是系统高亮蓝
    assert _nearest_distance(sample(1), system_blue) > 60, \
        f"缩进列出现了系统高亮蓝：{sample(1).name()}"
    # 胶囊必须盖住缩进列（content rect 从 indentation 处才开始）—— 整行一块，没有接缝
    # 采样点夹在「焦点描边」(x=ROW_INSET) 与「强调条」(x=ROW_INSET+SELECT_BAR_INSET) 之间
    assert _nearest_distance(sample(ROW_INSET + 1), accent_soft) <= 12, \
        f"选中行在缩进列不是主题浅紫：{sample(ROW_INSET + 1).name()}"

    # 左侧强调条：必须画在**委托之后**。委托的 initStyleOption 会把 selected 状态找回来，
    # QSS 的 ::item:selected 底色会盖住先画的竖条（实测只剩 1px）—— 这里量它确实够宽。
    from modu_workbench.boards.gallery.nav_tree import SELECT_BAR_INSET, SELECT_BAR_WIDTH

    bar_left = ROW_INSET + SELECT_BAR_INSET
    bar_pixels = [sample(bar_left + step) for step in range(SELECT_BAR_WIDTH)]
    assert all(_nearest_distance(color, accent) <= 12 for color in bar_pixels), (
        "选中行左侧强调条缺失或被委托底色覆盖："
        f"{[color.name() for color in bar_pixels]}（期望 {accent.name()}）"
    )

    # 键盘焦点：仍然可见，但换成自己画的强调色描边（不是 Fusion 虚线框）
    tree.setFocus()
    qapp.processEvents()
    assert tree.hasFocus()
    image = tree.grab().toImage()
    border = QColor(TOKENS.surface)
    for x in range(offset.x() + ROW_INSET + 8, offset.x() + row.right() - 8):
        color = image.pixelColor(x, offset.y() + row.top() + 1)
        if _nearest_distance(color, accent) < _nearest_distance(border, accent):
            border = color
    assert _nearest_distance(border, accent) < 110, "焦点可见性丢了（描边没画出来）"
    tree.close()



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
