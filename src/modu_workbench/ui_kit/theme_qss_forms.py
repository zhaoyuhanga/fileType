"""表单控件：输入框、下拉框与弹出列表、数字输入、勾选/单选、右键菜单

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


def qss_forms(t: ThemeTokens, control: int, arrow_down: str, arrow_up: str, check_icon: str, dot_icon: str) -> str:
    """表单控件对 QSS 最敏感（Qt 的下拉弹出层是独立窗口，圆角与选中色有专门的坑，见下面「弹出列表」那段注释）。集中一处，改表单观感不必翻遍主题文件。"""
    return f"""/* ---------- 输入控件 ---------- */
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

/* ---------- 下拉框 ----------
   要点（"下拉框丑/卡"的根源，都在这里踩过）：
   1) 右侧必须预留箭头的位置，否则长文本会压到箭头上；
   2) ::drop-down 必须显式去掉边框/底色，否则 Fusion 会在右侧画一个灰色按钮 + 竖分隔线；
   3) 箭头只能用 image（用 border 拼三角形在 Qt 里会渲染成小方块）；
   4) 弹出列表是**独立顶层窗口**，容器自带一层原生边框：
      我们自己再描一圈就会变双层边，所以 view 用 border: none，只留容器的边框；
   5) **Qt 6.11 实测的选择器坑**：
      - "后代选择器 + 子控件"（QComboBox QAbstractItemView::item）**不匹配**，写了等于没写；
      - 列表项只能用 QComboBox::item / :hover / :selected，且**不能加 padding**
        （会触发行高 1900px、弹出层暴涨到 792px 的几何爆炸，表现就是"点一下卡死"）；
      - view 上的 selection-background-color 无效，选中态必须写在 QComboBox::item:selected；
      - 也不要用"显示时改 window flags + 半透明"那套：会重建原生弹出窗口（点一下卡一下），
        全局事件过滤器还会给每个事件加一层 Python 回调。
      改这里务必先跑 tests/architecture/test_ui_design_system.py 与 packaging/ui_snapshot.py。 */
QComboBox {{
    background: {t.surface};
    border: 1px solid {t.border_strong};
    border-radius: {t.radius_sm}px;
    padding: 4px 30px 4px 12px;
    min-height: {control - 12}px;
    color: {t.text};
}}
QComboBox:hover:!disabled {{ border-color: {t.accent}; }}
QComboBox:focus, QComboBox:on {{ border-color: {t.accent}; }}
QComboBox:disabled {{
    color: {t.text_faint};
    background: {t.surface_2};
    border-color: {t.border};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{ {arrow_down} }}
/* 弹出列表：view 级只管背景/边距；**列表项只能用 `QComboBox::item*`**（6.11 实测：
   `QComboBox QAbstractItemView::item` 这种"后代 + 子控件"写法不匹配，
   `selection-background-color` 在 view 上也不生效）。
   另外千万别给 `QComboBox::item` 加 padding —— 会触发行高 1900px 的几何爆炸。 */
QComboBox QAbstractItemView {{
    background: {t.surface};
    color: {t.text};
    border: none;              /* 容器已有原生边框，再描一圈就是双层边 */
    padding: 4px;
    outline: 0;                /* 去掉当前项的虚线/细框 */
}}
QComboBox::item {{ color: {t.text}; }}
QComboBox::item:hover {{ background: {t.surface_hover}; color: {t.text_hi}; }}
QComboBox::item:selected {{ background: {t.accent_soft}; color: {t.accent_strong}; }}
QComboBox::item:disabled {{ color: {t.text_faint}; }}

/* ---------- 数字输入 ---------- */
QSpinBox, QDoubleSpinBox {{
    background: {t.surface};
    border: 1px solid {t.border_strong};
    border-radius: {t.radius_sm}px;
    padding: 4px 10px;
    min-height: {control - 12}px;
    color: {t.text};
}}
QSpinBox:hover:!disabled, QDoubleSpinBox:hover:!disabled {{ border-color: {t.accent}; }}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {t.accent}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {t.text_faint}; background: {t.surface_2}; border-color: {t.border};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    width: 22px;
    border: none;
    background: transparent;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-position: top right;
    border-top-right-radius: {t.radius_sm}px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right;
    border-bottom-right-radius: {t.radius_sm}px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {t.accent_soft};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ {arrow_up} }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ {arrow_down} }}

/* ---------- 勾选 / 单选 ---------- */
QCheckBox, QRadioButton {{ spacing: 8px; color: {t.text}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {t.text_faint}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 18px; height: 18px;
    background: {t.surface};
    border: 1px solid {t.border_strong};
    border-radius: 7px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{
    background: {t.accent};
    border-color: {t.accent};
    {check_icon}
}}
QCheckBox::indicator:indeterminate {{
    background: {t.accent_soft};
    border-color: {t.accent};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: {t.surface_2};
    border-color: {t.border};
}}
QRadioButton::indicator {{ border-radius: 9px; }}   /* 18px 方格的一半＝正圆 */
QRadioButton::indicator:checked {{ border-color: {t.accent}; {dot_icon} }}

/* ---------- 右键菜单（弹出层，同样需要去灰框） ---------- */
QMenu {{
    background: {t.surface};
    border: 1px solid {t.border_strong};
    border-radius: {t.radius_sm}px;
    padding: 6px;
    color: {t.text};
}}
QMenu::item {{
    min-height: 24px;
    padding: 6px 22px 6px 12px;
    border-radius: 7px;
    color: {t.text};
}}
QMenu::item:selected {{ background: {t.accent_soft}; color: {t.accent_strong}; }}
QMenu::item:disabled {{ color: {t.text_faint}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 10px; }}
QMenu::icon {{ padding-left: 6px; }}
"""
