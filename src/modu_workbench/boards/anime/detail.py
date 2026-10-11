"""墨软动漫 · 详情对话框：元数据 + 线路/剧集网格 + 追番 / 下载 / 外链。

数据流：
- 传入的可能是 `SeriesDetail`（调用方已经拉过详情，例如试跑/追番库）、
  也可能是 `SearchGroup`/`SearchHit`/`AnimeSeries`（需要现拉详情）。
  前者直接渲染，后者在后台线程里调 `library.detail()` —— 采集站详情页可能要好几秒，
  同步拉会把对话框冻住（用户看到的是白窗口）。
- 剧集进度（✓ / 百分比）走 `collect_progress()`：批量接口优先，逐集 `resume_position()` 兜底，
  都拿不到就显示"没有进度"，不编造数字。

元数据：有 Bangumi 元数据就用它（评分/简介/封面），没有就用规则给的数据 ——
**不因为缺元数据而不显示条目**（规则命中的作品本来就可能没建 Bangumi 条目）。
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.ui_kit.components import (
    EmptyState,
    SectionCard,
    chip,
    ghost_button,
    hint_label,
    primary_button,
)
from modu_workbench.ui_kit.toast import Toaster
from modu_workbench.ui_kit.tokens import SPACE

from .widgets import (
    EpisodeGrid,
    FollowButton,
    RoadSelector,
    collect_progress,
    detail_roads,
    episode_label,
    first_episode,
    load_cover,
    open_in_browser,
    road_episodes,
    series_of,
)

#: 没有剧集时的说明（详情命中了作品但线路选择器没命中，是最常见的规则失效点）
NO_ROAD_HINT = "详情页没解析出线路/剧集：规则里的 chapterRoads / chapterResult 可能已失效，去「规则」页试跑看看"


class AnimeDetailWorker(QThread):
    """后台拉取详情（线路 + 剧集）。"""

    finishedDetail = Signal(object)
    failed = Signal(str)

    def __init__(self, payload, library=None, parent: QWidget | None = None):  # noqa: ANN001
        super().__init__(parent)
        self._payload = payload
        self._library = library

    def run(self) -> None:  # noqa: D102
        library = self._library
        if library is None:
            self.failed.emit("动漫后端未就绪：核心模块还没装上，无法获取详情")
            return
        try:
            self.finishedDetail.emit(library.detail(self._payload))
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error) or error.__class__.__name__)


class AnimeDetailDialog(QDialog):
    """番剧详情：选集、追番、下载整季、外链/复制地址。"""

    playRequested = Signal(object, object)             # series, episode
    downloadRequested = Signal(object, object)         # series, "season" 或 [episodes]
    followChanged = Signal(object, bool)               # series, followed
    externalPlayerRequested = Signal(object, object)   # series, episode

    def __init__(self, payload, library=None, parent: QWidget | None = None,  # noqa: ANN001
                 *, toaster: Toaster | None = None, metadata: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("番剧详情")
        self.resize(860, 620)
        self._library = library
        self._toaster = toaster or Toaster(self)
        self._payload = payload
        self._detail = payload if detail_roads(payload) or hasattr(payload, "series") else None
        self._series = series_of(self._detail or payload)
        self._metadata = dict(metadata or {})
        self._worker: AnimeDetailWorker | None = None
        self._current_episode = None

        self._build_ui()
        if self._detail is not None:
            self._apply_detail(self._detail)
        else:
            self._load_detail()

    # ------------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(SPACE["md"])

        header = QWidget()
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(SPACE["md"])

        self._cover = QLabel("🎞")
        self._cover.setObjectName("animePoster")
        self._cover.setFixedSize(110, 146)
        header_row.addWidget(self._cover)

        head_texts = QVBoxLayout()
        head_texts.setContentsMargins(0, 0, 0, 0)
        head_texts.setSpacing(SPACE["xs"])
        self._title = QLabel("加载中…")
        self._title.setObjectName("pageTitle")
        self._title.setWordWrap(True)
        head_texts.addWidget(self._title)
        self._meta = QLabel("")
        self._meta.setObjectName("readerStatus")
        self._meta.setWordWrap(True)
        head_texts.addWidget(self._meta)
        self._summary = QTextBrowser()
        self._summary.setOpenExternalLinks(False)
        self._summary.setMinimumHeight(90)
        head_texts.addWidget(self._summary, 1)
        header_row.addLayout(head_texts, 1)
        layout.addWidget(header)

        # ---- 线路 + 剧集网格 ----
        self._episode_card = SectionCard("选集")
        road_line = QHBoxLayout()
        road_line.setContentsMargins(0, 0, 0, 0)
        road_line.setSpacing(SPACE["sm"])
        road_line.addWidget(QLabel("线路"))
        self._road_box = RoadSelector()
        self._road_box.currentIndexChanged.connect(lambda _index: self._rebuild_grid())
        road_line.addWidget(self._road_box)
        self._episode_chip = chip("0 集")
        road_line.addWidget(self._episode_chip)
        road_line.addStretch(1)
        road_line.addWidget(hint_label("双击播放 · 右键换外部播放器/重新解析"))
        self._episode_card.add_layout(road_line)

        self._grid = EpisodeGrid()
        self._grid.playRequested.connect(self._on_episode_activated)
        self._grid.externalPlayerRequested.connect(self._open_external)
        self._grid.reparseRequested.connect(self._reparse)
        self._empty = EmptyState("🎬", "还没有剧集", NO_ROAD_HINT)
        self._episode_card.add(self._empty)
        self._episode_card.add(self._grid)
        self._grid.setVisible(False)
        layout.addWidget(self._episode_card, 1)

        # ---- 动作 ----
        self._follow_button = FollowButton()
        self._follow_button.toggled.connect(self._on_follow_toggled)
        self._download_button = primary_button("⬇ 下载整季")
        self._download_button.clicked.connect(self._emit_download)
        self._browser_button = ghost_button("🌐 在浏览器打开")
        self._browser_button.clicked.connect(self._open_in_browser)
        self._copy_button = ghost_button("复制播放地址")
        self._copy_button.clicked.connect(self._copy_url)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(SPACE["sm"])
        for widget in (self._follow_button, self._download_button,
                       self._browser_button, self._copy_button):
            actions.addWidget(widget)
        actions.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)

        self._status = QLabel("")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._update_buttons()

    # ------------------------------------------------------------------ 数据

    def _load_detail(self) -> None:
        self._status.setText("正在获取详情…")
        worker = AnimeDetailWorker(self._payload, self._library, self)
        worker.finishedDetail.connect(self._apply_detail)
        worker.failed.connect(self._on_detail_failed)
        worker.finished.connect(lambda w=worker: self._forget_worker(w))
        self._worker = worker
        worker.start()

    def _forget_worker(self, worker: AnimeDetailWorker) -> None:
        if self._worker is worker:
            self._worker = None

    def _on_detail_failed(self, message: str) -> None:
        self._status.setText(f"获取详情失败：{message}")
        self._empty.setVisible(True)
        self._grid.setVisible(False)

    def _apply_detail(self, detail) -> None:  # noqa: ANN001
        if detail is None:
            self._on_detail_failed("规则没有返回详情")
            return
        self._detail = detail
        series = series_of(detail)
        if series is not None:
            self._series = series
        if not self._metadata:
            self._metadata = self._fetch_metadata(self._series)
        self._fill_header()
        roads = detail_roads(detail)
        self._road_box.set_roads(roads)
        self._rebuild_grid()
        self._refresh_follow()
        note = str(getattr(detail, "note", "") or "")
        self._status.setText(note)
        self._update_buttons()

    # ------------------------------------------------------------------ 头部

    def _fetch_metadata(self, series) -> dict:  # noqa: ANN001
        """Bangumi 元数据（可选）：后端没实现就返回空字典，界面用规则数据。"""
        if self._library is None or series is None:
            return {}
        for name in ("bangumi_metadata", "series_metadata", "metadata"):
            getter = getattr(self._library, name, None)
            if not callable(getter):
                continue
            try:
                found = getter(series)
            except Exception:  # noqa: BLE001  元数据只是锦上添花，失败不影响详情
                return {}
            if isinstance(found, dict):
                return found
            if found is not None and hasattr(found, "summary"):
                return {
                    "name": getattr(found, "name", ""),
                    "score": getattr(found, "score", 0),
                    "summary": getattr(found, "summary", ""),
                    "image": getattr(found, "image", ""),
                }
        return {}

    def _fill_header(self) -> None:
        series = self._series
        meta = self._metadata
        title = str(meta.get("name") or getattr(series, "title", "") or "未命名")
        self.setWindowTitle(f"{title} · 详情")
        self._title.setText(title)
        cover = str(meta.get("image") or getattr(series, "cover", "") or "")
        if cover:
            load_cover(self._cover, cover)

        parts: list[str] = []
        year = str(meta.get("year") or getattr(series, "year", "") or "").strip()
        if year:
            parts.append(year)
        score = meta.get("score") or getattr(series, "score", 0) or 0
        try:
            if float(score) > 0:
                parts.append(f"评分 {float(score):.1f}")
        except (TypeError, ValueError):
            pass
        total = int(getattr(series, "total_episodes", 0) or 0)
        if total:
            parts.append(f"共 {total} 集")
        latest = str(getattr(series, "latest_episode", "") or "")
        if latest:
            parts.append(f"最新 {latest}")
        rule_key = str(getattr(series, "rule_key", "") or "")
        if rule_key:
            parts.append(f"规则 {rule_key}")
        self._meta.setText(" · ".join(parts) or "（站点没给元数据）")

        summary = str(meta.get("summary") or getattr(series, "summary", "") or "")
        self._summary.setPlainText(summary or "（暂无简介：没匹配到 Bangumi 元数据，站点也没给简介）")

    # ------------------------------------------------------------------ 剧集

    def _rebuild_grid(self) -> None:
        detail = self._detail
        road = self._road_box.current_road(detail) if detail is not None else None
        episodes = road_episodes(road) if road is not None else []
        progress = collect_progress(self._library, self._series, episodes) if episodes else {}
        self._grid.set_episodes(episodes, progress)
        self._grid.setVisible(bool(episodes))
        self._empty.setVisible(not episodes)
        self._episode_chip.setText(f"{len(episodes)} 集")
        self._current_episode = episodes[0] if episodes else None
        self._update_buttons()

    def current_episode(self):  # noqa: ANN201
        """当前集：优先用户点过的那一集，否则第一条线路的第一集。"""
        if self._current_episode is not None:
            return self._current_episode
        if self._detail is None:
            return None
        return first_episode(self._detail)

    def current_road(self):  # noqa: ANN201
        return self._road_box.current_road(self._detail) if self._detail is not None else None

    def _on_episode_activated(self, episode) -> None:  # noqa: ANN001
        """单击/双击剧集格子：交给板块去解析并播放。"""
        self._current_episode = episode
        if self._series is None:
            self._toaster.error("缺少番剧信息，无法播放")
            return
        self.playRequested.emit(self._series, episode)

    # ------------------------------------------------------------------ 动作

    def _refresh_follow(self) -> None:
        followed = False
        if self._library is not None and self._series is not None:
            try:
                followed = bool(self._library.is_followed(self._series))
            except Exception:  # noqa: BLE001  后端未实现/报错：按未追番显示
                followed = False
        self._follow_button.set_followed(followed)

    def _on_follow_toggled(self, checked: bool) -> None:
        if self._library is None or self._series is None:
            return
        try:
            if checked:
                self._library.follow(self._series)
            else:
                self._library.unfollow(self._series)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"追番操作失败：{error}")
            return
        self._toaster.success("已加入追番" if checked else "已取消追番")
        self.followChanged.emit(self._series, checked)

    def _emit_download(self) -> None:
        if self._series is None:
            self._toaster.error("缺少番剧信息，无法下载")
            return
        road = self.current_road()
        episodes = road_episodes(road) if road is not None else []
        if not episodes:
            self._toaster.info("这条线路没有可下载的剧集")
            return
        self.downloadRequested.emit(self._series, list(episodes))

    def _open_in_browser(self) -> None:
        episode = self.current_episode()
        url = str(getattr(episode, "url", "") or "")
        if not url:
            self._toaster.error("没有可打开的地址（这条剧集没给播放页链接）")
            return
        open_in_browser(url)

    def _copy_url(self) -> None:
        """复制"能直接播的地址"：优先真解析结果，失败退回播放页地址。"""
        episode = self.current_episode()
        if episode is None or self._series is None:
            self._toaster.info("请先选择一集")
            return
        url = ""
        if self._library is not None:
            try:
                resolved = self._library.resolve(self._series, episode)
                url = str(getattr(resolved, "url", "") or "")
            except Exception as error:  # noqa: BLE001
                self._status.setText(f"解析失败（已退回播放页地址）：{error}")
        if not url:
            url = str(getattr(episode, "url", "") or "")
        if not url:
            self._toaster.error("没有可复制的地址")
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(url)
        self._toaster.success("已复制播放地址")

    def _open_external(self, episode) -> None:  # noqa: ANN001
        """用系统默认播放器打开：先解析出真实媒体地址，解析不到就给播放页。"""
        self._current_episode = episode
        url = str(getattr(episode, "url", "") or "")
        if self._library is not None and self._series is not None:
            try:
                resolved = self._library.resolve(self._series, episode)
                url = str(getattr(resolved, "url", "") or url)
            except Exception as error:  # noqa: BLE001
                self._status.setText(f"解析失败，改用播放页地址：{error}")
        if not open_in_browser(url):
            self._toaster.error("没有可打开的地址")

    def _reparse(self, episode) -> None:  # noqa: ANN001
        """重新解析地址（换线路/换策略）：结果写进状态栏，方便排查。"""
        self._current_episode = episode
        if self._library is None or self._series is None:
            self._toaster.error("动漫后端未就绪，无法解析")
            return
        self._status.setText(f"正在重新解析：{episode_label(episode)}…")
        try:
            resolved = self._library.resolve(self._series, episode)
        except Exception as error:  # noqa: BLE001
            self._status.setText(f"重新解析失败：{error}")
            self._toaster.error(f"重新解析失败：{error}")
            return
        url = str(getattr(resolved, "url", "") or "")
        strategy = str(getattr(resolved, "strategy", "") or "")
        note = str(getattr(resolved, "note", "") or "")
        self._status.setText(
            f"解析成功：{episode_label(episode)} → {url}"
            + (f"（策略 {strategy}）" if strategy else "")
            + (f"；{note}" if note else "")
        )

    def _update_buttons(self) -> None:
        has_series = self._series is not None
        has_episodes = bool(self._grid.cells())
        self._download_button.setEnabled(has_series and has_episodes)
        self._browser_button.setEnabled(has_series)
        self._copy_button.setEnabled(has_episodes)
        self._follow_button.setEnabled(has_series)

    # ------------------------------------------------------------------ 收尾

    def shutdown(self) -> None:
        """等后台详情线程收尾：运行中的 QThread 被 GC 会直接把进程干掉。"""
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        try:
            if worker.isRunning():
                worker.wait(3000)
        except RuntimeError:
            return

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)

    def done(self, result: int) -> None:  # noqa: N802
        self.shutdown()
        super().done(result)


__all__ = ["AnimeDetailDialog", "AnimeDetailWorker", "NO_ROAD_HINT"]
