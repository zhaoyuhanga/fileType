"""基础与容器：页面底色、文字、按钮、顶栏导航、面板/卡片、徽章、分组卡片

**为什么单独成文件**：`theme.py` 的 `app_qss` 原本是一个 **714 行的 f-string**，
24 个分节全挤在同一个字符串里。想改"表格行高"要先在 700 行 QSS 里滚到第 470 行，
还容易顺手改到隔壁的滚动条。现在按控件族拆开，每个模块只回答一个问题。

- 本模块由 `ui_kit/theme.py::app_qss` 按**固定顺序**拼接，拼接结果与拆分前逐字节一致
  （`tests/architecture/test_ui_design_system.py` 断言关键片段，重构时另做了 sha256 比对）；
- 各函数只声明自己真正用到的令牌参数（`ThemeTokens` 的属性通过 `t` 传入）；
- **不要在这里写死颜色/间距**：一律用令牌，改主题只动 `ui_kit/tokens.py`。
"""
from __future__ import annotations

from .tokens import ThemeTokens


def qss_base(t: ThemeTokens) -> str:
    """这是「设计语言的骨架」——改主色、圆角、控件高度这些令牌语义时，先看这一份。"""
    return f"""/* ---------- 基础 ---------- */
QMainWindow, QWidget#rootPage, QDialog {{
    background: {t.shell_bg};
}}
QWidget {{
    color: {t.text};
    font-size: 14px;
}}
QLabel {{
    background: transparent;
    color: {t.text};
}}
QLabel[dim="true"] {{
    color: {t.text_dim};
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {t.surface};
    border: 1px solid {t.border_strong};
    border-radius: {t.radius_sm}px;
    padding: 6px 13px;
    color: {t.text};
    font-weight: 500;
}}
QPushButton:hover:!disabled {{
    border-color: {t.accent};
    color: {t.accent_hover};
    background: {t.surface};
}}
QPushButton:pressed {{
    background: {t.surface_2};
}}
QPushButton:disabled {{
    color: {t.text_faint};
    border-color: {t.border};
    background: {t.surface_2};
}}
QPushButton#primaryButton, QPushButton.primary-button {{
    background: {t.accent};
    border-color: {t.accent};
    color: #ffffff;
}}
QPushButton#primaryButton:hover:!disabled, QPushButton.primary-button:hover:!disabled {{
    background: {t.accent_hover};
    border-color: {t.accent_hover};
    color: #ffffff;
}}
QPushButton#dangerButton, QPushButton.primary-button--danger {{
    background: {t.danger};
    border-color: {t.danger};
    color: #ffffff;
}}
QPushButton#dangerButton:hover:!disabled, QPushButton.primary-button--danger:hover:!disabled {{
    background: #c33c40;
    color: #ffffff;
}}

/* ---------- 顶栏导航 ---------- */
QFrame#topBar {{
    background: {t.surface};
    border-bottom: 1px solid {t.border};
}}
QLabel#brandTitle {{
    color: {t.text_hi};
    font-size: 17px;
    font-weight: 800;
    letter-spacing: 0.4px;
}}
QLabel#brandSub {{
    color: {t.text_dim};
    font-size: 11px;
}}
QPushButton#navButton, QPushButton#navButtonActive {{
    border: none;
    background: transparent;
    border-radius: 999px;
    padding: 6px 15px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton#navButton {{
    color: {t.text_dim};
}}
QPushButton#navButton:hover {{
    color: {t.text_hi};
    background: {t.surface_2};
}}
QPushButton#navButtonActive {{
    color: {t.accent_hover};
    background: {t.accent_soft};
    font-weight: 600;
}}

/* ---------- 面板 / 卡片 ---------- */
QWidget#dropZone {{
    background: {t.surface};
    border: 1px dashed {t.border_strong};
    border-radius: 12px;
}}
QWidget#dropZone:hover {{ border-color: {t.accent}; }}
QWidget#filePanel, QWidget#actionPanel, QWidget#bottomBar {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: 12px;
}}
QFrame#boardCard, QFrame#boardCardGhost {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: 12px;
}}
QFrame#boardCard:hover {{
    border-color: {t.accent};
    background: #fcfcff;
}}
QFrame#boardCardGhost {{
    background: transparent;
    border-style: dashed;
}}

QLabel#cardIcon {{ font-size: 30px; }}
QLabel#cardTitle {{
    color: {t.text_hi};
    font-size: 16px;
    font-weight: 700;
}}
QLabel#cardTagline {{ color: {t.text}; font-size: 13px; }}
QLabel#cardDesc {{ color: {t.text_dim}; font-size: 12px; }}
QLabel#ghostTitle {{ color: {t.text_dim}; font-size: 14px; font-weight: 600; }}
QLabel#ghostDesc {{ color: {t.text_dim}; font-size: 12px; }}

QLabel#introTitle {{ color: {t.text_hi}; font-size: 25px; font-weight: 800; }}
QLabel#introSub, QLabel#footerText, QLabel#sectionTitle {{
    color: {t.text_dim};
}}
QLabel#sectionTitle {{
    color: {t.text_hi};
    font-size: 14px;
    font-weight: 700;
}}
QLabel#footerText {{ font-size: 11px; color: {t.text_faint}; }}

/* ---------- 徽章 ---------- */
QLabel#chipReady {{
    color: {t.success};
    background: {t.success_bg};
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#chipPlanned {{
    color: {t.warn};
    background: {t.warn_bg};
    border-radius: 9px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
}}

/* ---------- 分组卡片与风格卡片 ---------- */
QFrame#card {{
    background: {t.surface_2};
    border: 1px solid {t.border};
    border-radius: {t.radius}px;
}}
QPushButton#styleChip {{
    background: {t.surface};
    border: 1px solid {t.border_strong};
    border-radius: {t.radius_sm}px;
    padding: 5px 12px;
    color: {t.text};
}}
QPushButton#styleChip:hover {{
    background: {t.accent_soft};
    border-color: {t.accent};
}}
QPushButton#styleChipActive {{
    background: {t.accent};
    border: 1px solid {t.accent_strong};
    border-radius: {t.radius_sm}px;
    padding: 5px 12px;
    color: #ffffff;
    font-weight: 700;
}}
QPushButton#styleChipActive:hover {{ background: {t.accent_hover}; }}
"""
