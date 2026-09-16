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
    QStackedWidget,
)

from modu_workbench.core.video import VIDEO_TARGETS, VideoLibrary, VideoStorage
from modu_workbench.ui_kit.components import (
    ColumnPage,
    EmptyState,
    SectionCard,
    chip,
    ghost_button,
    hint_label,
    primary_button,
    row,
)
from modu_workbench.ui_kit.toast import Toaster

from .widgets import (
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
                 parent: QWidget | None = None, *, task_bar=None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._toaster = toaster
        self._task = task_bar
        self._videos = []
        self._convert_worker: ConvertWorker | None = None
        self._tail_stretch = False
        self._fallback_status = QLabel("就绪。")   # 兼容旧引用/单页自测

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._page = ColumnPage(
            "我的视频",
            "同时管理「已下载到本地」与「只入库未下载（可在线播放）」的条目；"
            "可分类/收藏，也可把本地文件转成其他视频格式或提取音频。",
            actions=[ghost_button("打开影视库目录")],
        )
        self._open_dir_button = self._page.header.findChildren(QPushButton)[0]
        self._open_dir_button.clicked.connect(self._open_library_dir)
        outer.addWidget(self._page)

        # 过滤 + 列表
        self._keyword = QLineEdit()
        self._keyword.setObjectName("searchBox")
        self._keyword.setPlaceholderText("搜索片名 / 演员 / 标签")
        self._keyword.textChanged.connect(lambda _text: self.reload())
        self._kind = make_kind_combo(include_all=True)
        self._kind.currentIndexChanged.connect(lambda _index: self.reload())
        self._collection = QComboBox()
        self._collection.currentIndexChanged.connect(lambda _index: self.reload())
        self._order = QComboBox()
        for key, label in (("added", "最近加入"), ("title", "按片名"), ("played", "最近播放")):
            self._order.addItem(label, key)
        self._order.currentIndexChanged.connect(lambda _index: self.reload())

        self._count_chip = chip("0 项")
        self._list_card = SectionCard("影片列表", actions=[self._count_chip])
        self._list_card.add(row(self._keyword, self._kind, self._collection, self._order,
                                stretch=self._keyword))

        self._table = configure_table(QTableWidget(), COLUMNS)
        self._table.doubleClicked.connect(lambda _index: self._play_action())
        attach_context_menu(self._table, self._row_menu)
        self._empty = EmptyState(
            "🎬", "影视库还是空的",
            "用「导入本地影片」加入已有文件，或去「搜索下载」在线找片",
        )
        self._list_card.add(self._empty)
        self._list_card.add(self._table)
        self._table.setVisible(False)          # 无数据时显示空状态，有数据时切回表格

        # 条目操作
        self._check_all = ghost_button("全选")
        self._check_all.clicked.connect(lambda: select_all(self._table, True))
        self._check_none = ghost_button("取消全选")
        self._check_none.clicked.connect(lambda: select_all(self._table, False))
        self._play_button = primary_button("▶ 播放")
        self._play_button.clicked.connect(self._play_action)
        self._import_button = ghost_button("导入本地影片…")
        self._import_button.clicked.connect(self._import_local)
        self._collect_button = ghost_button("加入分类…")
        self._collect_button.clicked.connect(self._add_to_collection)
        self._favorite_button = ghost_button("★ 收藏 / 取消")
        self._favorite_button.clicked.connect(self._toggle_favorite)
        self._category_button = ghost_button("设置分类名…")
        self._category_button.clicked.connect(self._set_category)
        self._delete_button = QPushButton("删除条目")
        self._delete_button.setObjectName("dangerButton")
        self._delete_button.clicked.connect(self._delete_videos)
        self._list_card.add(row(self._check_all, self._check_none, self._play_button,
                                self._import_button, self._collect_button, self._favorite_button,
                                self._category_button, self._delete_button, stretch_last=True))

        # 转换
        self._target = QComboBox()
        for target in VIDEO_TARGETS:
            label = f"提取 {target.upper()} 音频" if target in ("mp3", "m4a", "wav") else target.upper()
            self._target.addItem(label, target)
        self._convert_button = primary_button("开始转换")
        self._convert_button.clicked.connect(self._start_convert)
        self._cancel_button = ghost_button("取消转换")
        self._cancel_button.clicked.connect(self._cancel_convert)
        self._cancel_button.setEnabled(False)
        self._list_card.add(row(QLabel("转换为"), self._target, self._convert_button,
                                self._cancel_button,
                                hint_label("转换需要 ffmpeg（可用 MODU_FFMPEG 指定）"),
                                stretch_last=True))

        self._page.add(self._list_card)
        self._set_tail_stretch(True)
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
        self._count_chip.setText(f"{len(videos)} 项")
        self._empty.setVisible(not videos)
        self._table.setVisible(bool(videos))
        self._set_tail_stretch(not videos)
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
        from modu_workbench.core.platform.paths import video_download_dir

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
        self._cancel_button.setEnabled(True)
        self._convert_button.setEnabled(False)
        self._busy(f"开始转换 {len(ids)} 个条目为 {str(target_format).upper()}…")
        self._convert_worker.start()

    def _cancel_convert(self) -> None:
        if self._convert_worker is not None:
            self._convert_worker.cancel()
            self._busy("正在取消转换…")

    def _on_convert_progress(self, done: int, total: int, message: str) -> None:
        self._report(message, done, total)

    def _on_convert_finished(self, outputs: list, errors: list) -> None:
        message = f"转换完成：成功 {len(outputs)}，失败 {len(errors)}"
        if errors:
            message += "；" + "；".join(errors[:3])
        self._idle(message)
        if outputs:
            self._toaster.success(f"已转换 {len(outputs)} 个文件")
        else:
            self._toaster.error("转换失败：请确认已安装 ffmpeg")
        self.reload()

    def _on_convert_failed(self, message: str) -> None:
        self._report(f"转换失败：{message}")
        self._toaster.error(f"转换失败：{message}")

    def _on_convert_thread_finished(self) -> None:
        self._convert_worker = None
        self._cancel_button.setEnabled(False)
        self._convert_button.setEnabled(True)


    def _set_tail_stretch(self, enabled: bool) -> None:
        """页面末尾弹簧：列表为空时插入，让卡片保持自然高度（避免行与行被拉开）。"""
        layout = self._page.body
        if enabled and not self._tail_stretch:
            layout.addStretch(1)
            self._tail_stretch = True
        elif not enabled and self._tail_stretch and layout.count():
            layout.takeAt(layout.count() - 1)
            self._tail_stretch = False

    # ------------------------------------------------------------------ 状态

    @property
    def _status(self) -> QLabel:
        """兼容旧引用：状态文字统一显示在板块任务条上。"""
        return self._task._label if self._task is not None else self._fallback_status   # noqa: SLF001

    def _note_fallback(self, message: str) -> None:
        """把状态文字写到兜底标签（未创建时静默忽略，避免状态助手成为崩溃点）。"""
        label = getattr(self, "_fallback_status", None)
        if label is not None:
            label.setText(message)

    def _report(self, message: str, done: int = 0, total: int = 0) -> None:
        self._note_fallback(message)
        if self._task is not None:
            if done or total:
                self._task.report(message, done, total)
            else:
                self._task.note(message)      # 纯信息：不显示进度条/取消

    def _busy(self, message: str) -> None:
        """进行中提示（搜索/导入/解析这类未知时长）。"""
        self._note_fallback(message)
        if self._task is not None:
            self._task.busy(message)

    def _idle(self, message: str = "") -> None:
        if message:
            self._note_fallback(message)
        if self._task is not None:
            self._task.idle(message)

    def _set_progress(self, value: int) -> None:
        """兼容旧写法：只更新进度数值；是否显示进度条由 report/busy/idle 决定。"""
        bar = self._task_bar._progress if hasattr(self, "_task_bar") else (
            self._task._progress if self._task is not None else None)     # noqa: SLF001
        if bar is not None:
            bar.setRange(0, 100)
            bar.setValue(int(value))

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
