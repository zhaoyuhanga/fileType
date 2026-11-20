"""共享播放器：QtMultimedia + `QGraphicsVideoItem` 渲染，含字幕/弹幕/诊断层。

这是第六（影视）与第七（动漫）**共用的**播放器实现 —— 因此它住在 `ui_kit/media/`，
而不是 `boards/video/`：架构闸门禁止板块之间互相引用
（`tests/architecture/test_architecture.py`），动漫板块要用播放器就只能走这里。

相对 v1.0.x 的变化（对应方案 V1-f / V3-a / V3-b / V3-c / V3-d）：

| 项 | 旧 | 新 |
|---|---|---|
| 渲染 | 只有 `QVideoWidget`（原生窗口，**不能叠加任何东西**） | `QGraphicsView + QGraphicsVideoItem`，可叠加字幕/弹幕/诊断；`video/render_path=widget` 可回退 |
| 起播超时 | 20s 后只改一行文字 | 3s / 8s / 20s 三段自愈信号（`stallDetected`） |
| 播放结束 | 只提示 | `episodeRequested(+1)` 自动连播（3s 倒计时可取消） |
| 字幕 | 无 | 外挂 `.srt/.vtt` 自动匹配 + 手动加载 |
| 弹幕 | 无 | 只读弹幕层（数据由调用方给），轨道分配 + 性能分级 |
| 诊断 | 无 | F12 浮层（后端/硬解/代理计数/卡顿次数），可复制/导出 |

对外 API 与旧 `boards/video/player.py::VideoPlayerDialog` **完全兼容**
（同名信号 + 同参数 + 同方法），旧调用方与旧测试不需要改动。
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QKeySequence, QPen, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGraphicsSimpleTextItem,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.services import media_proxy as proxy
from modu_workbench.ui_kit.shortcuts import load_player_shortcuts
from modu_workbench.ui_kit.toast import Toaster

from . import danmaku as danmaku_ops
from . import subtitle as subtitle_ops
from .surface import VideoSurface, make_surface, read_render_path
from modu_workbench.core.platform.logging import swallow

SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)

#: 播放偏好：音量与倍速（上次调到 30 / 1.5x，下次打开还是它）
VIDEO_VOLUME_KEY = "video/volume"
VIDEO_SPEED_KEY = "video/speed"
DEFAULT_VOLUME = 80


def _video_settings():  # noqa: ANN202
    from PySide6.QtCore import QSettings

    from modu_workbench.core.platform.paths import SETTINGS_APP, SETTINGS_ORG

    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def load_playback_prefs() -> tuple[int, float]:
    """读取 `(音量, 倍速)`；非法值回落到 80 / 1.0。"""
    try:
        settings = _video_settings()
        volume = int(settings.value(VIDEO_VOLUME_KEY, DEFAULT_VOLUME) or DEFAULT_VOLUME)
        speed = float(settings.value(VIDEO_SPEED_KEY, 1.0) or 1.0)
    except (TypeError, ValueError):
        return DEFAULT_VOLUME, 1.0
    volume = max(0, min(100, volume))
    if speed not in SPEEDS:
        speed = 1.0
    return volume, speed


def save_playback_pref(key: str, value: int | float) -> None:
    try:
        settings = _video_settings()
        settings.setValue(key, value)
        settings.sync()
    except Exception as error:  # noqa: BLE001  记不住偏好不该影响播放
        from modu_workbench.core.platform.logging import swallow

        swallow(f"保存播放偏好（{key}）", error)

#: 小窗模式（浮窗置顶）的窗口尺寸：只有 16:9 画面，控制条悬浮在画面上、不占高度
MINI_WIDTH = 400
MINI_VIDEO_HEIGHT = 225
MINI_BAR_HEIGHT = 28
#: 拖边能缩到的下限（再小按钮和画面都没法看）
MINI_MIN_WIDTH = 200
MINI_MIN_HEIGHT = 112
#: 无边框窗口没有系统缩放热区，自己留一圈这么宽的边
MINI_RESIZE_MARGIN = 6
#: 解除固定尺寸时的"无上限"（Qt 的 QWIDGETSIZE_MAX，PySide6 未导出这个常量）
MAX_WIDGET_SIZE = 16777215

#: 起播三段自愈的阈值（毫秒）：3s 降档 → 8s 换源 → 20s 建议先下后播
SELF_HEAL_DOWNGRADE_MS = 3000
SELF_HEAL_SWITCH_SOURCE_MS = 8000
SELF_HEAL_DOWNLOAD_MS = 20000
#: 播放中 stall 的阈值
STALL_DOWNGRADE_MS = 6000
STALL_SWITCH_SOURCE_MS = 12000
#: 判定"position 是否前进"的采样间隔与最小步进
POSITION_SAMPLE_MS = 1000
POSITION_MIN_DELTA_MS = 200
#: 自动连播倒计时
AUTO_NEXT_COUNTDOWN_S = 3
#: 旧常量（保留名字以兼容既有测试/引用）：本地文件的起播宽限
NATIVE_GRACE_MS = 5000
NATIVE_GRACE_HLS_MS = 20000


def format_time(ms: int) -> str:
    if not ms or ms < 0:
        return "00:00"
    total = int(ms // 1000)
    hours, minutes, seconds = total // 3600, (total % 3600) // 60, total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class _Overlay:
    """在 scene 上的一组可复用文本 item（字幕 / 弹幕 / 诊断）。"""

    def __init__(self, surface: VideoSurface) -> None:
        self.surface = surface
        self._pool: list[QGraphicsSimpleTextItem] = []
        self._in_use: list[QGraphicsSimpleTextItem] = []

    def acquire(self) -> QGraphicsSimpleTextItem:
        if self._pool:
            item = self._pool.pop()
        else:
            item = QGraphicsSimpleTextItem()
            item.setBrush(QBrush(QColor("#ffffff")))
            item.setPen(QPen(QColor(0, 0, 0, 220), 2))
            self.surface.scene.addItem(item)
            item.setVisible(False)
        self._in_use.append(item)
        return item

    def release_all(self) -> None:
        for item in self._in_use:
            item.setVisible(False)
            self._pool.append(item)
        self._in_use.clear()

    @property
    def items(self) -> list[QGraphicsSimpleTextItem]:
        return list(self._in_use)


class _MiniBar(QWidget):
    """小窗悬浮条：标题 + 暂停 / 最小化 / 最大化还原 / 关闭。

    平时藏起来，鼠标移进小窗才浮在画面上（不占窗口高度）。无边框窗口没有
    系统标题栏，按住这条的空白处可以拖动窗口。
    """

    def __init__(self, player: "VideoPlayerDialog") -> None:
        super().__init__(player)
        self._player = player
        self._drag_offset = None
        self.setObjectName("miniBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 2, 6, 2)
        row.setSpacing(4)

        self._title = QLabel("小窗播放")
        self._title.setObjectName("miniTitle")
        row.addWidget(self._title, 1)

        self._play_button = QPushButton("▶")
        self._play_button.setObjectName("playerButton")
        self._play_button.setFixedWidth(30)
        self._play_button.setToolTip("播放 / 暂停（空格）")
        self._play_button.clicked.connect(player.toggle_pause)
        row.addWidget(self._play_button)

        self._minimize_button = QPushButton("—")
        self._minimize_button.setObjectName("playerButton")
        self._minimize_button.setFixedWidth(30)
        self._minimize_button.setToolTip("最小化小窗（收进任务栏，声音继续）")
        self._minimize_button.clicked.connect(player.minimize_mini)
        row.addWidget(self._minimize_button)

        self._restore_button = QPushButton("⤢")
        self._restore_button.setObjectName("playerButton")
        self._restore_button.setFixedWidth(30)
        self._restore_button.setToolTip("最大化还原（Esc / 双击画面）")
        self._restore_button.clicked.connect(player.exit_mini)
        row.addWidget(self._restore_button)

        self._close_button = QPushButton("✕")
        self._close_button.setObjectName("playerButton")
        self._close_button.setFixedWidth(30)
        self._close_button.setToolTip("关闭播放器")
        self._close_button.clicked.connect(player.close)
        row.addWidget(self._close_button)

    def update_shortcut_hints(self, toggle: str, minimize: str) -> None:
        """按钮提示跟着用户配置的键位走（否则提示里写着 Ctrl+M，实际却不是）。"""
        self._minimize_button.setToolTip(f"最小化小窗（{minimize}，可在 设置 → 影视 改）")
        self._restore_button.setToolTip(f"最大化还原（{toggle} / Esc / 双击画面）")

    # ---- 拖动 ---------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self._player.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._player.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """双击顶栏 = 还原（和系统标题栏的习惯一致）。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._player.exit_mini()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class VideoPlayerDialog(QDialog):
    """视频播放窗口（影视 / 动漫共用）。

    信号：
    - `qualityRequested(label)`  用户换了清晰度
    - `episodeRequested(step)`   上一集(-1) / 下一集(+1)，含自动连播
    - `closed(position_ms)`      窗口关闭，上报进度供续播
    - `stallDetected(reason)`    卡顿/起播超时（reason=downgrade/switch_source/download）
    - `diagnosticsRequested()`   用户按 F12
    """

    qualityRequested = Signal(str)
    episodeRequested = Signal(int)
    closed = Signal(int)
    stallDetected = Signal(str)
    diagnosticsRequested = Signal()
    #: 弹幕按钮被打开（调用方去取数据，取到后调 `set_danmaku`）
    danmakuRequested = Signal()
    #: 用户要手动导入本地弹幕 XML（自动匹配不上时的兜底）
    danmakuImportRequested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "",
        url: str = "",
        is_hls: bool = False,
        referer: str = "",
        start_ms: int = 0,
        qualities: list[str] | None = None,
        current_quality: str = "",
        episode_label: str = "",
        has_prev: bool = False,
        has_next: bool = False,
        render_path: str = "",
        diagnostics_provider=None,  # noqa: ANN001  Callable[[], dict]
        subtitle_path: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(title or "墨软影视 · 播放")
        self.resize(1120, 700)
        # 小窗模式：记住进入前的窗口标志与几何，退出时原样还原
        self._mini = False
        self._normal_flags = self.windowFlags()
        self._restore_geometry = None
        self._mini_saved_mins: list[tuple[QWidget, int, int]] = []
        # 小窗是独立窗口：进入时与主窗口解除"从属"，主窗口最小化不会把它一起带走
        self._mini_owner: QWidget | None = None
        # 无边框小窗的拖动 / 拖边缩放（系统边框没了，得自己做）
        self._mini_dragging = False
        self._mini_press_pending = False
        self._mini_resize_edges = Qt.Edge(0)
        self._mini_press_global: QPoint | None = None
        self._mini_press_geom: QRect | None = None
        self._mini_press_pos: QPoint | None = None
        # Esc 会走 QDialog.reject（只隐藏、不发 closeEvent）。关窗必须先把声音放掉，
        # 否则旧窗口还在播，再开一个就是两路声音。
        self._closing = False
        self._playback_released = False
        self._player_shortcuts: list[QShortcut] = []
        self._mini_shortcut: QShortcut | None = None
        self._mini_minimize_shortcut: QShortcut | None = None
        self._toaster = Toaster(self)
        self._url = url
        self._is_hls = bool(is_hls)
        self._referer = referer
        self._play_url = url
        self._start_ms = max(0, int(start_ms))
        self._position_ms = self._start_ms
        self._duration_ms = 0
        self._dragging = False
        self._native_ok = False
        self._seeking = False
        self._grace_waits = 0
        self._has_next = bool(has_next)
        self._diagnostics_provider = diagnostics_provider
        self._render_path = render_path or read_render_path()

        # 自愈状态
        self._first_frame_at: float | None = None
        self._last_position = -1
        self._stall_count = 0
        self._stall_seconds = 0.0
        self._started_at = 0.0
        self._healed = {"downgrade": False, "switch_source": False, "download": False}
        self._countdown = 0

        # 弹幕 / 字幕
        self._danmaku_items: list = []
        self._danmaku_allocator = danmaku_ops.TrackAllocator()
        self._danmaku_enabled = False
        self._subtitle = None
        self._overlay: _Overlay | None = None
        self._diag_item = None

        self._player = None
        self._audio = None
        self._surface = None
        self._native_holder = None

        self._build_ui(qualities or [], current_quality, episode_label, has_prev, has_next)
        self.reload_shortcuts()
        if subtitle_path:
            self.load_subtitle(subtitle_path)
        self._load(url, is_hls, referer)

    # ------------------------------------------------------------------ 界面

    def _build_ui(
        self,
        qualities: list[str],
        current_quality: str,
        episode_label: str,
        has_prev: bool,
        has_next: bool,
    ) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        self._header = header
        header.setObjectName("viewerHeader")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(14, 8, 14, 8)
        header_row.setSpacing(10)
        self._title_label = QLabel(self.windowTitle())
        self._title_label.setObjectName("viewerName")
        header_row.addWidget(self._title_label, 1)
        self._engine_label = QLabel("原生解码")
        self._engine_label.setObjectName("viewerHint")
        header_row.addWidget(self._engine_label)
        layout.addWidget(header)

        # 小窗控制条浮在画面上，不进布局（进了布局就会把 16:9 的高度吃掉一截）
        self._mini_bar = _MiniBar(self)
        self._mini_bar.hide()

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        self._status = QLabel("")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        self._status.setContentsMargins(14, 4, 14, 0)
        layout.addWidget(self._status)

        controls = QWidget()
        self._controls = controls
        controls.setObjectName("bottomBar")
        row = QHBoxLayout(controls)
        row.setContentsMargins(14, 8, 14, 10)
        row.setSpacing(10)

        self._play_button = QPushButton("▶")
        self._play_button.setObjectName("playerButtonPrimary")
        self._play_button.setFixedWidth(46)
        self._play_button.setToolTip("播放 / 暂停（空格）")
        self._play_button.clicked.connect(self.toggle_pause)
        row.addWidget(self._play_button)

        self._prev_button = QPushButton("⏮")
        self._prev_button.setObjectName("playerButton")
        self._prev_button.setToolTip("上一集（P）")
        self._prev_button.setEnabled(has_prev)
        self._prev_button.clicked.connect(lambda: self.episodeRequested.emit(-1))
        row.addWidget(self._prev_button)

        self._next_button = QPushButton("⏭")
        self._next_button.setObjectName("playerButton")
        self._next_button.setToolTip("下一集（N）")
        self._next_button.setEnabled(has_next)
        self._next_button.clicked.connect(lambda: self.episodeRequested.emit(1))
        row.addWidget(self._next_button)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 1000)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(self._on_slider_moved)
        row.addWidget(self._slider, 1)

        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setObjectName("musicMeta")
        self._time_label.setMinimumWidth(110)
        row.addWidget(self._time_label)

        row.addWidget(QLabel("清晰度"))
        self._quality_box = QComboBox()
        self._quality_box.setMinimumWidth(96)
        self._quality_box.addItem(current_quality or "自动")
        for label in qualities:
            if label and label != (current_quality or ""):
                self._quality_box.addItem(label)
        self._quality_box.setEnabled(self._quality_box.count() > 1)
        self._quality_box.currentTextChanged.connect(self._on_quality_changed)
        row.addWidget(self._quality_box)

        self._speed_box = QComboBox()
        for speed in SPEEDS:
            self._speed_box.addItem(f"{speed:g}x", speed)
        volume_pref, speed_pref = load_playback_prefs()
        self._speed_box.setCurrentIndex(SPEEDS.index(speed_pref))
        self._speed_box.currentIndexChanged.connect(self._on_speed_changed)
        row.addWidget(self._speed_box)

        self._subtitle_button = QPushButton("字幕")
        self._subtitle_button.setObjectName("playerButton")
        self._subtitle_button.setToolTip("加载外挂字幕（.srt/.vtt）；与视频同名的字幕会自动加载")
        self._subtitle_button.clicked.connect(self._pick_subtitle)
        row.addWidget(self._subtitle_button)

        self._danmaku_button = QPushButton("弹幕")
        self._danmaku_button.setObjectName("playerButton")
        self._danmaku_button.setCheckable(True)
        self._danmaku_button.setToolTip("显示/隐藏弹幕（D）；右键可手动导入本地弹幕 XML")
        self._danmaku_button.toggled.connect(self.set_danmaku_enabled)
        self._danmaku_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._danmaku_button.customContextMenuRequested.connect(self._danmaku_menu)
        row.addWidget(self._danmaku_button)

        self._mute_button = QPushButton("🔊")
        self._mute_button.setObjectName("playerButton")
        self._mute_button.setFixedWidth(46)
        self._mute_button.clicked.connect(self._toggle_mute)
        row.addWidget(self._mute_button)

        self._volume = QSlider(Qt.Orientation.Horizontal)
        self._volume.setRange(0, 100)
        self._volume.setValue(volume_pref)
        self._volume.setFixedWidth(90)
        self._volume.valueChanged.connect(self._on_volume_changed)
        row.addWidget(self._volume)

        self._mini_button = QPushButton("小窗")
        self._mini_button.setObjectName("playerButton")
        self._mini_button.setToolTip("小窗播放：缩成置顶浮窗继续播，可边看边做别的（M）")
        self._mini_button.clicked.connect(self.toggle_mini)
        row.addWidget(self._mini_button)

        self._fullscreen_button = QPushButton("⛶")
        self._fullscreen_button.setObjectName("playerButton")
        self._fullscreen_button.setFixedWidth(46)
        self._fullscreen_button.setToolTip("全屏（双击画面 / F11）")
        self._fullscreen_button.clicked.connect(self.toggle_fullscreen)
        row.addWidget(self._fullscreen_button)

        if episode_label:
            self._episode_label = QLabel(episode_label)
            self._episode_label.setObjectName("musicMeta")
            row.addWidget(self._episode_label)

        layout.addWidget(controls)

        self._build_native_surface()
        self._install_pointer_filters()

        # 播放中每 2s 更新一次"该预取哪一段"（交给会话层用）
        self._prefetch_timer = QTimer(self)
        self._prefetch_timer.setInterval(2000)
        self._prefetch_timer.timeout.connect(self._on_prefetch_tick)

    def _build_native_surface(self) -> None:
        """原生解码画面：优先 `QGraphicsVideoItem`，失败回退 `QVideoWidget`。"""
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except Exception:  # noqa: BLE001  无多媒体后端
            return

        surface = make_surface(self._render_path)
        if surface is None:
            return
        self._surface = surface
        self._stack.addWidget(surface)
        self._native_holder = surface
        surface.clicked.connect(self._on_surface_clicked)
        surface.doubleClicked.connect(self.toggle_fullscreen)
        if isinstance(surface, VideoSurface):
            self._overlay = _Overlay(surface)
        self._engine_label.setText(
            "原生解码 · 可叠加" if isinstance(surface, VideoSurface) else "原生解码 · 基础路径"
        )

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(self._volume.value() / 100)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(surface.take_video_output())
        self._player.positionChanged.connect(self._on_native_position)
        self._player.durationChanged.connect(self._on_native_duration)
        self._player.playbackStateChanged.connect(self._on_native_state)
        self._player.errorOccurred.connect(self._on_native_error)
        self._player.mediaStatusChanged.connect(self._on_native_status)
        if hasattr(self._player, "setPlaybackRate"):
            self._player.setPlaybackRate(1.0)

    # ------------------------------------------------------------------ 加载

    def _load(self, url: str, is_hls: bool, referer: str) -> None:
        if not url:
            self._status.setText("⚠ 播放地址为空")
            return
        self._url = url
        self._is_hls = bool(is_hls)
        self._referer = referer

        # 远程地址统一走本机代理（新代理：连接池 + 流式 + 磁盘缓存 + 预热）
        if url.lower().startswith(("http://", "https://")) and not proxy.is_local_url(url):
            try:
                play_url = proxy.hls_url(url, referer) if is_hls else proxy.stream_url(url, referer)
            except Exception:  # noqa: BLE001  代理不可用时退回直连
                play_url = url
        else:
            play_url = url
        self._play_url = play_url

        if self._player is not None:
            self._start_native(play_url)
        else:
            self._status.setText(
                "⚠ 当前环境缺少 QtMultimedia（音视频后端），无法播放；请安装完整版 PySide6"
            )

    def _start_native(self, play_url: str) -> None:
        import time

        self._native_ok = False
        self._grace_waits = 0
        self._started_at = time.monotonic()
        self._first_frame_at = None
        self._last_position = -1
        self._healed = {"downgrade": False, "switch_source": False, "download": False}
        self._engine_label.setText(
            "原生解码 · 可叠加"
            if isinstance(self._surface, VideoSurface)
            else "原生解码 · 基础路径"
        )
        if self._native_holder is not None:
            self._stack.setCurrentWidget(self._native_holder)
        self._player.setSource(QUrl(play_url, QUrl.ParsingMode.TolerantMode))
        self._player.play()
        self._status.setText("正在加载…（已由本地代理预取前几个分片，通常很快）")
        self._prefetch_timer.start()
        QTimer.singleShot(self._grace_ms(), self._check_native_started)
        QTimer.singleShot(SELF_HEAL_DOWNGRADE_MS, lambda: self._self_heal("downgrade"))
        QTimer.singleShot(SELF_HEAL_SWITCH_SOURCE_MS, lambda: self._self_heal("switch_source"))
        QTimer.singleShot(SELF_HEAL_DOWNLOAD_MS, lambda: self._self_heal("download"))

    def _grace_ms(self) -> int:
        """兼容旧接口：首段缓冲等待时间。"""
        return NATIVE_GRACE_HLS_MS if self._is_hls else NATIVE_GRACE_MS

    # ------------------------------------------------------------------ 三段自愈

    def _progressing(self) -> bool:
        """判定"是否真的在前进"（比只看 mediaStatus 准，见方案 3.1.6）。"""
        if self._player is None:
            return False
        position = int(self._player.position())
        advanced = position - self._last_position >= POSITION_MIN_DELTA_MS
        self._last_position = position
        return self._native_ok and advanced

    def _self_heal(self, stage: str) -> None:
        """起播三段自愈：3s 降档 / 8s 换源 / 20s 建议先下后播。"""
        if self._closing or self._playback_released:
            return
        if self._native_ok or self._progressing() or stage in self._healed:
            return
        self._healed[stage] = True
        if stage == "downgrade":
            self._status.setText("线路偏慢，正在尝试更低清晰度起播…")
            self.stallDetected.emit("downgrade")
        elif stage == "switch_source":
            self._status.setText("仍未能起播，正在尝试更换线路…")
            self.stallDetected.emit("switch_source")
        else:
            self._status.setText("该地址长时间未能起播：建议先「下载到本地」再播放，或手动换源。")
            self.stallDetected.emit("download")

    def _check_native_started(self) -> None:
        """兼容旧接口的超时检查（现在改为 position 前进判定 + 三段自愈）。"""
        if self._native_ok or self._player is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer

        status = self._player.mediaStatus()
        if status in (
            QMediaPlayer.MediaStatus.LoadingMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.StalledMedia,
        ):
            self._grace_waits += 1
            return
        self._status.setText("⚠ 该地址未能起播。可尝试切换清晰度/换源，或先下载到本地再播放。")

    # ------------------------------------------------------------------ 控制

    def _on_surface_clicked(self) -> None:
        """点画面暂停。小窗里点画面是拖动，暂停交给悬浮按钮和空格。"""
        if self._mini:
            return
        self.toggle_pause()

    def toggle_pause(self) -> None:
        if self._player is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_slider_pressed(self) -> None:
        self._dragging = True

    def _on_slider_moved(self, value: int) -> None:
        if self._duration_ms:
            self._time_label.setText(
                f"{format_time(int(value * self._duration_ms / 1000))} / "
                f"{format_time(self._duration_ms)}"
            )

    def _on_slider_released(self) -> None:
        self._dragging = False
        if not self._duration_ms:
            return
        self.seek_ms(int(self._slider.value() * self._duration_ms / 1000))

    def seek_ms(self, position_ms: int) -> None:
        """设置播放进度（拖动进度条 / 续播 / 换集都走这里）。"""
        target = max(0, int(position_ms))
        self._position_ms = target
        if self._player is not None:
            self._player.setPosition(target)
        self._on_prefetch_tick()

    def _on_volume_changed(self, value: int) -> None:
        if self._audio is not None:
            self._audio.setVolume(value / 100)
        self._mute_button.setText("🔈" if value == 0 else "🔊")
        save_playback_pref(VIDEO_VOLUME_KEY, int(value))

    def _toggle_mute(self) -> None:
        self._volume.setValue(0 if self._volume.value() > 0 else DEFAULT_VOLUME)

    def _on_speed_changed(self) -> None:
        speed = float(self._speed_box.currentData() or 1.0)
        if self._player is not None and hasattr(self._player, "setPlaybackRate"):
            self._player.setPlaybackRate(speed)
        save_playback_pref(VIDEO_SPEED_KEY, speed)

    def _on_quality_changed(self, label: str) -> None:
        if label:
            self.qualityRequested.emit(label)

    def toggle_fullscreen(self) -> None:
        if self._mini:
            # 小窗里双击画面/按 F11 的直觉是「放大」：先还原成正常窗口
            self.exit_mini()
            return
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ------------------------------------------------------------------ 小窗

    def is_mini(self) -> bool:
        """当前是否处在小窗（浮窗置顶）模式。"""
        return self._mini

    def toggle_mini(self) -> None:
        """在「正常播放窗口」与「置顶小窗」之间切换（默认 M，可在设置里改）。"""
        if self._mini:
            self.exit_mini()
        else:
            self.enter_mini()

    def minimize_mini(self) -> None:
        """把小窗收进任务栏（声音继续；从任务栏点回来仍在原位、仍是小窗）。

        不是小窗时先切成小窗再最小化 —— 这个快捷键的语义是"我要它别挡路"，
        用户不该先按一次小窗再按一次最小化。
        """
        if not self._mini:
            self.enter_mini()
        self.showMinimized()

    def reload_shortcuts(self) -> None:
        """按设置（`设置 → 影视 → 播放器快捷键`）重装小窗快捷键。

        键位改完不必重启应用：设置保存后再打开播放器就是新键位；
        已经开着的播放器也可以调用本方法即时生效。
        """
        for shortcut in self._player_shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._player_shortcuts = []

        toggle, minimize = load_player_shortcuts()
        self._mini_shortcut = self._make_shortcut(toggle, self.toggle_mini)
        # 两个键配成一样时 Qt 会报 ambiguous，两个都不触发；这里保证切换键优先
        if minimize != toggle:
            self._mini_minimize_shortcut = self._make_shortcut(minimize, self.minimize_mini)
        else:
            self._mini_minimize_shortcut = None

        self._mini_button.setToolTip(
            f"小窗播放：缩成置顶浮窗继续播，可边看边做别的（{toggle or '无快捷键'}）"
        )
        self._mini_bar.update_shortcut_hints(toggle, minimize)

    def _make_shortcut(self, sequence: str, handler) -> QShortcut | None:  # noqa: ANN001
        text = (sequence or "").strip()
        if not text:
            return None
        shortcut = QShortcut(QKeySequence(text), self)
        # 控件级：焦点在播放器里（含子控件）才生效，不会抢主窗口/其它窗口的键
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(handler)
        self._player_shortcuts.append(shortcut)
        return shortcut

    def enter_mini(self) -> None:
        """缩成置顶小窗：只有画面，控制按钮鼠标移上来才出现。

        小窗是给「边看边做别的事」用的：窗口置顶、无边框，默认 16:9。
        首次进入停在屏幕右下角；按住画面或悬浮条可拖动，贴着边拖可改大小。
        """
        if self._mini:
            return
        if self.isFullScreen():
            self.showNormal()
        self._restore_geometry = self.geometry()
        self._mini = True
        # 解除与主窗口的从属关系：否则主窗口一最小化，小窗会被系统一起收走
        self._mini_owner = self.parentWidget()
        if self._mini_owner is not None:
            self.setParent(None)
        self._relax_height_limits()
        self._header.hide()
        self._status.hide()
        self._controls.hide()
        self._set_mini_chrome(False)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # 钉住当前尺寸，免得子控件的最小尺寸把窗口顶大；拖边时改的就是这个钉
        self.setFixedSize(MINI_WIDTH, MINI_VIDEO_HEIGHT)
        self._place_mini_window()
        self._place_mini_chrome()
        self.show()
        self._refit_surface()
        self.raise_()
        self.activateWindow()

    def exit_mini(self) -> None:
        """还原成正常播放窗口（Esc / 双击画面 / M / 小窗里的还原按钮）。"""
        if not self._mini:
            return
        self._end_mini_gesture()
        self._mini = False
        self._mini_bar.hide()
        self.unsetCursor()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(MAX_WIDGET_SIZE, MAX_WIDGET_SIZE)
        self._restore_height_limits()
        # 还原从属关系（父窗口可能已经销毁，例如应用退出流程里）
        owner = self._mini_owner
        self._mini_owner = None
        if owner is not None:
            try:
                self.setParent(owner)
            except RuntimeError:  # 父对象已被销毁
                pass
        self._header.show()
        self._status.show()
        self._controls.show()
        self.setWindowFlags(self._normal_flags)
        if self._restore_geometry is not None:
            self.setGeometry(self._restore_geometry)
        self.show()
        self._refit_surface()
        self.raise_()
        self.activateWindow()

    def _relax_height_limits(self) -> None:
        """小窗默认只有 225px 高，先放开子控件的最小尺寸（退出时还原）。

        `QVideoWidget` 回退路径自带 `setMinimumHeight(320)`，`QStackedWidget`
        的最小尺寸也来自子控件；不放开的话小窗会被撑高，16:9 就变形了。
        """
        self._mini_saved_mins = []
        candidates = [self._stack, self._surface, getattr(self._surface, "widget", None)]
        for widget in candidates:
            if widget is None:
                continue
            self._mini_saved_mins.append((widget, widget.minimumWidth(), widget.minimumHeight()))
            widget.setMinimumSize(0, 0)

    def _restore_height_limits(self) -> None:
        for widget, width, height in self._mini_saved_mins:
            try:
                widget.setMinimumSize(width, height)
            except RuntimeError:  # 对象已被销毁（换过渲染路径）时忽略
                continue
        self._mini_saved_mins = []

    def _refit_surface(self) -> None:
        """窗口尺寸/标志变化后让画面重新贴合（小窗里否则会只显示视频一角）。"""
        surface = self._surface
        refit = getattr(surface, "refit", None)
        if callable(refit):
            refit()
        # 布局在本次事件里还没走完，下一轮事件循环再补一次（拿到最终 viewport 尺寸）
        QTimer.singleShot(0, self._refit_surface_deferred)

    def _refit_surface_deferred(self) -> None:
        surface = self._surface
        refit = getattr(surface, "refit", None)
        if callable(refit):
            refit()

    def _place_mini_window(self) -> None:
        """首次进入小窗时停到当前屏幕右下角（离任务栏留一点空隙）。"""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 48)

    def showEvent(self, event) -> None:  # noqa: N802
        """小窗从任务栏点回来时重新贴合画面（最小化期间 viewport 尺寸可能变过）。"""
        super().showEvent(event)
        if self._mini:
            self._place_mini_chrome()
            self._refit_surface()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._mini:
            self._place_mini_chrome()
            self._refit_surface()

    # ------------------------------------------------------------------ 小窗：悬浮控件 / 拖动 / 拖边

    def _install_pointer_filters(self) -> None:
        """小窗的鼠标事件发生在画面子控件上，对话框自己收不到，得挂过滤器。"""
        targets: list[QWidget | None] = [self, self._stack, self._mini_bar, self._surface]
        surface = self._surface
        if surface is not None:
            targets.append(getattr(surface, "view", None))
            targets.append(getattr(surface, "widget", None))
        seen: set[int] = set()
        for widget in targets:
            if widget is None or id(widget) in seen:
                continue
            seen.add(id(widget))
            widget.installEventFilter(self)
            widget.setMouseTracking(True)
        self.setMouseTracking(True)

    def _set_mini_chrome(self, visible: bool) -> None:
        """暂停 / 最小化 / 最大化 / 关闭：只有鼠标在小窗里才显示。"""
        if not self._mini:
            self._mini_bar.hide()
            return
        if visible:
            self._place_mini_chrome()
            self._mini_bar.show()
            self._mini_bar.raise_()
        else:
            self._mini_bar.hide()

    def _place_mini_chrome(self) -> None:
        self._mini_bar.setGeometry(0, 0, max(1, self.width()), MINI_BAR_HEIGHT)

    def _mini_edges_at(self, pos: QPoint) -> Qt.Edge:
        """光标贴着哪条边（贴角时两条都算）。不在边上返回 0。"""
        margin = MINI_RESIZE_MARGIN
        edges = Qt.Edge(0)
        if pos.x() <= margin:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - margin:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= margin:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor_for_edges(edges: Qt.Edge) -> Qt.CursorShape:
        left = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        top = bool(edges & Qt.Edge.TopEdge)
        bottom = bool(edges & Qt.Edge.BottomEdge)
        if (left and top) or (right and bottom):
            return Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        if top or bottom:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _pointer_in_dialog(self, watched: QWidget, event) -> QPoint:  # noqa: ANN001
        if watched is self and hasattr(event, "position"):
            return event.position().toPoint()
        if hasattr(event, "globalPosition"):
            return self.mapFromGlobal(event.globalPosition().toPoint())
        return QPoint(0, 0)

    def _arm_mini_press(self, global_pos: QPoint) -> None:
        self._mini_press_global = QPoint(global_pos)
        self._mini_press_geom = QRect(self.geometry())
        self._mini_press_pos = QPoint(self.pos())

    def _end_mini_gesture(self) -> None:
        self._mini_dragging = False
        self._mini_press_pending = False
        self._mini_resize_edges = Qt.Edge(0)
        self._mini_press_global = None
        self._mini_press_geom = None
        self._mini_press_pos = None
        if self.mouseGrabber() is self:
            self.releaseMouse()
        if self._mini:
            self.unsetCursor()

    def _apply_mini_resize(self, global_pos: QPoint) -> None:
        origin = self._mini_press_geom
        anchor = self._mini_press_global
        if origin is None or anchor is None:
            return
        dx = global_pos.x() - anchor.x()
        dy = global_pos.y() - anchor.y()
        edges = self._mini_resize_edges
        left = origin.left()
        top = origin.top()
        width = origin.width()
        height = origin.height()
        if edges & Qt.Edge.LeftEdge:
            left = origin.left() + dx
            width = origin.width() - dx
        elif edges & Qt.Edge.RightEdge:
            width = origin.width() + dx
        if edges & Qt.Edge.TopEdge:
            top = origin.top() + dy
            height = origin.height() - dy
        elif edges & Qt.Edge.BottomEdge:
            height = origin.height() + dy
        if width < MINI_MIN_WIDTH:
            if edges & Qt.Edge.LeftEdge:
                left -= MINI_MIN_WIDTH - width
            width = MINI_MIN_WIDTH
        if height < MINI_MIN_HEIGHT:
            if edges & Qt.Edge.TopEdge:
                top -= MINI_MIN_HEIGHT - height
            height = MINI_MIN_HEIGHT
        self.setFixedSize(width, height)
        self.move(left, top)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802, ANN001
        if self._mini and not self._closing and self._filter_mini_pointer(watched, event):
            return True
        return super().eventFilter(watched, event)

    def _filter_mini_pointer(self, watched, event) -> bool:  # noqa: ANN001
        """小窗指针：移入显示按钮，贴边拖改大小，其余区域拖动窗口。"""
        etype = event.type()
        if etype == QEvent.Type.Enter:
            self._set_mini_chrome(True)
            return False
        if etype == QEvent.Type.Leave and watched is self:
            self._set_mini_chrome(False)
            self.unsetCursor()
            return False
        if etype == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            local = self._pointer_in_dialog(watched, event)
            if not self._mini_edges_at(local) and watched is not self._mini_bar:
                self.exit_mini()
                return True
            return False
        if etype == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            local = self._pointer_in_dialog(watched, event)
            edges = self._mini_edges_at(local)
            # 悬浮条的空白处仍走它自己的拖动；贴边则优先缩放
            if watched is self._mini_bar and not edges:
                return False
            self._arm_mini_press(event.globalPosition().toPoint())
            if edges:
                self._mini_resize_edges = edges
                self._mini_dragging = False
                self._mini_press_pending = False
                self.setCursor(self._cursor_for_edges(edges))
                if self.mouseGrabber() is None:
                    self.grabMouse()
                return True
            # 先不吃掉点击，好让双击还原还能送到画面；真的拖动了再接手
            self._mini_press_pending = True
            return False
        if etype == QEvent.Type.MouseMove and hasattr(event, "globalPosition"):
            global_pos = event.globalPosition().toPoint()
            if self._mini_resize_edges:
                self._apply_mini_resize(global_pos)
                return True
            if (
                self._mini_press_pending
                and self._mini_press_global is not None
                and event.buttons() & Qt.MouseButton.LeftButton
            ):
                delta = global_pos - self._mini_press_global
                if abs(delta.x()) + abs(delta.y()) >= 4:
                    self._mini_press_pending = False
                    self._mini_dragging = True
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    if self.mouseGrabber() is None:
                        self.grabMouse()
            if self._mini_dragging and self._mini_press_global is not None:
                delta = global_pos - self._mini_press_global
                self.move(self._mini_press_pos + delta)
                return True
            local = self._pointer_in_dialog(watched, event)
            edges = self._mini_edges_at(local)
            if edges:
                self.setCursor(self._cursor_for_edges(edges))
            elif watched is not self._mini_bar:
                self.unsetCursor()
            if not self._mini_bar.isVisible():
                self._set_mini_chrome(True)
            return False
        if etype == QEvent.Type.MouseButtonRelease and (
            self._mini_dragging or self._mini_resize_edges or self._mini_press_pending
        ):
            consumed = bool(self._mini_dragging or self._mini_resize_edges)
            self._end_mini_gesture()
            return consumed
        return False

    # ------------------------------------------------------------------ 快捷键

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        modifiers = event.modifiers()
        if key == Qt.Key.Key_Space:
            self.toggle_pause()
        elif key == Qt.Key.Key_Left:
            self.seek_ms(max(0, self._position_ms - 10_000))
        elif key == Qt.Key.Key_Right:
            self.seek_ms(self._position_ms + 10_000)
        elif key in (Qt.Key.Key_F11, Qt.Key.Key_F):
            self.toggle_fullscreen()
        elif key == Qt.Key.Key_Escape and self._mini:
            self.exit_mini()
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        elif key == Qt.Key.Key_Escape:
            # 不能交给 QDialog：它的 Esc 走 reject()，只隐藏窗口，closeEvent 不跑，声音还在
            self.close()
        elif key == Qt.Key.Key_F12:
            self.toggle_diagnostics()
        elif key == Qt.Key.Key_N:
            self.episodeRequested.emit(1)
        elif key == Qt.Key.Key_P:
            self.episodeRequested.emit(-1)
        elif key == Qt.Key.Key_D:
            self._danmaku_button.setChecked(not self._danmaku_button.isChecked())
        elif key == Qt.Key.Key_BracketLeft:
            self._step_speed(-1)
        elif key == Qt.Key.Key_BracketRight:
            self._step_speed(1)
        elif key == Qt.Key.Key_S and modifiers & Qt.KeyboardModifier.ControlModifier:
            self.copy_diagnostics()
        else:
            super().keyPressEvent(event)

    def _step_speed(self, step: int) -> None:
        index = max(0, min(len(SPEEDS) - 1, self._speed_box.currentIndex() + step))
        self._speed_box.setCurrentIndex(index)

    # ------------------------------------------------------------------ 原生回调

    def _on_native_position(self, position: int) -> None:
        self._position_ms = int(position)
        if self._duration_ms:
            if not self._dragging:
                self._slider.setValue(int(position * 1000 / self._duration_ms))
            self._time_label.setText(f"{format_time(position)} / {format_time(self._duration_ms)}")
        self._render_overlays(position)

    def _on_native_duration(self, duration: int) -> None:
        if not duration:
            return
        self._duration_ms = int(duration)
        self._time_label.setText(f"{format_time(self._position_ms)} / {format_time(duration)}")
        if self._start_ms and not self._seeking:
            self._seeking = True
            self._player.setPosition(self._start_ms)

    def _on_native_state(self, state) -> None:  # noqa: ANN001
        from PySide6.QtMultimedia import QMediaPlayer

        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_button.setText("⏸")
            self._mini_bar._play_button.setText("⏸")  # noqa: SLF001  小窗里同一个"播放/暂停"
        else:
            self._play_button.setText("▶")
            self._mini_bar._play_button.setText("▶")  # noqa: SLF001

    def _on_native_status(self, status) -> None:  # noqa: ANN001
        import time

        from PySide6.QtMultimedia import QMediaPlayer

        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            # 只有真正"有数据/已加载"才算起播成功；BufferingMedia 不算
            # （否则三段自愈会在还在缓冲时就误判成"已起播"）
            if not self._native_ok:
                self._native_ok = True
                self._first_frame_at = time.monotonic()
                self._stall_started = None
            self._status.setText("")
        elif status in (
            QMediaPlayer.MediaStatus.StalledMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
        ):
            self._status.setText("正在缓冲…（已由本地代理预取，通常很快）")
            self._on_stall()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._on_end_of_media()

    def _on_stall(self) -> None:
        """播放中卡顿：`stall_watchdog` 的三段阈值（6s 降档 / 12s 换源）。

        阈值从"卡顿开始"算起，不是"缓冲事件次数"—— 次数在不同后端下差别很大，
        而"已经卡了多久"才是用户真正感知到的东西。
        """
        import time

        now = time.monotonic()
        if getattr(self, "_stall_started", None) is None:
            self._stall_started = now
            self._stall_count += 1
        elapsed_ms = (now - self._stall_started) * 1000
        if elapsed_ms >= STALL_SWITCH_SOURCE_MS:
            self._stall_started = now
            self.stallDetected.emit("switch_source")
        elif elapsed_ms >= STALL_DOWNGRADE_MS:
            self._stall_started = now
            self.stallDetected.emit("downgrade")

    def _on_end_of_media(self) -> None:
        """播放结束：自动连播下一集（3 秒倒计时，可点取消）。"""
        if not self._has_next or not self._auto_next_enabled():
            self._status.setText("播放结束。可点「下一集」继续。")
            return
        self._countdown = AUTO_NEXT_COUNTDOWN_S
        self._tick_countdown()

    def _tick_countdown(self) -> None:
        if self._closing or self._playback_released:
            return
        if self._countdown <= 0:
            self.episodeRequested.emit(1)
            return
        self._status.setText(f"播放结束，{self._countdown} 秒后自动播放下一集（按空格取消）")
        self._countdown -= 1
        QTimer.singleShot(1000, self._tick_countdown)

    @staticmethod
    def _auto_next_enabled() -> bool:
        try:
            from PySide6.QtCore import QSettings

            return QSettings("ModuWorkbench", "modu-workbench").value(
                "video/auto_next", True, type=bool
            )
        except Exception:  # noqa: BLE001
            return True

    def _on_native_error(self, error, message: str = "") -> None:  # noqa: ANN001
        from PySide6.QtMultimedia import QMediaPlayer

        if error == QMediaPlayer.Error.NoError:
            return
        detail = message or "解码失败或地址不可用"
        self._status.setText(f"⚠ 播放失败：{detail}。可切换清晰度/线路，或下载到本地后播放。")
        self.stallDetected.emit("switch_source")

    # ------------------------------------------------------------------ 叠加层

    def _render_overlays(self, position_ms: int) -> None:
        if self._overlay is None:
            return
        self._overlay.release_all()
        self._draw_subtitle(position_ms)
        if self._danmaku_enabled:
            self._draw_danmaku(position_ms)

    def _draw_subtitle(self, position_ms: int) -> None:
        if self._overlay is None or not self._subtitle:
            return
        text = self._subtitle.at(position_ms)
        if not text:
            return
        width, height = self._overlay.surface.viewport_size()
        item = self._overlay.acquire()
        item.setText(text)
        font = QFont()
        font.setPointSize(self._subtitle_font_size())
        item.setFont(font)
        rect = item.boundingRect()
        item.setPos((width - rect.width()) / 2, height - rect.height() - 36)
        item.setVisible(True)

    def _draw_danmaku(self, position_ms: int) -> None:
        if self._overlay is None or not self._danmaku_items:
            return
        width, height = self._overlay.surface.viewport_size()
        grade = self._danmaku_grade()
        track_count = max(4, min(24, int(height // 30) or 12))
        allocator = self._danmaku_allocator
        allocator.track_count = track_count
        upcoming = danmaku_ops.slice_around(self._danmaku_items, position_ms, window_ms=1500)
        for entry in upcoming:
            index = allocator.assign(entry)
            if index is None:
                continue
            allocator.occupy(index, entry)
            item = self._overlay.acquire()
            item.setText(entry.text)
            font = QFont()
            font.setPointSize(max(8, int((entry.size or 25) * height / 720)))
            item.setFont(font)
            item.setBrush(QBrush(QColor(f"#{entry.color:06x}")))
            top = 8 + index * 30
            item.setPos(width, top)
            item.setVisible(True)
        self._danmaku_track_count = track_count
        self._danmaku_grade_name = grade

    @staticmethod
    def _subtitle_font_size() -> int:
        try:
            from PySide6.QtCore import QSettings

            # 显式 type=int：不指定时 QSettings 会把 int 读回字符串，
            # 现在靠外层 int() 兜住了，但统一成显式类型更稳（也更快）。
            return QSettings("ModuWorkbench", "modu-workbench").value(
                "video/subtitle_font_size", 22, type=int) or 22
        except Exception:  # noqa: BLE001
            return 22

    def _danmaku_grade(self) -> str:
        if not hasattr(self, "_grade_cache"):
            try:
                from PySide6.QtCore import QSettings

                configured = int(
                    QSettings("ModuWorkbench", "modu-workbench").value("video/danmaku_max_lines", 0)
                    or 0
                )
            except Exception:  # noqa: BLE001
                configured = 0
            self._grade_cache = danmaku_ops.machine_grade()
            self._danmaku_max_lines = danmaku_ops.max_lines_for(self._grade_cache, configured)
        return self._grade_cache

    def _on_prefetch_tick(self) -> None:
        """播放进度变化时通知外部更新预取位置（会话层用）。"""
        return None

    # ------------------------------------------------------------------ 字幕

    def _pick_subtitle(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "选择字幕文件", "", "字幕 (*.srt *.vtt *.ass *.ssa *.sub);;所有文件 (*)"
        )
        if path:
            self.load_subtitle(path)

    def load_subtitle(self, path: str | Path) -> bool:
        """加载外挂字幕；返回是否成功解析出内容。"""
        track = subtitle_ops.load_subtitle(path)
        self._subtitle = track if track.cues else None
        if self._subtitle is None:
            self.set_status(f"⚠ 未能从「{Path(path).name}」解析出字幕内容")
            return False
        self.set_status(f"已加载字幕：{Path(path).name}（{len(track.cues)} 条）")
        self._render_overlays(self._position_ms)
        return True

    # ------------------------------------------------------------------ 弹幕

    def set_danmaku(self, items: list) -> None:
        """设置弹幕数据（`ui_kit.media.danmaku.DanmakuItem` 列表）。"""
        grade = self._danmaku_grade()
        self._danmaku_items = danmaku_ops.filter_items(items, grade=grade)
        self._danmaku_allocator.reset()
        self._danmaku_button.blockSignals(True)
        self._danmaku_button.setChecked(bool(self._danmaku_items))
        self._danmaku_button.blockSignals(False)
        self._danmaku_enabled = bool(self._danmaku_items)
        self._render_overlays(self._position_ms)

    def danmaku_items(self) -> list:
        """当前弹幕（导出用）。"""
        return list(self._danmaku_items)

    def _danmaku_menu(self, _pos=None) -> None:  # noqa: ANN001
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        action = menu.addAction("导入本地弹幕 XML…")
        action.triggered.connect(lambda: self.danmakuImportRequested.emit())
        menu.addAction(
            f"当前 {len(self._danmaku_items)} 条"
            + ("（已显示）" if self._danmaku_enabled else "（已隐藏）")
        ).setEnabled(False)
        menu.exec(self._danmaku_button.mapToGlobal(self._danmaku_button.rect().bottomLeft()))

    def set_danmaku_enabled(self, enabled: bool) -> None:
        """开关弹幕；**首次打开且还没有数据**时向调用方要数据。"""
        was_enabled = self._danmaku_enabled
        self._danmaku_enabled = bool(enabled)
        if not enabled and self._overlay is not None:
            self._overlay.release_all()
            self._draw_subtitle(self._position_ms)
            return
        if enabled and not self._danmaku_items and not was_enabled:
            self.danmakuRequested.emit()
        self._render_overlays(self._position_ms)

    # ------------------------------------------------------------------ 诊断

    def diagnostics(self) -> dict:
        """当前播放诊断快照（F12 面板 / 复制 / 导出都用它）。"""
        import time

        info: dict = {
            "后端": os.environ.get("QT_MEDIA_BACKEND", "(默认)"),
            "硬解设备": os.environ.get("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", "(默认)"),
            "纹理转换": (
                "关闭" if os.environ.get("QT_DISABLE_HW_TEXTURES_CONVERSION") == "1" else "开启"
            ),
            "渲染路径": (
                "QGraphicsVideoItem" if isinstance(self._surface, VideoSurface) else "QVideoWidget"
            ),
            "播放地址": self._play_url[:120],
            "时长": format_time(self._duration_ms),
            "当前位置": format_time(self._position_ms),
            "起播耗时_ms": (
                int((self._first_frame_at - self._started_at) * 1000)
                if self._first_frame_at
                else -1
            ),
            "卡顿次数": self._stall_count,
            "已播放_s": int(time.monotonic() - self._started_at) if self._started_at else 0,
        }
        try:
            info.update(
                {f"代理/{key}": value for key, value in proxy.get_stats().snapshot().items()}
            )
            cache = proxy.get_cache().stats()
            info["缓存/占用_MB"] = round(cache["bytes"] / 1048576, 1)
            info["缓存/分片数"] = cache["count"]
        except Exception as error:  # noqa: BLE001  可忽略但必须留痕
            swallow("player 中的可选步骤", error)
        if self._diagnostics_provider is not None:
            try:
                info.update(self._diagnostics_provider() or {})
            except Exception as error:  # noqa: BLE001  外部回调/第三方边界
                swallow("player 中的可选步骤", error)
        return info

    def toggle_diagnostics(self) -> None:
        """F12：显示/隐藏诊断浮层。"""
        self.diagnosticsRequested.emit()
        if self._diag_item is not None:
            self._diag_item = None
            self._render_overlays(self._position_ms)
            return
        self._show_diagnostics()

    def _show_diagnostics(self) -> None:
        info = self.diagnostics()
        lines = [f"{key}: {value}" for key, value in info.items()]
        self.set_status(" | ".join(lines[:6]))
        if self._overlay is None:
            return
        self._overlay.release_all()
        item = self._overlay.acquire()
        item.setText("\n".join(lines))
        font = QFont("Consolas")
        font.setPointSize(9)
        item.setFont(font)
        item.setPos(12, 12)
        item.setVisible(True)
        self._diag_item = item

    def copy_diagnostics(self) -> None:
        from PySide6.QtWidgets import QApplication

        text = "\n".join(f"{key}: {value}" for key, value in self.diagnostics().items())
        QApplication.clipboard().setText(text)
        self._toaster.show("诊断信息已复制到剪贴板")

    def export_diagnostics(self, path: str | Path) -> Path:
        target = Path(path)
        text = "\n".join(f"{key}: {value}" for key, value in self.diagnostics().items())
        target.write_text(text, encoding="utf-8")
        return target

    # ------------------------------------------------------------------ 对外方法

    def set_qualities(self, labels: list[str], current: str = "") -> None:
        self._quality_box.blockSignals(True)
        self._quality_box.clear()
        self._quality_box.addItem(current or "自动")
        for label in labels:
            if label and label != (current or ""):
                self._quality_box.addItem(label)
        self._quality_box.blockSignals(False)
        self._quality_box.setEnabled(self._quality_box.count() > 1)

    def set_episode_nav(self, *, has_prev: bool, has_next: bool) -> None:
        self._prev_button.setEnabled(has_prev)
        self._next_button.setEnabled(has_next)
        self._has_next = bool(has_next)

    def set_status(self, message: str) -> None:
        self._status.setText(message)

    def switch_source(
        self,
        url: str,
        *,
        is_hls: bool,
        referer: str = "",
        title: str = "",
        start_ms: int = 0,
        status: str = "",
    ) -> None:
        """换清晰度 / 换线路 / 换集后，就地切换播放地址（不重建窗口）。"""
        if title:
            self._title_label.setText(title)
            self.setWindowTitle(title)
        self._start_ms = max(0, int(start_ms))
        self._seeking = self._start_ms <= 0
        self._position_ms = self._start_ms
        self._duration_ms = 0
        self._slider.setValue(0)
        if self._player is not None:
            self._player.stop()
        self._load(url, is_hls, referer)
        self.set_status(status or "正在切换播放地址…")

    def _release_playback(self) -> None:
        """停掉解码和音频设备。只 stop() 在 Windows 上经常停不干净，关窗后还有声音。"""
        if self._playback_released:
            return
        self._playback_released = True
        self._countdown = 0
        try:
            self._prefetch_timer.stop()
        except Exception as error:  # noqa: BLE001
            swallow("player 中的可选步骤", error)
        audio = self._audio
        player = self._player
        if audio is not None:
            try:
                audio.setMuted(True)
                audio.setVolume(0.0)
            except Exception as error:  # noqa: BLE001
                swallow("player 中的可选步骤", error)
        if player is None:
            return
        try:
            player.pause()
        except Exception as error:  # noqa: BLE001
            swallow("player 中的可选步骤", error)
        try:
            player.setAudioOutput(None)
        except Exception as error:  # noqa: BLE001
            swallow("player 中的可选步骤", error)
        try:
            player.stop()
        except Exception as error:  # noqa: BLE001
            swallow("player 中的可选步骤", error)
        try:
            player.setSource(QUrl())
        except Exception as error:  # noqa: BLE001
            swallow("player 中的可选步骤", error)

    def reject(self) -> None:  # noqa: N802
        """Esc / reject 也要走关闭：QDialog.reject 默认不发 closeEvent，声音会留在后台。"""
        if not self._closing:
            self.close()
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._closing:
            self._closing = True
            self._end_mini_gesture()
            # 先记下进度再拆音频，stop 之后 position 可能变成 0
            self._position_ms = self.current_position_ms()
            self._release_playback()
            self.closed.emit(int(self._position_ms))
        super().closeEvent(event)

    def current_position_ms(self) -> int:
        if self._playback_released or self._player is None:
            return int(self._position_ms)
        return int(self._player.position())

    def duration_ms(self) -> int:
        return int(self._duration_ms)


def player_available() -> bool:
    """是否有可用的播放后端（Qt 单栈：只看 QtMultimedia）。"""
    try:
        from PySide6.QtMultimedia import QMediaPlayer  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def local_file_url(path: str | Path) -> str:
    """本地文件 → 可播放的本机流地址（带 Range 支持，可拖动进度）。"""
    return proxy.media_url(str(path))


__all__ = [
    "AUTO_NEXT_COUNTDOWN_S",
    "NATIVE_GRACE_HLS_MS",
    "NATIVE_GRACE_MS",
    "SELF_HEAL_DOWNGRADE_MS",
    "SELF_HEAL_DOWNLOAD_MS",
    "SELF_HEAL_SWITCH_SOURCE_MS",
    "SPEEDS",
    "STALL_DOWNGRADE_MS",
    "STALL_SWITCH_SOURCE_MS",
    "VideoPlayerDialog",
    "format_time",
    "local_file_url",
    "player_available",
]
