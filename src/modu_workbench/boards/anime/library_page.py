"""墨软动漫 · 番剧库：追番列表（有新集徽章）、继续观看、检查更新、导入导出。

三件必须说清楚的事：

1. **"有新集"来自真实比对**：`check_updates()` 会把每部追番的集数/最新集与上次记录对比，
   只有 `SeriesUpdate.has_new` 为真的那条才打徽章。没有更新报告时**不打徽章**，
   而不是按"最近更新过"猜。
2. **检查更新是网络操作**，跑在 `QThread` 里（几十部追番逐条抓站，同步必然卡死界面）。
   后端会并发 2、可取消；界面只负责把进度写到 `TaskBar`。
3. **导入导出走 `AnimeStorage.export_payload()/import_payload()`**（JSON 文本），
   手机/另一台机器上追番与进度都能还原。后端没就绪时给出可读提示，不静默失败。
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.ui_kit.components import (
    ColumnPage,
    EmptyState,
    SectionCard,
    TaskBar,
    chip,
    ghost_button,
    hint_label,
    primary_button,
)
from modu_workbench.ui_kit.toast import Toaster
from modu_workbench.ui_kit.tokens import SPACE

from .widgets import PosterCard, episode_label, normalize_title_safe

#: 空状态文案：给出下一步（导入 JSON 还原 / 去搜索页追番）
EMPTY_HINT = "还没有追番：在「搜索」页找到番剧后点「追番」，或用「导入 JSON」还原之前的追番列表"
NO_BACKEND = "动漫后端未就绪：核心模块还没装上，番剧库暂时不可用"


class AnimeUpdateWorker(QThread):
    """检查追番更新（后台线程；后端并发 2、可取消）。"""

    progressed = Signal(str)
    finishedReport = Signal(object)
    failed = Signal(str)

    def __init__(self, library, parent: QWidget | None = None):  # noqa: ANN001
        super().__init__(parent)
        self._library = library

    def run(self) -> None:  # noqa: D102
        library = self._library
        if library is None:
            self.failed.emit(NO_BACKEND)
            return
        try:
            try:
                report = library.check_updates(
                    on_progress=lambda message: self.progressed.emit(str(message)))
            except TypeError:
                # 后端 check_updates 还没有 on_progress 参数：退回无进度调用
                report = library.check_updates()
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error) or error.__class__.__name__)
            return
        self.finishedReport.emit(report)


def _update_items(report) -> list:
    return list(getattr(report, "items", None) or [])


def _update_new_map(report) -> dict[str, object]:
    """`{归一化标题: SeriesUpdate}`：只有 has_new 的才算"有新集"。"""
    found: dict[str, object] = {}
    for item in _update_items(report):
        if not bool(getattr(item, "has_new", False)):
            continue
        series = getattr(item, "series", None)
        title = str(getattr(series, "title", "") or "")
        key = normalize_title_safe(title)
        if key:
            found[key] = item
    return found


class AnimeLibraryPage(QWidget):
    """番剧库：追番 + 继续观看 + 检查更新 + 导入导出。"""

    playRequested = Signal(object, object)      # series, episode | None
    detailRequested = Signal(object)            # series
    downloadRequested = Signal(object)          # series
    libraryChanged = Signal()

    def __init__(self, library=None, storage=None, toaster: Toaster | None = None,  # noqa: ANN001
                 parent: QWidget | None = None, *, task_bar: TaskBar | None = None):
        super().__init__(parent)
        self._library = library
        self._storage = storage
        self._toaster = toaster or Toaster(self)
        self._task = task_bar
        self._cards: dict[str, PosterCard] = {}
        self._update_worker: AnimeUpdateWorker | None = None
        #: 最近一次 `UpdateReport`（"有新集"徽章的唯一来源；名字别叫 _report —— 那是状态助手约定名）
        self._update_report = None
        self._storage_error = ""
        self._tail_stretch = False
        self._fallback_status = QLabel("就绪。")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._page = ColumnPage(
            "番剧库",
            "追番的作品集中在这里：有更新会打「有新集」徽章，点「检查更新」逐条核对集数。"
            "「继续观看」按上次进度续播；追番与进度可以导出为 JSON 备份或换机还原。",
        )
        outer.addWidget(self._page)

        # ---- 继续观看 ----
        self._continue_card = SectionCard("继续观看")
        self._continue_host = QWidget()
        self._continue_layout = QVBoxLayout(self._continue_host)
        self._continue_layout.setContentsMargins(0, 0, 0, 0)
        self._continue_layout.setSpacing(SPACE["sm"])
        self._continue_card.add(self._continue_host)
        self._continue_host.setVisible(False)
        self._page.add(self._continue_card)

        # ---- 追番列表 ----
        self._count_chip = chip("0 部")
        self._check_button = primary_button("检查更新")
        self._check_button.setToolTip("逐条核对追番的集数变化（后台执行，可继续用其它页面）")
        self._check_button.clicked.connect(self._check_updates)
        self._export_button = ghost_button("导出 JSON")
        self._export_button.clicked.connect(self._export_json)
        self._import_button = ghost_button("导入 JSON")
        self._import_button.clicked.connect(self._import_json)
        self._favorite_card = SectionCard(
            "追番", actions=[self._count_chip, self._check_button,
                             self._export_button, self._import_button])
        self._empty = EmptyState("📺", "还没有追番", EMPTY_HINT)
        self._favorites_host = QWidget()
        self._favorites_layout = QVBoxLayout(self._favorites_host)
        self._favorites_layout.setContentsMargins(0, 0, 0, 0)
        self._favorites_layout.setSpacing(SPACE["sm"])
        self._favorite_card.add(self._empty)
        self._favorite_card.add(self._favorites_host)
        self._favorites_host.setVisible(False)
        self._favorite_card.add(hint_label(
            "追番只保存「规则 + 站点作品 id」：规则被删除或站点改版后需要重新搜索追番。"))
        self._page.add(self._favorite_card)
        self._set_tail_stretch(True)
        self.reload()

    # ------------------------------------------------------------------ 状态

    @property
    def _status(self) -> QLabel:
        """兼容旧引用：状态文字统一显示在板块任务条上。"""
        return self._task._label if self._task is not None else self._fallback_status  # noqa: SLF001

    def _note_fallback(self, message: str) -> None:
        label = getattr(self, "_fallback_status", None)
        if label is not None:
            label.setText(message)

    def _report_status(self, message: str, done: int = 0, total: int = 0) -> None:
        self._note_fallback(message)
        if self._task is not None:
            if done or total:
                self._task.report(message, done, total)
            else:
                self._task.note(message)

    def _busy(self, message: str) -> None:
        self._note_fallback(message)
        if self._task is not None:
            self._task.busy(message)

    def _idle(self, message: str = "") -> None:
        if message:
            self._note_fallback(message)
        if self._task is not None:
            self._task.idle(message)

    def _set_tail_stretch(self, enabled: bool) -> None:
        layout = self._page.body
        if enabled and not self._tail_stretch:
            layout.addStretch(1)
            self._tail_stretch = True
        elif not enabled and self._tail_stretch and layout.count():
            layout.takeAt(layout.count() - 1)
            self._tail_stretch = False

    # ------------------------------------------------------------------ 存储

    def storage(self):  # noqa: ANN201
        """动漫存储：构造时没给就按需从板块上下文取（取不到就返回 None）。"""
        if self._storage is None and not self._storage_error:
            try:
                from . import context as app_context

                self._storage = app_context.anime_storage()
            except Exception as error:  # noqa: BLE001
                self._storage_error = str(error) or error.__class__.__name__
        return self._storage

    # ------------------------------------------------------------------ 数据

    def reload(self) -> None:
        self._clear_layout(self._favorites_layout)
        self._clear_layout(self._continue_layout)
        self._cards.clear()

        series_list: list = []
        if self._library is None:
            self._note_fallback(NO_BACKEND)
        else:
            try:
                series_list = list(self._library.favorites() or [])
            except Exception as error:  # noqa: BLE001
                self._note_fallback(f"读取追番失败：{error}")
        for series in series_list:
            card = self._make_favorite_card(series)
            self._favorites_layout.addWidget(card)

        continue_rows = self._continue_rows()
        for series, episode, progress in continue_rows:
            self._continue_layout.addWidget(self._make_continue_card(series, episode, progress))

        new_map = _update_new_map(self._update_report) if self._update_report is not None else {}
        for series in series_list:
            card = self._cards.get(normalize_title_safe(str(getattr(series, "title", "") or "")))
            update = new_map.get(normalize_title_safe(str(getattr(series, "title", "") or "")))
            if card is not None and update is not None:
                latest = str(getattr(update, "latest_episode", "") or "")
                card.add_badge("有新集")
                if latest:
                    card.set_subtitle(f"最新：{latest} · 之前 {getattr(update, 'old_total', 0)} 集"
                                      f" → 现在 {getattr(update, 'new_total', 0)} 集")

        has_favorites = bool(series_list)
        self._count_chip.setText(f"{len(series_list)} 部")
        self._empty.setVisible(not has_favorites)
        self._favorites_host.setVisible(has_favorites)
        self._continue_card.setVisible(bool(continue_rows))
        self._set_tail_stretch(not has_favorites)

    def _make_favorite_card(self, series) -> PosterCard:  # noqa: ANN001
        title = str(getattr(series, "title", "") or "未命名")
        subtitle_parts = [str(getattr(series, "year", "") or "")]
        total = int(getattr(series, "total_episodes", 0) or 0)
        if total:
            subtitle_parts.append(f"{total} 集")
        latest = str(getattr(series, "latest_episode", "") or "")
        if latest:
            subtitle_parts.append(f"最新 {latest}")
        card = PosterCard(series, title=title,
                          subtitle=" · ".join(item for item in subtitle_parts if item),
                          cover=str(getattr(series, "cover", "") or ""))
        # 卡片自带的信号在番剧库里没有接收方（板块只接搜索页的），
        # 这里直接给按钮挂上"库语义"的处理：不 disconnect，避免空连接报错
        card.play_button.clicked.connect(lambda: self.playRequested.emit(series, None))
        card.detail_button.clicked.connect(lambda: self.detailRequested.emit(series))
        card.follow_button.setText("取消追番")
        card.follow_button.clicked.connect(lambda: self._unfollow(series))
        card.download_button.clicked.connect(lambda: self.downloadRequested.emit(series))
        self._cards[normalize_title_safe(title)] = card
        return card

    def _continue_rows(self) -> list[tuple]:
        """继续观看：兼容"整体条目 / (series, episode, progress) 元组"两种形态。"""
        if self._library is None:
            return []
        try:
            entries = list(self._library.continue_watching() or [])
        except Exception as error:  # noqa: BLE001
            self._note_fallback(f"读取继续观看失败：{error}")
            return []
        rows: list[tuple] = []
        for entry in entries:
            series = episode = progress = None
            if isinstance(entry, (list, tuple)):
                if len(entry) >= 2:
                    series, episode = entry[0], entry[1]
                if len(entry) >= 3:
                    progress = entry[2]
            else:
                series = getattr(entry, "series", None) or entry
                episode = getattr(entry, "episode", None)
                progress = getattr(entry, "progress", None)
            if series is not None:
                rows.append((series, episode, progress))
        return rows

    def _make_continue_card(self, series, episode, progress) -> PosterCard:  # noqa: ANN001
        title = str(getattr(series, "title", "") or "未命名")
        subtitle_parts: list[str] = []
        if episode is not None:
            subtitle_parts.append(episode_label(episode))
        percent = int(getattr(progress, "percent", 0) or 0) if progress is not None else 0
        if percent > 0:
            subtitle_parts.append(f"看到 {percent}%")
        card = PosterCard(series, title=title,
                          subtitle=" · ".join(subtitle_parts) or "上次没看到一集就退出了",
                          cover=str(getattr(series, "cover", "") or ""))
        card.play_button.clicked.connect(lambda: self.playRequested.emit(series, episode))
        card.detail_button.clicked.connect(lambda: self.detailRequested.emit(series))
        card.follow_button.setText("取消追番")
        card.follow_button.clicked.connect(lambda: self._unfollow(series))
        card.download_button.setVisible(False)
        return card

    @staticmethod
    def _clear_layout(layout) -> None:  # noqa: ANN001
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    # ------------------------------------------------------------------ 动作

    def _unfollow(self, series) -> None:  # noqa: ANN001
        if self._library is None:
            self._toaster.error(NO_BACKEND)
            return
        try:
            self._library.unfollow(series)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"取消追番失败：{error}")
            return
        self._toaster.success("已取消追番")
        self.reload()
        self.libraryChanged.emit()

    def _check_updates(self) -> None:
        if self._update_worker is not None:
            self._toaster.info("正在检查更新，请稍候")
            return
        self._busy("正在检查追番更新…")
        worker = AnimeUpdateWorker(self._library, self)
        worker.progressed.connect(lambda message: self._report_status(message))
        worker.finishedReport.connect(self._on_updates_finished)
        worker.failed.connect(self._on_updates_failed)
        worker.finished.connect(self._on_update_thread_finished)
        self._update_worker = worker
        self._check_button.setEnabled(False)
        worker.start()

    def _on_updates_finished(self, report) -> None:  # noqa: ANN001
        self._update_report = report
        with_new = int(getattr(report, "with_new", 0) or 0)
        checked = int(getattr(report, "checked", 0) or 0)
        failed = int(getattr(report, "failed", 0) or 0)
        message = f"检查更新完成：核对 {checked} 部，其中 {with_new} 部有新集"
        if failed:
            message += f"，{failed} 部失败"
        self._idle(message)
        if with_new:
            self._toaster.success(f"{with_new} 部追番有新集")
        self.reload()

    def _on_updates_failed(self, message: str) -> None:
        self._idle(f"检查更新失败：{message}")
        self._toaster.error(f"检查更新失败：{message}")

    def _on_update_thread_finished(self) -> None:
        self._update_worker = None
        self._check_button.setEnabled(True)

    # ------------------------------------------------------------------ 导入导出

    def _export_json(self) -> None:
        storage = self.storage()
        if storage is None:
            self._toaster.error(f"无法导出：{self._storage_error or NO_BACKEND}")
            return
        try:
            payload = storage.export_payload()
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"导出失败：{error}")
            return
        text = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False, indent=2)
        path, _filter = QFileDialog.getSaveFileName(
            self, "导出追番与进度", "anime-backup.json", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as error:
            self._toaster.error(f"写入失败：{error}")
            return
        self._toaster.success(f"已导出：{path}")
        self._report_status(f"已导出追番与进度到 {path}")

    def _import_json(self) -> None:
        storage = self.storage()
        if storage is None:
            self._toaster.error(f"无法导入：{self._storage_error or NO_BACKEND}")
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, "导入追番与进度", "", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as error:
            self._toaster.error(f"读取失败：{error}")
            return
        try:
            storage.import_payload(text)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"导入失败：{error}")
            return
        self._toaster.success("已导入追番与进度")
        self.reload()
        self.libraryChanged.emit()

    def on_shown(self) -> None:
        self.reload()

    def shutdown(self) -> None:
        worker = self._update_worker
        self._update_worker = None
        if worker is not None and worker.isRunning():
            worker.wait(3000)


__all__ = ["AnimeLibraryPage", "AnimeUpdateWorker", "EMPTY_HINT", "NO_BACKEND"]
