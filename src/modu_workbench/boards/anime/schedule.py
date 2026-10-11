"""墨软动漫 · 时间表：按星期展示"今天更新什么"。

数据来源只有两个，**都必须显式存在才显示**（不猜、不编）：

1. **Bangumi 元数据**：`BangumiClient` 的每日放送表（需要联网，因此只在用户点
   「从 Bangumi 刷新」时抓，进页面绝不自动联网）；
2. **规则提供的 `schedule`**：规则 JSON 里的 `schedule` 段（`catalogue` 的同族扩展）。

两样都没有时给**诚实的空状态**："时间表需要 Bangumi 元数据或规则提供 schedule，
当前没有可显示的数据" —— 而不是显示一个七列全空、让人以为坏了的表格。

已追番的条目若带 `air_weekday`（0=周一），也会自动落到对应星期下。
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QLabel,
    QTabWidget,
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

from .widgets import rule_label, set_empty_hint

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

#: 空状态文案（测试会断言）：说清楚"为什么没有"和"怎么才有"
EMPTY_HINT = "时间表需要 Bangumi 元数据或规则提供 schedule，当前没有可显示的数据"


class _ScheduleItem(QLabel):
    """时间表里的一条：双击打开详情。

    做成子类而不是给 QLabel 实例挂 `mouseDoubleClickEvent`：PySide6 只认
    **类上**重写的虚函数，实例属性赋值不会拦到 Qt 的事件分发（踩过）。
    """

    doubleClicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class BangumiScheduleWorker(QThread):
    """从 Bangumi 拉每日放送（后台线程，失败给可读原因）。"""

    finishedSchedule = Signal(object)   # list[dict]（每天一组）
    failed = Signal(str)

    def __init__(self, library=None, parent: QWidget | None = None):  # noqa: ANN001
        super().__init__(parent)
        self._library = library

    def run(self) -> None:  # noqa: D102
        try:
            days = None
            library = self._library
            # 优先问业务库（它可能已经把 Bangumi 结果缓存到本地）
            for name in ("bangumi_calendar", "calendar", "schedule"):
                getter = getattr(library, name, None)
                if callable(getter):
                    try:
                        days = getter()
                    except TypeError:
                        continue
                    except Exception as error:  # noqa: BLE001
                        self.failed.emit(str(error) or error.__class__.__name__)
                        return
                    break
            if days is None:
                from modu_workbench.core.anime import BangumiClient

                client = BangumiClient()
                for name in ("calendar", "get_calendar", "airing"):
                    getter = getattr(client, name, None)
                    if callable(getter):
                        days = getter()
                        break
            if days is None:
                self.failed.emit("Bangumi 客户端没有可用的放送表接口")
                return
        except Exception as error:  # noqa: BLE001  后端模块缺失/网络异常
            self.failed.emit(str(error) or error.__class__.__name__)
            return
        self.finishedSchedule.emit(days)


class AnimeSchedulePage(QWidget):
    """时间表：七个星期标签 + 已追番/元数据条目。"""

    detailRequested = Signal(object)   # series

    def __init__(self, library=None, toaster: Toaster | None = None,  # noqa: ANN001
                 parent: QWidget | None = None, *, task_bar: TaskBar | None = None):
        super().__init__(parent)
        self._library = library
        self._toaster = toaster or Toaster(self)
        self._task = task_bar
        self._worker: BangumiScheduleWorker | None = None
        self._days: list[list] = [[] for _ in WEEKDAYS]
        self._sources: list[str] = []
        self._fallback_status = QLabel("就绪。")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._page = ColumnPage(
            "时间表",
            "按星期查看更新：数据只来自 Bangumi 元数据或规则自带的 schedule 段，"
            "没有来源时不做任何猜测。",
        )
        outer.addWidget(self._page)

        self._count_chip = chip("0 部")
        refresh = ghost_button("刷新（本地数据）")
        refresh.clicked.connect(self.reload)
        bangumi = primary_button("从 Bangumi 刷新")
        bangumi.setToolTip("联网抓 Bangumi 每日放送表（只在点击时联网，进页面不会自动请求）")
        bangumi.clicked.connect(self._refresh_from_bangumi)
        self._card = SectionCard("本周更新", actions=[self._count_chip, refresh, bangumi])
        self._source_hint = hint_label("")
        self._card.add(self._source_hint)

        self._tabs = QTabWidget()
        self._tab_hosts: list[QWidget] = []
        for index, name in enumerate(WEEKDAYS):
            host = QWidget()
            layout = QVBoxLayout(host)
            layout.setContentsMargins(SPACE["sm"], SPACE["sm"], SPACE["sm"], SPACE["sm"])
            layout.setSpacing(SPACE["sm"])
            layout.addStretch(1)
            self._tabs.addTab(host, name)
            self._tab_hosts.append(host)
        self._card.add(self._tabs)

        self._empty = EmptyState("🗓", "时间表还是空的", EMPTY_HINT)
        self._card.add(self._empty)
        self._page.add(self._card)
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

    def _report_status(self, message: str) -> None:
        self._note_fallback(message)
        if self._task is not None:
            self._task.note(message)

    def _idle(self, message: str = "") -> None:
        if message:
            self._note_fallback(message)
        if self._task is not None:
            self._task.idle(message)

    def empty_hint_text(self) -> str:
        """空状态提示文字（测试断言"诚实边界"用）。"""
        label = self._empty.findChild(QLabel, "emptyHint")
        return label.text() if label is not None else ""

    # ------------------------------------------------------------------ 数据

    def reload(self) -> None:
        """只用**本地已有**的数据重建（已追番的 air_weekday + 规则里的 schedule 声明）。

        为什么不在 reload 里联 Bangumi：进页面就联网会让"点错板块"也产生请求，
        而且采集站的元数据接口有限流；联网刷新交给显式按钮。
        """
        days: list[list] = [[] for _ in WEEKDAYS]
        sources: list[str] = []

        for series in self._local_series():
            weekday = int(getattr(series, "air_weekday", -1) or -1)
            if 0 <= weekday < len(WEEKDAYS):
                days[weekday].append(series)
        if any(days):
            sources.append("追番的放送日")

        schedule_rules = self._rules_with_schedule()
        for rule in schedule_rules:
            sources.append(f"规则「{rule_label(rule)}」声明了 schedule（需联网抓取）")

        self._days = days
        self._sources = sources
        self._rebuild_tabs()
        total = sum(len(items) for items in days)
        self._count_chip.setText(f"{total} 部")
        self._source_hint.setText("；".join(sources) if sources else "")
        self._source_hint.setVisible(bool(sources))
        has_data = total > 0
        self._empty.setVisible(not has_data)
        self._tabs.setVisible(has_data)
        if has_data:
            suffix = f"（{self._sources[0]}）" if self._sources else ""
            self._idle(f"时间表：{total} 部{suffix}")
        else:
            set_empty_hint(self._empty, EMPTY_HINT)
            self._idle(EMPTY_HINT)

    def _local_series(self) -> list:
        """本地就能拿到的番剧（追番列表里的放送日）。"""
        if self._library is None:
            return []
        try:
            return list(self._library.favorites() or [])
        except Exception:  # noqa: BLE001  后端未就绪：当作没有本地数据
            return []

    def _rules_with_schedule(self) -> list:
        if self._library is None:
            return []
        try:
            rules = list(self._library.rules(enabled_only=True) or [])
        except Exception:  # noqa: BLE001
            return []
        found = []
        for rule in rules:
            schedule = getattr(rule, "schedule", None)
            if isinstance(schedule, dict) and schedule:
                found.append(rule)
        return found

    def _rebuild_tabs(self) -> None:
        for index, host in enumerate(self._tab_hosts):
            layout = host.layout()
            while layout.count() > 1:                     # 末尾的弹簧保留
                item = layout.takeAt(0)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
            for series in self._days[index]:
                layout.insertWidget(layout.count() - 1, self._item_label(series))
            self._tabs.setTabText(index, f"{WEEKDAYS[index]}（{len(self._days[index])}）")

    def _item_label(self, series) -> QLabel:  # noqa: ANN001
        title = str(getattr(series, "title", "") or "未命名")
        latest = str(getattr(series, "latest_episode", "") or "")
        text = f"· {title}" + (f"（最新 {latest}）" if latest else "")
        label = _ScheduleItem(text)
        label.setObjectName("cardTagline")
        label.setWordWrap(True)
        label.setToolTip("双击打开详情")
        label.doubleClicked.connect(lambda s=series: self.detailRequested.emit(s))
        return label

    # ------------------------------------------------------------------ 联网刷新

    def _refresh_from_bangumi(self) -> None:
        if self._worker is not None:
            self._toaster.info("正在刷新 Bangumi 时间表，请稍候")
            return
        self._report_status("正在从 Bangumi 获取每日放送…")
        worker = BangumiScheduleWorker(self._library, self)
        worker.finishedSchedule.connect(self._on_bangumi)
        worker.failed.connect(self._on_bangumi_failed)
        worker.finished.connect(lambda w=worker: self._forget_worker(w))
        self._worker = worker
        worker.start()

    def _forget_worker(self, worker: BangumiScheduleWorker) -> None:
        if self._worker is worker:
            self._worker = None

    def _on_bangumi(self, days) -> None:  # noqa: ANN001
        merged: list[list] = [[] for _ in WEEKDAYS]
        for index, group in enumerate(days or []):
            weekday = self._weekday_of(group, index)
            for item in self._items_of(group):
                merged[weekday].append(item)
        self._days = merged
        self._sources = ["Bangumi 每日放送"]
        self._rebuild_tabs()
        total = sum(len(items) for items in merged)
        self._count_chip.setText(f"{total} 部")
        self._source_hint.setText("Bangumi 每日放送")
        self._source_hint.setVisible(True)
        self._empty.setVisible(total == 0)
        self._tabs.setVisible(total > 0)
        if total == 0:
            set_empty_hint(self._empty, EMPTY_HINT)
        self._idle(f"Bangumi 时间表：{total} 部")

    @staticmethod
    def _weekday_of(group, index: int) -> int:  # noqa: ANN001
        """Bangumi 的放送表按周一到周日返回；带 weekday 字段的按字段取值。"""
        if isinstance(group, dict):
            for key in ("weekday", "day", "weekday_id"):
                value = group.get(key)
                if isinstance(value, int) and 0 <= value < len(WEEKDAYS):
                    return value
                if isinstance(value, dict):
                    value = value.get("id")
                    if isinstance(value, int) and 1 <= value <= 7:
                        return value - 1
        return index % len(WEEKDAYS)

    @staticmethod
    def _items_of(group) -> list:  # noqa: ANN001
        if isinstance(group, dict):
            for key in ("items", "subjects", "list"):
                value = group.get(key)
                if isinstance(value, list):
                    return value
            return []
        if isinstance(group, (list, tuple)):
            return list(group)
        return []

    def on_shown(self) -> None:
        self.reload()

    def shutdown(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None and worker.isRunning():
            worker.wait(3000)


__all__ = ["AnimeSchedulePage", "BangumiScheduleWorker", "EMPTY_HINT", "WEEKDAYS"]
