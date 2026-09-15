"""墨读音乐：在线搜索与下载页（支持按歌曲/歌手/专辑/类型检索，批量下载或加入歌单）。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
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

from modu_workbench.core.music import (
    MULTIMEDIA_AVAILABLE,
    SEARCH_KIND_LABELS,
    SEARCH_KINDS,
    MusicLibrary,
    MusicStorage,
    Track,
    get_source,
    list_sources,
)
from modu_workbench.ui_kit.toast import Toaster

from .music_widgets import (
    DownloadWorker,
    PlaylistPicker,
    SearchWorker,
    action_remotes,
    attach_context_menu,
    configure_table,
    fill_remote_row,
    jamendo_client_id_pref,
    music_download_dir_pref,
    row_remote,
    select_all,
)

try:  # 试听用播放器（不可用时按钮自动禁用）
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except Exception:  # noqa: BLE001
    QAudioOutput = None  # type: ignore[assignment]
    QMediaPlayer = None  # type: ignore[assignment]


class _PreviewResolver(SearchWorker):
    """复用线程基类：把「解析直链」当作一次搜索来跑。"""

    def __init__(self, remote, parent=None):  # noqa: ANN001
        super().__init__("", "song", "url", 1, parent)
        self._remote = remote

    def run(self) -> None:  # noqa: D102
        try:
            url = get_source(self._remote.source).download_url(self._remote)
            self.finishedResults.emit(url, self._remote)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class MusicSearchPage(QWidget):
    """在线搜索页：搜索 → 勾选 → 批量下载 / 加入歌单（可先试听）。"""

    tracksImported = Signal(list)   # 下载并入库的 Track 列表

    def __init__(self, library: MusicLibrary, storage: MusicStorage, toaster: Toaster,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._toaster = toaster
        self._results: list = []
        self._worker: SearchWorker | None = None
        self._download: DownloadWorker | None = None
        self._preview_worker: _PreviewResolver | None = None
        self._preview_player = None
        self._preview_audio = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 12)
        layout.setSpacing(10)

        title = QLabel("在线搜索（按需下载）")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "支持按歌曲 / 歌手 / 专辑 / 类型检索；搜索结果可勾选后批量下载，或先加入歌单稍后再下载。"
        )
        subtitle.setObjectName("pageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # ---- 搜索栏 ----
        search_row = QHBoxLayout()
        self._keyword = QLineEdit()
        self._keyword.setObjectName("searchBox")
        self._keyword.setPlaceholderText("输入歌名 / 歌手 / 专辑 / 风格，例如：周杰伦、民谣、Jay")
        self._keyword.returnPressed.connect(self._start_search)

        self._kind = QComboBox()
        for key in SEARCH_KINDS:
            self._kind.addItem(SEARCH_KIND_LABELS[key], key)
        self._kind.setToolTip("检索方式：歌曲=按歌名，歌手=该歌手热门曲目，专辑=整张专辑，类型=风格歌单")

        self._source = QComboBox()
        self._source.addItem("全部音源", "all")
        for info in list_sources():
            self._source.addItem(info.label, info.key)
        self._source.setToolTip("音源说明：\n" + "\n".join(f"· {i.label}：{i.note}" for i in list_sources()))

        self._search_button = QPushButton("搜索")
        self._search_button.setObjectName("primaryButton")
        self._search_button.clicked.connect(self._start_search)

        search_row.addWidget(self._keyword, 1)
        search_row.addWidget(self._kind)
        search_row.addWidget(self._source)
        search_row.addWidget(self._search_button)
        layout.addLayout(search_row)

        # ---- 结果表 ----
        self._table = configure_table(
            QTableWidget(), ["曲名", "歌手", "专辑", "时长", "音源"]
        )
        self._table.doubleClicked.connect(lambda _index: self._preview_selected())
        attach_context_menu(self._table, self._row_menu)
        layout.addWidget(self._table, 1)

        # ---- 操作栏 ----
        actions = QHBoxLayout()
        self._check_all = QPushButton("全选")
        self._check_all.clicked.connect(lambda: select_all(self._table, True))
        self._check_none = QPushButton("取消全选")
        self._check_none.clicked.connect(lambda: select_all(self._table, False))
        self._preview_button = QPushButton("试听选中")
        self._preview_button.clicked.connect(self._preview_selected)
        self._preview_button.setEnabled(bool(MULTIMEDIA_AVAILABLE))
        self._download_button = QPushButton("下载（单条或批量）")
        self._download_button.setObjectName("primaryButton")
        self._download_button.setToolTip("勾选多首=批量下载；只选中一行=下载该首")
        self._download_button.clicked.connect(self._start_download)
        self._playlist_button = QPushButton("加入歌单")
        self._playlist_button.setToolTip("勾选或选中一行后加入歌单（稍后可整单下载）")
        self._playlist_button.clicked.connect(self._add_to_playlist)
        self._cancel_button = QPushButton("取消下载")
        self._cancel_button.setObjectName("dangerButton")
        self._cancel_button.clicked.connect(self._cancel_download)
        self._cancel_button.setEnabled(False)

        for widget in (self._check_all, self._check_none, self._preview_button,
                       self._download_button, self._playlist_button, self._cancel_button):
            actions.addWidget(widget)
        actions.addStretch(1)
        layout.addLayout(actions)

        # ---- 下载目录 ----
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("下载到"))
        self._dir = QLineEdit(music_download_dir_pref())
        self._apply_jamendo_key()
        dir_row.addWidget(self._dir, 1)
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_dir)
        dir_row.addWidget(pick)
        open_dir = QPushButton("打开目录")
        open_dir.clicked.connect(self._open_dir)
        dir_row.addWidget(open_dir)
        layout.addLayout(dir_row)

        self._compliance = QCheckBox("仅用于个人学习、试听与自有内容备份，遵守平台条款与版权要求（默认开启）")
        self._compliance.setChecked(True)
        self._compliance.toggled.connect(self._update_buttons)
        layout.addWidget(self._compliance)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._status = QLabel("就绪。输入关键词后搜索。")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._update_buttons()

    # ---------- 搜索 ----------

    def _start_search(self) -> None:
        keyword = self._keyword.text().strip()
        if not keyword:
            self._toaster.error("请输入搜索关键词")
            return
        if not self._compliance.isChecked():
            self._toaster.error("请先确认合规声明")
            return
        if self._worker is not None:
            return
        kind = self._kind.currentData()
        source_key = self._source.currentData()
        self._status.setText(f"搜索中：{keyword}（{SEARCH_KIND_LABELS.get(kind, kind)}）…")
        self._search_button.setEnabled(False)
        self._worker = SearchWorker(keyword, kind, source_key, 40, self)
        self._worker.finishedResults.connect(self._on_results)
        self._worker.failed.connect(self._on_search_failed)
        self._worker.finished.connect(self._on_search_finished)
        self._worker.start()

    def _on_results(self, tracks: list, errors: list) -> None:
        self._results = list(tracks)
        self._table.setRowCount(len(self._results))
        for row, remote in enumerate(self._results):
            fill_remote_row(self._table, row, remote)
        select_all(self._table, False)
        message = f"共 {len(self._results)} 条结果"
        if errors:
            message += "；" + "；".join(errors)
        self._status.setText(message)
        self._update_buttons()

    def _on_search_failed(self, message: str) -> None:
        self._status.setText(f"搜索失败：{message}")
        self._toaster.error(f"搜索失败：{message}")

    def _on_search_finished(self) -> None:
        self._worker = None
        self._search_button.setEnabled(True)
        self._update_buttons()

    # ---------- 试听 ----------

    def _row_menu(self, row: int) -> list[tuple[str, object]]:
        remote = row_remote(self._table, row)
        if remote is None:
            return []
        return [
            ("▶ 试听这首", lambda: self._preview(remote)),
            ("⬇ 下载这首", lambda: self._start_download([remote])),
            ("加入歌单…", lambda: self._add_to_playlist([remote])),
        ]

    def _preview_selected(self) -> None:
        remotes = action_remotes(self._table)
        if not remotes:
            self._toaster.info("请先勾选或选中一条结果")
            return
        self._preview(remotes[0])

    def _preview(self, remote) -> None:  # noqa: ANN001
        self._status.setText(f"解析试听地址：{remote.display()}…")
        self._preview_worker = _PreviewResolver(remote, self)
        self._preview_worker.finishedResults.connect(self._play_preview)
        self._preview_worker.failed.connect(lambda msg: self._toaster.error(f"试听失败：{msg}"))
        self._preview_worker.start()

    def _play_preview(self, url: str, remote) -> None:  # noqa: ANN001
        if not MULTIMEDIA_AVAILABLE or QMediaPlayer is None:
            self._toaster.info("当前环境不支持内嵌试听，可直接下载后播放")
            return
        if self._preview_player is None:
            self._preview_player = QMediaPlayer(self)
            self._preview_audio = QAudioOutput(self)
            self._preview_audio.setVolume(0.8)
            self._preview_player.setAudioOutput(self._preview_audio)
        self._preview_player.setSource(QUrl(url))
        self._preview_player.play()
        self._status.setText(f"试听中：{remote.display()}")

    # ---------- 下载 ----------

    def _start_download(self, remotes: list | None = None) -> None:
        if remotes is None:
            remotes = action_remotes(self._table)
        if not remotes:
            self._toaster.info("请先勾选（可多选）或选中要下载的曲目")
            return
        if not self._compliance.isChecked():
            self._toaster.error("请先确认合规声明")
            return
        dest = self._dir.text().strip()
        if not dest:
            self._toaster.error("请选择下载目录")
            return
        Path(dest).mkdir(parents=True, exist_ok=True)
        self._download = DownloadWorker(remotes, dest, parent=self)
        self._download.progressed.connect(self._on_download_progress)
        self._download.finishedAll.connect(self._on_download_finished)
        self._download.failed.connect(self._on_download_failed)
        self._download.finished.connect(self._on_download_thread_finished)
        self._progress.setValue(0)
        self._status.setText(f"开始下载 {len(remotes)} 首…")
        self._cancel_button.setEnabled(True)
        self._update_buttons()
        self._download.start()

    def _cancel_download(self) -> None:
        if self._download is not None:
            self._download.cancel()
            self._status.setText("正在取消下载…")

    def _on_download_progress(self, done: int, total: int, message: str) -> None:
        percent = int(done * 100 / total) if total else 0
        self._progress.setValue(min(100, percent))
        self._status.setText(message)

    def _on_download_finished(self, results: list) -> None:
        ok = [r for r in results if r.ok]
        failed = [r for r in results if not r.ok]
        imported: list[Track] = self._library.import_download_results(ok)
        self._progress.setValue(100)
        summary = f"下载完成：成功 {len(ok)}，失败 {len(failed)}；已入库 {len(imported)} 首"
        if failed:
            summary += "；失败原因：" + "；".join(f"{r.track.display()}（{r.message}）" for r in failed[:3])
        self._status.setText(summary)
        if ok:
            self._toaster.success(f"已下载 {len(ok)} 首，入库 {len(imported)} 首")
            self.tracksImported.emit(imported)
        else:
            self._toaster.error("全部下载失败，请检查网络或音源可用性")

    def _on_download_failed(self, message: str) -> None:
        self._status.setText(f"下载失败：{message}")
        self._toaster.error(f"下载失败：{message}")

    def _on_download_thread_finished(self) -> None:
        self._download = None
        self._cancel_button.setEnabled(False)
        self._update_buttons()

    # ---------- 加入歌单 ----------

    def _add_to_playlist(self, remotes: list | None = None) -> None:
        if remotes is None:
            remotes = action_remotes(self._table)
        if not remotes:
            self._toaster.info("请先勾选或选中曲目")
            return
        picker = PlaylistPicker(self._storage, self, "添加在线曲目到歌单")
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        playlist_id = picker.selected_playlist_id()
        if playlist_id is None:
            self._toaster.error("未选择歌单")
            return
        added = self._storage.add_remotes_to_playlist(playlist_id, remotes)
        playlist = self._storage.get_playlist(playlist_id)
        name = playlist.name if playlist else "歌单"
        self._toaster.success(f"已加入「{name}」：新增 {added} 首（待下载）")
        self._status.setText(f"已加入歌单「{name}」：{added} 首待下载，可在歌单页一键下载")

    # ---------- 杂项 ----------

    def _pick_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择音乐下载目录", self._dir.text())
        if folder:
            self._dir.setText(folder)

    def _open_dir(self) -> None:
        from PySide6.QtGui import QDesktopServices

        target = self._dir.text().strip() or music_download_dir_pref()
        Path(target).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _apply_jamendo_key(self) -> None:
        """把设置里的 Jamendo client_id 注入音源（未填则保持不可用）。"""
        client_id = jamendo_client_id_pref()
        if not client_id:
            return
        source = get_source("jamendo")
        setter = getattr(source, "set_client_id", None)
        if callable(setter):
            setter(client_id)

    def _update_buttons(self) -> None:
        busy = self._worker is not None or self._download is not None
        enabled = self._compliance.isChecked() and not busy
        self._search_button.setEnabled(not busy)
        self._download_button.setEnabled(enabled and len(self._results) > 0)
        self._playlist_button.setEnabled(enabled and len(self._results) > 0)
        self._preview_button.setEnabled(bool(MULTIMEDIA_AVAILABLE) and len(self._results) > 0)
        self._check_all.setEnabled(len(self._results) > 0)
        self._check_none.setEnabled(len(self._results) > 0)

    def shutdown(self) -> None:
        """板块销毁前停止后台线程与试听。"""
        if self._preview_player is not None:
            self._preview_player.stop()
        for worker in (self._worker, self._download, self._preview_worker):
            if worker is not None and worker.isRunning():
                if isinstance(worker, DownloadWorker):
                    worker.cancel()
                worker.wait(3000)
