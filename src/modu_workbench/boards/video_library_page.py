"""墨软影视 · 我的视频：本地库、分类收藏、导入导出与视频格式转换。

覆盖诉求 3 / 4：
- 分类与收藏（可新建分类、把条目归类、一键收藏）；
- 视频格式转换（含「提取音频」），转换在后台线程执行、可取消。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.video import VIDEO_TARGETS, VideoLibrary, VideoStorage
from modu_workbench.ui_kit.toast import Toaster

from .video_widgets import (
    CollectionPicker,
    ConvertWorker,
    action_video_ids,
    attach_context_menu,
    configure_table,
    fill_video_row,
    make_kind_combo,
    row_video_id,
    select_all,
    video_download_dir_pref,
    video_settings,
)

COLUMNS = ["片名", "类型", "集", "画质", "时长", "大小", "来源", "本地"]


class VideoLibraryPage(QWidget):
    """我的视频：本地条目 + 分类收藏 + 转换。"""

    playRequested = Signal(list, int)   # List[Video], start_index
    downloadRequested = Signal(list)    # List[Video]：把在线条目交给板块下载
    libraryChanged = Signal()
    importRequested = Signal()

    def __init__(self, library: VideoLibrary, storage: VideoStorage, toaster: Toaster,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._toaster = toaster
        self._videos = []
        self._convert_worker: ConvertWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("我的视频")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self._open_dir_button = QPushButton("打开影视库目录")
        self._open_dir_button.clicked.connect(self._open_library_dir)
        header.addWidget(self._open_dir_button)
        layout.addLayout(header)

        subtitle = QLabel(
            "这里同时管理「已下载到本地」和「只入库未下载（可在线播放）」的条目；"
            "可新建分类归类、收藏，或把本地文件转换成其他视频格式 / 提取音频。"
        )
        subtitle.setObjectName("pageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # ---- 过滤条 ----
        filter_row = QHBoxLayout()
        self._keyword = QLineEdit()
        self._keyword.setObjectName("searchBox")
        self._keyword.setPlaceholderText("搜索片名 / 演员 / 标签")
        self._keyword.textChanged.connect(lambda _text: self.reload())
        filter_row.addWidget(self._keyword, 1)

        self._kind = make_kind_combo(include_all=True)
        self._kind.currentIndexChanged.connect(lambda _index: self.reload())
        filter_row.addWidget(self._kind)

        self._collection = QComboBox()
        self._collection.currentIndexChanged.connect(lambda _index: self.reload())
        filter_row.addWidget(self._collection)

        self._order = QComboBox()
        for key, label in (("added", "最近加入"), ("title", "按片名"), ("played", "最近播放")):
            self._order.addItem(label, key)
        self._order.currentIndexChanged.connect(lambda _index: self.reload())
        filter_row.addWidget(self._order)
        layout.addLayout(filter_row)

        # ---- 列表 ----
        self._table = configure_table(QTableWidget(), COLUMNS)
        self._table.doubleClicked.connect(lambda _index: self._play_action())
        attach_context_menu(self._table, self._row_menu)
        layout.addWidget(self._table, 1)

        # ---- 操作栏 ----
        actions = QHBoxLayout()
        self._check_all = QPushButton("全选")
        self._check_all.clicked.connect(lambda: select_all(self._table, True))
        self._check_none = QPushButton("取消全选")
        self._check_none.clicked.connect(lambda: select_all(self._table, False))
        self._play_button = QPushButton("▶ 播放")
        self._play_button.setObjectName("primaryButton")
        self._play_button.clicked.connect(self._play_action)
        self._import_button = QPushButton("导入本地影片…")
        self._import_button.clicked.connect(self._import_local)
        self._collect_button = QPushButton("加入分类…")
        self._collect_button.clicked.connect(self._add_to_collection)
        self._favorite_button = QPushButton("★ 收藏 / 取消")
        self._favorite_button.clicked.connect(self._toggle_favorite)
        self._category_button = QPushButton("设置分类名…")
        self._category_button.clicked.connect(self._set_category)
        self._delete_button = QPushButton("删除条目")
        self._delete_button.setObjectName("dangerButton")
        self._delete_button.clicked.connect(self._delete_videos)
        for widget in (self._check_all, self._check_none, self._play_button, self._import_button,
                       self._collect_button, self._favorite_button, self._category_button,
                       self._delete_button):
            actions.addWidget(widget)
        actions.addStretch(1)
        layout.addLayout(actions)

        # ---- 转换 ----
        convert_row = QHBoxLayout()
        convert_row.addWidget(QLabel("转换为"))
        self._target = QComboBox()
        for target in VIDEO_TARGETS:
            label = f"提取 {target.upper()} 音频" if target in ("mp3", "m4a", "wav") else target.upper()
            self._target.addItem(label, target)
        convert_row.addWidget(self._target)
        self._convert_button = QPushButton("开始转换")
        self._convert_button.clicked.connect(self._start_convert)
        convert_row.addWidget(self._convert_button)
        self._cancel_button = QPushButton("取消转换")
        self._cancel_button.setObjectName("dangerButton")
        self._cancel_button.clicked.connect(self._cancel_convert)
        self._cancel_button.setEnabled(False)
        convert_row.addWidget(self._cancel_button)
        convert_row.addStretch(1)
        hint = QLabel("转换需要 ffmpeg（未安装时请设置 MODU_FFMPEG）")
        hint.setObjectName("readerStatus")
        convert_row.addWidget(hint)
        layout.addLayout(convert_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._status = QLabel("就绪。")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self.reload()

    # ------------------------------------------------------------------ 数据

    def reload(self) -> None:
        playlist_id = self._collection.currentData()
        self._reload_collections(keep=playlist_id)
        playlist_id = self._collection.currentData()

        keyword = self._keyword.text().strip()
        kind = self._kind.currentData() or ""
        order = self._order.currentData() or "added"

        if playlist_id not in (None, "", "all"):
            videos = self._storage.list_playlist_videos(int(playlist_id))
            if keyword:
                videos = [v for v in videos if keyword.lower() in f"{v.title}{v.actors}{v.tags}".lower()]
            if kind and kind != "all":
                videos = [v for v in videos if v.kind == kind]
        else:
            videos = self._storage.list_videos(
                keyword=keyword, kind="" if kind == "all" else kind, order=order
            )

        self._videos = videos
        self._table.setRowCount(len(videos))
        for row, video in enumerate(videos):
            fill_video_row(self._table, row, video)
        select_all(self._table, False)
        self._status.setText(f"共 {len(videos)} 个条目（本板块同时管理本地与在线条目）")
        self._update_buttons()

    def _reload_collections(self, keep=None) -> None:  # noqa: ANN001
        self._collection.blockSignals(True)
        self._collection.clear()
        self._collection.addItem("全部（含在线）", "all")
        for playlist in self._storage.list_playlists():
            label = f"{'★ ' if playlist.is_favorite else ''}{playlist.name}（{playlist.video_count}）"
            self._collection.addItem(label, playlist.id)
        index = self._collection.findData(keep)
        self._collection.setCurrentIndex(index if index >= 0 else 0)
        self._collection.blockSignals(False)

    # ------------------------------------------------------------------ 动作

    def _row_menu(self, row: int) -> list[tuple[str, object]]:
        video_id = row_video_id(self._table, row)
        if video_id is None:
            return []
        video = self._library.storage.get_video(video_id)
        if video is None:
            return []
        entries = [("▶ 播放", lambda: self._play_action(row)),
                   ("★ 收藏 / 取消", lambda: self._toggle_favorite([video_id])),
                   ("加入分类…", lambda: self._add_to_collection([video_id]))]
        if video.online_only:
            entries.append(("⬇ 下载到本地（重新解析）", lambda: self._download_online(video)))
        else:
            entries.append(("📂 在文件夹中显示", lambda: self._reveal(video)))
        entries.append(("删除条目", lambda: self._delete_videos([video_id])))
        return entries

    def _target_ids(self) -> list[int]:
        ids = action_video_ids(self._table)
        if ids:
            return ids
        row = self._table.currentRow()
        video_id = row_video_id(self._table, row) if row >= 0 else None
        return [video_id] if video_id else []

    def _play_action(self, row: int | None = None) -> None:
        if row is not None and isinstance(row, int) and not isinstance(row, bool):
            video_id = row_video_id(self._table, row)
            ids = [video_id] if video_id else []
        else:
            ids = self._target_ids()
        if not ids:
            self._toaster.info("请先勾选或选中要播放的条目")
            return
        videos = [v for v in (self._library.storage.get_video(i) for i in ids) if v is not None]
        if not videos:
            return
        self.playRequested.emit(videos, 0)

    def _import_local(self) -> None:
        files, _filter = QFileDialog.getOpenFileNames(
            self, "选择本地影片", "", "视频文件 (*.mp4 *.mkv *.avi *.mov *.flv *.wmv *.webm *.m4v *.ts *.mpg *.rmvb)"
        )
        if not files:
            return
        self._toaster.info(f"正在导入 {len(files)} 个文件…")
        videos = self._library.import_and_copy(files)
        self._toaster.success(f"已导入 {len(videos)} 个影片")
        self.reload()
        self.libraryChanged.emit()

    def _add_to_collection(self, ids: list | None = None) -> None:
        ids = self._normalize_ids(ids)
        if not ids:
            self._toaster.info("请先勾选或选中条目")
            return
        picker = CollectionPicker(self._storage, self, "加入分类 / 收藏")
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        playlist_id = picker.selected_playlist_id()
        if playlist_id is None:
            return
        added = self._storage.add_to_playlist(playlist_id, ids)
        playlist = self._storage.get_playlist(playlist_id)
        self._toaster.success(f"已加入「{playlist.name if playlist else '分类'}」：{added} 个")
        self.reload()

    def _normalize_ids(self, ids: list | None) -> list[int]:
        if ids is None or isinstance(ids, bool):
            return self._target_ids()
        return [int(item) for item in ids if not isinstance(item, bool)]

    def _toggle_favorite(self, ids: list | None = None) -> None:
        ids = self._normalize_ids(ids)
        if not ids:
            self._toaster.info("请先勾选或选中条目")
            return
        first = self._library.storage.get_video(ids[0])
        target = not (first.favorited if first else False)
        self._library.storage.set_favorite(ids, target)
        self._toaster.success("已收藏" if target else "已取消收藏")
        self.reload()

    def _set_category(self) -> None:
        ids = self._target_ids()
        if not ids:
            self._toaster.info("请先勾选或选中条目")
            return
        name, ok = QInputDialog.getText(self, "设置分类名", "分类名（留空则清空）：")
        if not ok:
            return
        self._library.set_category(ids, (name or "").strip())
        self.reload()

    def _delete_videos(self, ids: list | None = None) -> None:
        ids = self._normalize_ids(ids)
        if not ids:
            self._toaster.info("请先勾选或选中条目")
            return
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self, "删除条目",
            f"将删除 {len(ids)} 个条目记录。\n是否同时删除本地文件？\n"
            "（选择 No＝只删记录，保留文件）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return
        removed = self._library.delete_with_files(
            ids, remove_file=answer == QMessageBox.StandardButton.Yes
        )
        self._toaster.success(f"已删除 {removed} 个条目")
        self.reload()
        self.libraryChanged.emit()

    def _download_online(self, video) -> None:  # noqa: ANN001
        self._toaster.info("已提交下载任务…")
        self.downloadRequested.emit([video])

    def _reveal(self, video) -> None:  # noqa: ANN001
        path = Path(video.file_path)
        if not path.exists():
            self._toaster.error("文件不存在（可能已被移动或删除）")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _open_library_dir(self) -> None:
        from modu_workbench.services.config import video_download_dir

        target = Path(video_download_dir_pref() or video_download_dir())
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    # ------------------------------------------------------------------ 转换

    def _start_convert(self) -> None:
        ids = self._target_ids()
        if not ids:
            self._toaster.info("请先勾选或选中要转换的条目")
            return
        if self._convert_worker is not None:
            return
        target_format = self._target.currentData()
        output_dir = video_settings().value("video/convert_dir", "", type=str) or ""
        self._convert_worker = ConvertWorker(ids, target_format, output_dir, self)
        self._convert_worker.progressed.connect(self._on_convert_progress)
        self._convert_worker.finishedAll.connect(self._on_convert_finished)
        self._convert_worker.failed.connect(self._on_convert_failed)
        self._convert_worker.finished.connect(self._on_convert_thread_finished)
        self._progress.setValue(0)
        self._cancel_button.setEnabled(True)
        self._convert_button.setEnabled(False)
        self._status.setText(f"开始转换 {len(ids)} 个条目为 {str(target_format).upper()}…")
        self._convert_worker.start()

    def _cancel_convert(self) -> None:
        if self._convert_worker is not None:
            self._convert_worker.cancel()
            self._status.setText("正在取消转换…")

    def _on_convert_progress(self, done: int, total: int, message: str) -> None:
        self._progress.setValue(int(done * 100 / total) if total else 0)
        self._status.setText(message)

    def _on_convert_finished(self, outputs: list, errors: list) -> None:
        self._progress.setValue(100)
        message = f"转换完成：成功 {len(outputs)}，失败 {len(errors)}"
        if errors:
            message += "；" + "；".join(errors[:3])
        self._status.setText(message)
        if outputs:
            self._toaster.success(f"已转换 {len(outputs)} 个文件")
        else:
            self._toaster.error("转换失败：请确认已安装 ffmpeg")
        self.reload()

    def _on_convert_failed(self, message: str) -> None:
        self._status.setText(f"转换失败：{message}")
        self._toaster.error(f"转换失败：{message}")

    def _on_convert_thread_finished(self) -> None:
        self._convert_worker = None
        self._cancel_button.setEnabled(False)
        self._convert_button.setEnabled(True)

    # ------------------------------------------------------------------ 其它

    def _update_buttons(self) -> None:
        has_rows = len(self._videos) > 0
        for button in (self._play_button, self._collect_button, self._favorite_button,
                       self._category_button, self._delete_button, self._convert_button):
            button.setEnabled(has_rows)
        self._check_all.setEnabled(has_rows)
        self._check_none.setEnabled(has_rows)

    def shutdown(self) -> None:
        worker = self._convert_worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.wait(3000)
