"""墨读音乐播放引擎：队列 / 顺序·循环·随机 / 进度 / 音量。

基于 QtMultimedia（QMediaPlayer + QAudioOutput）。QtMultimedia 不可用时
（极少数无后端环境）降级为「静默模式」：仍维护队列与状态，便于界面与测试。
"""
from __future__ import annotations

import random
from typing import Iterable, List

from PySide6.QtCore import QCoreApplication, QObject, QUrl, Signal

try:  # pragma: no cover - 依赖运行环境
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    MULTIMEDIA_AVAILABLE = True
except Exception:  # noqa: BLE001
    QAudioOutput = None  # type: ignore[assignment]
    QMediaPlayer = None  # type: ignore[assignment]
    MULTIMEDIA_AVAILABLE = False

from .models import PLAY_MODES, Track


class MusicPlayer(QObject):
    """播放器：负责队列推进与状态广播（不直接碰界面）。"""

    trackChanged = Signal(object)       # Track | None
    queueChanged = Signal()
    positionChanged = Signal(int, int)  # position_ms, duration_ms
    stateChanged = Signal(str)          # playing / paused / stopped / error
    modeChanged = Signal(str)
    errorOccurred = Signal(str)

    def __init__(self, parent: QObject | None = None, silent: bool = False):
        super().__init__(parent)
        self._queue: List[Track] = []
        self._index = -1
        self._mode = "order"
        self._shuffle_history: list[int] = []
        self._volume = 70
        self._state = "stopped"
        self._silent = silent

        self._player = None
        self._audio = None
        # 无 QCoreApplication（如纯逻辑测试）或强制静默时不创建多媒体对象
        if MULTIMEDIA_AVAILABLE and not silent and QCoreApplication.instance() is not None:
            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            self._audio.setVolume(self._volume / 100)
            self._player.setAudioOutput(self._audio)
            self._player.positionChanged.connect(self._on_position)
            self._player.durationChanged.connect(self._on_duration)
            self._player.mediaStatusChanged.connect(self._on_media_status)
            self._player.errorOccurred.connect(self._on_error)
            self._player.playbackStateChanged.connect(self._on_playback_state)

    # ---------- 只读属性 ----------

    @property
    def available(self) -> bool:
        return MULTIMEDIA_AVAILABLE and self._player is not None

    @property
    def queue(self) -> List[Track]:
        return list(self._queue)

    @property
    def current(self) -> Track | None:
        if 0 <= self._index < len(self._queue):
            return self._queue[self._index]
        return None

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def state(self) -> str:
        return self._state

    @property
    def volume(self) -> int:
        return self._volume

    # ---------- 队列 ----------

    def set_queue(self, tracks: Iterable[Track], start_index: int = 0, autoplay: bool = True) -> None:
        self._queue = list(tracks)
        self._shuffle_history = []
        self._index = start_index if 0 <= start_index < len(self._queue) else (0 if self._queue else -1)
        self.queueChanged.emit()
        if autoplay and self._queue:
            self.play_index(self._index)

    def append(self, tracks: Iterable[Track]) -> None:
        self._queue.extend(tracks)
        self.queueChanged.emit()

    def clear(self) -> None:
        self.stop()
        self._queue = []
        self._index = -1
        self.queueChanged.emit()

    # ---------- 播放控制 ----------

    def play_track(self, track: Track, queue: Iterable[Track] | None = None) -> None:
        """播放单曲；传入 queue 时以该列表作为播放队列。"""
        if queue is not None:
            items = list(queue)
            self._queue = items
            try:
                self._index = items.index(track)
            except ValueError:
                self._queue = [track, *items]
                self._index = 0
            self.queueChanged.emit()
        else:
            self._queue = [track]
            self._index = 0
            self.queueChanged.emit()
        self.play_index(self._index)

    def play_index(self, index: int) -> None:
        if not (0 <= index < len(self._queue)):
            return
        self._index = index
        track = self._queue[index]
        if not track.exists:
            self.errorOccurred.emit(f"文件不存在：{track.path}")
            self.next(autoplay=True)
            return
        self.trackChanged.emit(track)
        if self._player is not None:
            self._player.setSource(QUrl.fromLocalFile(track.path))
            self._player.play()
        self._set_state("playing")
        self.positionChanged.emit(0, track.duration_ms)

    def toggle_pause(self) -> None:
        if self._player is None:
            self._set_state("paused" if self._state == "playing" else "playing")
            return
        if self._state == "playing":
            self._player.pause()
        else:
            if self._player.source().isEmpty() and self.current is not None:
                self.play_index(self._index)
                return
            self._player.play()

    def pause(self) -> None:
        if self._player is not None:
            self._player.pause()
        if self._state == "playing":
            self._set_state("paused")

    def resume(self) -> None:
        if self._player is not None and self._player.source().isEmpty() and self.current is not None:
            self.play_index(self._index)
            return
        if self._player is not None:
            self._player.play()
        self._set_state("playing")

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()
        self._set_state("stopped")

    def next(self, autoplay: bool = True) -> None:
        if not self._queue:
            return
        if self._mode == "shuffle" and len(self._queue) > 1:
            index = self._random_index()
        elif self._index + 1 < len(self._queue):
            index = self._index + 1
        elif self._mode == "loop-all":
            index = 0
        else:
            self.stop()
            return
        self._index = index
        if autoplay:
            self.play_index(index)
        else:
            self.trackChanged.emit(self.current)

    def previous(self) -> None:
        if not self._queue:
            return
        if self._mode == "shuffle" and self._shuffle_history:
            index = self._shuffle_history.pop()
        elif self._index - 1 >= 0:
            index = self._index - 1
        elif self._mode == "loop-all":
            index = len(self._queue) - 1
        else:
            index = 0
        self._index = index
        self.play_index(index)

    def seek(self, position_ms: int) -> None:
        if self._player is not None:
            self._player.setPosition(max(0, int(position_ms)))

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))
        if self._audio is not None:
            self._audio.setVolume(self._volume / 100)
        self.stateChanged.emit(self._state)

    def set_mode(self, mode: str) -> None:
        if mode not in PLAY_MODES:
            return
        self._mode = mode
        self.modeChanged.emit(mode)

    def cycle_mode(self) -> str:
        order = list(PLAY_MODES)
        self.set_mode(order[(order.index(self._mode) + 1) % len(order)])
        return self._mode

    # ---------- 内部 ----------

    def _random_index(self) -> int:
        if len(self._queue) <= 1:
            return 0
        if self._index >= 0:
            self._shuffle_history.append(self._index)
            self._shuffle_history = self._shuffle_history[-50:]
        candidates = [i for i in range(len(self._queue)) if i != self._index]
        return random.choice(candidates or [0])

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.stateChanged.emit(state)

    def _on_position(self, position: int) -> None:
        duration = self._player.duration() if self._player is not None else 0
        self.positionChanged.emit(int(position), int(duration))

    def _on_duration(self, duration: int) -> None:
        self.positionChanged.emit(int(self._player.position()) if self._player else 0, int(duration))

    def _on_playback_state(self, state) -> None:  # noqa: ANN001
        if QMediaPlayer is None:
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._set_state("playing")
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self._set_state("paused")
        elif state == QMediaPlayer.PlaybackState.StoppedState and self._state != "stopped":
            self._set_state("stopped")

    def _on_media_status(self, status) -> None:  # noqa: ANN001
        if QMediaPlayer is None:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._mode == "loop-one" and self._index >= 0:
                self.play_index(self._index)
            else:
                self.next(autoplay=True)

    def _on_error(self, error, message: str = "") -> None:  # noqa: ANN001
        if QMediaPlayer is not None and error == QMediaPlayer.Error.NoError:
            return
        text = message or "播放失败（缺少解码器或文件损坏）"
        self._set_state("error")
        self.errorOccurred.emit(text)
