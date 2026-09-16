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
from modu_workbench.services import app_context
from modu_workbench.services.config import video_dir
from modu_workbench.ui_kit.toast import Toaster
from modu_workbench.ui_kit.widgets import make_nav_button, set_nav_active

from .detail import VideoDetailDialog
from .history import VideoHistoryPage
from .library_page import VideoLibraryPage
from .player import VideoPlayerDialog, local_file_url
from .search import VideoSearchPage
from .sources import VideoSourceDialog
from .widgets import (
    DetailWorker,
    DownloadWorker,
    ResolveWorker,
    video_auto_switch_pref,
    video_download_dir_pref,
)

SEARCH_KEY = "search"
LIBRARY_KEY = "library"
HISTORY_KEY = "history"

# 播放进度回写节流（毫秒）
PROGRESS_SAVE_MS = 10_000


def is_hls_url(url: str) -> bool:
    """地址是否是 HLS 清单（m3u8 需要走网页播放内核 + 清单代理）。"""
    return ".m3u8" in (url or "").lower().split("?", 1)[0]


class VideoBoardPage(QWidget):
    """墨软影视板块：三个子页 + 状态条 + 播放/下载编排。

    依赖（library / storage / registry）默认取应用级单例（见 services.app_context），
    同时支持注入 —— 便于测试隔离，也便于将来做「多库」扩展。
    """

    def __init__(self, parent: QWidget | None = None, *, library: VideoLibrary | None = None,
                 storage: VideoStorage | None = None,
                 registry: VideoRegistry | None = None):
        super().__init__(parent)
        self._toaster = Toaster(self)
        # 单一事实来源：一切播放/下载解析都走 self._library.registry。
        # 若只给了 registry（自定义源集 / 测试替身），就用它拼一个库，
        # 避免出现「库用内置源、播放走另一个源」这种自相矛盾的状态。
        if library is not None:
            self._library: VideoLibrary = library
        elif registry is not None:
            self._library = VideoLibrary(storage or app_context.video_storage(), video_dir(), registry)
        else:
            self._library = app_context.video_library()
        self._storage = storage or app_context.video_storage()
        # 注册表始终取库里的那一个，保证界面展示的源与解析用的源一致
        self._registry = self._library.registry

        self._detail_workers: list[DetailWorker] = []
        self._resolve_worker: ResolveWorker | None = None
        self._download_worker: DownloadWorker | None = None

        # 播放会话：当前在播放器里播放的条目与剧集，供换集/换清晰度复用
        self._player: VideoPlayerDialog | None = None
        self._session_video: Video | None = None
        self._session_remote: RemoteVideo | None = None
        self._session_episode: Episode | None = None
        self._session_source = ""
        self._session_referer = ""

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
        nav_row.addStretch(1)
        self._source_hint = QLabel("")
        self._source_hint.setObjectName("readerStatus")
        nav_row.addWidget(self._source_hint)
        layout.addWidget(nav)

        self._stack = QStackedWidget()
        self._search = VideoSearchPage(self._library, self._storage, self._toaster)
        self._library_page = VideoLibraryPage(self._library, self._storage, self._toaster)
        self._history_page = VideoHistoryPage(self._library, self._storage, self._toaster)
        self._indexes = {
            SEARCH_KEY: self._stack.addWidget(self._search),
            LIBRARY_KEY: self._stack.addWidget(self._library_page),
            HISTORY_KEY: self._stack.addWidget(self._history_page),
        }
        layout.addWidget(self._stack, 1)

        # 底部状态条（下载进度）
        bar = QWidget()
        bar.setObjectName("bottomBar")
        bar_row = QHBoxLayout(bar)
        bar_row.setContentsMargins(16, 8, 16, 8)
        bar_row.setSpacing(10)
        self._status = QLabel("就绪。搜索后双击条目选集，可在线播放或下载到本地。")
        self._status.setObjectName("readerStatus")
        bar_row.addWidget(self._status, 1)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedWidth(240)
        bar_row.addWidget(self._progress)
        self._cancel_download_button = QPushButton("取消下载")
        self._cancel_download_button.setObjectName("dangerButton")
        self._cancel_download_button.setEnabled(False)
        self._cancel_download_button.clicked.connect(self._cancel_download)
        bar_row.addWidget(self._cancel_download_button)
        layout.addWidget(bar)

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

    def _play_online(self, remote: RemoteVideo, episode: Episode | None,
                     quality) -> None:  # noqa: ANN001
        """搜索页请求播放：缺剧集就补，再解析地址。"""
        if remote is None:
            return
        if episode is None and not remote.episodes:
            self._status.setText(f"正在获取剧集：{remote.title}…")
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

    def _resolve_and_play(self, remote: RemoteVideo, episode: Episode | None,
                          quality, *, quality_label: str = "",
                          start_ms: int = 0, title: str = "") -> None:  # noqa: ANN001
        """解析播放地址 → 打开/复用播放器。"""
        if self._resolve_worker is not None and self._resolve_worker.isRunning():
            self._toaster.info("正在解析上一个播放地址，请稍候")
            return
        self._status.setText(f"正在解析播放地址：{remote.title} {episode.name if episode else ''}")
        worker = ResolveWorker(
            remote, episode, quality,
            allow_cross_source=video_auto_switch_pref(), parent=self,
            library=self._library,
        )
        worker.finishedResolve.connect(
            lambda resolved, video: self._on_resolved(resolved, video, quality_label, start_ms, title)
        )
        worker.failed.connect(self._on_resolve_failed)
        worker.finished.connect(self._on_resolve_finished)
        self._resolve_worker = worker
        worker.start()

    def _on_resolved(self, resolved, video, quality_label: str, start_ms: int, title: str) -> None:  # noqa: ANN001
        if resolved is None or video is None:
            return
        if resolved.switched:
            self._toaster.info(resolved.note or "已自动换源")
        self._session_video = video
        self._session_remote = resolved.video
        self._session_episode = resolved.episode
        self._session_source = resolved.source
        self._session_referer = resolved.referer

        label = resolved.quality_label or (quality_label if quality_label else (video.quality or ""))
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
            self._player.show()
        else:
            self._player.set_qualities(qualities, label)
            self._player.set_episode_nav(has_prev=has_prev, has_next=has_next)
            self._player.switch_source(
                resolved.url, is_hls=resolved.is_hls, referer=resolved.referer,
                title=display_title, start_ms=start_ms or self._resume_position(video),
                status=f"正在播放：{display_title}（{resolved.source}）",
            )
            self._player.show()
            self._player.raise_()

        self._library.record_play(video, quality=label, source=resolved.source)
        self._status.setText(
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
                remote, episode, None, quality_label=label,
                start_ms=position, title=self._play_title(remote, episode),
            )
            return
        if not looks_like_media_url(quality.url):
            # 地址是「播放页 / 分享页」而不是媒体文件：必须走解析链路拿到真实地址，
            # 否则播放器会把 HTML 当视频解，表现为「换了清晰度就播不了」
            self._toaster.info(f"正在解析「{label}」线路的真实播放地址…")
            self._resolve_and_play(
                remote, episode, quality, quality_label=label,
                start_ms=position, title=self._play_title(remote, episode),
            )
            return
        self._session_video_quality(label)
        if self._player is not None:
            self._player.switch_source(
                quality.url, is_hls=is_hls_url(quality.url),
                referer=self._session_referer, start_ms=position,
                status=f"已切换到 {label}",
            )

    def _session_video_quality(self, label: str) -> None:
        if self._session_video is not None:
            self._session_video.quality = label
            try:
                self._storage.upsert_video(self._session_video)
            except Exception:  # noqa: BLE001
                pass

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
            remote, target, None,
            title=self._play_title(remote, target),
        )

    def _on_player_closed(self, position_ms: int) -> None:
        if self._session_video is not None and position_ms > 0:
            from .widgets import video_settings

            video_settings().setValue(f"video/pos/{self._session_video.id}", int(position_ms))
        if getattr(self, "_progress_timer", None) is not None:
            self._progress_timer.stop()
        self._status.setText("已关闭播放器。")

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
                remote, episode, None,
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
        title = video.display()
        start = self._resume_position(video)
        if self._player is None:
            self._player = VideoPlayerDialog(
                self, title=title, url=url, is_hls=False, start_ms=start,
                qualities=[video.quality] if video.quality and video.quality != "本地" else [],
                current_quality=video.quality, episode_label=video.episode_label,
            )
            self._player.qualityRequested.connect(self._on_quality_requested)
            self._player.episodeRequested.connect(self._on_episode_step)
            self._player.closed.connect(self._on_player_closed)
            self._player.finished.connect(self._on_player_finished)
            self._player.show()
        else:
            self._player.set_qualities([], video.quality)
            self._player.switch_source(url, is_hls=False, start_ms=start, title=title)
            self._player.show()
            self._player.raise_()
        self._library.record_play(video, quality=video.quality or "本地", source="local")
        self._status.setText(f"正在播放本地文件：{title}")
        self._history_page.reload()
        self._start_progress_timer()

    # ------------------------------------------------------------------ 下载

    def _handle_download_request(self, remote: RemoteVideo, episodes, quality) -> None:  # noqa: ANN001
        """搜索页的下载请求：参数可能是「整部（episodes=None）」或「指定若干集」。"""
        if remote is None:
            return
        if episodes is None:
            if not remote.episodes:
                self._status.setText(f"正在获取剧集：{remote.title}…")
                worker = DetailWorker(remote, self)
                worker.finishedDetail.connect(
                    lambda detailed, r=remote, q=quality: self._handle_download_request(detailed or r, None, q)
                )
                worker.failed.connect(lambda message: self._toaster.error(f"获取剧集失败：{message}"))
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
        self._progress.setValue(0)
        self._cancel_download_button.setEnabled(True)
        self._download_worker = DownloadWorker(
            tasks, dest, allow_cross_source=video_auto_switch_pref(), parent=self,
            library=self._library,
        )
        self._download_worker.progressed.connect(self._on_download_progress)
        self._download_worker.taskDone.connect(self._on_task_done)
        self._download_worker.finishedAll.connect(self._on_download_finished)
        self._download_worker.failed.connect(self._on_download_failed)
        self._download_worker.finished.connect(self._on_download_thread_finished)
        self._status.setText(f"开始下载 {len(tasks)} 集到 {dest}")
        self._download_worker.start()

    def _cancel_download(self) -> None:
        if self._download_worker is not None:
            self._download_worker.cancel()
            self._status.setText("正在取消下载…")

    def _on_download_progress(self, done: int, total: int, message: str) -> None:
        self._progress.setValue(int(done * 100 / total) if total else 0)
        self._status.setText(message)

    def _on_task_done(self, result) -> None:  # noqa: ANN001
        if not result.ok:
            label = f"{result.title} {result.episode_label}".strip()
            self._toaster.error(f"{label}：{result.message}")

    def _on_download_finished(self, results: list) -> None:
        self._progress.setValue(100)
        ok = [item for item in results if item.ok]
        failed = [item for item in results if not item.ok]
        switched = [item for item in ok if item.switched_from]
        summary = f"下载完成：成功 {len(ok)}，失败 {len(failed)}"
        if switched:
            summary += f"；其中 {len(switched)} 集自动换源"
        if failed:
            reasons: list[str] = []
            for item in failed[:3]:
                if item.message not in reasons:
                    reasons.append(item.message)
            summary += "；失败原因：" + "；".join(reasons)
        self._status.setText(summary)
        if ok:
            self._toaster.success(f"已下载 {len(ok)} 集，可在「我的视频」查看")
        else:
            self._toaster.error("全部下载失败：可在「源设置」里调整源，或改用其他条目")
        self._refresh_pages()

    def _on_download_failed(self, message: str) -> None:
        self._status.setText(f"下载失败：{message}")
        self._toaster.error(f"下载失败：{message}")

    def _on_download_thread_finished(self) -> None:
        self._download_worker = None
        self._cancel_download_button.setEnabled(False)

    # ------------------------------------------------------------------ 线程清理

    def _forget_detail_worker(self, worker: DetailWorker) -> None:
        if worker in self._detail_workers:
            self._detail_workers.remove(worker)

    def _on_resolve_failed(self, message: str) -> None:
        self._status.setText(f"解析失败：{message}")
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
        for worker in (self._resolve_worker, self._download_worker):
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    if isinstance(worker, DownloadWorker):
                        worker.cancel()
                    worker.wait(3000)
            except RuntimeError:
                continue
        self._resolve_worker = None
        self._download_worker = None
        if getattr(self, "_progress_timer", None) is not None:
            self._progress_timer.stop()
        if self._player is not None:
            self._player.close()
            self._player = None
