"""墨软影视播放器：QtMultimedia 原生播放，必要时回退到内嵌 Web 播放。

播放内核（Qt 单栈）：
- **QtMultimedia(QMediaPlayer + QVideoWidget)**：原生控件、进度/音量/倍速最稳，
  在 Windows 上由 Media Foundation 解码，本地文件与多数 http 直链都能播；
- **单栈说明（v1.0.0）**：不再使用 QtWebEngine + hls.js 兜底。实测 QtWebEngine 自带的
  Chromium 不含 H.264/AAC，切过去只会更差；m3u8 由 QtMultimedia 的 FFmpeg 后端直接解码，
  远程地址统一经本机流服务（services.media_server）代理（服务端补 Referer/UA）。

对外只暴露 `VideoPlayerDialog`，两种内核的差异对调用方透明。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.services import media_server
from modu_workbench.ui_kit.toast import Toaster

# hls.js 的 CDN 候选（按序尝试；全部失败时退回 <video> 原生解码）
HLS_JS_CDNS = (
    "https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js",
    "https://unpkg.com/hls.js@1/dist/hls.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.5.13/hls.min.js",
)

SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)

# 原生播放器启动后多久仍未起播，就判定为「原生解码器不支持该地址」
NATIVE_GRACE_MS = 5000
# HLS 需要先拉清单、再缓冲若干分片，远端站点可能较慢，超时要宽松得多
NATIVE_GRACE_HLS_MS = 20000


def format_time(ms: int) -> str:
    if not ms or ms < 0:
        return "00:00"
    total = int(ms // 1000)
    hours, minutes, seconds = total // 3600, (total % 3600) // 60, total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class _ClickVideoWidget(QWidget):
    """包一层点击事件：单击切换播放/暂停，双击切换全屏。"""

    clicked = Signal()
    doubleClicked = Signal()

    def __init__(self, inner: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(inner)
        self.setMouseTracking(True)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class VideoPlayerDialog(QDialog):
    """视频播放窗口：原生优先、Web 兜底，带清晰度/剧集切换与倍速。

    信号：
    - `qualityRequested(label)`  用户在下拉里换了清晰度
    - `episodeRequested(step)`   用户点上一集(-1)/下一集(+1)
    - `closed(position_ms)`      窗口关闭，上报播放进度供续播
    """

    qualityRequested = Signal(str)
    episodeRequested = Signal(int)
    closed = Signal(int)

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
        self._web_engine_active = False
        self._grace_waits = 0

        self._player = None
        self._audio = None
        self._view = None
        self._web_page = None
        self._web_holder = None
        self._native_holder = None

        self._build_ui(qualities or [], current_quality, episode_label, has_prev, has_next)
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

        # 画面区（原生 / 网页两套内核叠在栈里，按需切换）
        # 注意：内核要在控制条之后构建 —— 原生内核会用 self._volume 初始化音量
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
        self._prev_button.setToolTip("上一集")
        self._prev_button.setEnabled(has_prev)
        self._prev_button.clicked.connect(lambda: self.episodeRequested.emit(-1))
        row.addWidget(self._prev_button)

        self._next_button = QPushButton("⏭")
        self._next_button.setObjectName("playerButton")
        self._next_button.setToolTip("下一集")
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

        # 控制条就绪后再建两套播放内核
        self._build_native_surface()
        self._build_web_surface()

    def _build_native_surface(self) -> None:
        """原生解码画面（QtMultimedia）。"""
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except Exception:  # noqa: BLE001  无多媒体后端时只保留网页内核
            return

        video = QVideoWidget()
        video.setMinimumHeight(320)
        holder = _ClickVideoWidget(video)
        holder.clicked.connect(self.toggle_pause)
        holder.doubleClicked.connect(self.toggle_fullscreen)
        self._stack.addWidget(holder)
        self._native_holder = holder

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(self._volume.value() / 100)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(video)
        self._player.positionChanged.connect(self._on_native_position)
        self._player.durationChanged.connect(self._on_native_duration)
        self._player.playbackStateChanged.connect(self._on_native_state)
        self._player.errorOccurred.connect(self._on_native_error)
        self._player.mediaStatusChanged.connect(self._on_native_status)
        if hasattr(self._player, "setPlaybackRate"):
            self._player.setPlaybackRate(1.0)

    def _build_web_surface(self) -> None:
        """v1.0.0：网页播放内核已移除（Qt 单栈），保留空实现以兼容既有调用。"""
        return

    # ------------------------------------------------------------------ 加载

    def _load(self, url: str, is_hls: bool, referer: str) -> None:
        if not url:
            self._status.setText("⚠ 播放地址为空")
            return
        self._url = url
        self._is_hls = bool(is_hls)
        self._referer = referer

        # 远程地址统一走本机代理：服务端补 Referer/UA，播放内核只需取本地流
        if url.lower().startswith(("http://", "https://")) and not media_server.is_local_url(url):
            try:
                play_url = (media_server.hls_url(url, referer) if is_hls
                            else media_server.stream_url(url, referer))
            except Exception:  # noqa: BLE001  代理不可用时退回直连
                play_url = url
        else:
            play_url = url
        self._play_url = play_url

        # 原生（QtMultimedia / FFmpeg）优先：
        # 实测 QtWebEngine 自带的 Chromium **不含 H.264/AAC 等专有编解码器**
        # （video.canPlayType('video/mp4; codecs="avc1..."') 返回空字符串），
        # 而影视源几乎全是 H.264 —— 所以网页内核根本无法解码这些片源，
        # 只能作为「原生失败后的兜底」而不是首选。
        if self._player is not None:
            self._start_native(play_url)
        elif self._web_holder is not None:
            self._start_web(play_url, referer)
        else:
            self._status.setText("⚠ 当前环境缺少 QtMultimedia（音视频后端），无法播放；请安装完整版 PySide6")

    def _start_native(self, play_url: str) -> None:
        self._native_ok = False
        self._grace_waits = 0
        # 若之前由网页内核在播，先停掉，避免两套内核同时出声
        if self._web_engine_active:
            self._web_js("try{document.getElementById('v').pause()}catch(e){}")
        self._web_engine_active = False
        self._engine_label.setText("原生解码")
        self._stack.setCurrentWidget(self._native_holder)
        self._player.setSource(QUrl(play_url, QUrl.ParsingMode.TolerantMode))
        self._player.play()
        self._status.setText("正在加载…（HLS 需要先缓冲若干分片，请稍候）")
        # 超时仍未起播 → 给出可操作提示（v1.0.0 起不再有 hls.js 网页兜底：
        # QtWebEngine 的 Chromium 不含 H.264/AAC，切过去只会更差）
        QTimer.singleShot(self._grace_ms(), self._check_native_started)

    def _grace_ms(self) -> int:
        """首段缓冲等待时间：HLS 要拉清单+若干分片，比本地文件慢得多。"""
        return NATIVE_GRACE_HLS_MS if self._is_hls else NATIVE_GRACE_MS

    def _check_native_started(self) -> None:
        if self._native_ok or self._player is None:
            return
        # 已进入缓冲/加载中就不再判失败（大清单 + 远端分片本来就慢，实测可能要 20~30 秒）
        status = self._player.mediaStatus() if self._player is not None else None
        from PySide6.QtMultimedia import QMediaPlayer

        if status in (QMediaPlayer.MediaStatus.LoadingMedia,
                      QMediaPlayer.MediaStatus.BufferingMedia,
                      QMediaPlayer.MediaStatus.BufferedMedia,
                      QMediaPlayer.MediaStatus.StalledMedia):
            # 仍在缓冲：继续等，并告诉用户在等什么（否则容易被当成卡死）
            waited = self._grace_waits * NATIVE_GRACE_MS
            self._grace_waits += 1
            self._status.setText(
                f"正在缓冲…已等待约 {waited // 1000} 秒（片源较慢时需要先取若干分片，请稍候）"
            )
            QTimer.singleShot(NATIVE_GRACE_MS, self._check_native_started)
            return
        # v1.0.0：只有原生内核（不再有 hls.js 网页兜底）
        self._status.setText(
            "⚠ 该地址未能起播。可能原因：片源线路已失效、需要更长时间，或编码不被原生解码器支持。"
            "可尝试切换清晰度/换源，或先下载到本地再播放。"
        )

    def _start_web(self, play_url: str, referer: str) -> None:
        """v1.0.0：不再有网页内核；直接给出可操作提示。"""
        self._web_engine_active = False
        self._status.setText(
            "⚠ 该地址原生解码器未能起播。可尝试切换清晰度/换源，或先下载到本地再播放。"
        )

    # ------------------------------------------------------------------ 控制

    def toggle_pause(self) -> None:
        if self._web_active:
            self._web_js("document.getElementById('v').paused ? "
                         "document.getElementById('v').play() : "
                         "document.getElementById('v').pause()")
            return
        if self._player is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    @property
    def _web_active(self) -> bool:
        """v1.0.0：网页内核已移除，恒为 False（保留属性以兼容既有调用）。"""
        return False

    def _web_js(self, code: str) -> None:  # noqa: ARG002
        """v1.0.0：网页内核已移除，保留空实现以兼容既有调用。"""
        return

    def _on_slider_pressed(self) -> None:
        self._dragging = True

    def _on_slider_moved(self, value: int) -> None:
        if self._duration_ms:
            self._time_label.setText(
                f"{format_time(int(value * self._duration_ms / 1000))} / {format_time(self._duration_ms)}"
            )

    def _on_slider_released(self) -> None:
        self._dragging = False
        if not self._duration_ms:
            return
        target = int(self._slider.value() * self._duration_ms / 1000)
        self.seek_ms(target)

    def seek_ms(self, position_ms: int) -> None:
        """设置播放进度（拖动进度条 / 续播 / 换集都走这里）。"""
        target = max(0, int(position_ms))
        self._position_ms = target
        if self._web_active:
            self._web_js(f"document.getElementById('v').currentTime = {target / 1000:.3f}")
        elif self._player is not None:
            self._player.setPosition(target)

    def _on_volume_changed(self, value: int) -> None:
        if self._audio is not None:
            self._audio.setVolume(value / 100)
        self._web_js(f"document.getElementById('v').volume = {value / 100:.2f}")
        self._mute_button.setText("🔈" if value == 0 else "🔊")

    def _toggle_mute(self) -> None:
        self._volume.setValue(0 if self._volume.value() > 0 else 80)

    def _on_speed_changed(self) -> None:
        speed = float(self._speed_box.currentData() or 1.0)
        if self._player is not None and hasattr(self._player, "setPlaybackRate"):
            self._player.setPlaybackRate(speed)
        self._web_js(f"document.getElementById('v').playbackRate = {speed}")

    def _on_quality_changed(self, label: str) -> None:
        if label:
            self.qualityRequested.emit(label)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
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
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------ 原生回调

    def _on_native_position(self, position: int) -> None:
        self._position_ms = int(position)
        if self._duration_ms:
            if not self._dragging:
                self._slider.setValue(int(position * 1000 / self._duration_ms))
            self._time_label.setText(f"{format_time(position)} / {format_time(self._duration_ms)}")

    def _on_native_duration(self, duration: int) -> None:
        if not duration:
            return
        self._duration_ms = int(duration)
        self._time_label.setText(f"{format_time(self._position_ms)} / {format_time(duration)}")
        # 续播：拿到时长后再定位，避免时长未知时 seek 无效
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
        from PySide6.QtMultimedia import QMediaPlayer

        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
                      QMediaPlayer.MediaStatus.BufferingMedia):
            self._native_ok = True
            self._status.setText("")
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._status.setText("播放结束。可点「下一集」继续。")

    def _on_native_error(self, error, message: str = "") -> None:  # noqa: ANN001
        from PySide6.QtMultimedia import QMediaPlayer

        if QMediaPlayer is not None and error == QMediaPlayer.Error.NoError:
            return
        detail = message or "解码失败或地址不可用"
        if self._web_holder is not None and not self._native_ok:
            self._status.setText(f"原生解码失败（{detail}），已切换网页播放内核…")
            self._start_web(self._play_url, self._referer)
            return
        self._status.setText(f"⚠ 播放失败：{detail}。可切换清晰度/线路，或下载到本地后播放。")

    # ------------------------------------------------------------------ 对外方法

    def set_qualities(self, labels: list[str], current: str = "") -> None:
        """解析出清晰度后回填下拉（不触发切换信号）。"""
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
        self._status.setText(status or "正在切换播放地址…")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._position_ms = self.current_position_ms()
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:  # noqa: BLE001
                pass
        self.closed.emit(int(self._position_ms))
        super().closeEvent(event)

    def current_position_ms(self) -> int:
        if self._web_active:
            return int(self._position_ms)
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
    return media_server.media_url(str(path))
