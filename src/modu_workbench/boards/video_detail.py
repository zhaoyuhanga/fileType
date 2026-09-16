"""墨软影视：详情与选集对话框（在线播放 / 选清晰度 / 下载，不离开搜索页）。

交互设计：
- 搜索页双击条目 → 本对话框拉详情（分集 + 画质）；
- 左侧剧集列表，右侧信息与操作；
- 清晰度下拉支持「多选项」（诉求第 7 条）：优先用 m3u8 主清单解析出的真实分辨率，
  解析不到时退回源给出的线路画质标签；
- 「在线播放」与「下载选中」都走同一套换源重试逻辑。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.video import (
    Episode,
    Quality,
    RemoteVideo,
    VideoLibrary,
    match_quality,
)
from modu_workbench.services import app_context
from modu_workbench.ui_kit.toast import Toaster

from .video_widgets import DetailWorker, QualityWorker


class VideoDetailDialog(QDialog):
    """条目详情：选集、选清晰度、在线播放、下载。"""

    playRequested = Signal(object, object, object)        # remote, episode, quality
    downloadRequested = Signal(object, object, object)    # remote, [episodes], quality

    def __init__(self, remote: RemoteVideo, library: VideoLibrary | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{remote.title} · 详情")
        self.resize(880, 580)
        self._toaster = Toaster(self)
        self._library = library or app_context.video_library()
        self._remote = remote
        self._detail_worker: DetailWorker | None = None
        self._quality_worker: QualityWorker | None = None
        self._qualities: list[Quality] = []
        self._variants_loaded = False

        self._build_ui()
        self._fill_info(remote)
        self._load_episodes(remote)

    # ------------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(12)
        self._title = QLabel("")
        self._title.setObjectName("pageTitle")
        self._title.setWordWrap(True)
        header.addWidget(self._title, 1)
        self._status = QLabel("")
        self._status.setObjectName("readerStatus")
        header.addWidget(self._status)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左：剧集列表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        left_layout.addWidget(QLabel("剧集（可多选下载）"))
        self._episode_list = QListWidget()
        self._episode_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._episode_list.currentRowChanged.connect(self._on_episode_changed)
        left_layout.addWidget(self._episode_list, 1)
        splitter.addWidget(left)

        # 右：信息
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        info_row = QHBoxLayout()
        info_row.addWidget(QLabel("清晰度"))
        self._quality_box = QComboBox()
        self._quality_box.setMinimumWidth(140)
        self._quality_box.setToolTip("优先展示 m3u8 主清单里的真实分辨率；解析不到时用源给出的线路画质")
        self._quality_box.currentIndexChanged.connect(self._on_quality_index_changed)
        info_row.addWidget(self._quality_box, 1)
        right_layout.addLayout(info_row)

        self._meta = QLabel("")
        self._meta.setObjectName("readerStatus")
        self._meta.setWordWrap(True)
        right_layout.addWidget(self._meta)

        self._description = QTextBrowser()
        self._description.setOpenExternalLinks(False)
        right_layout.addWidget(self._description, 1)
        splitter.addWidget(right)
        splitter.setSizes([320, 540])
        layout.addWidget(splitter, 1)

        actions = QHBoxLayout()
        self._play_button = QPushButton("▶ 在线播放")
        self._play_button.setObjectName("primaryButton")
        self._play_button.setToolTip("直接联网播放当前选中剧集（自动换源重试）")
        self._play_button.clicked.connect(self._emit_play)
        actions.addWidget(self._play_button)

        self._download_button = QPushButton("⬇ 下载选中集")
        self._download_button.setToolTip("下载剧集列表里选中的集（未选中则下载当前集）")
        self._download_button.clicked.connect(self._emit_download)
        actions.addWidget(self._download_button)

        self._all_button = QPushButton("⬇ 下载全部集")
        self._all_button.clicked.connect(lambda: self._emit_download(all_episodes=True))
        actions.addWidget(self._all_button)

        actions.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        actions.addWidget(buttons)
        layout.addLayout(actions)

    # ------------------------------------------------------------------ 数据

    def _fill_info(self, remote: RemoteVideo) -> None:
        self._title.setText(remote.display())
        self._description.setPlainText(remote.description or "（暂无简介）")
        parts = [f"类型：{remote.kind_label}"]
        if remote.category:
            parts.append(remote.category)
        if remote.region:
            parts.append(remote.region)
        if remote.actors:
            parts.append(f"主演：{remote.actors[:60]}")
        if remote.director:
            parts.append(f"导演：{remote.director}")
        parts.append(f"来源：{remote.source}")
        self._meta.setText(" · ".join(parts))

    def _load_episodes(self, remote: RemoteVideo) -> None:
        if remote.episodes:
            self._apply_detail(remote)
            return
        self._status.setText("正在获取剧集…")
        self._detail_worker = DetailWorker(remote, self)
        self._detail_worker.finishedDetail.connect(self._apply_detail)
        self._detail_worker.failed.connect(self._on_detail_failed)
        self._detail_worker.start()
        self._update_buttons()

    def _on_detail_failed(self, message: str) -> None:
        self._status.setText(f"获取剧集失败：{message}")

    def _apply_detail(self, remote: RemoteVideo) -> None:
        if remote is None:
            return
        self._remote = remote
        self._episode_list.clear()
        for episode in remote.episodes:
            item = QListWidgetItem(episode.name or f"第{episode.index + 1:02d}集")
            item.setData(Qt.ItemDataRole.UserRole, episode.index)
            self._episode_list.addItem(item)
        if self._episode_list.count():
            self._episode_list.setCurrentRow(0)
        if remote.episodes:
            self._status.setText(f"共 {len(remote.episodes)} 集")
        else:
            self._status.setText("该条目没有可用剧集（可换源重试）")
        self._fill_info(remote)
        self._update_buttons()

    def _on_episode_changed(self, row: int) -> None:
        episode = self.current_episode()
        if episode is None:
            return
        self._qualities = list(episode.qualities)
        self._variants_loaded = False
        self._refresh_quality_box()
        # 逐集探测真实分辨率（m3u8 主清单），失败时保留源标签
        url = episode.url
        if url and url.lower().split("?")[0].endswith(".m3u8"):
            self._quality_worker = QualityWorker(url, "", self)
            self._quality_worker.finishedQualities.connect(self._on_variants)
            self._quality_worker.failed.connect(lambda _m: None)
            self._quality_worker.start()

    def _on_variants(self, variants: list) -> None:
        """把 m3u8 主清单的清晰度并入下拉（不覆盖源标签，去重后按清晰度排序）。"""
        if not variants:
            return
        existing = {quality.label for quality in self._qualities}
        for variant in variants:
            label = variant.display
            if label and label not in existing:
                existing.add(label)
                self._qualities.append(Quality(label=label, url=variant.url))
        self._variants_loaded = True
        self._refresh_quality_box()

    def _refresh_quality_box(self) -> None:
        self._quality_box.blockSignals(True)
        self._quality_box.clear()
        if not self._qualities:
            self._quality_box.addItem("自动（源默认）")
        else:
            ordered = sorted(self._qualities, key=lambda item: item.rank, reverse=True)
            for quality in ordered:
                suffix = "（主清单）" if quality.url and quality.url.startswith("http") else ""
                self._quality_box.addItem(f"{quality.label}{suffix}", quality.label)
        self._quality_box.blockSignals(False)
        self._quality_box.setEnabled(self._quality_box.count() > 1)

    def _on_quality_index_changed(self, _index: int) -> None:
        pass   # 选择在读取时解析，不需要即时动作

    # ------------------------------------------------------------------ 对外

    def current_episode(self) -> Episode | None:
        item = self._episode_list.currentItem()
        if item is None:
            return None
        index = int(item.data(Qt.ItemDataRole.UserRole))
        for episode in self._remote.episodes:
            if episode.index == index:
                return episode
        return None

    def all_qualities(self) -> list[Quality]:
        """该集全部可选画质：源给出的线路 + m3u8 主清单解析出的真实分辨率。"""
        merged: list[Quality] = list(self._qualities)
        episode = self.current_episode()
        for quality in (episode.qualities if episode is not None else []) or []:
            if all(existing.label != quality.label for existing in merged):
                merged.append(quality)
        return merged

    def current_quality(self) -> Quality | None:
        """把下拉选择映射回 Quality（含 m3u8 主清单解析出的变体）。"""
        label = self._quality_box.currentData()
        if not label:
            return None
        return match_quality(self.all_qualities(), str(label))

    def selected_episodes(self) -> list[Episode]:
        items = self._episode_list.selectedItems()
        if not items:
            current = self.current_episode()
            return [current] if current is not None else []
        by_index = {episode.index: episode for episode in self._remote.episodes}
        selected = []
        for item in items:
            episode = by_index.get(int(item.data(Qt.ItemDataRole.UserRole)))
            if episode is not None:
                selected.append(episode)
        selected.sort(key=lambda episode: episode.index)
        return selected

    def _emit_play(self) -> None:
        episode = self.current_episode()
        if episode is None:
            self._toaster.info("请先选择要播放的剧集")
            return
        quality = self.current_quality()
        self.playRequested.emit(self._remote, episode, quality)

    def _emit_download(self, all_episodes: bool = False) -> None:
        episodes = self._remote.episodes if all_episodes else self.selected_episodes()
        if not episodes:
            self._toaster.info("没有可下载的剧集")
            return
        quality = self.current_quality()
        self.downloadRequested.emit(self._remote, episodes, quality)

    def _update_buttons(self) -> None:
        has_episodes = bool(self._remote.episodes)
        self._play_button.setEnabled(has_episodes)
        self._download_button.setEnabled(has_episodes)
        self._all_button.setEnabled(has_episodes)

    def shutdown(self) -> None:
        """关闭前等后台线程收尾。

        对话框可能刚打开就被关掉（用户点错、或详情接口很慢），此时线程还在跑。
        关键点：**绝不能把仍在运行的 QThread 引用丢掉** —— 一旦被垃圾回收，
        Qt 会以「QThread: Destroyed while thread is still running」终止整个进程。
        所以这里先耐心等待，实在等不到就强杀，绝不留下运行中的线程。
        """
        for attr in ("_detail_worker", "_quality_worker"):
            worker = getattr(self, attr, None)
            if worker is None:
                continue
            self._reap(worker)
            setattr(self, attr, None)

    @staticmethod
    def _reap(worker) -> None:  # noqa: ANN001
        """确保 worker 停下来再放手（native QThread 必须先结束）。"""
        try:
            if not worker.isRunning():
                return
            if not worker.wait(3000):
                worker.terminate()
                worker.wait(1000)
        except RuntimeError:
            # 底层对象已被销毁，无需再处理
            return

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)

    def done(self, result: int) -> None:  # noqa: N802
        # reject()/accept()/关闭按钮都会走 done()，统一在此收尾
        self.shutdown()
        super().done(result)
