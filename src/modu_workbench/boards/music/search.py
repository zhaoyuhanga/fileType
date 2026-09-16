"""墨软乐库：在线搜索与下载页（支持按歌曲/歌手/专辑/类型检索，批量下载或加入歌单）。"""
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
from . import context as app_context
from modu_workbench.ui_kit.components import EmptyState
from modu_workbench.ui_kit.toast import Toaster

from .widgets import (
    DownloadWorker,
    PlaylistPicker,
    SearchWorker,
    action_remotes,
    as_remote_batch,
    attach_context_menu,
    configure_table,
    fill_remote_row,
    jamendo_client_id_pref,
    music_download_dir_pref,
    row_remote,
    select_all,
)


class _PreviewResolver(SearchWorker):
    """复用线程基类：把「解析试听直链」当作一次搜索来跑（含跨源兜底）。"""

    def __init__(self, remote, parent=None):  # noqa: ANN001
        super().__init__("", "song", "url", 1, parent)
        self._remote = remote

    def run(self) -> None:  # noqa: D102
        try:
            registry = app_context.music_registry()
            resolved = registry.resolve(self._remote, allow_cross_source=True)
            self.finishedResults.emit(resolved.url, resolved.track if resolved.switched else self._remote)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class MusicSearchPage(QWidget):
    """在线搜索页：搜索 → 勾选 → 批量下载 / 加入歌单（可先试听）。"""

    tracksImported = Signal(list)   # 下载并入库的 Track 列表

    def _cancel_current(self) -> None:
        """供板块任务条调用：取消本页正在进行的任务。"""
        if getattr(self, "_download", None) is not None:
            try:
                self._download.cancel()
            except Exception:  # noqa: BLE001
                pass

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

    def __init__(self, library: MusicLibrary, storage: MusicStorage, toaster: Toaster,
                 parent: QWidget | None = None, *, task_bar=None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._task = task_bar
        self._fallback_status = QLabel("就绪。")
        self._toaster = toaster
        self._results: list = []
        self._worker: SearchWorker | None = None
        self._download: DownloadWorker | None = None
        self._preview_worker: _PreviewResolver | None = None

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
        self.reload_sources()
        self._source.setToolTip("音源说明：\n" + "\n".join(
            f"· {info.label}（{info.kind_label}）：{info.note}" for info in list_sources()
        ))

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
        self._empty = EmptyState(
            "🎵", "还没有搜索结果",
            "在上方输入歌名 / 歌手 / 专辑后点「搜索」；勾选结果可批量下载或加入歌单",
        )
        layout.addWidget(self._empty, 1)
        layout.addWidget(self._table, 1)
        self._table.setVisible(False)          # 无结果时显示空状态，有结果时切回表格

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
        self._download_button.clicked.connect(lambda: self._start_download())
        self._playlist_button = QPushButton("加入歌单")
        self._playlist_button.setToolTip("勾选或选中一行后加入歌单（稍后可整单下载）")
        self._playlist_button.clicked.connect(lambda: self._add_to_playlist())
        self._cancel_button = QPushButton("取消下载")
        self._cancel_button.setObjectName("dangerButton")
        self._cancel_button.clicked.connect(self._cancel_download)
        self._cancel_button.setVisible(False)     # 取消统一由底部任务条提供

        for widget in (self._check_all, self._check_none, self._preview_button,
                       self._download_button, self._playlist_button, self._cancel_button):
            actions.addWidget(widget)
        actions.addStretch(1)
        hint = QLabel("勾选多首＝批量；只选中一行＝单条；右键可试听/下载单曲")
        hint.setObjectName("readerStatus")
        actions.addWidget(hint)
        self._actions_widget = QWidget()
        self._actions_widget.setLayout(actions)
        self._actions_widget.setVisible(False)     # 无结果时不显示一排禁用按钮
        layout.addWidget(self._actions_widget)

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

        self._auto_switch = QCheckBox("下载/试听失败时自动换源重试（推荐：某音源失效或会员受限时改用其他音源）")
        self._auto_switch.setChecked(True)
        layout.addWidget(self._auto_switch)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._set_progress(0)
        self._progress.setVisible(False)          # 进度统一显示在板块任务条

        self._status_label = QLabel("就绪。输入关键词后搜索。")
        self._status_label.setObjectName("readerStatus")
        self._status_label.setWordWrap(True)
        self._status_label.setVisible(False)            # 状态统一显示在板块任务条

        self._update_buttons()

        # 试听错误（解码失败/网络中断）同步到本页状态栏
        app_context.music_player().errorOccurred.connect(self._on_preview_error)

    # ---------- 音源 ----------

    def reload_sources(self) -> None:
        """按注册表刷新音源下拉（含启用状态、能力类型与健康度）。"""
        registry = app_context.music_registry()
        current = self._source.currentData()
        self._source.blockSignals(True)
        self._source.clear()
        enabled = [info for info in registry.infos() if registry.is_enabled(info.key)]
        self._source.addItem(f"全部音源（自动，{len(enabled)} 个）", "all")
        for info in registry.infos():
            status = registry.status_text(info.key)
            self._source.addItem(f"{info.label} · {info.kind_label}（{status}）", info.key)
        index = self._source.findData(current)
        self._source.setCurrentIndex(index if index >= 0 else 0)
        self._source.blockSignals(False)

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
        self._busy(f"搜索中：{keyword}（{SEARCH_KIND_LABELS.get(kind, kind)}）…")
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
        self._empty.setVisible(not self._results)
        self._table.setVisible(bool(self._results))
        self._actions_widget.setVisible(bool(self._results))     # 空结果不显示一排禁用按钮
        self._report(message)
        self._update_buttons()

    def _on_search_failed(self, message: str) -> None:
        self._report(f"搜索失败：{message}")
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
        player = app_context.music_player()
        # 已在试听：再次点击即停止
        if player.is_preview:
            player.stop_preview()
            self._preview_button.setText("试听选中")
            self._report("已停止试听")
            return
        remotes = action_remotes(self._table)
        if not remotes:
            self._toaster.info("请先勾选或选中一条结果")
            return
        self._preview(remotes[0])

    def _preview(self, remote) -> None:  # noqa: ANN001
        self._report(f"解析试听地址：{remote.display()}…")
        self._preview_worker = _PreviewResolver(remote, self)
        self._preview_worker.finishedResults.connect(self._play_preview)
        self._preview_worker.failed.connect(self._on_preview_failed)
        self._preview_worker.start()

    def _on_preview_failed(self, message: str) -> None:
        self._preview_button.setText("试听选中")
        self._report(f"试听失败：{message}")
        self._toaster.error(f"试听失败：{message}")

    def _on_preview_error(self, message: str) -> None:
        """来自播放条的试听错误（解码失败等）同步到本页状态栏。"""
        if self._preview_button.text() == "停止试听":
            self._preview_button.setText("试听选中")
        self._report(message)

    def _play_preview(self, url: str, remote) -> None:  # noqa: ANN001
        """试听统一走底部播放条（与本地播放共用进度/错误/音量）。"""
        player = app_context.music_player()
        track = player.play_url(
            url, title=remote.title, artist=remote.artist,
            album=remote.album, duration_ms=remote.duration_ms,
        )
        if track is None:
            self._toaster.error("试听地址无效，可直接下载后再播放")
            return
        self._preview_button.setText("停止试听")
        self._report(f"试听中（进度见底部播放条）：{remote.display()}")

    # ---------- 下载 ----------

    def _start_download(self, remotes: list | None = None) -> None:
        # 注意：按钮 clicked 会传 bool，必须过滤掉（否则会被当成“没有目标”）
        remotes = as_remote_batch(remotes) if remotes is not None else None
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
        self._download = DownloadWorker(
            remotes, dest, parent=self,
            registry=app_context.music_registry(),
            allow_cross_source=self._auto_switch.isChecked(),
        )
        self._download.progressed.connect(self._on_download_progress)
        self._download.finishedAll.connect(self._on_download_finished)
        self._download.failed.connect(self._on_download_failed)
        self._download.finished.connect(self._on_download_thread_finished)
        self._set_progress(0)
        self._busy(f"开始下载 {len(remotes)} 首…")
        self._cancel_button.setEnabled(True)
        self._update_buttons()
        self._download.start()

    def _cancel_download(self) -> None:
        if self._download is not None:
            self._download.cancel()
            self._busy("正在取消下载…")

    def _on_download_progress(self, done: int, total: int, message: str) -> None:
        percent = int(done * 100 / total) if total else 0
        self._set_progress(min(100, percent))
        self._report(message)

    def _on_download_finished(self, results: list) -> None:
        self._report_download_result(results)

    def _report_download_result(self, results: list) -> None:
        ok = [r for r in results if r.ok]
        failed = [r for r in results if not r.ok]
        switched = [r for r in ok if r.switched_from]
        imported: list[Track] = self._library.import_download_results(ok)
        self._set_progress(100)
        summary = f"下载完成：成功 {len(ok)}，失败 {len(failed)}；已入库 {len(imported)} 首"
        if switched:
            pairs = "，".join(f"{r.track.title}→{r.source}" for r in switched[:3])
            summary += f"；其中 {len(switched)} 首自动换源（{pairs}）"
        if failed:
            reasons = []
            for result in failed[:3]:
                if result.message not in reasons:
                    reasons.append(result.message)
            summary += "；失败原因：" + "；".join(reasons)
        self._report(summary)
        if ok:
            self._toaster.success(f"已下载 {len(ok)} 首，入库 {len(imported)} 首")
            self.tracksImported.emit(imported)
        else:
            self._toaster.error("全部下载失败：可在设置里调整音源顺序/启用项，或改用「音频直链」")

    def _on_download_failed(self, message: str) -> None:
        self._report(f"下载失败：{message}")
        self._toaster.error(f"下载失败：{message}")

    def _on_download_thread_finished(self) -> None:
        self._download = None
        self._cancel_button.setEnabled(False)
        self._update_buttons()

    # ---------- 加入歌单 ----------

    def _add_to_playlist(self, remotes: list | None = None) -> None:
        # 同上：忽略按钮 clicked 传入的 bool
        remotes = as_remote_batch(remotes) if remotes is not None else None
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
        self._report(f"已加入歌单「{name}」：{added} 首待下载，可在歌单页一键下载")

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
        player = app_context.music_player()
        if player.is_preview:
            player.stop_preview()
        for worker in (self._worker, self._download, self._preview_worker):
            running = getattr(worker, "isRunning", None)
            if worker is not None and callable(running) and running():
                if isinstance(worker, DownloadWorker):
                    worker.cancel()
                worker.wait(3000)
