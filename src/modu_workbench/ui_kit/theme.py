"""全局设计令牌与 QSS 主题（墨读·工作台统一设计规范）。

设计语言：浅色专业风格 ——
- 页面底色柔和浅灰蓝，卡片/面板为白；
- 主色靛蓝、语义色（成功/危险/警示/信息）高对比；
- Fusion 风格保证 QSS 跨控件一致（表格头/下拉/滚动条等全覆盖）。
阅读主题（米白/纯白/薄荷/灰白/夜间）作用于阅读正文（HTML 内联），不在此主题内。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeTokens:
    # 外壳与表面
    shell_bg: str = "#eef1f7"
    surface: str = "#ffffff"
    surface_2: str = "#f6f8fc"
    surface_hover: str = "#f3f6fd"
    border: str = "#e3e8f2"
    border_strong: str = "#cdd6e8"
    # 文字
    text_hi: str = "#141b2e"
    text: str = "#2e3a52"
    text_dim: str = "#66728a"
    text_faint: str = "#97a2b8"
    # 强调与语义
    accent: str = "#4f5bd5"
    accent_hover: str = "#3e48bf"
    accent_strong: str = "#3a44ad"
    accent_soft: str = "#eef0fe"
    success: str = "#159a55"
    success_bg: str = "#e2f5ea"
    danger: str = "#dd4b4f"
    danger_bg: str = "#fdeaea"
    warn: str = "#b9790a"
    warn_bg: str = "#fbf1dd"
    info: str = "#2f6fdb"
    info_bg: str = "#e8f0fd"
    # 圆角 / 间距
    radius_sm: int = 6
    radius: int = 9
    radius_lg: int = 14
    space_xs: int = 4
    space_sm: int = 8
    space_md: int = 14
    space_lg: int = 22


# 全局默认令牌（apply_theme 与其它模块共用）
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

/* ---------- 输入控件 ---------- */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background: {t.surface};
    border: 1px solid {t.border_strong};
    border-radius: 7px;
    padding: 6px 9px;
    color: {t.text};
    selection-background-color: {t.accent_soft};
    selection-color: {t.text_hi};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {t.accent};
    background: {t.surface};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    color: {t.text_faint};
    background: {t.surface_2};
}}
QLineEdit#searchBox {{
    border-radius: 999px;
    padding: 6px 14px;
}}

QComboBox {{
    background: {t.surface};
    border: 1px solid {t.border_strong};
    border-radius: 7px;
    padding: 5px 10px;
    color: {t.text};
}}
QComboBox:hover {{ border-color: {t.accent}; }}
QComboBox:disabled {{ color: {t.text_faint}; background: {t.surface_2}; }}
QComboBox QAbstractItemView {{
    background: {t.surface};
    color: {t.text};
    border: 1px solid {t.border_strong};
    selection-background-color: {t.accent_soft};
    selection-color: {t.text_hi};
    outline: none;
    padding: 4px;
}}
QComboBox::down-arrow {{
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t.text_dim};
    margin-right: 6px;
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

/* ---------- 阅读正文区域留白（正文样式由页面 HTML 自行决定） ---------- */
QTextBrowser {{
    border: none;
    background: #ffffff;
}}

/* ---------- 文档查看器（对齐 main 分支 DocumentViewer） ---------- */
QWidget#viewerHeader {{
    background: #ffffff;
    border-bottom: 1px solid {t.border};
}}
QWidget#viewerMeta {{
    background: {t.surface_2};
    border-bottom: 1px solid {t.border};
}}
QWidget#viewerFooter {{
    background: {t.surface_2};
    border-top: 1px solid {t.border};
}}
QLabel#viewerName {{ color: {t.text_hi}; font-size: 15px; font-weight: 700; }}
QLabel#viewerMetaLabel, QLabel#viewerHint {{ color: {t.text_dim}; font-size: 12px; }}
QLabel#viewerDirty {{ color: {t.warn}; font-size: 12px; font-weight: 600; }}
QFrame#viewerSegmented {{
    background: {t.surface_2};
    border: 1px solid {t.border};
    border-radius: 9px;
}}
QPushButton#viewerTool {{
    border: none;
    background: transparent;
    border-radius: 7px;
    padding: 5px 11px;
    color: {t.text_dim};
    font-size: 13px;
}}
QPushButton#viewerTool:hover {{ color: {t.accent_hover}; background: #ffffff; }}
QPushButton#viewerTool[active="true"] {{
    color: {t.accent_strong};
    background: #ffffff;
    font-weight: 600;
}}

/* ---------- 转换动作选项（对齐 main 分支 action-button） ---------- */
QPushButton#actionOption {{
    background: {t.surface_2};
    border: 1px solid {t.border};
    border-radius: 7px;
    padding: 9px 12px;
    color: {t.text};
    font-weight: 500;
}}
QPushButton#actionOption:hover {{ background: {t.surface_hover}; }}
QPushButton#actionOption[active="true"] {{
    border-color: {t.accent};
    background: {t.accent_soft};
    color: {t.accent_strong};
}}
"""


def apply_theme(app) -> None:
    """套用墨读·工作台浅色主题（Fusion 风格保证一致性）。"""
    app.setStyle("Fusion")
    app.setStyleSheet(app_qss(TOKENS))
    # 兜底：原生控件（勾选框、单选框箭头等）用相近的浅色调色板
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#2e3a52"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f6f8fc"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#2e3a52"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#2e3a52"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#4f5bd5"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#97a2b8"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#141b2e"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#ffffff"))
    app.setPalette(palette)
