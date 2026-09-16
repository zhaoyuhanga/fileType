"""墨软乐库板块：搜索下载 / 我的音乐 / 歌单 / 格式转换 / 播放历史 + 底部播放条。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.music import (
    PLAY_MODE_LABELS,
    MusicPlayer,
    format_duration,
)
from modu_workbench.services import app_context
from modu_workbench.ui_kit.toast import Toaster
from modu_workbench.ui_kit.widgets import make_nav_button, set_nav_active

from .history import MusicHistoryPage
from .library_page import MusicConvertPage, MusicLibraryPage
from .playlist import MusicPlaylistPage
from .search import MusicSearchPage

SEARCH_KEY = "search"
LIBRARY_KEY = "library"
PLAYLIST_KEY = "playlist"
CONVERT_KEY = "convert"
HISTORY_KEY = "history"


class MusicPlayerBar(QFrame):
    """底部播放条：与页面无关，始终显示当前播放状态。"""

    favoriteToggled = Signal()

    def __init__(self, player: MusicPlayer, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("musicPlayerBar")
        self._player = player
        self._current: Track | None = None
        self._dragging = False
        self._duration_ms = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(10)

        info = QVBoxLayout()
        info.setSpacing(0)
        self._title = QLabel("未在播放")
        self._title.setObjectName("musicTitle")
        self._meta = QLabel("从「我的音乐」或搜索结果开始播放")
        self._meta.setObjectName("musicMeta")
        info.addWidget(self._title)
        info.addWidget(self._meta)
        holder = QWidget()
        holder.setLayout(info)
        holder.setMinimumWidth(200)
        layout.addWidget(holder, 2)

        self._prev = QPushButton("⏮")
        self._prev.setObjectName("playerButton")
        self._prev.setToolTip("上一首")
        self._prev.clicked.connect(player.previous)
        self._play = QPushButton("▶")
        self._play.setObjectName("playerButtonPrimary")
        self._play.setToolTip("播放 / 暂停")
        self._play.clicked.connect(player.toggle_pause)
        self._next = QPushButton("⏭")
        self._next.setObjectName("playerButton")
        self._next.setToolTip("下一首")
        self._next.clicked.connect(lambda: player.next(autoplay=True))
        for button in (self._prev, self._play, self._next):
            button.setFixedWidth(44)
            layout.addWidget(button)

        self._position = QSlider(Qt.Orientation.Horizontal)
        self._position.setRange(0, 1000)
        self._position.sliderPressed.connect(self._on_slider_pressed)
        self._position.sliderReleased.connect(self._on_slider_released)
        self._position.sliderMoved.connect(self._on_slider_moved)
        layout.addWidget(self._position, 3)

        self._time = QLabel("00:00 / 00:00")
        self._time.setObjectName("musicMeta")
        self._time.setMinimumWidth(96)
        layout.addWidget(self._time)

        self._mode = QPushButton(PLAY_MODE_LABELS["order"])
        self._mode.setObjectName("playerButton")
        self._mode.setToolTip("播放模式：顺序 / 列表循环 / 单曲循环 / 随机")
        self._mode.clicked.connect(self._cycle_mode)
        layout.addWidget(self._mode)

        self._favorite = QPushButton("☆ 收藏")
        self._favorite.setObjectName("playerButton")
        self._favorite.clicked.connect(self._toggle_favorite)
        layout.addWidget(self._favorite)

        volume_label = QLabel("🔊")
        layout.addWidget(volume_label)
        self._volume = QSlider(Qt.Orientation.Horizontal)
        self._volume.setRange(0, 100)
        self._volume.setValue(player.volume)
        self._volume.setFixedWidth(90)
        self._volume.valueChanged.connect(player.set_volume)
        layout.addWidget(self._volume)

        player.trackChanged.connect(self._on_track_changed)
        player.positionChanged.connect(self._on_position)
        player.stateChanged.connect(self._on_state)
        player.modeChanged.connect(self._on_mode)
        player.errorOccurred.connect(self._on_error)

    # ---------- 播放器信号 ----------

    def _on_track_changed(self, track) -> None:  # noqa: ANN001
        self._current = track
        if track is None:
            self._title.setText("未在播放")
            self._meta.setText("从「我的音乐」或搜索结果开始播放")
            return
        self._title.setText(track.title or "(未知曲名)")
        parts = [track.artist or "未知歌手"]
        if track.album:
            parts.append(track.album)
        parts.append("在线试听" if not getattr(track, "id", 0) else PLAY_MODE_LABELS.get(self._player.mode, ""))
        self._meta.setText(" · ".join(p for p in parts if p))
        self._favorite.setText("★ 已收藏" if track.favorited else "☆ 收藏")

    def _on_position(self, position: int, duration: int) -> None:
        if duration:
            self._duration_ms = duration
        if not self._dragging:
            self._position.setValue(int(position * 1000 / self._duration_ms) if self._duration_ms else 0)
        self._time.setText(f"{format_duration(position)} / {format_duration(self._duration_ms)}")

    def _on_state(self, state: str) -> None:
        self._play.setText("⏸" if state == "playing" else "▶")

    def _on_mode(self, mode: str) -> None:
        self._mode.setText(PLAY_MODE_LABELS.get(mode, mode))

    def _on_error(self, message: str) -> None:
        self._meta.setText(f"⚠ {message}")

    def show_error(self, message: str) -> None:
        """由板块转发播放错误（含曲名，便于定位是哪个文件有问题）。"""
        self._meta.setText(f"⚠ {message}")
        self._title.setToolTip(message)

    def set_preview_mode(self, preview: bool) -> None:
        """在线试听时禁用收藏（试听曲目尚未入库）。"""
        self._favorite.setEnabled(not preview)
        if preview:
            self._favorite.setText("☆ 试听中")

    # ---------- 控件 ----------

    def _on_slider_pressed(self) -> None:
        self._dragging = True

    def _on_slider_released(self) -> None:
        self._dragging = False
        if self._duration_ms:
            self._player.seek(int(self._position.value() * self._duration_ms / 1000))

    def _on_slider_moved(self, value: int) -> None:
        if self._duration_ms:
            self._time.setText(
                f"{format_duration(int(value * self._duration_ms / 1000))} / {format_duration(self._duration_ms)}"
            )

    def _cycle_mode(self) -> None:
        self._player.cycle_mode()

    def _toggle_favorite(self) -> None:
        if self._current is None:
            return
        self.favoriteToggled.emit()

    def refresh_favorite(self, favorited: bool) -> None:
        self._favorite.setText("★ 已收藏" if favorited else "☆ 收藏")


class MusicBoardPage(QWidget):
    """墨软乐库板块：五个子页 + 常驻播放条。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._toaster = Toaster(self)
        self._library = app_context.music_library()
        self._storage = app_context.music_storage()
        self._player = app_context.music_player()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        nav = QWidget()
        nav_row = QHBoxLayout(nav)
        nav_row.setContentsMargins(16, 8, 16, 4)
        nav_row.setSpacing(8)
        self._nav_buttons: dict[str, QPushButton] = {}
        for key, label in (
            (SEARCH_KEY, "🔍 搜索下载"),
            (LIBRARY_KEY, "🎵 我的音乐"),
            (PLAYLIST_KEY, "📁 歌单收藏"),
            (CONVERT_KEY, "🔄 格式转换"),
            (HISTORY_KEY, "🕘 播放历史"),
        ):
            button = make_nav_button(label)
            button.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self._nav_buttons[key] = button
            nav_row.addWidget(button)

        settings_button = make_nav_button("⚙ 板块设置")
        settings_button.setToolTip("打开「设置 → 乐库」：下载目录、音源启用、Jamendo 凭据")
        settings_button.clicked.connect(self._open_board_settings)
        nav_row.addWidget(settings_button)
        nav_row.addStretch(1)
        layout.addWidget(nav)

        self._stack = QStackedWidget()
        self._search = MusicSearchPage(self._library, self._storage, self._toaster)
        self._library_page = MusicLibraryPage(self._library, self._storage, self._player, self._toaster)
        self._playlist_page = MusicPlaylistPage(self._library, self._storage, self._player, self._toaster)
        self._convert_page = MusicConvertPage(self._library, self._storage, self._toaster)
        self._history_page = MusicHistoryPage(self._library, self._storage, self._player, self._toaster)

        self._indexes = {
            SEARCH_KEY: self._stack.addWidget(self._search),
            LIBRARY_KEY: self._stack.addWidget(self._library_page),
            PLAYLIST_KEY: self._stack.addWidget(self._playlist_page),
            CONVERT_KEY: self._stack.addWidget(self._convert_page),
            HISTORY_KEY: self._stack.addWidget(self._history_page),
        }
        layout.addWidget(self._stack, 1)

        self._bar = MusicPlayerBar(self._player)
        self._bar.favoriteToggled.connect(self._toggle_current_favorite)
        layout.addWidget(self._bar)

        # 数据联动
        self._search.tracksImported.connect(lambda _tracks: self._refresh_all())
        self._library_page.playRequested.connect(self._play_tracks)
        self._library_page.libraryChanged.connect(self._refresh_all)
        self._playlist_page.playRequested.connect(self._play_tracks_with_mode)
        self._playlist_page.libraryChanged.connect(self._refresh_all)
        self._history_page.playRequested.connect(self._play_tracks)
        self._player.trackChanged.connect(self._on_track_changed)
        self._player.durationKnown.connect(self._on_duration_known)
        self._player.errorOccurred.connect(self._on_player_error)

        self.show_page(SEARCH_KEY)

    # ---------- 导航 ----------

    def show_page(self, key: str) -> None:
        index = self._indexes.get(key)
        if index is None:
            return
        self._stack.setCurrentIndex(index)
        for nav_key, button in self._nav_buttons.items():
            set_nav_active(button, nav_key == key)
        if key == LIBRARY_KEY:
            self._library_page.reload()
        elif key == PLAYLIST_KEY:
            self._playlist_page.reload()
        elif key == HISTORY_KEY:
            self._history_page.reload()
        elif key == CONVERT_KEY:
            self._convert_page.reload()

    def on_shown(self) -> None:
        """主壳切换到本板块时刷新当前子页。"""
        for page_key, index in self._indexes.items():
            if index == self._stack.currentIndex():
                self.show_page(page_key)
                break

    def _open_board_settings(self) -> None:
        """打开统一设置对话框的「乐库」页（设置按板块区分）。"""
        from modu_workbench.ui_kit.settings import SettingsDialog

        SettingsDialog(self, initial="music").exec()
        self._search.reload_sources()

    # ---------- 播放联动 ----------

    def _play_tracks(self, tracks: list, start_index: int) -> None:
        self._start_playback(tracks, start_index, None)

    def _play_tracks_with_mode(self, tracks: list, start_index: int, mode: str) -> None:
        self._start_playback(tracks, start_index, mode)

    def _start_playback(self, tracks: list, start_index: int, mode: str | None) -> None:
        playable = [track for track in tracks if track.exists]
        if not playable:
            self._toaster.error("没有可播放的文件（可能已丢失）")
            return
        if mode:
            self._player.set_mode(mode)
        index = start_index if 0 <= start_index < len(playable) else 0
        self._player.set_queue(playable, index, autoplay=True)
        self._toaster.info(f"开始播放：{playable[index].display()}")

    def _on_track_changed(self, track) -> None:  # noqa: ANN001
        if track is None:
            return
        is_preview = self._player.is_preview or not getattr(track, "id", 0)
        self._bar.set_preview_mode(bool(is_preview))
        if is_preview:
            return   # 在线试听不入库、不记历史
        try:
            self._library.record_play(track.id)
        except Exception:  # noqa: BLE001
            pass
        self._history_page.reload()
        self._bar.refresh_favorite(track.favorited)

    def _on_duration_known(self, track_id: int, duration_ms: int) -> None:
        """播放器补全缺失时长后写回曲库（导入的本地文件也能显示正确时长）。"""
        if self._library.update_duration(track_id, duration_ms):
            self._library_page.reload()

    def _on_player_error(self, message: str) -> None:
        self._toaster.error(message)
        self._bar.show_error(message)

    def _toggle_current_favorite(self) -> None:
        track = self._player.current
        if track is None:
            return
        favorited = self._library.toggle_favorite(track.id)
        track.favorited = favorited
        self._bar.refresh_favorite(favorited)
        self._refresh_all()
        self._toaster.success("已收藏" if favorited else "已取消收藏")

    # ---------- 刷新 ----------

    def _refresh_all(self) -> None:
        self._library_page.reload()
        self._playlist_page.reload()
        self._history_page.reload()

    def shutdown(self) -> None:
        self._search.shutdown()
        self._playlist_page.shutdown()
        self._convert_page.shutdown()
        self._player.stop()
