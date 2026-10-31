"""墨软影视板块：搜索下载 / 我的视频 / 分类收藏 / 播放历史 / 源设置。

这是第四板块的装配层，负责把「多源解析 → 播放器 / 下载器 → 入库 → 历史」
串起来，界面细节都在各子页里。所有联网与磁盘操作都走后台线程。

关键流程：
1. **在线播放**：用户点播 → `DetailWorker` 补齐剧集（若缺）→ `ResolveWorker`
   走注册表解析地址（失败自动换源）→ 打开播放器 → 记历史；
2. **下载**：解析 → 后台下载（HLS 合流 / 直链）→ 入库 → 记历史；
3. **播放器内换清晰度/换集**：复用同一条解析链路，就地在播放器里切换地址。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.video import (
    Episode,
    RemoteVideo,
    Video,
    VideoLibrary,
    VideoRegistry,
    VideoStorage,
    apply_quality_choice,
    looks_like_media_url,
)
from . import context as app_context
from modu_workbench.core.platform.paths import video_dir
from modu_workbench.ui_kit.components import TaskBar, chip
from modu_workbench.ui_kit.toast import Toaster
from modu_workbench.ui_kit.tokens import PAGE_MARGIN, ROW_GAP, SPACE
from modu_workbench.ui_kit.widgets import make_nav_button, set_nav_active

from .history import VideoHistoryPage
from .library_page import VideoLibraryPage
from modu_workbench.ui_kit.media.player import VideoPlayerDialog, local_file_url
from .search import VideoSearchPage
from .sources import VideoSourceDialog
from .widgets import (
    DanmakuWorker,
    DetailWorker,
    DownloadWorker,
    ResolveWorker,
    video_auto_switch_pref,
    video_download_dir_pref,
)
from modu_workbench.core.platform.logging import swallow

SEARCH_KEY = "search"
LIBRARY_KEY = "library"
HISTORY_KEY = "history"

# 播放进度回写节流（毫秒）
PROGRESS_SAVE_MS = 10_000


def is_hls_url(url: str) -> bool:
    """地址是否是 HLS 清单（m3u8 需要走清单代理 + 分片预取）。"""
    return ".m3u8" in (url or "").lower().split("?", 1)[0]


def _episode_number(episode) -> int:  # noqa: ANN001
    """从剧集名里解析集数（弹幕匹配要用）；解析不出返回 0。

    真正的实现放在共享层（ui_kit.media.danmaku_bridge.episode_number_of）：
    板块直接引用 core/anime 会被架构闸门拦下。
    """
    from modu_workbench.ui_kit.media.danmaku_bridge import episode_number_of

    return episode_number_of(episode)


class VideoBoardPage(QWidget):
    """墨软影视板块：三个子页 + 状态条 + 播放/下载编排。

    依赖（library / storage / registry）默认取应用级单例（见 services.app_context），
    同时支持注入 —— 便于测试隔离，也便于将来做「多库」扩展。
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        library: VideoLibrary | None = None,
        storage: VideoStorage | None = None,
        registry: VideoRegistry | None = None,
    ):
        super().__init__(parent)
        self._toaster = Toaster(self)
        # 单一事实来源：一切播放/下载解析都走 self._library.registry。
        # 若只给了 registry（自定义源集 / 测试替身），就用它拼一个库，
        # 避免出现「库用内置源、播放走另一个源」这种自相矛盾的状态。
        if library is not None:
            self._library: VideoLibrary = library
        elif registry is not None:
            self._library = VideoLibrary(
                storage or app_context.video_storage(), video_dir(), registry
            )
        else:
            self._library = app_context.video_library()
        self._storage = storage or app_context.video_storage()
        # 注册表始终取库里的那一个，保证界面展示的源与解析用的源一致
        self._registry = self._library.registry

        self._detail_workers: list[DetailWorker] = []
        self._resolve_worker: ResolveWorker | None = None
        self._download_worker: DownloadWorker | None = None
        # 下载队列状态：失败的原始任务留着重试（`.parts/` 已保留，重试即续传）
        self._download_tasks: list = []
        self._failed_tasks: list = []
        self._danmaku_worker: DanmakuWorker | None = None

        # 播放会话：当前在播放器里播放的条目与剧集，供换集/换清晰度复用
        self._player: VideoPlayerDialog | None = None
        self._session_video: Video | None = None
        self._session_remote: RemoteVideo | None = None
        self._session_episode: Episode | None = None
        self._session_source = ""
        self._session_referer = ""
        # 正在播放的清晰度标签（stall 自愈用它决定降到哪一档）
        self._session_quality_label = ""
        # 自动降档计数（在构造函数里就初始化：不能在 _connect_self_heal 里才建，
        # 否则卡顿信号先到时会 AttributeError）
        self._downgrade_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 子页导航（页面切换）+ 源状态 + 源设置/板块设置
        nav = QWidget()
        nav_row = QHBoxLayout(nav)
        nav_row.setContentsMargins(PAGE_MARGIN, SPACE["md"], PAGE_MARGIN, SPACE["sm"])
        nav_row.setSpacing(ROW_GAP)
        self._nav_buttons: dict[str, QPushButton] = {}
        for key, label in (
            (SEARCH_KEY, "🔍 搜索下载"),
            (LIBRARY_KEY, "🎬 我的视频"),
            (HISTORY_KEY, "🕘 播放历史"),
        ):
            button = make_nav_button(label)
            button.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self._nav_buttons[key] = button
            nav_row.addWidget(button)

        sources_button = make_nav_button("🧩 源设置")
        sources_button.clicked.connect(self._open_sources)
        nav_row.addWidget(sources_button)

        settings_button = make_nav_button("⚙ 板块设置")
        settings_button.setToolTip("打开「设置 → 影视」：下载目录、自动换源、数据源启用")
        settings_button.clicked.connect(self._open_board_settings)
        nav_row.addWidget(settings_button)

        # 下载控制（暂停/继续 + 重试失败项）：
        # 放在导航行而不是 TaskBar 里 —— TaskBar 是全板块共用的，加按钮会影响别的板块
        self._pause_button = make_nav_button("⏸ 暂停下载")
        self._pause_button.setToolTip("暂停后已下载的分片会保留，继续时从断点接着下")
        self._pause_button.clicked.connect(self.toggle_pause_download)
        self._pause_button.setEnabled(False)
        nav_row.addWidget(self._pause_button)

        self._retry_button = make_nav_button("↻ 重试失败项")
        self._retry_button.setToolTip("重试上次失败的条目（已下载的分片不会重下）")
        self._retry_button.clicked.connect(self.retry_failed_downloads)
        self._retry_button.setEnabled(False)
        nav_row.addWidget(self._retry_button)

        nav_row.addStretch(1)
        self._source_hint = chip("")
        nav_row.addWidget(self._source_hint)
        layout.addWidget(nav)

        # 统一任务条：全板块只此一条，空闲时自动收起进度与取消
        self._task_bar = TaskBar("就绪。搜索后双击条目选集，可在线播放或下载到本地。")
        self._task_bar.cancelled.connect(self._cancel_download)
        self._cancel_download_button = self._task_bar._cancel  # noqa: SLF001  兼容既有调用

        self._stack = QStackedWidget()
        self._search = VideoSearchPage(
            self._library, self._storage, self._toaster, task_bar=self._task_bar
        )
        self._library_page = VideoLibraryPage(
            self._library, self._storage, self._toaster, task_bar=self._task_bar
        )
        self._history_page = VideoHistoryPage(
            self._library, self._storage, self._toaster, task_bar=self._task_bar
        )
        self._indexes = {
            SEARCH_KEY: self._stack.addWidget(self._search),
            LIBRARY_KEY: self._stack.addWidget(self._library_page),
            HISTORY_KEY: self._stack.addWidget(self._history_page),
        }
        layout.addWidget(self._stack, 1)
        layout.addWidget(self._task_bar)

        # 数据联动
        self._search.playRequested.connect(self._play_online)
        self._search.downloadRequested.connect(self._handle_download_request)
        self._search.libraryChanged.connect(self._refresh_pages)
        self._library_page.playRequested.connect(self._play_library)
        self._library_page.downloadRequested.connect(self._download_videos)
        self._library_page.libraryChanged.connect(self._refresh_pages)
        self._history_page.playRequested.connect(self._play_library)
        self._history_page.downloadRequested.connect(self._download_videos)
        self._history_page.libraryChanged.connect(self._refresh_pages)

        self.show_page(SEARCH_KEY)
        self._update_source_hint()

    # ------------------------------------------------------------------ 状态

    @property
    def _status(self) -> QLabel:
        """兼容旧引用：状态文字来自统一任务条。"""
        return self._task_bar._label  # noqa: SLF001

    @property
    def _progress(self) -> QProgressBar:
        """兼容旧引用：进度来自统一任务条。"""
        return self._task_bar._progress  # noqa: SLF001

    # ------------------------------------------------------------------ 导航

    def show_page(self, key: str) -> None:
        index = self._indexes.get(key)
        if index is None:
            return
        self._stack.setCurrentIndex(index)
        for nav_key, button in self._nav_buttons.items():
            set_nav_active(button, nav_key == key)
        if key == LIBRARY_KEY:
            self._library_page.reload()
        elif key == HISTORY_KEY:
            self._history_page.reload()
        elif key == SEARCH_KEY:
            self._search.reload_sources()
            self._update_source_hint()

    def on_shown(self) -> None:
        self._update_source_hint()
        for key, index in self._indexes.items():
            if index == self._stack.currentIndex():
                self.show_page(key)
                break

    def _update_source_hint(self) -> None:
        enabled = [info for info in self._registry.infos() if self._registry.is_enabled(info.key)]
        degraded = self._registry.degraded_keys()
        text = f"已启用 {len(enabled)} 个源"
        if degraded:
            text += f"；临时降级 {len(degraded)} 个"
        self._source_hint.setText(text)

    def _open_sources(self) -> None:
        dialog = VideoSourceDialog(self)
        dialog.exec()
        self._update_source_hint()
        self._search.reload_sources()

    def _open_board_settings(self) -> None:
        """打开统一设置对话框的「影视」页（设置按板块区分）。"""
        from modu_workbench.ui_kit.settings import SettingsDialog

        SettingsDialog(self, initial="video").exec()
        self._update_source_hint()
        self._search.reload_sources()

    def _refresh_pages(self) -> None:
        self._library_page.reload()
        self._history_page.reload()

    # ------------------------------------------------------------------ 在线播放

    def _play_online(
        self, remote: RemoteVideo, episode: Episode | None, quality
    ) -> None:  # noqa: ANN001
        """搜索页请求播放：缺剧集就补，再解析地址。"""
        if remote is None:
            return
        if episode is None and not remote.episodes:
            self._task_bar.report(f"正在获取剧集：{remote.title}…")
            worker = DetailWorker(remote, self)
            worker.finishedDetail.connect(
                lambda detailed, r=remote: self._play_online(detailed or r, None, None)
            )
            worker.failed.connect(lambda message: self._toaster.error(f"获取剧集失败：{message}"))
            worker.finished.connect(lambda w=worker: self._forget_detail_worker(w))
            self._detail_workers.append(worker)
            worker.start()
            return
        target = episode or (remote.episodes[0] if remote.episodes else None)
        self._resolve_and_play(remote, target, quality)

    def _resolve_and_play(
        self,
        remote: RemoteVideo,
        episode: Episode | None,
        quality,
        *,
        quality_label: str = "",
        start_ms: int = 0,
        title: str = "",
    ) -> None:  # noqa: ANN001
        """解析播放地址 → 打开/复用播放器。"""
        if self._resolve_worker is not None and self._resolve_worker.isRunning():
            self._toaster.info("正在解析上一个播放地址，请稍候")
            return
        self._task_bar.report(f"正在解析播放地址：{remote.title} {episode.name if episode else ''}")
        worker = ResolveWorker(
            remote,
            episode,
            quality,
            allow_cross_source=video_auto_switch_pref(),
            parent=self,
            library=self._library,
        )
        worker.finishedResolve.connect(
            lambda resolved, video: self._on_resolved(
                resolved, video, quality_label, start_ms, title
            )
        )
        worker.failed.connect(self._on_resolve_failed)
        worker.finished.connect(self._on_resolve_finished)
        self._resolve_worker = worker
        worker.start()

    def _on_resolved(
        self, resolved, video, quality_label: str, start_ms: int, title: str
    ) -> None:  # noqa: ANN001
        if resolved is None or video is None:
            return
        if resolved.switched:
            self._toaster.info(resolved.note or "已自动换源")
        self._session_video = video
        self._session_remote = resolved.video
        self._session_episode = resolved.episode
        self._session_source = resolved.source
        self._session_referer = resolved.referer

        label = resolved.quality_label or (
            quality_label if quality_label else (video.quality or "")
        )
        # 记住"正在播的是哪一档"：stall 自愈要靠它才知道该往哪一档降
        self._session_quality_label = str(label or "")
        qualities = self._quality_labels_for(resolved.episode)
        display_title = title or self._play_title(resolved.video, resolved.episode)

        has_prev, has_next = self._episode_nav_flags(resolved.episode)
        if self._player is None:
            self._player = VideoPlayerDialog(
                self,
                title=display_title,
                url=resolved.url,
                is_hls=resolved.is_hls,
                referer=resolved.referer,
                start_ms=start_ms or self._resume_position(video),
                qualities=qualities,
                current_quality=label,
                episode_label=resolved.episode.name if resolved.episode else "",
                has_prev=has_prev,
                has_next=has_next,
            )
            self._player.qualityRequested.connect(self._on_quality_requested)
            self._player.episodeRequested.connect(self._on_episode_step)
            self._player.closed.connect(self._on_player_closed)
            self._player.finished.connect(self._on_player_finished)
            self._connect_self_heal(self._player)
            self._player.show()
        else:
            self._player.set_qualities(qualities, label)
            self._player.set_episode_nav(has_prev=has_prev, has_next=has_next)
            self._player.switch_source(
                resolved.url,
                is_hls=resolved.is_hls,
                referer=resolved.referer,
                title=display_title,
                start_ms=start_ms or self._resume_position(video),
                status=f"正在播放：{display_title}（{resolved.source}）",
            )
            self._player.show()
            self._player.raise_()

        self._library.record_play(video, quality=label, source=resolved.source)
        self._task_bar.idle(
            f"正在播放：{display_title} · 画质 {label or '默认'} · 源 {resolved.source}"
        )
        self._history_page.reload()
        self._start_progress_timer()

    def _play_title(self, remote: RemoteVideo, episode: Episode | None) -> str:
        base = remote.title if remote is not None else "墨软影视"
        if episode is not None and episode.name:
            return f"{base} · {episode.name}"
        return base

    def _quality_labels_for(self, episode: Episode | None) -> list[str]:
        if episode is None:
            return []
        return [quality.label for quality in (episode.qualities or []) if quality.label]

    def _episode_nav_flags(self, episode: Episode | None) -> tuple[bool, bool]:
        remote = self._session_remote
        if episode is None or remote is None or not remote.episodes:
            return False, False
        indexes = [item.index for item in remote.episodes]
        if episode.index not in indexes:
            return False, False
        position = indexes.index(episode.index)
        return position > 0, position < len(indexes) - 1

    def _resume_position(self, video: Video | None) -> int:
        """续播：读取上次进度（本地条目用 QSettings，在线条目按播放记录）。"""
        if video is None or not video.id:
            return 0
        from .widgets import video_settings

        value = video_settings().value(f"video/pos/{video.id}", 0)
        try:
            position = int(value or 0)
        except (TypeError, ValueError):
            position = 0
        # 不足 10 秒的进度没有续播意义
        return position if position > 10_000 else 0

    def _start_progress_timer(self) -> None:
        if getattr(self, "_progress_timer", None) is None:
            self._progress_timer = QTimer(self)
            self._progress_timer.setInterval(PROGRESS_SAVE_MS)
            self._progress_timer.timeout.connect(self._save_progress)
        self._progress_timer.start()

    def _save_progress(self) -> None:
        if self._player is None or self._session_video is None:
            return
        from .widgets import video_settings

        position = self._player.current_position_ms()
        if position > 0:
            video_settings().setValue(f"video/pos/{self._session_video.id}", position)

    # ------------------------------------------------------------------ 播放器联动

    def _on_quality_requested(self, label: str) -> None:
        """播放器里换清晰度：按标签找到对应画质地址，就地切换。"""
        episode = self._session_episode
        remote = self._session_remote
        if episode is None or remote is None:
            return
        position = self._player.current_position_ms() if self._player is not None else 0
        quality = apply_quality_choice(episode, label)
        if quality is None or not quality.url:
            # 源没给多地址：退化为换源解析（其他源可能真的提供该清晰度）
            self._toaster.info(f"当前线路没有独立的「{label}」地址，改为换源解析")
            self._resolve_and_play(
                remote,
                episode,
                None,
                quality_label=label,
                start_ms=position,
                title=self._play_title(remote, episode),
            )
            return
        if not looks_like_media_url(quality.url):
            # 地址是「播放页 / 分享页」而不是媒体文件：必须走解析链路拿到真实地址，
            # 否则播放器会把 HTML 当视频解，表现为「换了清晰度就播不了」
            self._toaster.info(f"正在解析「{label}」线路的真实播放地址…")
            self._resolve_and_play(
                remote,
                episode,
                quality,
                quality_label=label,
                start_ms=position,
                title=self._play_title(remote, episode),
            )
            return
        self._session_quality_label = str(label or "")
        self._session_video_quality(label)
        if self._player is not None:
            self._player.switch_source(
                quality.url,
                is_hls=is_hls_url(quality.url),
                referer=self._session_referer,
                start_ms=position,
                status=f"已切换到 {label}",
            )

    def _session_video_quality(self, label: str) -> None:
        if self._session_video is not None:
            self._session_video.quality = label
            try:
                self._storage.upsert_video(self._session_video)
            except Exception as error:  # noqa: BLE001  外部回调/第三方边界
                swallow("board 中的可选步骤", error)

    def _on_episode_step(self, step: int) -> None:
        """播放器里点上一集/下一集。"""
        remote = self._session_remote
        episode = self._session_episode
        if remote is None or episode is None or not remote.episodes:
            return
        indexes = [item.index for item in remote.episodes]
        if episode.index not in indexes:
            return
        position = indexes.index(episode.index) + int(step)
        if not (0 <= position < len(indexes)):
            self._toaster.info("已经到最后一集了" if step > 0 else "已经是第一集了")
            return
        target = remote.episodes[position]
        self._resolve_and_play(
            remote,
            target,
            None,
            title=self._play_title(remote, target),
        )

    def _connect_self_heal(self, player) -> None:  # noqa: ANN001
        """接上播放器的自愈与诊断信号（V1-h）。"""
        for signal_name in ("stallDetected", "diagnosticsRequested"):
            if not hasattr(player, signal_name):
                return  # 旧替身播放器没有这些信号，静默跳过
        self._downgrade_count = 0
        player.stallDetected.connect(self._on_stall_detected)
        player.diagnosticsRequested.connect(self._on_diagnostics_requested)
        if hasattr(player, "danmakuRequested"):
            player.danmakuRequested.connect(self._load_danmaku)
        if hasattr(player, "danmakuImportRequested"):
            player.danmakuImportRequested.connect(self._import_danmaku)

    # ------------------------------------------------------------------ 弹幕（V3-c）

    def _load_danmaku(self) -> None:
        """取当前播放条目的弹幕（弹弹play，只读；未配置时给出明确提示）。"""
        remote = self._session_remote
        episode = self._session_episode
        if self._player is None or remote is None:
            return
        if self._danmaku_worker is not None and self._danmaku_worker.isRunning():
            return
        number = _episode_number(episode)
        worker = DanmakuWorker(
            remote.title,
            episode.name if episode else "",
            episode_number=number,
            year=remote.year or "",
            parent=self,
        )
        worker.fetched.connect(self._on_danmaku_fetched)
        worker.finished.connect(self._on_danmaku_thread_finished)
        self._danmaku_worker = worker
        if self._player is not None:
            self._player.set_status("正在获取弹幕…（未配置弹幕 AppId 时可右键弹幕按钮手动导入）")
        worker.start()

    def _on_danmaku_fetched(self, result) -> None:  # noqa: ANN001
        if self._player is None:
            return
        if getattr(result, "items", None):
            self._player.set_danmaku(result.items)
            self._player.set_status(result.note)
            if result.matched_title:
                self._toaster.success(f"弹幕：{result.note}")
        else:
            self._player.set_status(getattr(result, "note", "") or "没有可用的弹幕")

    def _import_danmaku(self) -> None:
        """手动导入本地弹幕 XML（自动匹配不上时的兜底）。"""
        from PySide6.QtWidgets import QFileDialog

        from modu_workbench.ui_kit.media.danmaku_bridge import import_danmaku_file

        path, _ = QFileDialog.getOpenFileName(
            self, "选择弹幕文件", "", "弹幕 XML (*.xml);;所有文件 (*)"
        )
        if not path:
            return
        result = import_danmaku_file(path)
        if getattr(result, "items", None) and self._player is not None:
            self._player.set_danmaku(result.items)
            self._player.set_status(result.note)
        elif self._player is not None:
            self._player.set_status(result.note)

    def _export_danmaku(self) -> None:
        """导出当前弹幕（与主流播放器互导）。"""
        from PySide6.QtWidgets import QFileDialog

        from modu_workbench.ui_kit.media.danmaku_bridge import export_danmaku_file

        if self._player is None:
            return
        items = self._player.danmaku_items() if hasattr(self._player, "danmaku_items") else []
        if not items:
            self._toaster.info("当前没有弹幕可导出（先右键弹幕按钮导入，或开启弹幕）")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出弹幕", "danmaku.xml", "弹幕 XML (*.xml)")
        if not path:
            return
        try:
            export_danmaku_file(path, items)
            self._toaster.success(f"已导出 {len(items)} 条弹幕")
        except OSError as error:
            self._toaster.error(f"导出失败：{error}")

    def _on_stall_detected(self, reason: str) -> None:
        """播放器报卡顿/起播超时 → 执行降档或换源（保留播放位置）。"""
        from modu_workbench.core.video.abr import MAX_DOWNGRADES, variant_label

        remote = self._session_remote
        episode = self._session_episode
        if remote is None or episode is None:
            return
        position = self._player.current_position_ms() if self._player is not None else 0
        if reason == "download":
            self._task_bar.note("该源长时间无法起播：建议先「下载到本地」，或手动换源。")
            return
        if reason == "downgrade" and self._downgrade_count < MAX_DOWNGRADES:
            variants = [quality for quality in (episode.qualities or []) if quality.url]
            target = self._lower_variant(variants, variant_label)
            if target is not None and getattr(target, "label", ""):
                self._downgrade_count += 1
                self._task_bar.note(
                    f"网络偏慢，正在自动降低清晰度到 {target.label}"
                    f"（第 {self._downgrade_count} 次）…"
                )
                self._on_quality_requested(target.label)
                return
            # 没有"比当前更低"的档可降 → 直接换源
        # 到底就换源（跨源定位同一集，保留进度）
        self._task_bar.note("该线路持续卡顿，正在自动更换片源…")
        self._swap_source_keeping_position(remote, episode, position)

    def _lower_variant(self, variants: list, label_of):  # noqa: ANN001, ANN202
        """挑一个**确实比当前更低**的档位；挑不到返回 None。

        为什么不能"从最高档往下数一格"（这是首版的真实缺陷）：
        播放的是主清单（`episode.qualities` 常常为空、`session_video.quality` 也没写），
        若把"当前档"当成最高档，`next_lower` 就会从 1080P 直接跳到最低的 538P —— 画质断崖。
        所以：**先看当前档标签，认不出来就认为"已经在最低可用档"，直接去换源**，
        宁可换源也不要莫名其妙掉一半分辨率。
        """
        from modu_workbench.core.video.abr import next_lower

        if len(variants) < 2:
            return None
        labels = set()
        for candidate in (
            self._session_quality_label,
            getattr(self._session_video, "quality", "") if self._session_video else "",
            getattr(self._session_episode, "quality_label", "") if self._session_episode else "",
        ):
            if candidate:
                labels.add(str(candidate))
        current = next(
            (
                item
                for item in variants
                if label_of(item) in labels or getattr(item, "label", "") in labels
            ),
            None,
        )
        if current is None:
            return None
        return next_lower(variants, current)

    def _swap_source_keeping_position(
        self, remote: RemoteVideo, episode: Episode, position_ms: int
    ) -> None:
        """换到其他源播同一集，并保留播放位置（`registry` 会排除已失败的源）。"""
        target = self._player
        if target is not None and hasattr(target, "set_status"):
            target.set_status("正在自动更换线路…")
        self._resolve_and_play(
            remote,
            episode,
            None,
            start_ms=position_ms,
            title=self._play_title(remote, episode),
        )

    def _on_diagnostics_requested(self) -> None:
        """F12：把诊断摘要打到任务栏（完整信息在播放器浮层里）。"""
        if self._player is None:
            return
        try:
            info = self._player.diagnostics()
        except Exception:  # noqa: BLE001
            return
        self._task_bar.note(
            f"播放诊断：{info.get('渲染路径', '?')} · 后端 {info.get('后端', '?')} · "
            f"卡顿 {info.get('卡顿次数', 0)} 次 · 缓存命中率 "
            f"{info.get('代理/hit_rate', 0)}"
        )

    # ------------------------------------------------------------------ 快捷键补充

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """板块级快捷键：`Ctrl+D` 导出当前弹幕（播放器内快捷键见 ui_kit/media/player.py）。

        注意：播放时焦点在播放器窗口上，播放器的 `keyPressEvent` 先拿到按键；
        这条只在焦点回到板块（例如播放器已关闭、弹幕仍在内存里）时生效。
        """
        from PySide6.QtCore import Qt

        if event.key() == Qt.Key.Key_D and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._export_danmaku()
            return
        super().keyPressEvent(event)

    def export_current_danmaku(self) -> None:
        """对外暴露的导出入口（菜单/测试用）。"""
        self._export_danmaku()

    def _on_player_closed(self, position_ms: int) -> None:
        if self._session_video is not None and position_ms > 0:
            from .widgets import video_settings

            video_settings().setValue(f"video/pos/{self._session_video.id}", int(position_ms))
        if getattr(self, "_progress_timer", None) is not None:
            self._progress_timer.stop()
        self._task_bar.idle("已关闭播放器。")

    def _on_player_finished(self) -> None:
        self._player = None

    # ------------------------------------------------------------------ 本地库播放

    def _play_library(self, videos: list, start_index: int) -> None:
        """从「我的视频 / 历史」播放：本地文件直接播，在线条目重新解析。"""
        if not videos:
            return
        index = start_index if 0 <= start_index < len(videos) else 0
        video = videos[index]
        if video.exists:
            self._play_local(video)
            return
        if video.online_only and video.source and video.remote_id:
            remote = video.to_remote()
            episode = remote.episodes[0] if remote.episodes else None
            if episode is not None and not episode.url:
                episode = None
            self._resolve_and_play(
                remote,
                episode,
                None,
                title=video.display(),
            )
            return
        self._toaster.error("该条目既没有本地文件，也缺少在线信息（源或地址已丢失）")

    def _play_local(self, video: Video) -> None:
        """播放本地文件（走本机流服务，支持拖动进度）。"""
        if not video.exists:
            self._toaster.error(f"文件不存在：{video.file_path}")
            return
        try:
            url = local_file_url(video.file_path)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"无法启动本地播放服务：{error}")
            return
        self._session_video = video
        self._session_remote = None
        self._session_episode = None
        self._session_source = "local"
        self._session_referer = ""
        # 本地文件没有多档可选：清掉会话档位，避免残留上一次在线播放的档位影响自愈判定
        self._session_quality_label = "" if video.quality == "本地" else str(video.quality or "")
        self._downgrade_count = 0
        title = video.display()
        start = self._resume_position(video)
        if self._player is None:
            self._player = VideoPlayerDialog(
                self,
                title=title,
                url=url,
                is_hls=False,
                start_ms=start,
                qualities=[video.quality] if video.quality and video.quality != "本地" else [],
                current_quality=video.quality,
                episode_label=video.episode_label,
            )
            self._player.qualityRequested.connect(self._on_quality_requested)
            self._player.episodeRequested.connect(self._on_episode_step)
            self._player.closed.connect(self._on_player_closed)
            self._player.finished.connect(self._on_player_finished)
            self._connect_self_heal(self._player)
            self._player.show()
        else:
            self._player.set_qualities([], video.quality)
            self._player.switch_source(url, is_hls=False, start_ms=start, title=title)
            self._player.show()
            self._player.raise_()
        self._library.record_play(video, quality=video.quality or "本地", source="local")
        self._task_bar.idle(f"正在播放本地文件：{title}")
        self._history_page.reload()
        self._start_progress_timer()

    # ------------------------------------------------------------------ 下载

    def _handle_download_request(
        self, remote: RemoteVideo, episodes, quality
    ) -> None:  # noqa: ANN001
        """搜索页的下载请求：参数可能是「整部（episodes=None）」或「指定若干集」。"""
        if remote is None:
            return
        if episodes is None:
            if not remote.episodes:
                self._task_bar.report(f"正在获取剧集：{remote.title}…")
                worker = DetailWorker(remote, self)
                worker.finishedDetail.connect(
                    lambda detailed, r=remote, q=quality: self._handle_download_request(
                        detailed or r, None, q
                    )
                )
                worker.failed.connect(
                    lambda message: self._toaster.error(f"获取剧集失败：{message}")
                )
                worker.finished.connect(lambda w=worker: self._forget_detail_worker(w))
                self._detail_workers.append(worker)
                worker.start()
                return
            episodes = remote.episodes
        if isinstance(episodes, (Episode,)) or not isinstance(episodes, (list, tuple)):
            episodes = [episodes]
        episodes = [item for item in episodes if isinstance(item, Episode)]
        if not episodes:
            self._toaster.info("没有可下载的剧集")
            return
        self._start_download(remote, episodes, quality)

    def _download_videos(self, videos: list) -> None:
        """「我的视频 / 历史」里的在线条目重新下载。"""
        if not videos:
            return
        for video in videos:
            if video.exists:
                self._toaster.info(f"{video.title} 已经是本地文件")
                continue
            remote = video.to_remote()
            episode = remote.episodes[0] if remote.episodes else None
            if episode is None or not episode.url:
                # 缺地址：按源重新解析（走一次换源逻辑）
                self._handle_download_request(video.to_remote(), None, None)
                continue
            self._start_download(remote, [episode], None)

    def _start_download(self, remote: RemoteVideo, episodes: list, quality) -> None:  # noqa: ANN001
        if self._download_worker is not None and self._download_worker.isRunning():
            self._toaster.info("已有下载任务在进行，请等待或先取消")
            return
        dest = video_download_dir_pref()
        Path(dest).mkdir(parents=True, exist_ok=True)
        tasks = [(remote, episode, quality) for episode in episodes]

        self._download_tasks = tasks  # 失败重试队列要用原始任务
        self._failed_tasks = []
        self._download_worker = DownloadWorker(
            tasks,
            dest,
            allow_cross_source=video_auto_switch_pref(),
            parent=self,
            library=self._library,
        )
        self._download_worker.progressed.connect(self._on_download_progress)
        self._download_worker.taskDone.connect(self._on_task_done)
        self._download_worker.finishedAll.connect(self._on_download_finished)
        self._download_worker.failed.connect(self._on_download_failed)
        self._download_worker.finished.connect(self._on_download_thread_finished)
        self._task_bar.report(f"开始下载 {len(tasks)} 集到 {dest}")
        self._pause_button.setEnabled(True)
        self._pause_button.setText("⏸ 暂停下载")
        self._download_worker.start()

    def toggle_pause_download(self) -> None:
        """暂停 / 继续下载（真续传：已下载分片保留在 `.parts/`）。"""
        worker = self._download_worker
        if worker is None or not worker.isRunning():
            self._toaster.info("当前没有下载任务")
            return
        if worker.is_paused:
            worker.resume()
            self._pause_button.setText("⏸ 暂停下载")
            self._task_bar.set_text("已继续下载（从已下载的分片接着下）")
            self._toaster.success("已继续下载")
        else:
            worker.pause()
            self._pause_button.setText("▶ 继续下载")
            self._task_bar.set_text("已暂停下载：已下载的分片会保留，可随时继续")

    def retry_failed_downloads(self) -> None:
        """重试上一次失败的条目（不带断点信息时也会自动续传）。"""
        failed = list(getattr(self, "_failed_tasks", []) or [])
        if not failed:
            self._toaster.info("没有需要重试的下载项")
            return
        if self._download_worker is not None and self._download_worker.isRunning():
            self._toaster.info("已有下载任务在进行，请等待或先取消")
            return
        dest = video_download_dir_pref()
        Path(dest).mkdir(parents=True, exist_ok=True)
        self._failed_tasks = []
        self._download_worker = DownloadWorker(
            failed,
            dest,
            allow_cross_source=video_auto_switch_pref(),
            parent=self,
            library=self._library,
        )
        self._download_worker.progressed.connect(self._on_download_progress)
        self._download_worker.taskDone.connect(self._on_task_done)
        self._download_worker.finishedAll.connect(self._on_download_finished)
        self._download_worker.failed.connect(self._on_download_failed)
        self._download_worker.finished.connect(self._on_download_thread_finished)
        self._task_bar.report(f"重试 {len(failed)} 个失败项（已下载分片会续传）")
        self._pause_button.setEnabled(True)
        self._pause_button.setText("⏸ 暂停下载")
        self._download_worker.start()

    def _cancel_download(self) -> None:
        if self._download_worker is not None:
            self._download_worker.cancel()
            self._task_bar.set_text("正在取消下载…")

    def _task_key(self, task) -> str:  # noqa: ANN001
        """任务的身份键（失败重试队列去重用）：源 + 条目 + 集。"""
        from modu_workbench.core.video.models import Episode, RemoteVideo

        remote, episode, _quality = task
        episode_label = episode.name if isinstance(episode, Episode) else ""
        remote_id = remote.remote_id if isinstance(remote, RemoteVideo) else ""
        return f"{getattr(remote, 'source', '')}:{remote_id}:{episode_label}"

    def _on_download_progress(self, done: int, total: int, message: str) -> None:
        self._task_bar.report(message, done, total)

    def _on_task_done(self, result) -> None:  # noqa: ANN001
        if not result.ok:
            label = f"{result.title} {result.episode_label}".strip()
            self._toaster.error(f"{label}：{result.message}")

    def _on_download_finished(self, results: list) -> None:
        ok = [item for item in results if item.ok]
        failed = [item for item in results if not item.ok]
        switched = [item for item in ok if item.switched_from]
        summary = f"下载完成：成功 {len(ok)}，失败 {len(failed)}"
        if switched:
            summary += f"；其中 {len(switched)} 集自动换源"
        # 记下失败项：即便用户手动取消，`.parts/` 也留着，重试即续传
        failed_keys = {item.episode_label for item in failed}
        self._failed_tasks = [
            task
            for task in (getattr(self, "_download_tasks", []) or [])
            if (task[1].name if task[1] is not None else "") in failed_keys
        ]
        if failed:
            reasons: list[str] = []
            for item in failed[:3]:
                if item.message not in reasons:
                    reasons.append(item.message)
            summary += "；失败原因：" + "；".join(reasons)
            summary += "（可用「重试失败项」继续，已下载的分片不会重下）"
        self._task_bar.idle(summary)
        if ok:
            self._toaster.success(f"已下载 {len(ok)} 集，可在「我的视频」查看")
        elif failed:
            self._toaster.error("全部下载失败：可在「源设置」里调整源，或改用其他条目")
        self._refresh_pages()

    def _on_download_failed(self, message: str) -> None:
        self._task_bar.idle(f"下载失败：{message}")
        self._toaster.error(f"下载失败：{message}")

    def _on_danmaku_thread_finished(self) -> None:
        self._danmaku_worker = None

    def _on_download_thread_finished(self) -> None:
        self._download_worker = None
        if hasattr(self, "_pause_button"):
            self._pause_button.setEnabled(False)
            self._pause_button.setText("⏸ 暂停下载")
        if hasattr(self, "_retry_button"):
            self._retry_button.setEnabled(bool(self._failed_tasks))

    # ------------------------------------------------------------------ 线程清理

    def _forget_detail_worker(self, worker: DetailWorker) -> None:
        if worker in self._detail_workers:
            self._detail_workers.remove(worker)

    def _on_resolve_failed(self, message: str) -> None:
        self._task_bar.idle(f"解析失败：{message}")
        self._toaster.error(f"解析播放地址失败：{message}")

    def _on_resolve_finished(self) -> None:
        self._resolve_worker = None

    def shutdown(self) -> None:
        self._save_progress()
        self._search.shutdown()
        self._library_page.shutdown()
        self._history_page.shutdown()
        # 详情/解析/下载线程要先收尾，再关播放器：
        # 播放器关闭会触发 closed 信号，此时进度仍可正常保存
        for worker in list(self._detail_workers):
            try:
                if worker.isRunning():
                    worker.wait(2000)
            except RuntimeError:
                continue
        self._detail_workers.clear()
        for worker in (self._resolve_worker, self._download_worker, self._danmaku_worker):
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    if isinstance(worker, (DownloadWorker, DanmakuWorker)):
                        worker.cancel()
                    worker.wait(3000)
            except RuntimeError:
                continue
        self._resolve_worker = None
        self._download_worker = None
        self._danmaku_worker = None
        if getattr(self, "_progress_timer", None) is not None:
            self._progress_timer.stop()
        if self._player is not None:
            self._player.close()
            self._player = None
