"""页面骨架：ColumnPage、区块卡片、空状态、工具条/次要按钮

**为什么单独成文件**：`theme.py` 的 `app_qss` 原本是一个 **714 行的 f-string**，
24 个分节全挤在同一个字符串里。想改"表格行高"要先在 700 行 QSS 里滚到第 470 行，
还容易顺手改到隔壁的滚动条。现在按控件族拆开，每个模块只回答一个问题。

- 本模块由 `ui_kit/theme.py::app_qss` 按**固定顺序**拼接，拼接结果与拆分前逐字节一致
  （`tests/architecture/test_ui_design_system.py` 断言关键片段，重构时另做了 sha256 比对）；
- 各函数只声明自己真正用到的令牌参数（`ThemeTokens` 的属性通过 `t` 传入）；
- **不要在这里写死颜色/间距**：一律用令牌，改主题只动 `ui_kit/tokens.py`。
"""
from __future__ import annotations

from .tokens import FONT, ThemeTokens


def qss_shell(t: ThemeTokens) -> str:
    """`ui_kit/components/layout.py` 的组件契约（页面结构、卡片、空状态）对应的样式，跟着组件库一起看最省事。"""
    return f"""/* ---------- 页面骨架（ui_kit/components/layout.py） ---------- */
QWidget#pageRoot {{ background: {t.shell_bg}; }}
QWidget#pageHeader {{ background: transparent; }}
QLabel#pageTitle {{
    color: {t.text_hi};
    font-size: {FONT['title']}px;
    font-weight: 800;
}}
QLabel#pageSubtitle, QLabel#pageSub {{
    color: {t.text_dim};
    font-size: {FONT['small']}px;
}}
QLabel#pageHint {{
    color: {t.text_dim};
    font-size: {FONT['small']}px;
}}
QFrame#pageDivider {{
    background: {t.border};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

/* ---------- 区块卡片 ---------- */
QFrame#sectionCard {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: {t.radius_lg}px;
}}
QFrame#sectionCard[flat="true"] {{
    background: {t.surface_2};
    border-color: transparent;
}}
QLabel#sectionCardTitle {{
    color: {t.text_hi};
    font-size: {FONT['body_lg']}px;
    font-weight: 700;
}}
QLabel#sectionCardHint {{
    color: {t.text_dim};
    font-size: {FONT['small']}px;
}}

/* ---------- 空状态（列表页必须有，禁止大片空白） ---------- */
QFrame#emptyState {{
    background: {t.surface};
    border: 1px dashed {t.border_strong};
    border-radius: {t.radius_lg}px;
}}
QLabel#emptyIcon {{ font-size: 34px; }}
QLabel#emptyTitle {{
    color: {t.text_hi};
    font-size: {FONT['body_lg']}px;
    font-weight: 700;
}}
QLabel#emptyHint {{
    color: {t.text_dim};
    font-size: {FONT['small']}px;
}}

/* ---------- 工具条 / 徽章 / 次要按钮 ---------- */
QWidget#toolbarRow {{ background: transparent; }}
QLabel#statChip {{
    background: {t.accent_soft};
    color: {t.accent_strong};
    border-radius: {t.radius_pill}px;
    padding: 3px 10px;
    font-size: {FONT['small']}px;
    font-weight: 600;
}}
QLabel#chipReady, QLabel#chipPlanned {{ border-radius: {t.radius_pill}px; }}
QPushButton#ghostButton {{
    background: transparent;
    border: 1px solid {t.border_strong};
    border-radius: {t.radius_sm}px;
    padding: 6px 12px;
}}
QPushButton#ghostButton:hover:!disabled {{
    background: {t.surface_hover};
    border-color: {t.accent};
    color: {t.accent_hover};
}}
QPushButton#linkButton {{
    background: transparent;
    border: none;
    color: {t.accent};
    padding: 2px 4px;
    font-weight: 600;
}}
QPushButton#linkButton:hover:!disabled {{ color: {t.accent_hover}; }}"""
