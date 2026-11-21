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

#: 本模块是「主题 + 设计令牌」的统一入口（调用方常写 `from ui_kit.theme import DARK`）。
#: 显式列出来：这些名字在本文件里可能不出现，但**是公开再导出**，
#: 不登记就会被"未使用导入"的清理误删（审计脚本已按 `__all__` + 别处 import 兜住）。
__all__ = [
    "CARD_PADDING",
    "DARK",
    "FONT",
    "LIGHT",
    "PAGE_MARGIN",
    "SECTION_GAP",
    "SPACE",
    "TOKENS",
    "ThemeTokens",
    "app_qss",
    "apply_theme",
    "ensure_token",
]


# 运行时小图标在 `theme_icons.py`（QPainter 现画 PNG，供 QSS 的 `image:` 用）。
# 这里只保留"给样式串用"的两个入口，避免主题文件继续膨胀。
from .theme_icons import _icon_urls, _image_rule  # noqa: E402  放在 __all__ 之后更易读
from .theme_qss_base import qss_base  # noqa: E402  同上：QSS 分节放在 __all__ 之后
from .theme_qss_boards import qss_boards  # noqa: E402
from .theme_qss_forms import qss_forms  # noqa: E402
from .theme_qss_shell import qss_shell  # noqa: E402
from .theme_qss_views import qss_views  # noqa: E402


def app_qss(t: ThemeTokens) -> str:
    """组装整份应用 QSS：五段「控件族」样式按固定顺序拼接。

    拆分前的 `app_qss` 是一个 714 行的 f-string（24 个分节全在里面），改一处样式要在
    700 行里滚半天。现在按**控件族**拆成 5 个模块（`theme_qss_*.py`），本函数只做两件事：

    1. 把"每份 QSS 都要用的东西"算一次：控件高度 `control`、QSS 里 `image:` 用的五个
       小图标（箭头/勾/圆点，由 `theme_icons.py` 现画成 PNG 再转成 QSS 的 url 规则）；
    2. 按 `基础 → 表单 → 视图 → 板块 → 骨架` 的顺序拼接，用换行分隔。

    ⚠️ 拼接用的是 `"\\n" + "\\n".join(...) + "\\n"`，**与原来的单个 f-string 字节序列
    完全一致**（首行空行、段间单换行、末尾单换行）。改这个拼接方式会让"整份 QSS 的
    sha256"变化，从而无法再用哈希比对证明"拆分没有改样式"。
    """
    control = t.control_height
    icons = _icon_urls(t)
    arrow_down = _image_rule(icons, "chevron_down", width=12, height=12)
    arrow_up = _image_rule(icons, "chevron_up", width=12, height=12)
    arrow_right = _image_rule(icons, "chevron_right", width=12, height=12)
    check_icon = _image_rule(icons, "check", width=12, height=12)
    dot_icon = _image_rule(icons, "dot", width=12, height=12)
    return (
        "\n"
        + "\n".join(
            (
                qss_base(t),
                qss_forms(t, control, arrow_down, arrow_up, check_icon, dot_icon),
                qss_views(t, arrow_down, arrow_right),
                qss_boards(t),
                qss_shell(t),
            )
        )
        + "\n"
    )


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
