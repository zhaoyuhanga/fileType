"""全局 QSS 主题：由 `ui_kit/tokens.py` 的设计令牌生成。

设计语言：浅色专业风格 ——
- 页面底色柔和浅灰蓝，卡片/面板为白；
- 主色靛蓝、语义色（成功/危险/警示/信息）高对比；
- **无直角**：所有容器/控件圆角 ≥ 10px（令牌 `radius_sm`）；
- **统一尺度**：控件高度、表格行高、页面留白全部取自令牌，避免各页各写一套；
- Fusion 风格保证 QSS 跨控件一致（表格头/下拉/滚动条等全覆盖）。
阅读主题（米白/纯白/薄荷/灰白/夜间）作用于阅读正文（HTML 内联），不在此主题内。
"""
from __future__ import annotations

from .tokens import (  # noqa: F401  对外沿用 ThemeTokens / TOKENS 名字
    CARD_PADDING,
    DARK,
    FONT,
    LIGHT,
    PAGE_MARGIN,
    SECTION_GAP,
    SPACE,
    TOKENS,
    ThemeTokens,
    ensure_token,
)


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

/* ---------- 墨软乐库：播放条与控件 ---------- */
QFrame#musicPlayerBar {{
    background: {t.surface};
    border-top: 1px solid {t.border};
}}
QLabel#musicTitle {{ color: {t.text_hi}; font-size: 14px; font-weight: 700; }}
QLabel#musicMeta {{ color: {t.text_dim}; font-size: 12px; }}
QPushButton#playerButton, QPushButton#playerButtonPrimary {{
    background: {t.surface_2};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 6px 10px;
    color: {t.text};
}}
QPushButton#playerButton:hover {{ background: {t.surface_hover}; border-color: {t.border_strong}; }}
QPushButton#playerButtonPrimary {{
    background: {t.accent};
    border-color: {t.accent};
    color: #ffffff;
    font-weight: 700;
}}
QPushButton#playerButtonPrimary:hover {{ background: {t.accent_hover}; }}
QSlider::groove:horizontal {{
    height: 4px;
    background: {t.border};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {t.accent};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: #ffffff;
    border: 2px solid {t.accent};
    width: 12px;
    height: 12px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ border-color: {t.accent_hover}; }}
QListWidget {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: {t.radius_sm}px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 6px 8px;
    border-radius: 6px;
    color: {t.text};
}}
QListWidget::item:selected {{
    background: {t.accent_soft};
    color: {t.accent_strong};
}}
QListWidget::item:hover {{ background: {t.surface_hover}; }}

/* ---------- 页面骨架（ui_kit/components/layout.py） ---------- */
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
QPushButton#linkButton:hover:!disabled {{ color: {t.accent_hover}; }}
"""


def apply_theme(app) -> None:
    """套用墨软·工作台浅色主题（Fusion 风格保证一致性）。"""
    app.setStyle("Fusion")
    app.setStyleSheet(app_qss(TOKENS))
    _apply_app_icon(app)
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


def _apply_app_icon(app) -> None:
    """设置应用/任务栏图标（随包资源，缺失时静默跳过）。"""
    try:
        from PySide6.QtGui import QIcon

        from modu_workbench.services.assets import app_icon_path

        path = app_icon_path()
        if path is not None:
            app.setWindowIcon(QIcon(str(path)))
    except Exception:  # noqa: BLE001
        return
