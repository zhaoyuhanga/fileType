"""墨读音乐：歌单页（歌单/收藏/分类管理，支持顺序、循环、随机播放与待下载项）。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.music import (
    MusicLibrary,
    MusicPlayer,
    MusicStorage,
    Track,
    format_duration,
)
from modu_workbench.services.config import music_download_dir
from modu_workbench.ui_kit.toast import Toaster

from .music_widgets import (
    DownloadWorker,
    ask_text,
    configure_table,
    fill_remote_row,
    fill_track_row,
    selected_track_ids,
)


class MusicPlaylistPage(QWidget):
    """歌单管理：左侧歌单列表，右侧曲目与「待下载」在线曲目。"""

    playRequested = Signal(list, int, str)   # (tracks, start_index, mode)
    libraryChanged = Signal()

    def __init__(self, library: MusicLibrary, storage: MusicStorage, player: MusicPlayer,
                 toaster: Toaster, parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._player = player
        self._toaster = toaster
        self._playlist_id: int | None = None
        self._tracks: list[Track] = []
        self._download: DownloadWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 12)
        layout.setSpacing(10)

        title = QLabel("歌单与收藏")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "歌单支持顺序 / 列表循环 / 单曲循环 / 随机播放；搜索结果可先加入歌单（显示为「待下载」），稍后一键下载。"
        )
        subtitle.setObjectName("pageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(6)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_playlist_changed)
        left_layout.addWidget(self._list, 1)
        left_buttons = QHBoxLayout()
        for label, handler in (("新建", self._create_playlist), ("重命名", self._rename_playlist),
                               ("删除", self._delete_playlist)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            left_buttons.addWidget(button)
        left_layout.addLayout(left_buttons)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(6)
        self._header = QLabel("未选择歌单")
        self._header.setObjectName("sectionTitle")
        right_layout.addWidget(self._header)

        self._table = configure_table(QTableWidget(), ["曲名", "歌手", "专辑", "时长", "格式", "收藏"])
        self._table.doubleClicked.connect(lambda _i: self._play(0, "order"))
        right_layout.addWidget(self._table, 2)

        self._pending_label = QLabel("待下载（0）")
        self._pending_label.setObjectName("readerStatus")
        right_layout.addWidget(self._pending_label)
        self._pending = configure_table(QTableWidget(), ["曲名", "歌手", "专辑", "时长", "音源"])
        right_layout.addWidget(self._pending, 1)

        actions = QHBoxLayout()
        for label, handler, obj in (
            ("播放", lambda: self._play(0, "order"), "primaryButton"),
            ("列表循环", lambda: self._play(0, "loop-all"), None),
            ("随机播放", lambda: self._play(0, "shuffle"), None),
            ("单曲循环", lambda: self._play(0, "loop-one"), None),
            ("移除选中", self._remove_selected, "dangerButton"),
            ("下载待下载项", self._download_pending, None),
            ("移除待下载", self._remove_pending, None),
            ("添加本地曲目…", self._add_local_tracks, None),
        ):
            button = QPushButton(label)
            if obj:
                button.setObjectName(obj)
            button.clicked.connect(handler)
            actions.addWidget(button)
        actions.addStretch(1)
        right_layout.addLayout(actions)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        right_layout.addWidget(self._progress)

        self._status = QLabel("就绪。")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        right_layout.addWidget(self._status)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        self.reload()

    # ---------- 数据 ----------

    def reload(self) -> None:
        current = self._playlist_id
        self._list.blockSignals(True)
        self._list.clear()
        for playlist in self._storage.list_playlists():
            label = f"{'★ ' if playlist.is_favorite else '♪ '}{playlist.name}（{playlist.track_count}"
            if playlist.pending_count:
                label += f"+{playlist.pending_count}"
            label += "）"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, playlist.id)
            self._list.addItem(item)
        self._list.blockSignals(False)

        target_row = 0
        if current is not None:
            for row in range(self._list.count()):
                if self._list.item(row).data(Qt.ItemDataRole.UserRole) == current:
                    target_row = row
                    break
        if self._list.count():
            self._list.setCurrentRow(target_row)
        else:
            self._playlist_id = None
            self._render()

    def _on_playlist_changed(self, row: int) -> None:
        item = self._list.item(row)
        self._playlist_id = int(item.data(Qt.ItemDataRole.UserRole)) if item else None
        self._render()

    def _render(self) -> None:
        if self._playlist_id is None:
            self._tracks = []
            self._table.setRowCount(0)
            self._pending.setRowCount(0)
            self._header.setText("未选择歌单")
            self._pending_label.setText("待下载（0）")
            return
        playlist = self._storage.get_playlist(self._playlist_id)
        name = playlist.name if playlist else "歌单"
        self._tracks = self._storage.list_playlist_tracks(self._playlist_id)
        self._table.setRowCount(len(self._tracks))
        for row, track in enumerate(self._tracks):
            fill_track_row(self._table, row, track,
                           ["title", "artist", "album", "duration", "format", "favorite"])
        remotes = self._storage.list_playlist_remotes(self._playlist_id)
        self._pending.setRowCount(len(remotes))
        for row, remote in enumerate(remotes):
            fill_remote_row(self._pending, row, remote)
        self._header.setText(f"歌单：{name}")
        self._pending_label.setText(f"待下载（{len(remotes)}）")
        total_ms = sum(t.duration_ms for t in self._tracks)
        self._status.setText(
            f"{len(self._tracks)} 首本地曲目 · 总时长 {format_duration(total_ms)} · 待下载 {len(remotes)} 首"
        )

    # ---------- 歌单维护 ----------

    def _create_playlist(self) -> None:
        name = ask_text(self, "新建歌单", "歌单名称：")
        if not name:
            return
        playlist_id = self._storage.create_playlist(name)
        self._playlist_id = playlist_id
        self.reload()
        self.libraryChanged.emit()
        self._toaster.success(f"已创建歌单「{name}」")

    def _rename_playlist(self) -> None:
        if self._playlist_id is None:
            return
        playlist = self._storage.get_playlist(self._playlist_id)
        if playlist is None:
            return
        if playlist.is_favorite:
            self._toaster.info("「我的收藏」不支持重命名")
            return
        name = ask_text(self, "重命名歌单", "新名称：", playlist.name)
        if not name:
            return
        if self._storage.rename_playlist(self._playlist_id, name):
            self.reload()
            self.libraryChanged.emit()
            self._toaster.success("已重命名")
        else:
            self._toaster.error("重命名失败（名称可能重复）")

    def _delete_playlist(self) -> None:
        if self._playlist_id is None:
            return
        playlist = self._storage.get_playlist(self._playlist_id)
        if playlist is None:
            return
        if playlist.is_favorite:
            self._toaster.info("「我的收藏」不可删除")
            return
        self._storage.delete_playlist(self._playlist_id)
        self._playlist_id = None
        self.reload()
        self.libraryChanged.emit()
        self._toaster.success(f"已删除歌单「{playlist.name}」")

    def _add_local_tracks(self) -> None:
        if self._playlist_id is None:
            self._toaster.info("请先选择歌单")
            return
        all_tracks = self._storage.list_tracks(order="title")
        if not all_tracks:
            self._toaster.info("曲库为空，请先导入或下载音乐")
            return
        labels = [f"{t.display()}（{t.format.upper()}）" for t in all_tracks]
        item, ok = QInputDialog.getItem(self, "加入歌单", "选择曲目：", labels, 0, False)
        if not ok or not item:
            return
        index = labels.index(item)
        added = self._storage.add_to_playlist(self._playlist_id, [all_tracks[index].id])
        self._render()
        self.libraryChanged.emit()
        self._toaster.success(f"已加入 {added} 首")

    def _remove_selected(self) -> None:
        if self._playlist_id is None:
            return
        ids = selected_track_ids(self._table)
        if not ids:
            self._toaster.info("请先选择要移除的曲目")
            return
        self._storage.remove_from_playlist(self._playlist_id, ids)
        self._render()
        self.libraryChanged.emit()
        self._toaster.success(f"已从歌单移除 {len(ids)} 首")

    # ---------- 待下载项 ----------

    def _remove_pending(self) -> None:
        if self._playlist_id is None:
            return
        rows = {index.row() for index in self._pending.selectionModel().selectedRows()}
        if not rows:
            self._toaster.info("请先选择待下载项")
            return
        remotes = self._storage.list_playlist_remotes(self._playlist_id)
        keys = [remotes[row].extra.get("remote_key", "") for row in sorted(rows) if row < len(remotes)]
        self._storage.remove_playlist_remotes(self._playlist_id, keys)
        self._render()
        self.libraryChanged.emit()

    def _download_pending(self) -> None:
        if self._playlist_id is None:
            return
        remotes = self._storage.list_playlist_remotes(self._playlist_id)
        if not remotes:
            self._toaster.info("该歌单没有待下载项")
            return
        rows = {index.row() for index in self._pending.selectionModel().selectedRows()}
        if rows:
            remotes = [remotes[row] for row in sorted(rows) if row < len(remotes)]
        dest = str(music_download_dir())
        self._download = DownloadWorker(remotes, dest, parent=self)
        self._download.progressed.connect(self._on_progress)
        self._download.finishedAll.connect(self._on_downloaded)
        self._download.failed.connect(lambda msg: self._toaster.error(f"下载失败：{msg}"))
        self._download.finished.connect(self._on_download_finished)
        self._progress.setValue(0)
        self._status.setText(f"开始下载 {len(remotes)} 首待下载曲目…")
        self._download.start()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self._progress.setValue(min(100, int(done * 100 / total)) if total else 0)
        self._status.setText(message)

    def _on_downloaded(self, results: list) -> None:
        ok = [r for r in results if r.ok]
        imported = self._library.import_download_results(ok)
        if self._playlist_id is not None and imported:
            self._storage.add_to_playlist(self._playlist_id, [t.id for t in imported])
            keys = [r.track.extra.get("remote_key", f"{r.track.source}:{r.track.remote_id}") for r in ok]
            self._storage.remove_playlist_remotes(self._playlist_id, keys)
        self._progress.setValue(100)
        self._render()
        self.libraryChanged.emit()
        self._toaster.success(f"已下载并入歌单：{len(imported)} 首")

    def _on_download_finished(self) -> None:
        self._download = None

    # ---------- 播放 ----------

    def _play(self, start_index: int, mode: str) -> None:
        if not self._tracks:
            self._toaster.info("该歌单还没有本地曲目（可先下载待下载项）")
            return
        playable = [t for t in self._tracks if t.exists]
        if not playable:
            self._toaster.error("歌单内曲目文件均已丢失")
            return
        index = start_index if 0 <= start_index < len(playable) else 0
        self.playRequested.emit(playable, index, mode)

    def shutdown(self) -> None:
        if self._download is not None and self._download.isRunning():
            self._download.cancel()
            self._download.wait(3000)
