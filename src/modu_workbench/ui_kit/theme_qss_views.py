"""数据视图：树、选项卡、表格、滚动条/分割条/提示、状态标签

**为什么单独成文件**：`theme.py` 的 `app_qss` 原本是一个 **714 行的 f-string**，
24 个分节全挤在同一个字符串里。想改"表格行高"要先在 700 行 QSS 里滚到第 470 行，
还容易顺手改到隔壁的滚动条。现在按控件族拆开，每个模块只回答一个问题。

- 本模块由 `ui_kit/theme.py::app_qss` 按**固定顺序**拼接，拼接结果与拆分前逐字节一致
  （`tests/architecture/test_ui_design_system.py` 断言关键片段，重构时另做了 sha256 比对）；
- 各函数只声明自己真正用到的令牌参数（`ThemeTokens` 的属性通过 `t` 传入）；
- **不要在这里写死颜色/间距**：一律用令牌，改主题只动 `ui_kit/tokens.py`。
"""
from __future__ import annotations

from .tokens import SPACE, ThemeTokens


def qss_views(t: ThemeTokens, arrow_down: str, arrow_right: str) -> str:
    """这些是「多行数据」的呈现控件，共用行高与选中态语义；改表格观感时只需动这一份。"""
    return f"""/* ---------- 树（图库左侧分类导航） ----------
   三条实测结论（逐像素对比离屏渲染得出，改之前先读 docs/UI_GUIDE.md「树的坑」）：
   1) `show-decoration-selected` 必须是 0。设成 1 时分支（缩进）列会被 Fusion 按
      系统高亮色画成**方块**，而内容列是 QSS 的圆角块，拼起来就是「左蓝右紫」的接缝
      —— 用户反馈的「点击会有蓝色标记」就是它。整行底色改由 CategoryNavTree.drawRow()
      画一个通栏圆角块，选中/悬停都是完整一块。
   2) `QTreeView::branch:selected {{ background: transparent; }}` **不生效**：
      QSS 把 transparent 当成"没写这条规则"，继续退回系统蓝（给具体颜色才生效，
      但分支列仍是方块、仍有接缝）。所以只能靠 1) 从根上不画。
   3) QSS 里**没有** `indentation` 属性（写了会报 "Unknown property indentation"），
      缩进只能由控件按 SPACE 令牌代码设置。
   行高 = min-height 22 + 上下 padding 2×2 = 26px（原来 30+3×2=36，明显偏空）。 */
QTreeView, QTreeWidget {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: {t.radius_sm}px;
    padding: {SPACE['xs']}px;
    outline: none;
    color: {t.text};
    show-decoration-selected: 0;
}}
QTreeView::item, QTreeWidget::item {{
    min-height: 22px;
    padding: 2px 8px;
    border-radius: {t.radius_sm}px;
    color: {t.text};
}}
QTreeView::item:hover, QTreeWidget::item:hover {{
    background: {t.surface_hover};
}}
QTreeView::item:selected, QTreeWidget::item:selected {{
    background: {t.accent_soft};
    color: {t.accent_strong};
}}
QTreeView::branch, QTreeWidget::branch {{ background: transparent; }}
QTreeView::branch:closed:has-children, QTreeWidget::branch:closed:has-children {{
    {arrow_right}
}}
QTreeView::branch:open:has-children, QTreeWidget::branch:open:has-children {{
    {arrow_down}
}}

/* ---------- 选项卡 ---------- */
QTabWidget::pane {{
    border: 1px solid {t.border};
    border-radius: {t.radius_sm}px;
    background: {t.surface};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {t.text_dim};
    border: 1px solid transparent;
    border-radius: {t.radius_sm}px;
    padding: 6px 14px;
    margin-right: 4px;
}}
QTabBar::tab:hover {{ color: {t.accent_hover}; background: {t.surface_hover}; }}
QTabBar::tab:selected {{
    background: {t.accent_soft};
    color: {t.accent_strong};
    font-weight: 600;
}}

/* ---------- 表格 ---------- */
QTableWidget, QTableView {{
    background: {t.surface};
    alternate-background-color: {t.surface_2};
    gridline-color: {t.border};
    border: none;
    selection-background-color: {t.accent_soft};
    selection-color: {t.text_hi};
}}
QHeaderView::section {{
    background: {t.surface_2};
    color: {t.text_dim};
    font-weight: 600;
    font-size: 12px;
    padding: 8px 10px;
    border: none;
    border-bottom: 1px solid {t.border};
}}
QTableWidget::item {{
    padding: 6px 6px;
    border: none;
}}
QTableWidget::item:selected {{
    background: {t.accent_soft};
    color: {t.text_hi};
}}
QTableCornerButton::section {{
    background: {t.surface_2};
    border: none;
}}

/* ---------- 滚动条 / 分割条 / 提示 ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #c6cfdd; border-radius: 5px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.accent}; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: #c6cfdd; border-radius: 5px; min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t.accent}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QSplitter::handle {{ background: {t.border}; }}
QSplitter::handle:hover {{ background: {t.accent_soft}; }}

QToolTip {{
    background: {t.text_hi};
    color: #ffffff;
    border: none;
    padding: 5px 9px;
    border-radius: 6px;
    font-size: 12px;
}}

/* ---------- 状态标签 ---------- */
QFrame#bookReaderBar {{
    background: {t.surface};
    border-bottom: 1px solid {t.border};
}}
QLabel#readerTitle {{ color: {t.text_hi}; font-size: 15px; font-weight: 700; }}
QLabel#readerStatus, QLabel#shelfHint {{
    color: {t.text_dim};
    font-size: 12px;
}}
QProgressBar {{
    background: {t.surface_2};
    border: 1px solid {t.border};
    border-radius: 6px;
    height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: {t.accent};
    border-radius: 5px;
}}
"""
