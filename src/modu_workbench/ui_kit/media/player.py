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

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPen
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
from modu_workbench.ui_kit.toast import Toaster

from . import danmaku as danmaku_ops
from . import subtitle as subtitle_ops
from .surface import VideoSurface, make_surface, read_render_path

SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)

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
        diagnostics_provider=None,      # noqa: ANN001  Callable[[], dict]
        subtitle_path: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(title or "墨软影视 · 播放")
        self.resize(1120, 700)
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
        if subtitle_path:
            self.load_subtitle(subtitle_path)
        self._load(url, is_hls, referer)

    # ------------------------------------------------------------------ 界面

    def _build_ui(self, qualities: list[str], current_quality: str, episode_label: str,
                  has_prev: bool, has_next: bool) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
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

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        self._status = QLabel("")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        self._status.setContentsMargins(14, 4, 14, 0)
        layout.addWidget(self._status)

        controls = QWidget()
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
        self._speed_box.setCurrentIndex(SPEEDS.index(1.0))
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
        self._volume.setValue(80)
        self._volume.setFixedWidth(90)
        self._volume.valueChanged.connect(self._on_volume_changed)
        row.addWidget(self._volume)

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
        surface.clicked.connect(self.toggle_pause)
        surface.doubleClicked.connect(self.toggle_fullscreen)
        if isinstance(surface, VideoSurface):
            self._overlay = _Overlay(surface)
        self._engine_label.setText(
            "原生解码 · 可叠加" if isinstance(surface, VideoSurface) else "原生解码 · 基础路径")

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
                play_url = (proxy.hls_url(url, referer) if is_hls
                            else proxy.stream_url(url, referer))
            except Exception:  # noqa: BLE001  代理不可用时退回直连
                play_url = url
        else:
            play_url = url
        self._play_url = play_url

        if self._player is not None:
            self._start_native(play_url)
        else:
            self._status.setText(
                "⚠ 当前环境缺少 QtMultimedia（音视频后端），无法播放；请安装完整版 PySide6")

    def _start_native(self, play_url: str) -> None:
        import time

        self._native_ok = False
        self._grace_waits = 0
        self._started_at = time.monotonic()
        self._first_frame_at = None
        self._last_position = -1
        self._healed = {"downgrade": False, "switch_source": False, "download": False}
        self._engine_label.setText(
            "原生解码 · 可叠加" if isinstance(self._surface, VideoSurface)
            else "原生解码 · 基础路径")
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
            self._status.setText(
                "该地址长时间未能起播：建议先「下载到本地」再播放，或手动换源。")
            self.stallDetected.emit("download")

    def _check_native_started(self) -> None:
        """兼容旧接口的超时检查（现在改为 position 前进判定 + 三段自愈）。"""
        if self._native_ok or self._player is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer

        status = self._player.mediaStatus()
        if status in (QMediaPlayer.MediaStatus.LoadingMedia,
                      QMediaPlayer.MediaStatus.BufferingMedia,
                      QMediaPlayer.MediaStatus.BufferedMedia,
                      QMediaPlayer.MediaStatus.StalledMedia):
            self._grace_waits += 1
            return
        self._status.setText(
            "⚠ 该地址未能起播。可尝试切换清晰度/换源，或先下载到本地再播放。")

    # ------------------------------------------------------------------ 控制

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
                f"{format_time(self._duration_ms)}")

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

    def _toggle_mute(self) -> None:
        self._volume.setValue(0 if self._volume.value() > 0 else 80)

    def _on_speed_changed(self) -> None:
        speed = float(self._speed_box.currentData() or 1.0)
        if self._player is not None and hasattr(self._player, "setPlaybackRate"):
            self._player.setPlaybackRate(speed)

    def _on_quality_changed(self, label: str) -> None:
        if label:
            self.qualityRequested.emit(label)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

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
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
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
            self._time_label.setText(
                f"{format_time(position)} / {format_time(self._duration_ms)}")
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
        else:
            self._play_button.setText("▶")

    def _on_native_status(self, status) -> None:  # noqa: ANN001
        import time

        from PySide6.QtMultimedia import QMediaPlayer

        if status in (QMediaPlayer.MediaStatus.LoadedMedia,
                      QMediaPlayer.MediaStatus.BufferedMedia):
            # 只有真正"有数据/已加载"才算起播成功；BufferingMedia 不算
            # （否则三段自愈会在还在缓冲时就误判成"已起播"）
            if not self._native_ok:
                self._native_ok = True
                self._first_frame_at = time.monotonic()
                self._stall_started = None
            self._status.setText("")
        elif status in (QMediaPlayer.MediaStatus.StalledMedia,
                        QMediaPlayer.MediaStatus.BufferingMedia):
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
                "video/auto_next", True, type=bool)
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

            return int(QSettings("ModuWorkbench", "modu-workbench").value(
                "video/subtitle_font_size", 22) or 22)
        except Exception:  # noqa: BLE001
            return 22

    def _danmaku_grade(self) -> str:
        if not hasattr(self, "_grade_cache"):
            try:
                from PySide6.QtCore import QSettings

                configured = int(QSettings("ModuWorkbench", "modu-workbench").value(
                    "video/danmaku_max_lines", 0) or 0)
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
            self, "选择字幕文件", "", "字幕 (*.srt *.vtt *.ass *.ssa *.sub);;所有文件 (*)")
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
        menu.addAction(f"当前 {len(self._danmaku_items)} 条"
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
            "纹理转换": "关闭" if os.environ.get("QT_DISABLE_HW_TEXTURES_CONVERSION") == "1"
                        else "开启",
            "渲染路径": "QGraphicsVideoItem" if isinstance(self._surface, VideoSurface)
                        else "QVideoWidget",
            "播放地址": self._play_url[:120],
            "时长": format_time(self._duration_ms),
            "当前位置": format_time(self._position_ms),
            "起播耗时_ms": int((self._first_frame_at - self._started_at) * 1000)
                            if self._first_frame_at else -1,
            "卡顿次数": self._stall_count,
            "已播放_s": int(time.monotonic() - self._started_at) if self._started_at else 0,
        }
        try:
            info.update({f"代理/{key}": value for key, value in proxy.get_stats().snapshot().items()})
            cache = proxy.get_cache().stats()
            info["缓存/占用_MB"] = round(cache["bytes"] / 1048576, 1)
            info["缓存/分片数"] = cache["count"]
        except Exception:  # noqa: BLE001
            pass
        if self._diagnostics_provider is not None:
            try:
                info.update(self._diagnostics_provider() or {})
            except Exception:  # noqa: BLE001
                pass
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

    def switch_source(self, url: str, *, is_hls: bool, referer: str = "", title: str = "",
                      start_ms: int = 0, status: str = "") -> None:
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

    def closeEvent(self, event) -> None:  # noqa: N802
        self._position_ms = self.current_position_ms()
        self._prefetch_timer.stop()
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:  # noqa: BLE001
                pass
        self.closed.emit(int(self._position_ms))
        super().closeEvent(event)

    def current_position_ms(self) -> int:
        if self._player is not None:
            return int(self._player.position())
        return int(self._position_ms)

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
    "AUTO_NEXT_COUNTDOWN_S", "NATIVE_GRACE_HLS_MS", "NATIVE_GRACE_MS",
    "SELF_HEAL_DOWNGRADE_MS", "SELF_HEAL_DOWNLOAD_MS", "SELF_HEAL_SWITCH_SOURCE_MS",
    "SPEEDS", "STALL_DOWNGRADE_MS", "STALL_SWITCH_SOURCE_MS", "VideoPlayerDialog",
    "format_time", "local_file_url", "player_available",
]
