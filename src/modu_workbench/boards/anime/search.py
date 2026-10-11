"""墨软动漫 · 搜索页：跨规则搜索，**边搜边出**，同一作品合并成一张卡。

为什么必须"边搜边出"：动漫规则可以有很多条（内置 + 用户 + 订阅），
一次搜索要逐条抓站，全等完再渲染的话用户盯着空列表等十几秒 —— 实测体验很差。
所以搜索跑在 `QThread` 里，每完成一条规则就通过 `on_rule_done` 回调回到 UI 线程
**立刻插入这一条规则的卡片**，界面永远不阻塞。

合并规则：同一部作品在不同规则下标题写法不同（书名号、间隔号、空格），
用 `normalize_title_safe`（优先 `core.anime.normalize_title`）归一后同类合并，
卡片上按来源逐条列出"哪条规则命中 + 年份 + 备注"。

空状态必须给出**下一步**：没有规则时说"先到规则页导入或启用一条"，
有规则但没搜到结果时说清楚"是规则没命中，可以去规则页试跑看看"。
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.ui_kit.components import (
    ColumnPage,
    EmptyState,
    SectionCard,
    TaskBar,
    chip,
    hint_label,
    primary_button,
    row,
)
from modu_workbench.ui_kit.toast import Toaster
from modu_workbench.ui_kit.tokens import SPACE

from .widgets import (
    PosterCard,
    normalize_title_safe,
    rule_label,
    rule_supported,
    rule_warning,
    search_hits,
    set_empty_hint,
)

#: 没有规则时给用户的下一步（空状态文案，测试会断言它）
NO_RULE_HINT = "还没有规则：先到「规则」页导入或启用一条"
NO_RULE_MESSAGE = "还没有规则：先到「规则」页导入或启用一条（内置规则随包提供，可在规则页启用）"
#: 后端不可用时的说明（板块顶栏也用它，保证只有一份文案）
NO_BACKEND = "动漫后端未就绪：核心模块还没装上，暂时无法搜索"


class AnimeSearchWorker(QThread):
    """跨规则搜索（在后台线程里逐条规则回调，主线程只负责插卡片）。"""

    ruleDone = Signal(object)          # SearchGroup：某条规则刚完成
    finishedAll = Signal(int, object)  # (完成条数, 错误列表)
    failed = Signal(str)

    def __init__(self, keyword: str, library=None, *, rule_keys: list[str] | None = None,  # noqa: ANN001
                 limit: int = 30, parent: QWidget | None = None):
        super().__init__(parent)
        self._keyword = keyword
        self._library = library
        self._rule_keys = list(rule_keys or []) or None
        self._limit = limit

    def run(self) -> None:  # noqa: D102
        library = self._library
        if library is None:
            self.failed.emit(NO_BACKEND)
            return
        done = 0

        def on_rule_done(group) -> None:  # noqa: ANN001
            nonlocal done
            if group is None:
                return
            done += 1
            # 跨线程信号是队列投递：卡片插入发生在主线程，这里绝不能碰控件
            self.ruleDone.emit(group)

        try:
            try:
                groups = library.search(
                    self._keyword, rule_keys=self._rule_keys, limit=self._limit,
                    on_rule_done=on_rule_done,
                )
            except TypeError:
                # 后端 search 还没带 on_rule_done（并行开发中）：退回一次性结果，
                # 但仍然逐条走同一个回调，界面代码只有一条路径
                groups = library.search(
                    self._keyword, rule_keys=self._rule_keys, limit=self._limit)
                for group in groups or []:
                    on_rule_done(group)
        except Exception as error:  # noqa: BLE001  后端异常 → 可读原因，不是栈
            self.failed.emit(str(error) or error.__class__.__name__)
            return
        errors = [str(item) for item in (getattr(library, "last_errors", None) or [])]
        self.finishedAll.emit(done, errors)


class AnimeSearchPage(QWidget):
    """搜索页：关键词 + 规则范围 → 增量结果卡片。"""

    playRequested = Signal(object, object)   # payload(SearchGroup), episode | None
    detailRequested = Signal(object)         # payload
    followRequested = Signal(object)         # payload
    downloadRequested = Signal(object)       # payload（下载整部）
    libraryChanged = Signal()
    resultAdded = Signal(int)                # 已插入卡片数（增量插入的顺序证据）

    def __init__(self, library=None, toaster: Toaster | None = None,  # noqa: ANN001
                 parent: QWidget | None = None, *, task_bar: TaskBar | None = None):
        super().__init__(parent)
        self._library = library
        self._toaster = toaster or Toaster(self)
        self._task = task_bar
        self._worker: AnimeSearchWorker | None = None
        self._cards: dict[str, PosterCard] = {}
        self._rules: list = []
        self._tail_stretch = False
        self._fallback_status = QLabel("就绪。输入番剧名后搜索。")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._page = ColumnPage(
            "搜索",
            "逐条规则并行搜索并按作品合并结果：同一条番剧命中的多个来源会列在一张卡上。"
            "结果边搜边出，某条规则失败不影响其它规则。",
        )
        outer.addWidget(self._page)

        # ---- 搜索区 ----
        self._keyword = QLineEdit()
        self._keyword.setObjectName("searchBox")
        self._keyword.setPlaceholderText("输入番剧名，例如：葬送的芙莉莲")
        self._keyword.returnPressed.connect(self._start_search)

        self._rule_filter = QComboBox()
        self._rule_filter.setMinimumWidth(180)
        self._rule_filter.setToolTip("只搜某一条规则（排查「这条规则还灵不灵」时用）")

        self._enabled_only = QCheckBox("只搜已启用规则")
        self._enabled_only.setChecked(True)
        self._enabled_only.setToolTip("未启用/已退役的规则默认不参与搜索，可在「规则」页调整")

        self._search_button = primary_button("搜索（全部规则）")
        self._search_button.clicked.connect(self._start_search)

        search_card = SectionCard("搜索")
        search_card.add(row(self._keyword, self._rule_filter, self._enabled_only,
                            self._search_button, stretch=self._keyword))
        search_card.add(hint_label(
            "规则驱动的采集：站点结构变了就换规则。遇到纯 JS 渲染的站点会解析失败（我们没有浏览器内核）。"))
        self._page.add(search_card)

        # ---- 结果区 ----
        self._count_chip = chip("0 部")
        self._result_card = SectionCard("搜索结果", actions=[self._count_chip])
        self._empty = EmptyState("🔍", "还没有搜索结果", "输入番剧名后点「搜索（全部规则）」")
        self._results_host = QWidget()
        self._results_layout = QVBoxLayout(self._results_host)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.setSpacing(SPACE["sm"])
        self._result_card.add(self._empty)
        self._result_card.add(self._results_host)
        self._results_host.setVisible(False)
        self._page.add(self._result_card)
        self._set_tail_stretch(True)

        self.reload_rules()

    # ------------------------------------------------------------------ 状态

    @property
    def _status(self) -> QLabel:
        """兼容旧引用：状态文字统一显示在板块任务条上。"""
        return self._task._label if self._task is not None else self._fallback_status  # noqa: SLF001

    def _note_fallback(self, message: str) -> None:
        label = getattr(self, "_fallback_status", None)
        if label is not None:
            label.setText(message)

    def _report(self, message: str, done: int = 0, total: int = 0) -> None:
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

    # ------------------------------------------------------------------ 规则

    def reload_rules(self) -> None:
        """刷新规则下拉；后端拿不到规则时给空列表 + 明确的下一步提示。"""
        current = self._rule_filter.currentData()
        rules: list = []
        warning = ""
        if self._library is None:
            warning = "动漫后端未就绪：核心模块还没装上，规则列表暂时为空。"
        else:
            try:
                rules = list(self._library.rules(enabled_only=False) or [])
            except Exception as error:  # noqa: BLE001
                warning = f"读取规则失败：{error}"
        self._rules = rules

        self._rule_filter.blockSignals(True)
        self._rule_filter.clear()
        self._rule_filter.addItem(f"全部规则（{len(rules)} 条）", "")
        for rule in rules:
            text = f"{rule_label(rule)}"
            note = rule_warning(rule)
            if note:
                text += f"（{note}）"
            self._rule_filter.addItem(text, str(getattr(rule, "key", "") or ""))
        index = self._rule_filter.findData(current)
        self._rule_filter.setCurrentIndex(index if index >= 0 else 0)
        self._rule_filter.blockSignals(False)

        if not rules:
            set_empty_hint(self._empty, NO_RULE_HINT)
        elif warning:
            set_empty_hint(self._empty, warning)
        else:
            set_empty_hint(self._empty, "输入番剧名后点「搜索（全部规则）」")
        self._update_buttons()

    def _target_rule_keys(self) -> list[str] | None:
        """本次搜索的范围：指定规则 → 只搜它；否则按"只搜已启用"过滤。"""
        chosen = self._rule_filter.currentData()
        if chosen:
            return [str(chosen)]
        if not self._enabled_only.isChecked():
            return None
        keys = [str(getattr(rule, "key", "")) for rule in self._rules
                if bool(getattr(rule, "enabled", True)) and rule_supported(rule)]
        return [key for key in keys if key] or None

    # ------------------------------------------------------------------ 搜索

    def _start_search(self) -> None:
        keyword = self._keyword.text().strip()
        if not keyword:
            self._toaster.error("请输入番剧名")
            return
        if not self._rules:
            self._toaster.error(NO_RULE_MESSAGE)
            set_empty_hint(self._empty, NO_RULE_HINT)
            return
        if self._worker is not None:
            return
        self._clear_cards()
        self._busy(f"搜索中：{keyword}…")
        self._search_button.setEnabled(False)
        worker = AnimeSearchWorker(keyword, self._library,
                                   rule_keys=self._target_rule_keys(), limit=30, parent=self)
        worker.ruleDone.connect(self._on_group)
        worker.finishedAll.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_thread_finished)
        self._worker = worker
        worker.start()

    def _on_group(self, group) -> None:  # noqa: ANN001
        """一条规则搜完了：合并进已有卡片，或新建一张。"""
        title = str(getattr(group, "title", "") or "").strip()
        key = normalize_title_safe(title)
        if not key:
            # 标题为空（规则选择器只命中了链接）：不能让它和别的空标题合并成一张卡
            key = f"#blank-{len(self._cards)}"
        hits = search_hits(group)
        card = self._cards.get(key)
        if card is None:
            card = PosterCard(
                group,
                title=title or "（规则没给作品名）",
                subtitle=self._subtitle_for(group),
                cover=str(getattr(group, "cover", "") or ""),
            )
            card.playRequested.connect(self._emit_play)
            card.detailRequested.connect(self.detailRequested)
            card.followRequested.connect(self.followRequested)
            card.downloadRequested.connect(self.downloadRequested)
            self._results_layout.addWidget(card)
            self._cards[key] = card
        for hit in hits:
            card.add_source(hit)
        if not hits:
            # 没有来源：至少把规则名写出来，否则用户不知道这条结果从哪来
            card.add_source(None, rule_label(self._current_rule_for(group)))
        self._update_results_state()
        self.resultAdded.emit(len(self._cards))

    def _subtitle_for(self, group) -> str:  # noqa: ANN001
        parts: list[str] = []
        year = str(getattr(group, "year", "") or "").strip()
        if year:
            parts.append(year)
        count = len(search_hits(group))
        if count:
            parts.append(f"命中 {count} 个来源")
        return " · ".join(parts)

    def _current_rule_for(self, group):  # noqa: ANN001
        """兜底：结果分组不带规则对象时，从第一条来源里反查规则。"""
        hits = search_hits(group)
        if not hits:
            return None
        rule_key = str(getattr(hits[0], "rule_key", "") or "")
        for rule in self._rules:
            if str(getattr(rule, "key", "")) == rule_key:
                return rule
        return hits[0]

    def _emit_play(self, payload) -> None:  # noqa: ANN001
        self.playRequested.emit(payload, None)

    def _update_results_state(self) -> None:
        count = len(self._cards)
        self._count_chip.setText(f"{count} 部")
        self._empty.setVisible(count == 0)
        self._results_host.setVisible(count > 0)
        self._set_tail_stretch(count == 0)

    def _clear_cards(self) -> None:
        for card in list(self._cards.values()):
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._update_results_state()

    def _on_finished(self, done: int, errors: list) -> None:
        count = len(self._cards)
        message = f"搜索完成：{count} 部作品（{done} 条规则返回结果）"
        if errors:
            message += "；部分规则失败：" + "；".join(str(item) for item in errors[:3])
        self._idle(message)
        if count == 0:
            set_empty_hint(
                self._empty,
                "没有搜到结果：可能是规则没命中（去「规则」页点「试跑」看看哪一步断了）"
                "，或关键词换一种写法",
            )

    def _on_failed(self, message: str) -> None:
        self._idle(f"搜索失败：{message}")
        self._toaster.error(f"搜索失败：{message}")
        set_empty_hint(self._empty, f"搜索失败：{message}")

    def _on_thread_finished(self) -> None:
        self._worker = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        self._search_button.setEnabled(self._worker is None)

    def on_shown(self) -> None:
        self.reload_rules()

    def shutdown(self) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.wait(3000)
        self._worker = None


__all__ = ["AnimeSearchPage", "AnimeSearchWorker", "NO_BACKEND", "NO_RULE_HINT",
           "NO_RULE_MESSAGE"]
