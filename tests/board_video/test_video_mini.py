"""播放器「小窗」模式（置顶浮窗）的测试。

## 背景

影视播放器此前只有「窗口 / 全屏」两档：想边看边做别的事，要么把播放器缩得很小
（还挡在别的窗口后面），要么来回 Alt+Tab。这轮补上第三档「小窗」：

- 置顶（``WindowStaysOnTopHint``）+ 无边框，盖在别的窗口上面继续播；
- 只留一条窄控制栏（播放 / 暂停、还原、关闭），画面保持 16:9；
- 按住顶栏可拖动，首次进入停在屏幕右下角；
- ``M`` 切换，``Esc`` / 双击画面 / 还原按钮退出，退出后几何与控件原样还原。

这里守两类约束：

1. **小窗必须真的置顶且无边框** —— 否则「边看边做别的」这个目的就不成立；
2. **进出小窗不能丢状态** —— 退出后窗口几何与控制栏都要回来，且不影响播放。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from modu_workbench.ui_kit.media.player import (
    MINI_BAR_HEIGHT,
    MINI_VIDEO_HEIGHT,
    MINI_WIDTH,
    VideoPlayerDialog,
)


def _dialog(**kwargs) -> VideoPlayerDialog:  # noqa: ANN003
    """建一个不联网的播放器（url 为空：只建界面，不真的起播）。"""
    return VideoPlayerDialog(title="测试片", url="", render_path="graphics", **kwargs)


def test_mini_button_present(qapp: QApplication) -> None:
    """工具条里有「小窗」入口，点了就进小窗。"""
    dialog = _dialog()
    try:
        assert dialog._mini_button.text() == "小窗"          # noqa: SLF001
        assert "小窗" in dialog._mini_button.toolTip()       # noqa: SLF001
        assert dialog.is_mini() is False

        dialog._mini_button.click()                          # noqa: SLF001
        assert dialog.is_mini() is True
    finally:
        dialog.close()


def test_enter_mini_is_topmost_frameless_and_compact(qapp: QApplication) -> None:
    """小窗 = 置顶 + 无边框 + 只留一条控制栏。"""
    dialog = _dialog()
    try:
        dialog.enter_mini()
        flags = dialog.windowFlags()
        assert flags & Qt.WindowType.WindowStaysOnTopHint, "小窗没有置顶，会藏在别的窗口后面"
        assert flags & Qt.WindowType.FramelessWindowHint, "小窗还有系统标题栏，不够紧凑"
        assert dialog._header.isHidden()                     # noqa: SLF001
        assert dialog._controls.isHidden()                   # noqa: SLF001
        assert dialog._status.isHidden()                     # noqa: SLF001
        assert dialog._mini_bar.isHidden() is False          # noqa: SLF001
        assert dialog.width() == MINI_WIDTH
        assert dialog.height() == MINI_VIDEO_HEIGHT + MINI_BAR_HEIGHT
    finally:
        dialog.close()


def test_exit_mini_restores_geometry_and_controls(qapp: QApplication) -> None:
    """退出小窗后：几何、窗口标志、控制栏全部还原。"""
    dialog = _dialog()
    try:
        dialog.show()
        QApplication.processEvents()
        before = dialog.geometry()

        dialog.enter_mini()
        dialog.exit_mini()

        assert dialog.is_mini() is False
        assert dialog._controls.isHidden() is False          # noqa: SLF001
        assert dialog._header.isHidden() is False            # noqa: SLF001
        assert dialog._mini_bar.isHidden() is True           # noqa: SLF001
        assert not dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert dialog.geometry() == before, "退出小窗没有还原到进入前的几何"
    finally:
        dialog.close()


def test_escape_restores_from_mini(qapp: QApplication) -> None:
    """`Esc` 退出小窗（不再吞掉退出键、也不需要鼠标找按钮）。"""
    dialog = _dialog()
    try:
        dialog.enter_mini()
        QApplication.sendEvent(
            dialog,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
        )
        assert dialog.is_mini() is False
    finally:
        dialog.close()


def test_default_shortcuts_toggle_mini(qapp: QApplication) -> None:
    """默认键位：``M`` 进出小窗、``Ctrl+M`` 最小化（可在设置里改）。"""
    from PySide6.QtGui import QKeySequence

    dialog = _dialog()
    try:
        assert dialog._mini_shortcut is not None                       # noqa: SLF001
        assert dialog._mini_shortcut.key() == QKeySequence("M")        # noqa: SLF001
        assert dialog._mini_minimize_shortcut.key() == QKeySequence("Ctrl+M")  # noqa: SLF001

        dialog._mini_shortcut.activated.emit()                         # noqa: SLF001
        assert dialog.is_mini() is True
        dialog._mini_shortcut.activated.emit()                         # noqa: SLF001
        assert dialog.is_mini() is False
    finally:
        dialog.close()


def test_shortcuts_can_be_reconfigured(qapp: QApplication) -> None:
    """设置里的键位真的会被播放器读到（不是只存在页面上）。"""
    from PySide6.QtCore import QSettings

    from modu_workbench.ui_kit.media.player import VideoPlayerDialog

    settings = QSettings("ModuWorkbench", "modu-workbench")
    settings.setValue("video/shortcut_mini", "K")
    settings.setValue("video/shortcut_mini_minimize", "Ctrl+Shift+K")
    dialog = VideoPlayerDialog(title="t", url="", render_path="graphics")
    try:
        from PySide6.QtGui import QKeySequence

        assert dialog._mini_shortcut.key() == QKeySequence("K")                      # noqa: SLF001
        assert dialog._mini_minimize_shortcut.key() == QKeySequence("Ctrl+Shift+K")  # noqa: SLF001
        dialog._mini_shortcut.activated.emit()                                       # noqa: SLF001
        assert dialog.is_mini() is True
    finally:
        dialog.close()
        settings.remove("video/shortcut_mini")
        settings.remove("video/shortcut_mini_minimize")


def test_double_click_in_mini_restores_instead_of_fullscreen(qapp: QApplication) -> None:
    """小窗里双击画面 / F11 的直觉是「放大」：先还原成正常窗口，不是直接全屏。"""
    dialog = _dialog()
    try:
        dialog.enter_mini()
        dialog.toggle_fullscreen()
        assert dialog.is_mini() is False
        assert dialog.isFullScreen() is False
    finally:
        dialog.close()


def test_enter_mini_from_fullscreen_shows_normal(qapp: QApplication) -> None:
    """全屏中点「小窗」：先退出全屏，再进小窗。"""
    dialog = _dialog()
    try:
        dialog.showFullScreen()
        dialog.enter_mini()
        assert dialog.is_mini() is True
        assert dialog.isFullScreen() is False
    finally:
        dialog.close()


def test_mini_bar_drags_window(qapp: QApplication) -> None:
    """无边框窗口靠顶栏拖动：按住移动，窗口跟着走同样的位移。"""
    dialog = _dialog()
    try:
        dialog.enter_mini()
        start = dialog.pos()

        def _mouse(kind, global_pos: QPoint, button, buttons) -> QMouseEvent:  # noqa: ANN001
            return QMouseEvent(
                kind,
                QPointF(10, 10),
                QPointF(global_pos),
                button,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            )

        dialog._mini_bar.mousePressEvent(                                  # noqa: SLF001
            _mouse(QEvent.Type.MouseButtonPress, QPoint(200, 200),
                   Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        )
        dialog._mini_bar.mouseMoveEvent(                                   # noqa: SLF001
            _mouse(QEvent.Type.MouseMove, QPoint(260, 240),
                   Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        )
        assert dialog.pos() - start == QPoint(60, 40)
    finally:
        dialog.close()


def test_double_click_on_bar_restores(qapp: QApplication) -> None:
    """双击顶栏 = 还原（和系统标题栏的习惯一致）。"""
    dialog = _dialog()
    try:
        dialog.enter_mini()
        dialog._mini_bar.mouseDoubleClickEvent(                        # noqa: SLF001
            QMouseEvent(
                QEvent.Type.MouseButtonDblClick,
                QPointF(10, 10),
                QPointF(100, 100),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        assert dialog.is_mini() is False
    finally:
        dialog.close()


def test_mini_placement_stays_on_screen(qapp: QApplication) -> None:
    """首次进入小窗停在当前屏幕右下角，不会跑到屏幕外。"""
    dialog = _dialog()
    try:
        dialog.enter_mini()
        screen = dialog.screen()
        if screen is None:          # offscreen 平台可能没有屏幕，跳过几何断言
            pytest.skip("当前平台没有可用屏幕")
        area = screen.availableGeometry()
        assert area.contains(dialog.frameGeometry()), "小窗被放到了屏幕外"
    finally:
        dialog.close()


def test_mini_play_button_follows_state(qapp: QApplication) -> None:
    """小窗里的播放按钮要和主控制栏同步（否则小窗中看不出是播是停）。"""
    pytest.importorskip("PySide6.QtMultimedia")
    from PySide6.QtMultimedia import QMediaPlayer

    dialog = _dialog()
    try:
        dialog.enter_mini()
        dialog._on_native_state(QMediaPlayer.PlaybackState.PlayingState)   # noqa: SLF001
        assert dialog._mini_bar._play_button.text() == "⏸"                 # noqa: SLF001
        dialog._on_native_state(QMediaPlayer.PlaybackState.PausedState)    # noqa: SLF001
        assert dialog._mini_bar._play_button.text() == "▶"                 # noqa: SLF001
    finally:
        dialog.close()


def test_mini_refits_video_surface(qapp: QApplication) -> None:
    """小窗里画面必须重新贴合 viewport。

    实际踩到的坑：窗口标志变化时窗口先隐藏再显示，`resizeEvent` 没赶上最终尺寸，
    `QGraphicsVideoItem` 还按 1120×593 画，小窗里就只看到视频左上角一块。
    """
    from modu_workbench.ui_kit.media.surface import VideoSurface

    dialog = _dialog()
    try:
        dialog.resize(1000, 640)
        dialog.show()
        QApplication.processEvents()
        dialog.enter_mini()
        QApplication.processEvents()
        QApplication.processEvents()

        surface = dialog._surface
        if not isinstance(surface, VideoSurface):
            pytest.skip("当前环境走 QVideoWidget 回退路径，不需要手动贴合")
        viewport = surface.view.viewport().size()
        scene = surface.scene.sceneRect().size()
        assert abs(scene.width() - viewport.width()) <= 1, "画面宽度没跟上小窗"
        assert abs(scene.height() - viewport.height()) <= 1, "画面高度没跟上小窗"
    finally:
        dialog.close()


def test_mini_window_is_independent_of_main_window(qapp: QApplication) -> None:
    """小窗与主窗口解除从属：主窗口最小化时它不会被系统一起收走。"""
    parent = QWidget()
    parent.resize(600, 400)
    dialog = VideoPlayerDialog(parent, title="t", url="", render_path="graphics")
    try:
        assert dialog.parentWidget() is parent
        dialog.enter_mini()
        assert dialog.parentWidget() is None, (
            "小窗仍从属于主窗口，主窗口一最小化它就会被一起收走"
        )
        dialog.exit_mini()
        assert dialog.parentWidget() is parent, "退出小窗后没有恢复从属关系"
    finally:
        dialog.close()
        parent.close()


def test_mini_minimize_button_and_shortcut(qapp: QApplication) -> None:
    """「—」和最小化快捷键都只是收进任务栏，窗口状态仍是小窗。"""
    dialog = _dialog()
    try:
        dialog.enter_mini()
        dialog._mini_bar._minimize_button.click()        # noqa: SLF001
        assert dialog.isMinimized() is True
        assert dialog.is_mini() is True

        dialog.showNormal()
        assert dialog.is_mini() is True, "从任务栏恢复后应仍是小窗"
        assert dialog._mini_bar.isHidden() is False      # noqa: SLF001

        dialog._mini_minimize_shortcut.activated.emit()  # noqa: SLF001
        assert dialog.isMinimized() is True
        assert dialog.is_mini() is True
    finally:
        dialog.showNormal()
        dialog.close()


def test_minimize_shortcut_enters_mini_first(qapp: QApplication) -> None:
    """在正常窗口直接按最小化键：先变小窗再收进任务栏（恢复后是小窗）。"""
    dialog = _dialog()
    try:
        dialog.show()
        dialog._mini_minimize_shortcut.activated.emit()  # noqa: SLF001
        assert dialog.is_mini() is True
        assert dialog.isMinimized() is True
    finally:
        dialog.showNormal()
        dialog.close()


def test_close_in_mini_reports_position(qapp: QApplication) -> None:
    """小窗里关闭播放器：照旧上报进度（续播不受小窗影响）。"""
    dialog = _dialog()
    positions: list[int] = []
    dialog.closed.connect(positions.append)
    dialog.enter_mini()
    dialog.close()
    assert positions and positions[0] >= 0, "关闭小窗没有上报进度"
