"""共享渲染层：`QGraphicsView + QGraphicsVideoItem`（含 `QVideoWidget` 回退）。

为什么必须换掉纯 `QVideoWidget`（对应方案 3.1.4）：

`QVideoWidget` 在 Windows 上是**原生子窗口**，Qt 控件无法可靠叠在上面
（QSS、透明、z-order 都会出问题）→ 弹幕、字幕、缓冲指示、诊断浮层全都做不了。
而第七大板块「墨软动漫」的核心就是弹幕，所以这一步是它的前置条件。

本模块只负责"视频画在哪、怎么贴合、鼠标事件给谁"，播放控制仍在 `player.py`：

- `VideoSurface`：`QGraphicsScene` + `QGraphicsVideoItem`，叠加层走 scene；
- `WidgetSurface`：`QVideoWidget` 旧路径（显卡异常时的一键回退）；
- `make_surface(prefer)`：按设置选，`auto` 时优先 graphics。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QVBoxLayout, QWidget
from modu_workbench.core.platform.logging import swallow

#: `video/render_path` 的取值
RENDER_AUTO = "auto"
RENDER_GRAPHICS = "graphics"
RENDER_WIDGET = "widget"


class _ClickCatcher(QGraphicsView):
    """把鼠标事件转成信号（替代旧的 `_ClickVideoWidget`）。"""

    clicked = Signal()
    doubleClicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class VideoSurface(QWidget):
    """`QGraphicsView + QGraphicsVideoItem` 视频面（可叠加字幕/弹幕/统计）。

    信号与 `WidgetSurface` 一致，`player.py` 不需要区分两种实现。
    """

    clicked = Signal()
    doubleClicked = Signal()

    #: 场景坐标里的视频原始尺寸（用于弹幕轨道换算）
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.scene = QGraphicsScene(self)
        self.view = _ClickCatcher(self.scene, self)
        self.view.setRenderHint(self.view.renderHints())
        self.view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.view.setOptimizationFlags(
            QGraphicsView.OptimizationFlag.DontSavePainterState
            | QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing
        )
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.view.setStyleSheet("background:#000;border:0;")
        self.view.setMouseTracking(True)
        self.view.clicked.connect(self.clicked.emit)
        self.view.doubleClicked.connect(self.doubleClicked.emit)
        layout.addWidget(self.view)

        self.item = QGraphicsVideoItem()
        self.item.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.scene.addItem(self.item)
        self._video_size = (1280.0, 720.0)

    # ---------------------------------------------------------------- 场景尺寸

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        rect = self.view.viewport().rect()
        if rect.isValid():
            self.scene.setSceneRect(QRectF(0, 0, rect.width(), rect.height()))
            self.item.setSize(rect.size())
        self.view.setSceneRect(self.scene.sceneRect())

    def native_size_changed(self, size) -> None:  # noqa: ANN001
        """视频分辨率变化（`QMediaPlayer` 的 `sourceChanged` 类事件）。"""
        if size and size.width() > 0 and size.height() > 0:
            self._video_size = (float(size.width()), float(size.height()))

    @property
    def video_size(self) -> tuple[float, float]:
        return self._video_size

    def viewport_size(self) -> tuple[float, float]:
        rect = self.view.viewport().rect()
        return float(rect.width()), float(rect.height())

    def set_overlay_visible(self, visible: bool) -> None:
        """诊断浮层/缓冲指示等叠加物的显隐（子类或调用方自行管理 item）。"""
        self.view.setVisible(True)

    def take_video_output(self):  # noqa: ANN201
        """交给 `QMediaPlayer.setVideoOutput()` 的对象。"""
        return self.item


class WidgetSurface(QWidget):
    """`QVideoWidget` 旧路径：最稳，但不能叠加任何东西（弹幕/字幕做不了）。"""

    clicked = Signal()
    doubleClicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtMultimediaWidgets import QVideoWidget

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.widget = QVideoWidget()
        self.widget.setMinimumHeight(320)
        layout.addWidget(self.widget)
        self.widget.setMouseTracking(True)

    def take_video_output(self):  # noqa: ANN201
        return self.widget

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)

    def native_size_changed(self, size) -> None:  # noqa: ANN001
        return None

    @property
    def video_size(self) -> tuple[float, float]:
        return (1280.0, 720.0)

    def viewport_size(self) -> tuple[float, float]:
        rect = self.widget.rect()
        return float(rect.width()), float(rect.height())


def read_render_path() -> str:
    """读取 `video/render_path` 设置（读不到时用 auto）。"""
    try:
        from PySide6.QtCore import QSettings

        value = QSettings("ModuWorkbench", "modu-workbench").value(
            "video/render_path", RENDER_AUTO, type=str
        )
        if value in (RENDER_AUTO, RENDER_GRAPHICS, RENDER_WIDGET):
            return str(value)
    except (ImportError, RuntimeError, TypeError, ValueError) as error:
        swallow("读取设置", error)
    return RENDER_AUTO


def graphics_available() -> bool:
    """`QGraphicsVideoItem` 是否可用（缺 QtMultimediaWidgets 时为 False）。"""
    try:
        from PySide6.QtMultimediaWidgets import QGraphicsVideoItem  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def make_surface(prefer: str = "") -> VideoSurface | WidgetSurface | None:
    """按 `video/render_path` 构造视频面。

    `auto` → 优先 graphics，构造失败回退 widget；两者都不可用返回 None
    （调用方据此提示"当前环境缺少 QtMultimedia"）。
    """
    choice = (prefer or read_render_path() or RENDER_AUTO).strip().lower()
    if choice == RENDER_WIDGET:
        try:
            return WidgetSurface()
        except Exception:  # noqa: BLE001
            return None
    try:
        if graphics_available():
            return VideoSurface()
    except Exception:  # noqa: BLE001  某些显卡/驱动会在建场景时抛错
        if choice == RENDER_GRAPHICS:
            return None
    try:
        return WidgetSurface()
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "RENDER_AUTO",
    "RENDER_GRAPHICS",
    "RENDER_WIDGET",
    "VideoSurface",
    "WidgetSurface",
    "graphics_available",
    "make_surface",
    "read_render_path",
]
