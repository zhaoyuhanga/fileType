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

from pathlib import Path

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


# ---------------------------------------------------------------- 运行时小图标
#
# 为什么需要：QSS 里的箭头/勾选标记都只能靠 `image:` 指定图片，
# 用 border 拼三角形在 Qt 里不可靠（实测渲染成一个小方块 —— 下拉框显丑的主因之一）。
# 这里用 QPainter 现画几个小 PNG 缓存到数据目录，颜色随主题变化，
# 不引入二进制资源、也不用改打包配置。

_ICON_DIR: "Path | None" = None
_ICON_CACHE: dict[str, str] = {}


def _icon_dir():  # noqa: ANN202
    global _ICON_DIR
    if _ICON_DIR is None:
        try:
            from modu_workbench.core.platform.paths import app_data_dir

            _ICON_DIR = app_data_dir() / "cache" / "ui"
        except Exception:  # noqa: BLE001  取不到目录就退化到临时目录
            import tempfile
            from pathlib import Path as _Path

            _ICON_DIR = _Path(tempfile.gettempdir()) / "modu-ui-icons"
        _ICON_DIR.mkdir(parents=True, exist_ok=True)
    return _ICON_DIR


def _draw_icon(name: str, color: str, size: int, painter_fn) -> str:  # noqa: ANN001
    """生成（或复用）一枚小图标，返回 QSS 可用的 posix 路径。"""
    key = f"{name}-{color.lstrip('#')}-{size}"
    cached = _ICON_CACHE.get(key)
    if cached:
        return cached
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

    scale = 2                                   # 画 2 倍图再按 1 倍尺寸用，边缘更干净
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(scale, scale)
    painter.setPen(QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter_fn(painter)
    painter.end()
    path = _icon_dir() / f"{key}.png"
    if not path.is_file() or path.stat().st_size == 0:
        pixmap.save(str(path), "PNG")
    _ICON_CACHE[key] = path.as_posix()
    return _ICON_CACHE[key]


def _icon_urls(t: ThemeTokens) -> dict[str, str]:
    """按主题色生成箭头/勾选图标；没有 GUI 应用时返回空（QSS 退化为不画图）。"""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:      # 纯文本环境（如单元测试）不生成图片
        return {}

    def chevron_down(painter):  # noqa: ANN001
        _polyline(painter, QPointF(2.6, 4.2), QPointF(7, 8.6), QPointF(11.4, 4.2))

    def chevron_up(painter):  # noqa: ANN001
        _polyline(painter, QPointF(2.6, 8.0), QPointF(7, 3.6), QPointF(11.4, 8.0))

    def chevron_right(painter):  # noqa: ANN001
        _polyline(painter, QPointF(4.0, 2.4), QPointF(8.6, 7), QPointF(4.0, 11.6))

    def check(painter):  # noqa: ANN001
        _polyline(painter, QPointF(2.4, 7.4), QPointF(5.6, 10.6), QPointF(11.6, 3.6))

    def dot(painter):  # noqa: ANN001
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QBrush, QColor

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(t.accent)))
        painter.drawEllipse(QPointF(7, 7), 2.8, 2.8)

    return {
        "chevron_down": _draw_icon("chevron-down", t.text_dim, 14, chevron_down),
        "chevron_up": _draw_icon("chevron-up", t.text_dim, 14, chevron_up),
        "chevron_right": _draw_icon("chevron-right", t.text_dim, 14, chevron_right),
        "check": _draw_icon("check", "#ffffff", 14, check),
        "dot": _draw_icon("radio-dot", t.accent, 14, dot),
    }


def _polyline(painter, *points) -> None:  # noqa: ANN001
    for start, end in zip(points, points[1:]):
        painter.drawLine(start, end)


def _image_rule(icons: dict, key: str, *, width: int = 12, height: int = 12) -> str:
    """生成一条 `image: url(...); width: …; height: …` 规则（图标缺失时留空）。"""
    path = icons.get(key)
    if not path:
        return ""
    return f'image: url("{path}"); width: {width}px; height: {height}px;'


def app_qss(t: ThemeTokens) -> str:
    control = t.control_height
    icons = _icon_urls(t)
    arrow_down = _image_rule(icons, "chevron_down", width=12, height=12)
    arrow_up = _image_rule(icons, "chevron_up", width=12, height=12)
    arrow_right = _image_rule(icons, "chevron_right", width=12, height=12)
    check_icon = _image_rule(icons, "check", width=12, height=12)
    dot_icon = _image_rule(icons, "dot", width=12, height=12)
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

/* ---------- 树（图库左侧分类导航等） ---------- */
QTreeView, QTreeWidget {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: {t.radius_sm}px;
    padding: 6px;
    outline: none;
    color: {t.text};
    show-decoration-selected: 1;
}}
QTreeView::item, QTreeWidget::item {{
    min-height: 30px;
    padding: 3px 8px;
    border-radius: 7px;
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


# ---------------------------------------------------------------- 关于弹出层（重要教训）
#
# 曾试过"应用级事件过滤器 + 显示时给弹出层设 FramelessWindowHint/WA_TranslucentBackground"
# 来让 QSS 圆角生效。**已移除**，原因：
#   1) 在已创建的弹出窗口上改 window flags 会触发原生窗口重建（Windows 上表现为点一下卡一下，
#      半透明分层窗口还要额外合成），首次展开下拉框会有明显停顿；
#   2) 全局事件过滤器对**每个控件、每个事件**都会回调进 Python，等于给整个界面加了一层税；
#   3) 收益无法验证：离屏渲染不做 alpha 合成，圆角效果无法确认。
# 现在的做法：弹出列表只设 view 级样式（`QComboBox QAbstractItemView`），
# 边框交给系统原生弹出边框 —— 与 QMenu 一致，一层边、不闪烁、不卡顿。
