"""墨软动漫板块：搜索 / 番剧库 / 时间表 / 规则 / 历史 五个子页 + 设置入口。

装配层的职责只有三件：

1. **把后端单例交给各子页**：`core/anime` 由并行开发，构造失败时整个板块必须
   还能打开（各子页显示空状态 + 可读原因），所以这里对 `anime_library()` 等
   全部 `try/except`，并把结果**注入**给子页（子页自己不碰单例）。
2. **编排播放与下载**：解析地址 → 打开共享播放器（`ui_kit/media/player.py`，
   与影视板块同一个）→ 定时写回进度；下载整季/单集走后台线程。
3. **收尾**：关闭时先把后台线程停下来，再关播放器（播放器关闭会触发进度回写）。

播放"上一集/下一集"的语义按方案 §4.5 实现：**同线路下一集 → 换线路第一集 →
换规则**（后两条由 `AnimeLibrary.next_episode()` 决定，板块只负责问它）。
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.ui_kit.components import TaskBar, chip
from modu_workbench.ui_kit.toast import Toaster
from modu_workbench.ui_kit.tokens import PAGE_MARGIN, ROW_GAP, SPACE
from modu_workbench.ui_kit.widgets import make_nav_button, set_nav_active

from . import context as app_context
from .detail import AnimeDetailDialog
from .history import AnimeHistoryPage
from .library_page import AnimeLibraryPage
from .rules_page import AnimeRulesPage
from .schedule import AnimeSchedulePage
from .search import NO_BACKEND as SEARCH_NO_BACKEND
from .search import AnimeSearchPage
from .widgets import (
    anime_download_dir_pref,
    episode_label,
    series_of,
    setting_bool,
)

SEARCH_KEY = "search"
LIBRARY_KEY = "library"
SCHEDULE_KEY = "schedule"
RULES_KEY = "rules"
HISTORY_KEY = "history"

#: 进度回写节流（毫秒）：与影视板块同频，避免频繁写库
PROGRESS_SAVE_MS = 10_000

#: 子页顺序即导航顺序（测试按这个顺序断言按钮文案）
NAV_ITEMS = (
    (SEARCH_KEY, "🔍 搜索"),
    (LIBRARY_KEY, "📺 番剧库"),
    (SCHEDULE_KEY, "🗓 时间表"),
    (RULES_KEY, "🧩 规则"),
    (HISTORY_KEY, "🕘 历史"),
)

NO_BACKEND = "动漫后端未就绪：core/anime 核心模块尚未可用"


def _message_of(error: Exception) -> str:
    """异常 → 可读原因（后端常用 `str(error)`，空串时退回类名）。"""
    return str(error) or error.__class__.__name__


class AnimeOpenWorker(QThread):
    """"从头播"：先拿详情（搜索结果是 SearchHit/SearchGroup，还不是番剧对象），
    再取第一条线路的第一集并解析地址。"""

    finishedOpen = Signal(object, object, object)   # series, episode, resolved
    failed = Signal(str)

    def __init__(self, library, payload, parent: QWidget | None = None):  # noqa: ANN001
        super().__init__(parent)
        self._library = library
        self._payload = payload

    def run(self) -> None:  # noqa: D102
        library = self._library
        if library is None:
            self.failed.emit(NO_BACKEND)
            return
        try:
            payload = self._payload
            if hasattr(payload, "roads") or hasattr(payload, "series"):
                detail = payload                       # 已经是详情
            else:
                detail = library.detail(payload)
            series = series_of(detail) or series_of(payload)
            episode = None
            for road in list(getattr(detail, "roads", None) or []):
                episodes = list(getattr(road, "episodes", None) or [])
                if episodes:
                    episode = episodes[0]
                    break
            if series is None or episode is None:
                self.failed.emit("详情里没有剧集（检查规则的 chapterRoads / chapterResult）")
                return
            resolved = library.resolve(series, episode)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(_message_of(error))
            return
        self.finishedOpen.emit(series, episode, resolved)


class AnimeStepWorker(QThread):
    """换集：问后端"上一集/下一集是哪一集"，再解析地址。"""

    finishedStep = Signal(object, object)   # episode, resolved
    failed = Signal(str)

    def __init__(self, library, series, road_index: int, episode_index: int,  # noqa: ANN001
                 step: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._series = series
        self._road_index = road_index
        self._episode_index = episode_index
        self._step = step

    def run(self) -> None:  # noqa: D102
        library = self._library
        if library is None or self._series is None:
            self.failed.emit(NO_BACKEND)
            return
        try:
            getter = getattr(library, "next_episode" if self._step > 0 else "prev_episode", None)
            if not callable(getter):
                self.failed.emit("后端还不支持自动换集（next_episode/prev_episode 缺失）")
                return
            pair = getter(self._series, self._road_index, self._episode_index)
            if not pair:
                self.failed.emit("已经到最后一集了" if self._step > 0 else "已经是第一集了")
                return
            road_index, episode_index = int(pair[0]), int(pair[1])
            detail = library.detail(self._series)
            road = detail.road(road_index) if hasattr(detail, "road") else None
            episodes = list(getattr(road, "episodes", None) or [])
            episode = next((item for item in episodes
                            if int(getattr(item, "index", -1)) == episode_index), None)
            if episode is None and episodes:
                episode = episodes[min(max(episode_index, 0), len(episodes) - 1)]
            if episode is None:
                self.failed.emit("找到了集号但取不到剧集对象（详情接口结果不完整）")
                return
            resolved = library.resolve(series_of(detail) or self._series, episode)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(_message_of(error))
            return
        self.finishedStep.emit(episode, resolved)


class AnimeDownloadWorker(QThread):
    """下载整季或单集（复用后端的并发分片下载；可取消）。"""

    progressed = Signal(int, int, str)
    finishedAll = Signal(object)
    failed = Signal(str)

    def __init__(self, library, series, episodes: list | None, dest_dir: str,  # noqa: ANN001
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._series = series
        self._episodes = episodes
        self._dest = dest_dir
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D102
        library = self._library
        if library is None or self._series is None:
            self.failed.emit(NO_BACKEND)
            return
        series = self._resolved_series(library)
        Path(self._dest).mkdir(parents=True, exist_ok=True)

        def on_progress(*args) -> None:
            """后端进度回调签名不统一：`(done, total, message)` 或 `(message,)` 都接住。"""
            if len(args) >= 3:
                self.progressed.emit(int(args[0]), int(args[1]), str(args[2]))
            elif args:
                self.progressed.emit(0, 0, str(args[0]))

        try:
            if self._episodes:
                results = []
                total = len(self._episodes)
                for index, episode in enumerate(self._episodes, start=1):
                    if self._cancel.is_set():
                        break
                    self.progressed.emit(index - 1, total,
                                         f"[{index}/{total}] {episode_label(episode)}")
                    results.append(library.download_episode(
                        series, episode, self._dest,
                        on_progress=on_progress, cancel=self._cancel))
                self.progressed.emit(len(results), total, f"完成 {len(results)}/{total}")
                self.finishedAll.emit(results)
                return
            result = library.enqueue_season(
                series, self._dest, on_progress=on_progress, cancel=self._cancel)
            self.finishedAll.emit([result])
        except Exception as error:  # noqa: BLE001
            self.failed.emit(_message_of(error))

    def _resolved_series(self, library):  # noqa: ANN001, ANN201
        """搜索页给的是 SearchHit/SearchGroup（站点侧作品），下载需要真正的番剧对象。

        这里先试着用详情把它"升格"；详情失败就用原对象继续（让后端的报错如实传出来），
        **不静默改成 follow()** —— 下载不该顺手改用户的追番状态。
        """
        candidate = self._series
        if getattr(candidate, "roads", None) or hasattr(candidate, "series"):
            return series_of(candidate) or candidate
        try:
            detail = library.detail(candidate)
        except Exception:  # noqa: BLE001
            return candidate
        return series_of(detail) or candidate


class AnimeBoardPage(QWidget):
    """墨软动漫板块页：五个子页 + 状态条 + 播放/下载编排。"""

    def __init__(self, parent: QWidget | None = None, *, library=None,  # noqa: ANN001
                 storage=None, rules=None):
        super().__init__(parent)
        self._toaster = Toaster(self)
        self._backend_error = ""
        self._library = library
        self._storage = storage
        self._rules = rules
        self._resolve_backend()

        self._dialogs: list[AnimeDetailDialog] = []
        self._step_worker: AnimeStepWorker | None = None
        self._open_worker: AnimeOpenWorker | None = None
        self._download_worker: AnimeDownloadWorker | None = None

        # 播放会话（换集/换线路复用同一套状态）
        self._player = None
        self._session_series = None
        self._session_episode = None
        self._session_road_index = 0
        self._session_episode_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        nav = QWidget()
        nav_row = QHBoxLayout(nav)
        nav_row.setContentsMargins(PAGE_MARGIN, SPACE["md"], PAGE_MARGIN, SPACE["sm"])
        nav_row.setSpacing(ROW_GAP)
        self._nav_buttons: dict[str, QPushButton] = {}
        for key, label in NAV_ITEMS:
            button = make_nav_button(label)
            button.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self._nav_buttons[key] = button
            nav_row.addWidget(button)

        settings_button = make_nav_button("⚙ 设置")
        settings_button.setToolTip("打开「设置 → 动漫」：下载目录、弹幕、Bangumi 元数据")
        settings_button.clicked.connect(self._open_board_settings)
        nav_row.addWidget(settings_button)
        nav_row.addStretch(1)
        self._backend_hint = chip("")
        nav_row.addWidget(self._backend_hint)
        layout.addWidget(nav)

        self._task_bar = TaskBar("就绪。搜索番剧后双击条目选集，可在线播放或下载。")
        self._task_bar.cancelled.connect(self._cancel_download)
        self._cancel_download_button = self._task_bar._cancel   # noqa: SLF001  兼容既有引用

        self._stack = QStackedWidget()
        self._search = AnimeSearchPage(self._library, self._toaster, task_bar=self._task_bar)
        self._library_page = AnimeLibraryPage(self._library, self._storage, self._toaster,
                                             task_bar=self._task_bar)
        self._schedule_page = AnimeSchedulePage(self._library, self._toaster,
                                                task_bar=self._task_bar)
        self._rules_page = AnimeRulesPage(self._library, self._rules, self._toaster,
                                          task_bar=self._task_bar)
        self._history_page = AnimeHistoryPage(self._library, self._storage, self._toaster,
                                              task_bar=self._task_bar)
        self._indexes = {
            SEARCH_KEY: self._stack.addWidget(self._search),
            LIBRARY_KEY: self._stack.addWidget(self._library_page),
            SCHEDULE_KEY: self._stack.addWidget(self._schedule_page),
            RULES_KEY: self._stack.addWidget(self._rules_page),
            HISTORY_KEY: self._stack.addWidget(self._history_page),
        }
        layout.addWidget(self._stack, 1)
        layout.addWidget(self._task_bar)

        # 数据联动
        self._search.playRequested.connect(self._play)
        self._search.detailRequested.connect(self._open_detail)
        self._search.followRequested.connect(self._follow)
        self._search.downloadRequested.connect(self._download_group)
        self._search.libraryChanged.connect(self._refresh_pages)
        self._library_page.playRequested.connect(self._play)
        self._library_page.detailRequested.connect(self._open_detail)
        self._library_page.downloadRequested.connect(self._download_season)
        self._library_page.libraryChanged.connect(self._refresh_pages)
        self._schedule_page.detailRequested.connect(self._open_detail)
        self._rules_page.rulesChanged.connect(self._refresh_rule_pages)
        self._history_page.playRequested.connect(self._play)
        self._history_page.libraryChanged.connect(self._refresh_pages)

        self.show_page(SEARCH_KEY)
        self._update_backend_hint()

    # ------------------------------------------------------------------ 后端

    def _resolve_backend(self) -> None:
        """取后端单例：任何一步失败都记原因，板块照常构造（子页显示空状态）。"""
        if self._library is None:
            try:
                self._library = app_context.anime_library()
            except Exception as error:  # noqa: BLE001
                self._backend_error = _message_of(error)
        if self._library is not None and self._storage is None:
            self._storage = getattr(self._library, "storage", None)
        if self._storage is None:
            try:
                self._storage = app_context.anime_storage()
            except Exception as error:  # noqa: BLE001
                self._backend_error = self._backend_error or _message_of(error)
        if self._rules is None:
            try:
                self._rules = app_context.anime_rules()
            except Exception as error:  # noqa: BLE001
                self._backend_error = self._backend_error or _message_of(error)

    def _update_backend_hint(self) -> None:
        if self._library is None:
            self._backend_hint.setText("后端未就绪")
            self._backend_hint.setToolTip(
                f"{SEARCH_NO_BACKEND}\n原因：{self._backend_error or '未知'}")
            return
        count = ""
        try:
            count = f"{len(self._library.rules(enabled_only=False) or [])} 条规则"
        except Exception:  # noqa: BLE001  规则数只是提示，取不到就不显示
            count = ""
        self._backend_hint.setText(count or "就绪")
        self._backend_hint.setToolTip("规则是采集的唯一入口：没有可用规则时搜索页会提示先去导入")

    # ------------------------------------------------------------------ 状态

    @property
    def _status(self) -> QLabel:
        """兼容旧引用：状态文字来自统一任务条。"""
        return self._task_bar._label          # noqa: SLF001

    def _note(self, message: str) -> None:
        self._task_bar.note(message)

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
        elif key == SCHEDULE_KEY:
            self._schedule_page.reload()
        elif key == RULES_KEY:
            self._rules_page.reload()
            # 首次进规则页且一条可用规则都没有 → 自动从官方仓库导入（无需手动操作）。
            # 只试一次（标记文件记住），失败也不弹窗，只写在状态栏。
            self._rules_page.maybe_auto_import_official()
        elif key == HISTORY_KEY:
            self._history_page.reload()
        elif key == SEARCH_KEY:
            self._search.reload_rules()
        self._update_backend_hint()

    def on_shown(self) -> None:
        for key, index in self._indexes.items():
            if index == self._stack.currentIndex():
                self.show_page(key)
                break

    def _open_board_settings(self) -> None:
        """打开统一设置对话框的「动漫」页（设置按板块区分）。"""
        from modu_workbench.ui_kit.settings import SettingsDialog

        SettingsDialog(self, initial="anime").exec()
        self._search.reload_rules()

    def _refresh_pages(self) -> None:
        self._library_page.reload()
        self._history_page.reload()

    def _refresh_rule_pages(self) -> None:
        self._search.reload_rules()
        self._update_backend_hint()

    # ------------------------------------------------------------------ 详情 / 追番

    def _open_detail(self, payload) -> None:  # noqa: ANN001
        dialog = AnimeDetailDialog(payload, self._library, self, toaster=self._toaster)
        dialog.playRequested.connect(self._play)
        dialog.downloadRequested.connect(self._download_episodes)
        dialog.followChanged.connect(lambda _series, _followed: self._refresh_pages())
        dialog.finished.connect(lambda _code, d=dialog: self._forget_dialog(d))
        self._dialogs.append(dialog)
        dialog.show()

    def _forget_dialog(self, dialog: AnimeDetailDialog) -> None:
        if dialog in self._dialogs:
            self._dialogs.remove(dialog)

    def _follow(self, payload) -> None:  # noqa: ANN001
        series = series_of(payload)
        if self._library is None or series is None:
            self._toaster.error(NO_BACKEND)
            return
        try:
            if bool(self._library.is_followed(series)):
                self._library.unfollow(series)
                self._toaster.info("已取消追番")
            else:
                self._library.follow(series)
                self._toaster.success("已加入追番，可在「番剧库」查看更新")
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"追番失败：{_message_of(error)}")
            return
        self._refresh_pages()

    # ------------------------------------------------------------------ 播放

    def _play(self, series, episode) -> None:  # noqa: ANN001
        """解析地址 → 打开/复用共享播放器。

        `episode is None` 表示"从头播"：搜索结果是站点侧作品（还不是番剧对象），
        需要先拿详情（`AnimeOpenWorker`），这一步必须离开 UI 线程。
        """
        if self._library is None:
            self._play_failed(NO_BACKEND)
            return
        if series is None:
            self._play_failed("缺少作品信息，无法播放")
            return
        if episode is None:
            self._open_and_play(series)
            return
        series = series_of(series)
        label = episode_label(episode)
        self._note(f"正在解析播放地址：{getattr(series, 'title', '')} {label}…")
        try:
            resolved = self._library.resolve(series, episode)
        except Exception as error:  # noqa: BLE001
            self._play_failed(f"解析播放地址失败：{_message_of(error)}")
            return
        self._begin_session(series, episode, resolved)

    def _begin_session(self, series, episode, resolved) -> bool:  # noqa: ANN001
        """校验解析结果并开始播放会话（点播/从头播/换集三条路都汇到这里）。"""
        if resolved is None or episode is None:
            self._play_failed("详情里没有可播放的剧集")
            return False
        url = str(getattr(resolved, "url", "") or "")
        if not url:
            self._play_failed("解析不到媒体地址（可用规则页试跑定位是哪一步）")
            return False
        self._session_series = series
        self._session_episode = episode
        self._session_road_index = int(getattr(episode, "road_index", 0) or 0)
        self._session_episode_index = int(getattr(episode, "index", 0) or 0)
        title = str(getattr(series, "title", "") or "动漫")
        self._show_player(series, episode, resolved, title, episode_label(episode))
        return True

    def _play_failed(self, message: str) -> None:
        self._note(message)
        self._toaster.error(message)

    def _open_and_play(self, payload) -> None:  # noqa: ANN001
        if self._open_worker is not None and self._open_worker.isRunning():
            self._toaster.info("正在获取剧集，请稍候")
            return
        self._note("正在获取剧集（第一条线路第一集）…")
        worker = AnimeOpenWorker(self._library, payload, self)
        worker.finishedOpen.connect(self._on_open_ready)
        worker.failed.connect(self._play_failed)
        worker.finished.connect(lambda w=worker: self._forget_open_worker(w))
        self._open_worker = worker
        worker.start()

    def _forget_open_worker(self, worker: AnimeOpenWorker) -> None:
        if self._open_worker is worker:
            self._open_worker = None

    def _on_open_ready(self, series, episode, resolved) -> None:  # noqa: ANN001
        self._begin_session(series, episode, resolved)

    def _on_open_failed(self, message: str) -> None:
        self._play_failed(f"播放失败：{message}")

    def _show_player(self, series, episode, resolved, title: str, label: str) -> None:  # noqa: ANN001
        from modu_workbench.ui_kit.media.player import VideoPlayerDialog

        url = str(getattr(resolved, "url", "") or "")
        is_hls = bool(getattr(resolved, "is_hls", False))
        referer = str(getattr(resolved, "referer", "") or "")
        start_ms = self._resume_position(series, episode)
        display = f"{title} · {label}" if label else title
        if self._player is None:
            player = VideoPlayerDialog(
                self, title=display, url=url, is_hls=is_hls, referer=referer,
                start_ms=start_ms, episode_label=label,
                has_prev=self._has_neighbor(-1), has_next=self._has_neighbor(1),
            )
            player.episodeRequested.connect(self._on_episode_step)
            player.closed.connect(self._on_player_closed)
            if hasattr(player, "stallDetected"):
                player.stallDetected.connect(self._on_stall)
            self._player = player
            player.show()
        else:
            self._player.set_episode_nav(has_prev=self._has_neighbor(-1),
                                        has_next=self._has_neighbor(1))
            self._player.switch_source(
                url, is_hls=is_hls, referer=referer, title=display, start_ms=start_ms,
                status=f"正在播放：{display}")
            self._player.show()
            self._player.raise_()
        self._attach_danmaku(series, episode)
        self._note(f"正在播放：{display}")
        self._history_page.reload()
        self._start_progress_timer()

    def _has_neighbor(self, step: int) -> bool:
        """是否还有上一集/下一集（问后端；问到才显示按钮，不问就按没有处理）。"""
        library = self._library
        if library is None or self._session_series is None:
            return False
        getter = getattr(library, "next_episode" if step > 0 else "prev_episode", None)
        if not callable(getter):
            return False
        try:
            return bool(getter(self._session_series, self._session_road_index,
                               self._session_episode_index))
        except Exception:  # noqa: BLE001
            return False

    def _resume_position(self, series, episode) -> int:  # noqa: ANN001
        if self._library is None or episode is None:
            return 0
        try:
            position = int(self._library.resume_position(series, episode) or 0)
        except Exception:  # noqa: BLE001  后端未实现：从头播，不编造进度
            return 0
        # 不足 10 秒的进度没有续播意义（与影视板块同规则）
        return position if position > 10_000 else 0

    def _attach_danmaku(self, series, episode) -> None:  # noqa: ANN001
        """挂只读弹幕（默认关）。后端没有弹幕接口时明确说明，不假装已加载。"""
        if self._player is None:
            return
        enabled = setting_bool("anime/danmaku_enabled", False)
        if hasattr(self._player, "set_danmaku_enabled"):
            self._player.set_danmaku_enabled(enabled)
        if not enabled:
            return
        loader = None
        for name in ("danmaku", "load_danmaku", "episode_danmaku"):
            candidate = getattr(self._library, name, None)
            if callable(candidate):
                loader = candidate
                break
        if loader is None:
            self._note("弹幕已开启，但后端还没有弹幕接口（只读弹幕需要 core/anime/danmaku）")
            return
        try:
            items = list(loader(series, episode) or [])
        except Exception as error:  # noqa: BLE001
            self._note(f"弹幕加载失败：{_message_of(error)}")
            return
        if hasattr(self._player, "set_danmaku"):
            self._player.set_danmaku(items)
        self._note(f"已加载 {len(items)} 条弹幕" if items else "这部番没有弹幕")

    def _on_episode_step(self, step: int) -> None:
        """播放器里点上一集/下一集：交给后端决定下一集是谁（同线路 → 换线路 → 换规则）。"""
        if self._library is None or self._session_series is None:
            return
        if self._step_worker is not None and self._step_worker.isRunning():
            self._toaster.info("正在切换剧集，请稍候")
            return
        self._note("正在切换剧集…")
        worker = AnimeStepWorker(self._library, self._session_series,
                                 self._session_road_index, self._session_episode_index,
                                 step, self)
        worker.finishedStep.connect(self._on_step_ready)
        worker.failed.connect(self._on_step_failed)
        worker.finished.connect(lambda w=worker: self._forget_step_worker(w))
        self._step_worker = worker
        worker.start()

    def _forget_step_worker(self, worker: AnimeStepWorker) -> None:
        if self._step_worker is worker:
            self._step_worker = None

    def _on_step_ready(self, episode, resolved) -> None:  # noqa: ANN001
        if resolved is None or episode is None:
            self._note("换集失败：后端没有返回可播放的地址")
            return
        series = self._session_series
        title = str(getattr(series, "title", "") or "动漫")
        label = episode_label(episode)
        self._session_episode = episode
        self._session_road_index = int(getattr(episode, "road_index", 0) or 0)
        self._session_episode_index = int(getattr(episode, "index", 0) or 0)
        self._show_player(series, episode, resolved, title, label)

    def _on_step_failed(self, message: str) -> None:
        self._note(message)
        self._toaster.info(message)

    def _on_stall(self, reason: str) -> None:
        """播放卡顿：只做诚实提示（跨规则换源由用户决定，不静默换）。"""
        if reason == "download":
            self._note("该线路长时间无法起播：建议先「下载整季」，或换一条线路再试。")
        else:
            self._note("播放卡顿：可以换一条线路（详情对话框的线路下拉）。")

    def _on_player_closed(self, position_ms: int) -> None:
        if self._session_series is not None and self._session_episode is not None:
            self._save_progress(int(position_ms or 0))
        if getattr(self, "_progress_timer", None) is not None:
            self._progress_timer.stop()
        self._note("已关闭播放器。")

    def _start_progress_timer(self) -> None:
        if getattr(self, "_progress_timer", None) is None:
            self._progress_timer = QTimer(self)
            self._progress_timer.setInterval(PROGRESS_SAVE_MS)
            self._progress_timer.timeout.connect(self._tick_progress)
        self._progress_timer.start()

    def _tick_progress(self) -> None:
        if self._player is None:
            return
        try:
            position = int(self._player.current_position_ms())
        except Exception:  # noqa: BLE001  播放器形态不定（测试替身）→ 跳过这一拍
            return
        if position > 0:
            self._save_progress(position)

    def _save_progress(self, position_ms: int) -> None:
        if self._library is None or self._session_series is None or self._session_episode is None:
            return
        duration = 0
        if self._player is not None and hasattr(self._player, "duration_ms"):
            try:
                duration = int(self._player.duration_ms() or 0)
            except Exception:  # noqa: BLE001
                duration = 0
        try:
            self._library.save_progress(self._session_series, self._session_episode,
                                        int(position_ms), duration)
        except Exception:  # noqa: BLE001  写进度失败不该打断播放
            return

    # ------------------------------------------------------------------ 下载

    def _download_group(self, payload) -> None:  # noqa: ANN001
        """搜索页的"下载"：整部（整季）交给后端排队。"""
        self._download_season(series_of(payload))

    def _download_season(self, series) -> None:  # noqa: ANN001
        self._start_download(series, None)

    def _download_episodes(self, series, episodes) -> None:  # noqa: ANN001
        self._start_download(series, list(episodes or []) or None)

    def _start_download(self, series, episodes) -> None:  # noqa: ANN001
        series = series_of(series)
        if self._library is None or series is None:
            self._toaster.error(NO_BACKEND)
            return
        if self._download_worker is not None and self._download_worker.isRunning():
            self._toaster.info("已有下载任务在进行，请等待或先取消")
            return
        dest = anime_download_dir_pref()
        Path(dest).mkdir(parents=True, exist_ok=True)
        worker = AnimeDownloadWorker(self._library, series, episodes, dest, self)
        worker.progressed.connect(self._on_download_progress)
        worker.finishedAll.connect(self._on_download_finished)
        worker.failed.connect(self._on_download_failed)
        worker.finished.connect(self._on_download_thread_finished)
        self._download_worker = worker
        scope = f"{len(episodes)} 集" if episodes else "整季"
        self._task_bar.report(f"开始下载{scope}到 {dest}")
        worker.start()

    def _cancel_download(self) -> None:
        if self._download_worker is not None:
            self._download_worker.cancel()
            self._task_bar.set_text("正在取消下载…")

    def _on_download_progress(self, done: int, total: int, message: str) -> None:
        self._task_bar.report(message, done, total)

    def _on_download_finished(self, results: list) -> None:
        count = len(results or [])
        self._task_bar.idle(f"下载完成：{count} 个任务（文件在 {anime_download_dir_pref()}）")
        self._toaster.success(f"下载完成：{count} 个任务")
        self._refresh_pages()

    def _on_download_failed(self, message: str) -> None:
        self._task_bar.idle(f"下载失败：{message}")
        self._toaster.error(f"下载失败：{message}")

    def _on_download_thread_finished(self) -> None:
        self._download_worker = None

    # ------------------------------------------------------------------ 收尾

    def shutdown(self) -> None:
        """先停后台线程，再关播放器（播放器关闭会触发进度回写，顺序不能反）。"""
        self._tick_progress()
        for page in (self._search, self._library_page, self._schedule_page,
                     self._rules_page, self._history_page):
            try:
                page.shutdown()
            except Exception:  # noqa: BLE001  单页收尾失败不该影响退出
                pass
        for dialog in list(self._dialogs):
            try:
                dialog.shutdown()
            except Exception:  # noqa: BLE001
                pass
            dialog.close()
        self._dialogs.clear()
        for worker in (self._step_worker, self._open_worker, self._download_worker):
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    if isinstance(worker, AnimeDownloadWorker):
                        worker.cancel()
                    worker.wait(3000)
            except RuntimeError:
                continue
        self._step_worker = None
        self._open_worker = None
        self._download_worker = None
        if getattr(self, "_progress_timer", None) is not None:
            self._progress_timer.stop()
        if self._player is not None:
            self._player.close()
            self._player = None

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)


__all__ = [
    "AnimeBoardPage",
    "AnimeDownloadWorker",
    "AnimeStepWorker",
    "HISTORY_KEY",
    "LIBRARY_KEY",
    "NAV_ITEMS",
    "RULES_KEY",
    "SCHEDULE_KEY",
    "SEARCH_KEY",
]
