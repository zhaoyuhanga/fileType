"""墨软乐库：我的音乐（本地曲库）与音频格式转换页。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.music import (
    MUSIC_TARGETS,
    MusicLibrary,
    MusicPlayer,
    MusicStorage,
    Track,
    scan_audio_files,
)
from modu_workbench.services.config import music_dir
from modu_workbench.ui_kit.toast import Toaster

from .widgets import (
    ConvertWorker,
    PlaylistPicker,
    action_ids,
    ask_text,
    attach_context_menu,
    configure_table,
    fill_track_row,
    row_track_id,
    selected_track_ids,
)

# 曲目表列序（用于「收藏」列点击）
FAVORITE_COLUMN = 6


class MusicLibraryPage(QWidget):
    """本地曲库：导入、筛选、收藏、分类、加入歌单、播放。"""

    playRequested = Signal(list, int)   # (tracks, start_index)
    libraryChanged = Signal()

    def __init__(self, library: MusicLibrary, storage: MusicStorage, player: MusicPlayer,
                 toaster: Toaster, parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._player = player
        self._toaster = toaster
        self._tracks: list[Track] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 12)
        layout.setSpacing(10)

        title = QLabel("我的音乐")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel("导入本地音频（mp3 / flac / m4a / wav / aac / ogg / opus / wma），支持收藏、分类与歌单管理。")
        subtitle.setObjectName("pageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # ---- 过滤栏 ----
        filter_row = QHBoxLayout()
        self._keyword = QLineEdit()
        self._keyword.setObjectName("searchBox")
        self._keyword.setPlaceholderText("筛选：曲名 / 歌手 / 专辑")
        self._keyword.textChanged.connect(lambda _text: self.reload())
        self._artist = QComboBox()
        self._artist.currentIndexChanged.connect(lambda _i: self.reload())
        self._category = QComboBox()
        self._category.currentIndexChanged.connect(lambda _i: self.reload())
        self._favorite_only = QComboBox()
        self._favorite_only.addItem("全部曲目", False)
        self._favorite_only.addItem("仅收藏", True)
        self._favorite_only.currentIndexChanged.connect(lambda _i: self.reload())
        self._order = QComboBox()
        for key, label in (("added", "按加入时间"), ("title", "按曲名"), ("artist", "按歌手"), ("played", "按最近播放")):
            self._order.addItem(label, key)
        self._order.currentIndexChanged.connect(lambda _i: self.reload())

        filter_row.addWidget(self._keyword, 1)
        filter_row.addWidget(self._artist)
        filter_row.addWidget(self._category)
        filter_row.addWidget(self._favorite_only)
        filter_row.addWidget(self._order)
        layout.addLayout(filter_row)

        # ---- 曲目表 ----
        self._table = configure_table(
            QTableWidget(), ["曲名", "歌手", "专辑", "时长", "格式", "分类", "收藏", "播放次数"]
        )
        self._table.doubleClicked.connect(lambda _index: self.play_selected())
        # 点击「收藏」列即可单独切换该曲目收藏（无需先选中再点按钮）
        self._table.itemClicked.connect(self._on_item_clicked)
        attach_context_menu(self._table, self._row_menu)
        layout.addWidget(self._table, 1)

        # ---- 操作栏 ----
        actions = QHBoxLayout()
        buttons = (
            ("播放选中", self.play_selected, None),
            ("随机播放", self.play_shuffle, None),
            ("导入文件…", self._import_files, None),
            ("导入文件夹…", self._import_folder, None),
            ("收藏/取消", self._toggle_favorite, None),
            ("设置分类…", self._set_category, None),
            ("加入歌单…", self._add_to_playlist, None),
            ("从库中移除", self._remove_tracks, "dangerButton"),
            ("刷新时长", self._refresh_durations, None),
            ("打开音乐目录", self._open_dir, None),
        )
        for label, handler, object_name in buttons:
            button = QPushButton(label)
            if object_name:
                button.setObjectName(object_name)
            button.clicked.connect(handler)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._status = QLabel("就绪。")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

    # ---------- 数据 ----------

    def reload(self) -> None:
        def current_data(combo: QComboBox) -> str:
            value = combo.currentData()
            return "" if value in (None, "") else str(value)

        keyword = self._keyword.text().strip()
        artist = current_data(self._artist)
        category = current_data(self._category)
        favorite_only = bool(self._favorite_only.currentData())
        order = current_data(self._order) or "added"

        self._rebuild_filter_combos(artist, category)

        self._tracks = self._storage.list_tracks(
            keyword=keyword, artist=artist, category=category,
            favorite_only=favorite_only, order=order,
        )
        self._table.setRowCount(len(self._tracks))
        for row, track in enumerate(self._tracks):
            fill_track_row(
                self._table, row, track,
                ["title", "artist", "album", "duration", "format", "category", "favorite", "played"],
            )
        missing = [t for t in self._tracks if not t.exists]
        message = f"共 {len(self._tracks)} 首"
        if missing:
            message += f"；其中 {len(missing)} 首文件已丢失（播放时会跳过）"
        self._status.setText(message)

    def _rebuild_filter_combos(self, artist: str, category: str) -> None:
        for combo, items, current, all_label in (
            (self._artist, self._storage.list_artists(), artist, "全部歌手"),
            (self._category, self._storage.list_categories(), category, "全部分类"),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(all_label, "")
            for name, count in items:
                combo.addItem(f"{name}（{count}）", name)
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    def tracks(self) -> list[Track]:
        return list(self._tracks)

    # ---------- 播放 ----------

    def play_selected(self) -> None:
        ids = set(selected_track_ids(self._table))
        if not ids:
            self._toaster.info("请先选择曲目")
            return
        playable = [t for t in self._tracks if t.id in ids]
        if not playable:
            self._toaster.error("选中的曲目文件已丢失")
            return
        self.playRequested.emit(playable, 0)

    def play_shuffle(self) -> None:
        if not self._tracks:
            self._toaster.info("曲库为空，先导入音乐")
            return
        self.playRequested.emit(list(self._tracks), -1)

    def play_track(self, track: Track) -> None:
        self.playRequested.emit([track], 0)

    # ---------- 导入 / 维护 ----------

    def _import_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择音频文件", "",
            "音频文件 (*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus *.wma);;所有文件 (*.*)",
        )
        if not files:
            return
        self._import(list(files))

    def _import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择音频文件夹（递归导入）", str(music_dir()))
        if not folder:
            return
        self._import([folder])

    def _import(self, paths: list[str]) -> None:
        files = scan_audio_files(paths)
        if not files:
            self._toaster.info("未找到可导入的音频文件")
            return
        tracks = self._library.import_paths(files)
        self.reload()
        self.libraryChanged.emit()
        self._toaster.success(f"已导入 {len(tracks)} 首")
        self._status.setText(f"已导入 {len(tracks)} 首（共扫描 {len(files)} 个文件）")

    def _toggle_favorite(self) -> None:
        ids = action_ids(self._table)
        if not ids:
            self._toaster.info("请先选择（或勾选）要收藏的曲目")
            return
        track = self._storage.get_track(ids[0])
        if track is None:
            return
        target = not track.favorited
        self._storage.set_favorite(ids, target)
        if not target:
            self._storage.remove_from_playlist(self._storage.favorite_playlist_id, ids)
        self.reload()
        self.libraryChanged.emit()
        self._toaster.success(("已收藏 " if target else "已取消收藏 ") + f"{len(ids)} 首")

    def _toggle_favorite_one(self, track_id: int, favorited: bool | None = None) -> None:
        """切换单曲收藏（收藏列点击 / 右键菜单）。"""
        track = self._storage.get_track(track_id)
        if track is None:
            return
        target = (not track.favorited) if favorited is None else favorited
        self._storage.set_favorite([track_id], target)
        if not target:
            self._storage.remove_from_playlist(self._storage.favorite_playlist_id, [track_id])
        self.reload()
        self.libraryChanged.emit()
        self._toaster.success(f"{'已收藏' if target else '已取消收藏'}：{track.display()}")

    def _on_item_clicked(self, item) -> None:  # noqa: ANN001
        if item.column() != FAVORITE_COLUMN:
            return
        track_id = row_track_id(self._table, item.row())
        if track_id is not None:
            self._toggle_favorite_one(track_id)

    def _row_menu(self, row: int) -> list[tuple[str, object]]:
        track_id = row_track_id(self._table, row)
        if track_id is None:
            return []
        track = self._storage.get_track(track_id)
        if track is None:
            return []
        return [
            ("▶ 播放这首", lambda: self._play_one(track_id)),
            ("★ 取消收藏" if track.favorited else "☆ 收藏这首",
             lambda: self._toggle_favorite_one(track_id)),
            ("设置分类…", lambda: self._set_category_for([track_id])),
            ("加入歌单…", lambda: self._add_to_playlist_for([track_id])),
            ("从库中移除", lambda: self._remove_tracks_for([track_id])),
            ("打开文件位置", lambda: self._open_file_location(track.path)),
        ]

    def _play_one(self, track_id: int) -> None:
        track = self._storage.get_track(track_id)
        if track is None:
            return
        if not track.exists:
            self._toaster.error(f"文件已丢失：{track.path}")
            return
        self.playRequested.emit([track], 0)

    def _open_file_location(self, path: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        target = Path(path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent if target.parent.exists() else Path.home())))

    def _set_category(self) -> None:
        ids = action_ids(self._table)
        if not ids:
            self._toaster.info("请先选择要设置分类的曲目")
            return
        self._set_category_for(ids)

    def _set_category_for(self, ids: list[int]) -> None:
        categories = [name for name, _count in self._storage.list_categories()]
        current = categories[0] if categories else ""
        category = ask_text(self, "设置分类", "分类名称（如：流行 / 古典 / 通勤）：", current)
        if not category:
            return
        count = self._storage.set_category(ids, category)
        self.reload()
        self.libraryChanged.emit()
        self._toaster.success(f"已为 {count} 首设置分类「{category}」")

    def _add_to_playlist(self) -> None:
        ids = action_ids(self._table)
        if not ids:
            self._toaster.info("请先选择要加入歌单的曲目")
            return
        self._add_to_playlist_for(ids)

    def _add_to_playlist_for(self, ids: list[int]) -> None:
        picker = PlaylistPicker(self._storage, self, "加入歌单")
        from PySide6.QtWidgets import QDialog

        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        playlist_id = picker.selected_playlist_id()
        if playlist_id is None:
            return
        added = self._storage.add_to_playlist(playlist_id, ids)
        playlist = self._storage.get_playlist(playlist_id)
        name = playlist.name if playlist else "歌单"
        self.libraryChanged.emit()
        self._toaster.success(f"已加入「{name}」：新增 {added} 首")

    def _remove_tracks(self) -> None:
        ids = action_ids(self._table)
        if not ids:
            self._toaster.info("请先选择要移除的曲目")
            return
        self._remove_tracks_for(ids)

    def _remove_tracks_for(self, ids: list[int]) -> None:
        self._storage.delete_tracks(ids)
        self.reload()
        self.libraryChanged.emit()
        self._toaster.success(f"已从曲库移除 {len(ids)} 首（不会删除磁盘文件）")

    def _refresh_durations(self) -> None:
        updated = self._library.refresh_durations()
        self.reload()
        self._toaster.info(f"已补齐 {updated} 首时长信息" + ("" if updated else "（需要 ffprobe 支持）"))

    def _open_dir(self) -> None:
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._library.music_dir)))


class MusicConvertPage(QWidget):
    """音频格式转换：选择曲目 → 目标格式 → 批量转换。"""

    def __init__(self, library: MusicLibrary, storage: MusicStorage, toaster: Toaster,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._toaster = toaster
        self._tracks: list[Track] = []
        self._worker: ConvertWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 12)
        layout.setSpacing(10)

        title = QLabel("音乐格式转换")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "批量转换音频格式（mp3 / m4a / wav / flac / aac / ogg / opus / wma）。"
            "转换依赖 ffmpeg：未检测到时请安装 ffmpeg 或用环境变量 MODU_FFMPEG 指定。"
        )
        subtitle.setObjectName("pageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        row = QHBoxLayout()
        self._keyword = QLineEdit()
        self._keyword.setObjectName("searchBox")
        self._keyword.setPlaceholderText("筛选曲目…")
        self._keyword.textChanged.connect(lambda _t: self.reload())
        row.addWidget(self._keyword, 1)
        reload_button = QPushButton("刷新")
        reload_button.clicked.connect(self.reload)
        row.addWidget(reload_button)
        layout.addLayout(row)

        self._table = configure_table(QTableWidget(), ["曲名", "歌手", "时长", "格式", "大小", "分类"])
        layout.addWidget(self._table, 1)

        options = QHBoxLayout()
        options.addWidget(QLabel("目标格式"))
        self._target = QComboBox()
        for fmt in MUSIC_TARGETS:
            self._target.addItem(fmt.upper(), fmt)
        options.addWidget(self._target)
        options.addWidget(QLabel("输出到"))
        self._out = QLineEdit(str(music_dir()))
        options.addWidget(self._out, 1)
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_dir)
        options.addWidget(pick)
        self._start = QPushButton("开始转换")
        self._start.setObjectName("primaryButton")
        self._start.clicked.connect(self._start_convert)
        options.addWidget(self._start)
        self._cancel = QPushButton("取消")
        self._cancel.setObjectName("dangerButton")
        self._cancel.clicked.connect(self._cancel_convert)
        self._cancel.setEnabled(False)
        options.addWidget(self._cancel)
        layout.addLayout(options)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._status = QLabel("就绪。勾选曲目后开始转换。")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self.reload()

    def reload(self) -> None:
        self._tracks = self._storage.list_tracks(keyword=self._keyword.text().strip(), order="title")
        self._table.setRowCount(len(self._tracks))
        for row, track in enumerate(self._tracks):
            fill_track_row(self._table, row, track, ["title", "artist", "duration", "format", "size", "category"])
        self._status.setText(f"共 {len(self._tracks)} 首可选")

    def _pick_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self._out.text())
        if folder:
            self._out.setText(folder)

    def _start_convert(self) -> None:
        ids = selected_track_ids(self._table)
        if not ids:
            ids = [t.id for t in self._tracks]
        if not ids:
            self._toaster.info("请先选择要转换的曲目")
            return
        target = self._target.currentData()
        out_dir = self._out.text().strip() or str(music_dir())
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        self._worker = ConvertWorker(self._library, ids, target, out_dir, self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.finishedAll.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_thread_finished)
        self._progress.setValue(0)
        self._start.setEnabled(False)
        self._cancel.setEnabled(True)
        self._status.setText(f"开始转换 {len(ids)} 首 → {str(target).upper()}")
        self._worker.start()

    def _cancel_convert(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._status.setText("正在取消…")

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self._progress.setValue(min(100, int(done * 100 / total)) if total else 0)
        self._status.setText(message)

    def _on_finished(self, outputs: list, errors: list) -> None:
        self._progress.setValue(100)
        if outputs:
            self.reload()   # 刷新列表（内部会重置状态文本）
        message = f"转换完成：成功 {len(outputs)}，失败 {len(errors)}"
        if errors:
            message += "；" + "；".join(errors[:3])
        self._status.setText(message)
        if outputs:
            self._toaster.success(f"已转换 {len(outputs)} 首")
        else:
            self._toaster.error("转换失败，请检查 ffmpeg 是否可用")

    def _on_failed(self, message: str) -> None:
        self._status.setText(f"转换失败：{message}")
        self._toaster.error(f"转换失败：{message}")

    def _on_thread_finished(self) -> None:
        self._worker = None
        self._start.setEnabled(True)
        self._cancel.setEnabled(False)

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
