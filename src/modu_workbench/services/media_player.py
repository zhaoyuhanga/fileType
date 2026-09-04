"""本地视频播放器对话框（供嵌入式前端 mp4 预览调用）。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget


class MediaPlayerDialog(QDialog):
    """简单的本地媒体播放器。"""

    def __init__(self, file_path: str, parent: QWidget | None = None):
        super().__init__(parent)
        path = Path(file_path)
        self.setWindowTitle(f"播放：{path.name}")
        self.resize(900, 600)

        layout = QVBoxLayout(self)
        self._video = QVideoWidget(self)
        layout.addWidget(self._video, 1)

        controls = QHBoxLayout()
        self._toggle_btn = QPushButton("播放 / 暂停")
        self._toggle_btn.clicked.connect(self._toggle)
        self._slider = QSlider()
        self._slider.setRange(0, 1000)
        self._slider.sliderMoved.connect(self._seek)
        self._time_label = QLabel("00:00 / 00:00")
        controls.addWidget(self._toggle_btn)
        controls.addWidget(self._slider, 1)
        controls.addWidget(self._time_label)
        layout.addLayout(controls)
        layout.addWidget(QLabel(f"{path.name} · 本地文件"))

        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def _toggle(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _seek(self, value: int) -> None:
        duration = self._player.duration()
        if duration > 0:
            self._player.setPosition(int(duration * value / 1000))

    def _on_duration(self, duration: int) -> None:
        self._duration = duration
        self._update_label()

    def _on_position(self, position: int) -> None:
        if hasattr(self, "_duration") and self._duration > 0:
            self._slider.blockSignals(True)
            self._slider.setValue(int(position * 1000 / self._duration))
            self._slider.blockSignals(False)
        self._update_label()

    def _update_label(self) -> None:
        def fmt(ms: int) -> str:
            s = int(ms / 1000)
            return f"{s // 60:02d}:{s % 60:02d}"

        pos = self._player.position()
        dur = getattr(self, "_duration", 0)
        self._time_label.setText(f"{fmt(pos)} / {fmt(dur)}")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._player.stop()
        super().closeEvent(event)
