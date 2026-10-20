"""运行时小图标：QSS 需要的箭头/勾选/圆点，用 QPainter 现画成 PNG。

**为什么单独成文件**：这些都是"给 QSS 的 `image:` 用"的位图生成逻辑，
与主题色板/QSS 组装没有关系；单独放一处，改图标不会碰到 700 行的样式串，
也方便单独测试（`tests/architecture/test_ui_design_system.py` 只依赖 `_icon_urls`）。

为什么不用二进制资源：QSS 里的箭头/勾选标记只能用 `image:` 指定图片，
用 border 拼三角形在 Qt 里会渲染成小方块（下拉框显丑的主因）；现画 PNG 到
数据目录还能随主题换色，且不引入资源文件、不用改打包配置。
"""
from __future__ import annotations

from pathlib import Path

from .theme import ThemeTokens

# ---------------------------------------------------------------- 运行时小图标
#
# 为什么需要：QSS 里的箭头/勾选标记都只能靠 `image:` 指定图片，
# 用 border 拼三角形在 Qt 里不可靠（实测渲染成一个小方块 —— 下拉框显丑的主因之一）。
# 这里用 QPainter 现画几个小 PNG 缓存到数据目录，颜色随主题变化，
# 不引入二进制资源、也不用改打包配置。

_ICON_DIR: Path | None = None
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


