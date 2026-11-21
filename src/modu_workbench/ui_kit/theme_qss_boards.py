"""板块专用外观：阅读正文留白、文档查看器、播放器小窗、转换动作选项、乐库播放条

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


def qss_boards(t: ThemeTokens) -> str:
    """这些样式只服务某一个板块的特定控件（阅读器/播放器/动作按钮），与通用控件无关，因此与上面的「控件族」分开，免得改表格时误伤播放器。"""
    return f"""/* ---------- 阅读正文区域留白（正文样式由页面 HTML 自行决定） ---------- */
QTextBrowser {{
    border: none;
    background: #ffffff;
}}

/* ---------- 文档查看器（对齐 main 分支 DocumentViewer） ---------- */
QWidget#viewerHeader {{
    background: #ffffff;
    border-bottom: 1px solid {t.border};
}}
/* 播放器小窗：无边框，控制条悬浮在画面上，平时藏起来 */
QWidget#miniBar {{
    background: rgba(16, 16, 16, 190);
    border: none;
}}
QLabel#miniTitle {{ color: #f4f4f4; font-size: 12px; }}
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
"""
