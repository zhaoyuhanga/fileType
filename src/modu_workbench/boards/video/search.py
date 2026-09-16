"""墨软影视 · 搜索下载页：跨源搜索电影/电视剧/动漫，支持在线播放与下载。

覆盖诉求 1 / 6 / 7 / 8：
- 联网搜索 + 多源聚合 + 一键换源；
- 单个源可指定（便于「这个源有我要的片」时直取）；
- 下载目录可选、可打开；
- 全程有网即可看、即可下。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
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

from modu_workbench.core.video import MEDIA_KIND_LABELS, RemoteVideo, VideoLibrary, VideoStorage
from modu_workbench.services import app_context
from modu_workbench.ui_kit.toast import Toaster

from .detail import VideoDetailDialog
from .widgets import (
    SearchWorker,
    action_remotes,
    as_remote_batch,
    attach_context_menu,
    configure_table,
    fill_remote_row,
    make_kind_combo,
    row_remote,
    select_all,
    video_auto_switch_pref,
    video_download_dir_pref,
    video_settings,
)

COLUMNS = ["片名", "类型", "年份", "地区", "集数", "备注", "源"]


class VideoSearchPage(QWidget):
    """在线搜索页：搜索 → 选集 → 在线播放 / 下载 / 加入分类收藏。"""

    playRequested = Signal(object, object, object)        # remote, episode, quality
    downloadRequested = Signal(object, object, object)    # remote, [episodes], quality
    libraryChanged = Signal()

    def __init__(self, library: VideoLibrary, storage: VideoStorage, toaster: Toaster,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._toaster = toaster
        self._results: list[RemoteVideo] = []
        self._worker: SearchWorker | None = None
        self._dialogs: list[VideoDetailDialog] = []
        self._page = 1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 12)
        layout.setSpacing(10)

        title = QLabel("在线搜索（电影 / 电视剧 / 动漫）")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "多个免费数据源并行搜索，结果可去重合并；双击条目选集，可在线播放或下载到本地。"
            "某个源失效时会自动换源重试（可在「源设置」里调整启用项与优先级）。"
        )
        subtitle.setObjectName("pageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # ---- 搜索栏 ----
        search_row = QHBoxLayout()
        self._keyword = QLineEdit()
        self._keyword.setObjectName("searchBox")
        self._keyword.setPlaceholderText("输入片名，例如：流浪地球、庆余年、进击的巨人；也可直接粘贴 m3u8/mp4 直链")
        self._keyword.returnPressed.connect(self._start_search)

        self._kind = make_kind_combo(include_all=True)
        self._kind.setToolTip("按类型检索（源支持分类过滤时生效）")

        self._source = QComboBox()
        self.reload_sources()
        self._source.setToolTip("选择搜索范围：全部源（自动/去重）或指定某个源")

        self._search_button = QPushButton("搜索")
        self._search_button.setObjectName("primaryButton")
        self._search_button.clicked.connect(self._start_search)

        search_row.addWidget(self._keyword, 1)
        search_row.addWidget(self._kind)
        search_row.addWidget(self._source)
        search_row.addWidget(self._search_button)
        layout.addLayout(search_row)

        # ---- 结果表 ----
        self._table = configure_table(QTableWidget(), COLUMNS)
        self._table.doubleClicked.connect(lambda _index: self._open_detail())
        attach_context_menu(self._table, self._row_menu)
        layout.addWidget(self._table, 1)

        # ---- 操作栏 ----
        actions = QHBoxLayout()
        self._check_all = QPushButton("全选")
        self._check_all.clicked.connect(lambda: select_all(self._table, True))
        self._check_none = QPushButton("取消全选")
        self._check_none.clicked.connect(lambda: select_all(self._table, False))
        self._detail_button = QPushButton("选集 / 详情")
        self._detail_button.setObjectName("primaryButton")
        self._detail_button.setToolTip("查看分集与清晰度，并在线播放或下载")
        self._detail_button.clicked.connect(self._open_detail)
        self._play_button = QPushButton("▶ 播放")
        self._play_button.setToolTip("直接播放首选剧集（自动换源重试）")
        self._play_button.clicked.connect(self._play_selected)
        self._download_button = QPushButton("⬇ 下载整部")
        self._download_button.setToolTip("下载勾选条目的全部剧集")
        self._download_button.clicked.connect(self._download_selected)
        self._collect_button = QPushButton("加入分类")
        self._collect_button.clicked.connect(self._add_to_collection)

        for widget in (self._check_all, self._check_none, self._detail_button,
                       self._play_button, self._download_button, self._collect_button):
            actions.addWidget(widget)
        actions.addStretch(1)
        hint = QLabel("双击＝选集；右键＝单条操作")
        hint.setObjectName("readerStatus")
        actions.addWidget(hint)
        layout.addLayout(actions)

        # ---- 下载目录与选项 ----
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("下载到"))
        self._dir = QLineEdit(video_download_dir_pref())
        dir_row.addWidget(self._dir, 1)
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_dir)
        dir_row.addWidget(pick)
        open_dir = QPushButton("打开目录")
        open_dir.clicked.connect(self._open_dir)
        dir_row.addWidget(open_dir)
        layout.addLayout(dir_row)

        options = QHBoxLayout()
        self._auto_switch = QCheckBox("下载/播放失败时自动换源重试（推荐）")
        self._auto_switch.setChecked(video_auto_switch_pref())
        self._auto_switch.toggled.connect(self._save_auto_switch)
        options.addWidget(self._auto_switch)

        self._compliance = QCheckBox("仅用于个人学习与技术研究，遵守各站点条款与版权要求")
        self._compliance.setChecked(True)
        self._compliance.toggled.connect(self._update_buttons)
        options.addWidget(self._compliance)
        options.addStretch(1)
        layout.addLayout(options)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._status = QLabel("就绪。输入片名后搜索；也可粘贴 m3u8 / mp4 直链直接播放或下载。")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._update_buttons()

    # ------------------------------------------------------------------ 源

    def reload_sources(self) -> None:
        """按注册表刷新源下拉（含启用状态与健康度）。"""
        registry = app_context.video_registry()
        current = self._source.currentData() if self._source.count() else None
        self._source.blockSignals(True)
        self._source.clear()
        enabled = [info for info in registry.infos() if registry.is_enabled(info.key)]
        self._source.addItem(f"全部源（自动，{len(enabled)} 个）", "all")
        for info in registry.infos():
            status = registry.status_text(info.key)
            self._source.addItem(f"{info.label}（{status}）", info.key)
        index = self._source.findData(current)
        self._source.setCurrentIndex(index if index >= 0 else 0)
        self._source.blockSignals(False)

    # ------------------------------------------------------------------ 搜索

    def _start_search(self) -> None:
        keyword = self._keyword.text().strip()
        if not keyword:
            self._toaster.error("请输入搜索关键词或视频直链")
            return
        if not self._compliance.isChecked():
            self._toaster.error("请先确认合规声明")
            return
        if self._worker is not None:
            return
        kind = self._kind.currentData() or "all"
        source_key = self._source.currentData() or "all"
        self._page = 1
        self._status.setText(f"搜索中：{keyword}（{MEDIA_KIND_LABELS.get(kind, '全部类型')}）…")
        self._search_button.setEnabled(False)
        self._worker = SearchWorker(keyword, kind, source_key, 40, self._page, self,
                                    library=self._library)
        self._worker.finishedResults.connect(self._on_results)
        self._worker.failed.connect(self._on_search_failed)
        self._worker.finished.connect(self._on_search_finished)
        self._worker.start()

    def _on_results(self, videos: list, errors: list) -> None:
        self._results = list(videos)
        self._table.setRowCount(len(self._results))
        for row, remote in enumerate(self._results):
            fill_remote_row(self._table, row, remote)
        select_all(self._table, False)
        message = f"共 {len(self._results)} 条结果"
        if errors:
            message += "；部分源失败：" + "；".join(errors[:3])
        self._status.setText(message)
        self._update_buttons()

    def _on_search_failed(self, message: str) -> None:
        self._status.setText(f"搜索失败：{message}")
        self._toaster.error(f"搜索失败：{message}")

    def _on_search_finished(self) -> None:
        self._worker = None
        self._search_button.setEnabled(True)
        self._update_buttons()

    # ------------------------------------------------------------------ 详情 / 播放 / 下载

    def _row_menu(self, row: int) -> list[tuple[str, object]]:
        remote = row_remote(self._table, row)
        if remote is None:
            return []
        return [
            ("选集 / 详情…", lambda: self._open_detail(remote)),
            ("▶ 播放首选剧集", lambda: self._play_remote(remote)),
            ("⬇ 下载整部", lambda: self._download_remote(remote)),
            ("加入分类…", lambda: self._add_to_collection([remote])),
        ]

    def _open_detail(self, remote: RemoteVideo | None = None) -> None:
        remote = remote or self._first_target()
        if remote is None:
            self._toaster.info("请先选中或勾选一条结果")
            return
        dialog = VideoDetailDialog(remote, self._library, self)
        dialog.playRequested.connect(self.playRequested)
        dialog.downloadRequested.connect(self.downloadRequested)
        dialog.finished.connect(lambda _code, d=dialog: self._forget_dialog(d))
        self._dialogs.append(dialog)
        dialog.show()

    def _forget_dialog(self, dialog: VideoDetailDialog) -> None:
        if dialog in self._dialogs:
            self._dialogs.remove(dialog)

    def _first_target(self) -> RemoteVideo | None:
        remotes = self._targets()
        return remotes[0] if remotes else None

    def _targets(self) -> list[RemoteVideo]:
        remotes = action_remotes(self._table)
        if remotes:
            return remotes
        row = self._table.currentRow()
        if row >= 0:
            remote = row_remote(self._table, row)
            if remote is not None:
                return [remote]
        return []

    def _play_selected(self) -> None:
        remote = self._first_target()
        if remote is None:
            self._toaster.info("请先选中或勾选一条结果")
            return
        self._play_remote(remote)

    def _play_remote(self, remote: RemoteVideo) -> None:
        # 未拉详情时先补齐，随后由板块统一解析播放地址
        self.playRequested.emit(remote, None, None)

    def _download_selected(self) -> None:
        remotes = self._targets()
        if not remotes:
            self._toaster.info("请先勾选或选中要下载的条目")
            return
        if not self._compliance.isChecked():
            self._toaster.error("请先确认合规声明")
            return
        for remote in remotes:
            self._download_remote(remote)

    def _download_remote(self, remote: RemoteVideo) -> None:
        dest = self._dir.text().strip()
        if dest:
            Path(dest).mkdir(parents=True, exist_ok=True)
            video_settings().setValue("video/download_dir", dest)
        # 具体下载哪些集由板块统一补齐剧集信息后决定（detail or None）
        self.downloadRequested.emit(remote, None, None)
        self._status.setText(f"已提交下载任务：{remote.title}")

    # ------------------------------------------------------------------ 分类

    def _add_to_collection(self, remotes: list | None = None) -> None:
        remotes = as_remote_batch(remotes) if remotes is not None else None
        if remotes is None:
            remotes = self._targets()
        if not remotes:
            self._toaster.info("请先勾选或选中条目")
            return
        from .widgets import CollectionPicker

        picker = CollectionPicker(self._storage, self, "加入分类")
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        playlist_id = picker.selected_playlist_id()
        if playlist_id is None:
            self._toaster.error("未选择分类")
            return
        added = 0
        for remote in remotes:
            try:
                video = self._library.ensure_video(remote, remote.episodes[0] if remote.episodes else None)
            except Exception:  # noqa: BLE001
                continue
            added += self._storage.add_to_playlist(playlist_id, [video.id])
        playlist = self._storage.get_playlist(playlist_id)
        name = playlist.name if playlist else "分类"
        self._toaster.success(f"已加入「{name}」：{added} 个条目")
        self._status.setText(f"已加入分类「{name}」：{added} 个条目")
        self.libraryChanged.emit()

    # ------------------------------------------------------------------ 杂项

    def _pick_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择视频下载目录", self._dir.text())
        if folder:
            self._dir.setText(folder)
            video_settings().setValue("video/download_dir", folder)

    def _open_dir(self) -> None:
        target = self._dir.text().strip() or video_download_dir_pref()
        Path(target).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _save_auto_switch(self, checked: bool) -> None:
        video_settings().setValue("video/auto_switch", bool(checked))

    def _update_buttons(self) -> None:
        busy = self._worker is not None
        has_results = len(self._results) > 0
        ok = self._compliance.isChecked() and not busy and has_results
        self._search_button.setEnabled(not busy)
        for button in (self._detail_button, self._play_button, self._download_button, self._collect_button):
            button.setEnabled(ok)
        self._check_all.setEnabled(has_results)
        self._check_none.setEnabled(has_results)

    def on_shown(self) -> None:
        self.reload_sources()

    def shutdown(self) -> None:
        for dialog in list(self._dialogs):
            try:
                dialog.shutdown()   # 等对话框自己的详情/清晰度线程收尾
            except Exception:  # noqa: BLE001
                pass
            dialog.close()
        self._dialogs.clear()
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.wait(2000)
