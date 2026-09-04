"""全局设计令牌与 QSS 主题（墨读·工作台统一设计规范）。

设计原则：
- 所有颜色 / 字号 / 间距由 ThemeTokens 一处定义，页面代码不写死颜色；
- QSS 由令牌生成，通过 objectName 施加到各控件，保证两板块视觉一致；
- 阅读主题（米白/纯白/薄荷/灰白/夜间）作为可替换令牌组，后续并入。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeTokens:
    # 外壳（墨读深色外壳）
    shell_bg: str = "#0d1524"
    surface: str = "#151f36"
    surface_2: str = "#1b2a47"
    border: str = "#28385e"
    border_strong: str = "#3a4f82"
    # 文字
    text_hi: str = "#f3f6fc"
    text: str = "#c9d3e4"
    text_dim: str = "#8c98b3"
    # 强调与语义
    accent: str = "#d9a05f"      # 暖橙高亮
    accent_soft: str = "#2c2540"
    success: str = "#6fbf8e"
    danger: str = "#e07474"
    warn: str = "#e0b464"
    info: str = "#82a8ff"
    # 圆角 / 间距
    radius_sm: int = 6
    radius: int = 10
    radius_lg: int = 16
    space_xs: int = 4
    space_sm: int = 8
    space_md: int = 14
    space_lg: int = 22


def app_qss(t: ThemeTokens) -> str:
    return f"""
QWidget {{
    color: {t.text};
    font-size: 14px;
}}
QMainWindow, QWidget#rootPage {{
    background: {t.shell_bg};
}}
QLabel {{
    background: transparent;
}}

/* ---- 顶栏 ---- */
QFrame#topBar {{
    background: {t.surface};
    border-bottom: 1px solid {t.border};
}}
QLabel#brandTitle {{
    color: {t.text_hi};
    font-size: 17px;
    font-weight: 700;
}}
QLabel#brandSub {{
    color: {t.text_dim};
    font-size: 11px;
}}
QPushButton#navButton, QPushButton#navButtonActive {{
    border: none;
    border-radius: {t.radius_sm}px;
    padding: 7px 14px;
    background: transparent;
    color: {t.text_dim};
    font-size: 13px;
}}
QPushButton#navButton:hover {{
    color: {t.text_hi};
    background: {t.surface_2};
}}
QPushButton#navButtonActive {{
    color: {t.text_hi};
    background: {t.surface_2};
    border-bottom: 2px solid {t.accent};
    font-weight: 600;
}}

/* ---- 首页 ---- */
QFrame#boardCard, QFrame#boardCardGhost {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: {t.radius_lg}px;
}}
QFrame#boardCard:hover {{
    border: 1px solid {t.accent};
    background: {t.surface_2};
}}
QLabel#cardIcon {{
    font-size: 30px;
}}
QLabel#cardTitle {{
    color: {t.text_hi};
    font-size: 17px;
    font-weight: 700;
}}
QLabel#cardTagline {{
    color: {t.text};
    font-size: 13px;
}}
QLabel#cardDesc {{
    color: {t.text_dim};
    font-size: 12px;
}}
QLabel#introTitle {{
    color: {t.text_hi};
    font-size: 26px;
    font-weight: 700;
}}
QLabel#introSub {{
    color: {t.text_dim};
    font-size: 13px;
}}
QLabel#sectionTitle {{
    color: {t.text_hi};
    font-size: 14px;
    font-weight: 600;
}}
QLabel#footerText {{
    color: {t.text_dim};
    font-size: 11px;
}}
QLabel#ghostTitle {{
    color: {t.text_dim};
    font-size: 15px;
    font-weight: 600;
}}
QLabel#ghostDesc {{
    color: {t.text_dim};
    font-size: 12px;
}}

/* ---- 占位板块页 ---- */
QLabel#pageIcon {{
    font-size: 42px;
}}
QLabel#pageTitle {{
    color: {t.text_hi};
    font-size: 22px;
    font-weight: 700;
}}
QLabel#pageSub {{
    color: {t.text_dim};
    font-size: 13px;
}}
QLabel#pageLine {{
    color: {t.text};
    font-size: 13px;
}}

/* ---- 徽章 ---- */
QLabel#chipReady, QLabel#chipPlanned {{
    border-radius: 9px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#chipReady {{
    color: {t.success};
    background: rgba(111, 191, 142, 0.16);
}}
QLabel#chipPlanned {{
    color: {t.warn};
    background: rgba(224, 180, 100, 0.14);
}}

/* ---- 滚动条 ---- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {t.border_strong}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.accent}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{
    background: {t.border_strong}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---- 输入控件与阅读器 ---- */
QLineEdit, QComboBox, QTextBrowser, QTextEdit {{
    background: {t.surface_2};
    border: 1px solid {t.border};
    border-radius: {t.radius_sm}px;
    padding: 5px 9px;
    color: {t.text};
    selection-background-color: {t.accent};
    selection-color: #1a130a;
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: {t.accent};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    selection-background-color: {t.accent_soft};
    selection-color: {t.text_hi};
}}
QTextBrowser, QTextEdit {{
    border: none;
    background: transparent;
}}
QLabel#shelfHint {{ color: {t.text_dim}; font-size: 13px; }}
QLabel#readerTitle {{ color: {t.text_hi}; font-size: 15px; font-weight: 700; }}
QLabel#readerStatus {{ color: {t.text_dim}; font-size: 12px; }}
QFrame#bookReaderBar {{
    background: {t.surface};
    border-bottom: 1px solid {t.border};
}}
QProgressBar {{
    background: {t.surface_2};
    border: 1px solid {t.border};
    border-radius: 5px;
    height: 8px;
}}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 4px; }}
"""


def apply_theme(app) -> None:
    """对 QApplication 套用墨读外壳主题。"""
    app.setStyleSheet(app_qss(ThemeTokens()))
