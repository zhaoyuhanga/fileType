"""全局设计令牌与 QSS 主题（对齐旧版「万能格式转换器」米金证件风）。

以旧版 0.1.x（installed exe）样式为基准：
- 页面底色米金 #f5f1e8（带柔和金/蓝光晕感），面板米白 #fffaf0，边框 #d4c6ab；
- 按钮为米白→浅金渐变，主按钮金棕渐变 #efdfb6→#d8b76f；
- 语义状态沿用旧版柔和底色（运行浅蓝/成功浅绿/失败浅红）。
阅读正文主题仍由页面 HTML 内联（米白等 5 套）决定。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeTokens:
    # 外壳与表面（米金证件风）
    shell_bg: str = "#f5f1e8"
    surface: str = "#fffaf0"          # 面板米白
    surface_2: str = "#f8f1e1"        # 次级米黄
    surface_hover: str = "#fdf6e7"
    border: str = "#d4c6ab"
    border_strong: str = "#c8b99c"
    # 文字
    text_hi: str = "#26221c"
    text: str = "#1f2328"
    text_dim: str = "#6f6557"
    text_faint: str = "#8c8271"
    # 强调（金棕）
    accent: str = "#b1822a"           # 深金（active/hover 用）
    accent_hover: str = "#9a6f16"
    accent_soft: str = "#f5e3b4"      # 浅金底
    gold_from: str = "#efdfb6"
    gold_to: str = "#d8b76f"
    gold_border: str = "#c39a47"
    # 语义色（旧版柔和底 + 深字）
    success: str = "#2e7d46"
    success_bg: str = "#d8efdf"
    danger: str = "#b23a3a"
    danger_bg: str = "#f5d7d7"
    warn: str = "#8a5b06"
    warn_bg: str = "#f7e7c1"
    info: str = "#2f5f9e"
    info_bg: str = "#d7e7fb"
    # 圆角 / 间距
    radius_sm: int = 6
    radius: int = 8
    radius_lg: int = 12
    space_xs: int = 4
    space_sm: int = 8
    space_md: int = 14
    space_lg: int = 22


TOKENS = ThemeTokens()


def app_qss(t: ThemeTokens) -> str:
    return f"""
/* ---------- 基础 ---------- */
QMainWindow, QWidget#rootPage, QDialog {{
    background: {t.shell_bg};
}}
QWidget {{
    color: {t.text};
    font-size: 14px;
    font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
}}
QLabel {{
    background: transparent;
    color: {t.text};
}}

/* ---------- 按钮（米金渐变，同旧版） ---------- */
QPushButton {{
    border: 1px solid {t.border_strong};
    border-radius: {t.radius}px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #fffaf0, stop:1 #f4ead7);
    color: #26221c;
    padding: 7px 13px;
    font-weight: 500;
}}
QPushButton:hover:!disabled {{
    border-color: {t.accent};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #fffdf6, stop:1 #eadfbf);
    color: #26221c;
}}
QPushButton:pressed {{
    background: {t.surface_2};
}}
QPushButton:disabled {{
    color: {t.text_faint};
    border-color: {t.border};
    background: #f6efdf;
}}
QPushButton#primaryButton, QPushButton.primary-button {{
    border-color: {t.gold_border};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {t.gold_from}, stop:1 {t.gold_to});
    color: #3a2a06;
    font-weight: 600;
}}
QPushButton#primaryButton:hover:!disabled, QPushButton.primary-button:hover:!disabled {{
    border-color: {t.accent};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #f3e3b8, stop:1 {t.gold_to});
    color: #3a2a06;
}}
QPushButton#dangerButton, QPushButton.primary-button--danger {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #f3b1ad, stop:1 {t.danger_bg});
    border-color: #c8a08e;
    color: #6e1616;
}}

/* ---------- 顶栏（延续旧版轻标题） ---------- */
QFrame#topBar {{
    background: transparent;
    border: none;
}}
QLabel#brandTitle {{
    color: #26221c;
    font-size: 18px;
    font-weight: 700;
}}
QLabel#brandSub {{
    color: {t.text_dim};
    font-size: 11px;
}}
QPushButton#navButton, QPushButton#navButtonActive {{
    border: 1px solid transparent;
    background: transparent;
    border-radius: 999px;
    padding: 5px 15px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton#navButton {{
    color: {t.text_dim};
}}
QPushButton#navButton:hover {{
    color: #26221c;
    background: rgba(241, 230, 208, 0.9);
    border-color: {t.border};
}}
QPushButton#navButtonActive {{
    color: #7a5714;
    background: {t.accent_soft};
    border-color: #d9c59a;
    font-weight: 600;
}}

/* ---------- 面板 / 卡片 ---------- */
QWidget#dropZone {{
    background: {t.surface};
    border: 1px dashed {t.border_strong};
    border-radius: {t.radius}px;
}}
QWidget#dropZone:hover {{ border-color: {t.accent}; }}
QWidget#filePanel, QWidget#actionPanel, QWidget#bottomBar {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: {t.radius}px;
}}
QFrame#boardCard, QFrame#boardCardGhost {{
    background: #fffdf6;
    border: 1px solid {t.border};
    border-radius: 12px;
}}
QFrame#boardCard:hover {{
    border-color: #b1822a;
    background: {t.surface_hover};
}}
QFrame#boardCardGhost {{
    background: transparent;
    border-style: dashed;
}}
QLabel#cardIcon {{ font-size: 30px; }}
QLabel#cardTitle {{ color: #26221c; font-size: 16px; font-weight: 700; }}
QLabel#cardTagline {{ color: {t.text}; font-size: 13px; }}
QLabel#cardDesc {{ color: {t.text_dim}; font-size: 12px; }}
QLabel#ghostTitle {{ color: {t.text_dim}; font-size: 14px; font-weight: 600; }}
QLabel#ghostDesc {{ color: {t.text_dim}; font-size: 12px; }}
QLabel#introTitle {{ color: #26221c; font-size: 25px; font-weight: 700; }}
QLabel#introSub {{ color: {t.text_dim}; font-size: 13px; }}
QLabel#sectionTitle {{ color: #3a2f1c; font-size: 14px; font-weight: 700; }}
QLabel#footerText {{ color: {t.text_faint}; font-size: 11px; }}

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

/* ---------- 输入控件（米白暖色） ---------- */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background: #fffdf6;
    border: 1px solid {t.border_strong};
    border-radius: 7px;
    padding: 6px 9px;
    color: #26221c;
    selection-background-color: {t.accent_soft};
    selection-color: #3a2a06;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {t.accent};
    background: #ffffff;
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    color: {t.text_faint};
    background: #f6efdf;
}}
QComboBox {{
    background: #fffdf6;
    border: 1px solid {t.border_strong};
    border-radius: 7px;
    padding: 5px 10px;
    color: #26221c;
}}
QComboBox:hover {{ border-color: {t.accent}; }}
QComboBox:disabled {{ color: {t.text_faint}; background: #f6efdf; }}
QComboBox QAbstractItemView {{
    background: #fffdf6;
    color: #26221c;
    border: 1px solid {t.border_strong};
    selection-background-color: {t.accent_soft};
    selection-color: #3a2a06;
    outline: none;
}}
QComboBox::down-arrow {{
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t.text_dim};
    margin-right: 6px;
}}

/* ---------- 表格（旧版暖色表） ---------- */
QTableWidget, QTableView {{
    background: #fffdf6;
    alternate-background-color: {t.surface_2};
    gridline-color: {t.border};
    border: none;
    selection-background-color: {t.accent_soft};
    selection-color: #3a2a06;
}}
QHeaderView::section {{
    background: #f8f1e1;
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
    border-bottom: 1px solid #e6dcc7;
}}
QTableWidget::item:selected {{ background: {t.accent_soft}; color: #3a2a06; }}
QTableCornerButton::section {{
    background: #f8f1e1;
    border: none;
}}

/* ---------- 滚动条 / 分割条 / 提示 ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: #d9cba9; border-radius: 5px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.accent}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{
    background: #d9cba9; border-radius: 5px; min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t.accent}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: {t.border}; }}
QSplitter::handle:hover {{ background: {t.accent_soft}; }}
QToolTip {{
    background: #4a3b1d;
    color: #fff6e0;
    border: none;
    padding: 5px 9px;
    border-radius: 6px;
    font-size: 12px;
}}

/* ---------- 阅读/状态标签 ---------- */
QFrame#bookReaderBar {{
    background: #fffdf6;
    border-bottom: 1px solid {t.border};
}}
QLabel#readerTitle {{ color: #26221c; font-size: 15px; font-weight: 700; }}
QLabel#readerStatus, QLabel#shelfHint {{
    color: {t.text_dim};
    font-size: 12px;
}}
QProgressBar {{
    background: #f1e6d0;
    border: 1px solid {t.border};
    border-radius: 6px;
    height: 8px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {t.gold_from}, stop:1 {t.gold_to});
    border-radius: 5px;
}}
QTextBrowser {{
    border: none;
    background: #fffdf6;
}}
"""


def apply_theme(app) -> None:
    """套用米金证件风主题（Fusion 保证一致性）。"""
    app.setStyle("Fusion")
    app.setStyleSheet(app_qss(TOKENS))
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#fffdf6"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#1f2328"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#fffdf6"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8f1e1"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#1f2328"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#fffaf0"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#26221c"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#b1822a"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#fffdf6"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8c8271"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#4a3b1d"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#fff6e0"))
    app.setPalette(palette)
