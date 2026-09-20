"""墨软文档：AI 循环美化面板（MR-DOC-305）。

用户输入目标（如"美化这份报告""统一为正式公文风格""优化表格并生成摘要"）后，
引擎按「分析 → 计划 → 工具调用 → 五维评分 → 未达标继续」循环执行；
界面上能实时看到每轮计划、工具调用、评分、耗时与 diff，并能：
- 随时「停止」；
- 用「回滚到选中轮次」回到任意一轮的结果（每次都会存快照）；
- 「导出结果」写成文件。

循环跑在后台线程里，因此界面不会卡；事件用信号回主线程更新。
"""
from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.document import (
    EXPORT_TARGETS,
    DocumentIR,
    DocumentLibrary,
    LoopOptions,
    LoopResult,
    export_label,
    scene_choices,
    template_choices,
    tool_names,
    TOOL_REGISTRY,
)
from modu_workbench.core.document import quality as quality_engine
from modu_workbench.ui_kit.components import TaskBar
from modu_workbench.ui_kit.settings import app_settings
from modu_workbench.ui_kit.toast import Toaster

from . import widgets as W

GOAL_EXAMPLES = (
    "美化这份报告并统一为正式风格",
    "统一为公文风格并生成目录",
    "润色语言、压缩篇幅并修正标点",
    "优化表格、补全计算并生成摘要",
)


class LoopWorker(W.DocumentWorker):
    """循环美化后台线程：把引擎事件转成 Qt 信号。"""

    loopEvent = Signal(object)
    confirmRequested = Signal(object)      # 请求主线程弹「继续下一轮？」（见 answer_confirm）

    #: 等用户点确认的最长时间（秒）：界面没了也不至于把线程永久挂住
    CONFIRM_TIMEOUT = 3600.0

    def __init__(self, job, *, label: str = "", parent: Optional[QWidget] = None) -> None:  # noqa: ANN001
        super().__init__(job, label=label, parent=parent)
        self._confirm_event = threading.Event()
        self._confirm_answer = False

    def ask_confirm(self, report: object) -> bool:
        """**在工作线程里**被引擎调用：发信号给主线程弹框，然后阻塞等答复。

        设置页的「每轮结束人工确认」打开时才用得上（见 `LoopPanel._start`）。
        """
        self._confirm_answer = False
        self._confirm_event.clear()
        self.confirmRequested.emit(report)
        if not self._confirm_event.wait(timeout=self.CONFIRM_TIMEOUT):
            return False
        return self._confirm_answer

    def answer_confirm(self, ok: bool) -> None:
        """主线程给出答复（点「停止」也会用它解锁，避免线程卡在等待里）。"""
        self._confirm_answer = bool(ok)
        self._confirm_event.set()


class LoopPanel(QWidget):
    """AI 循环美化。"""

    irChanged = Signal(object)
    statusMessage = Signal(str)

    def __init__(self, library: DocumentLibrary, *, task_bar: Optional[TaskBar] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._task = task_bar
        self._library = library
        self._toaster = Toaster(self)
        self._ir: Optional[DocumentIR] = None
        self._result: Optional[LoopResult] = None
        self._worker: Optional[LoopWorker] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        body = QHBoxLayout()
        body.setSpacing(10)

        # ---- 左：目标与参数 ----
        host = QWidget()
        host.setObjectName("actionPanel")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        title = QLabel("循环美化目标")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        hint = QLabel("用一句话说清楚想要的结果；工具列表可限制本轮允许做的操作。")
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._goal = QLineEdit()
        self._goal.setPlaceholderText(GOAL_EXAMPLES[0])
        layout.addWidget(self._goal)
        example_row = QHBoxLayout()
        for text in GOAL_EXAMPLES[:3]:
            button = QPushButton(text[:8] + "…")
            button.setToolTip(text)
            button.clicked.connect(lambda _=False, value=text: self._goal.setText(value))
            example_row.addWidget(button)
        layout.addLayout(example_row)

        form = QFormLayout()
        form.setSpacing(8)
        self._scene = QComboBox()
        for key, label in scene_choices():
            self._scene.addItem(label, key)
        form.addRow("主场景", self._scene)
        self._template = QComboBox()
        for key, label in template_choices():
            self._template.addItem(label, key)
        form.addRow("风格模板", self._template)
        self._rounds = QSpinBox()
        self._rounds.setRange(1, 10)
        self._rounds.setValue(3)
        form.addRow("最大轮次", self._rounds)
        self._threshold = QDoubleSpinBox()
        self._threshold.setRange(0.5, 1.0)
        self._threshold.setSingleStep(0.01)
        self._threshold.setValue(0.9)
        form.addRow("质量阈值", self._threshold)
        self._max_cost = QDoubleSpinBox()
        self._max_cost.setRange(0.0, 1000.0)
        self._max_cost.setSingleStep(0.1)
        self._max_cost.setToolTip("0 = 不限制；需要在大模型设置里填单价才会累计成本")
        form.addRow("成本上限", self._max_cost)
        self._max_seconds = QSpinBox()
        self._max_seconds.setRange(0, 3600)
        self._max_seconds.setSuffix(" 秒")
        self._max_seconds.setToolTip("0 = 不限制")
        form.addRow("时间上限", self._max_seconds)
        layout.addLayout(form)

        self._use_ai = QCheckBox("调用大模型（关闭则只用本地规则）")
        self._use_ai.setChecked(True)
        layout.addWidget(self._use_ai)
        self._snapshot = QCheckBox("每轮保存版本快照（可回滚）")
        self._snapshot.setChecked(True)
        layout.addWidget(self._snapshot)
        self._stop_on_no_change = QCheckBox("本轮无可改进项就停止")
        self._stop_on_no_change.setChecked(True)
        layout.addWidget(self._stop_on_no_change)
        self._auto_apply = QCheckBox("每轮自动把结果同步到查看器")
        self._auto_apply.setChecked(True)
        layout.addWidget(self._auto_apply)

        tools_title = QLabel("允许调用的工具")
        tools_title.setObjectName("sectionTitle")
        layout.addWidget(tools_title)
        self._tool_list = QListWidget()
        self._tool_list.setMaximumHeight(150)
        for name in tool_names():
            spec = TOOL_REGISTRY.get(name)
            item = QListWidgetItem(f"{spec.label}｜{spec.description}" if spec else name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            if spec is not None and spec.needs_ai:
                item.setToolTip("需要已配置大模型")
            self._tool_list.addItem(item)
        layout.addWidget(self._tool_list)

        buttons = QHBoxLayout()
        self._start_button = QPushButton("开始循环美化")
        self._start_button.setObjectName("primaryButton")
        self._start_button.clicked.connect(self._start)
        buttons.addWidget(self._start_button)
        self._stop_button = QPushButton("停止")
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self._stop)
        buttons.addWidget(self._stop_button)
        layout.addLayout(buttons)

        result_row = QHBoxLayout()
        self._rollback_button = QPushButton("回滚到选中轮次")
        self._rollback_button.setEnabled(False)
        self._rollback_button.clicked.connect(self._rollback)
        result_row.addWidget(self._rollback_button)
        self._apply_button = QPushButton("应用到文档")
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._apply)
        result_row.addWidget(self._apply_button)
        result_row.addStretch(1)
        layout.addLayout(result_row)
        export_row = QHBoxLayout()
        export_row.addWidget(QLabel("导出："))
        self._export_target = QComboBox()
        for key, label in EXPORT_TARGETS:
            self._export_target.addItem(label, key)
        export_row.addWidget(self._export_target)
        self._export_button = QPushButton("导出结果")
        self._export_button.setEnabled(False)
        self._export_button.clicked.connect(self._export)
        export_row.addWidget(self._export_button)
        export_row.addStretch(1)
        layout.addLayout(export_row)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(host)
        body.addWidget(scroll, 3)

        # ---- 右：轮次 / diff / 日志 ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        self._summary = QLabel("还没有运行：先填目标再点「开始循环美化」")
        self._summary.setObjectName("readerStatus")
        self._summary.setWordWrap(True)
        right_layout.addWidget(self._summary)

        rounds_title = QLabel("每轮结果（计划 / 工具 / 修改 / 评分 / 耗时）")
        rounds_title.setObjectName("sectionTitle")
        right_layout.addWidget(rounds_title)
        self._rounds_table = QTableWidget(0, 7)
        self._rounds_table.setHorizontalHeaderLabels(
            ["轮次", "计划", "工具调用", "修改", "质量", "AI", "耗时"])
        header = self._rounds_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._rounds_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._rounds_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._rounds_table.setMaximumHeight(200)
        right_layout.addWidget(self._rounds_table)

        diff_title = QLabel("选中轮次的 diff（相对上一轮）")
        diff_title.setObjectName("sectionTitle")
        right_layout.addWidget(diff_title)
        self._diff = W.DiffBrowser()
        right_layout.addWidget(self._diff, 3)

        log_title = QLabel("运行日志")
        log_title.setObjectName("sectionTitle")
        right_layout.addWidget(log_title)
        self._log = W.ReportBrowser()
        self._log.setMaximumHeight(160)
        right_layout.addWidget(self._log, 2)
        body.addWidget(right, 4)

        outer.addLayout(body, 1)
        self._rounds_table.itemSelectionChanged.connect(self._show_selected_round)
        self._apply_settings_defaults()

    # ---------------------------------------------------------------- 设置默认值

    def _apply_settings_defaults(self) -> None:
        """循环默认值取自设置页（未保存过的项保持面板默认，不覆盖用户手填的值）。"""
        settings = app_settings()
        for key, widget, cast in (
            ("document/max_rounds", self._rounds, int),
            ("document/quality_threshold", self._threshold, float),
            ("document/max_cost", self._max_cost, float),
        ):
            if not settings.contains(key):
                continue
            try:
                widget.setValue(cast(settings.value(key)))
            except (TypeError, ValueError):
                continue

    # ---------------------------------------------------------------- 参数

    def set_ir(self, ir: Optional[DocumentIR]) -> None:
        self._ir = ir
        self._start_button.setEnabled(ir is not None)
        if ir is None:
            self._summary.setText("先在左侧打开一个文档")

    def _selected_tools(self) -> tuple[str, ...]:
        names: list[str] = []
        for row in range(self._tool_list.count()):
            item = self._tool_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                names.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return tuple(names)

    def require_confirm(self) -> bool:
        """设置页的「每轮结束人工确认」（实时读，改完不用重进板块）。

        用 `type=bool`：Qt 在 Windows 注册表里把 bool 存成 "true"/"false" 字符串，
        `bool("false")` 会得到 True，等于"关不掉人工确认"（踩过一次）。
        """
        try:
            return bool(app_settings().value("document/require_confirm", False, type=bool))
        except Exception:  # noqa: BLE001  设置后端异常时按"不确认"处理，别打断循环
            return False

    def options(self) -> LoopOptions:
        return LoopOptions(
            goal=self._goal.text().strip(),
            scene=self._scene.currentData() or "polish",
            template=self._template.currentData() or "general",
            max_rounds=self._rounds.value(),
            quality_threshold=self._threshold.value(),
            max_cost=self._max_cost.value(),
            max_seconds=float(self._max_seconds.value()),
            stop_on_no_change=self._stop_on_no_change.isChecked(),
            use_ai=self._use_ai.isChecked(),
            snapshot=self._snapshot.isChecked(),
            allow_tools=self._selected_tools(),
            require_confirm=self.require_confirm(),
        )

    # ---------------------------------------------------------------- 运行

    def _start(self) -> None:
        if self._ir is None:
            self._toaster.info("先在左侧打开一个文档")
            return
        if self._worker is not None:
            return
        options = self.options()
        if options.use_ai and (self._library.ai is None or not self._library.ai.available):
            self._toaster.info("没有可用的大模型：本次将只用本地规则（可在「设置 → 大模型」配置）")
        self._rounds_table.setRowCount(0)
        self._log.set_lines([])
        self._diff.set_diff([])

        # 在工作线程里只用快照，避免后台线程读取界面线程正在改的对象
        ir_snapshot = self._ir.clone()
        worker = LoopWorker(
            lambda worker_: self._library.run_loop(
                ir_snapshot, options,
                on_event=lambda event: worker_.loopEvent.emit(event),
                should_stop=lambda: worker_.cancelled,
                # 「每轮结束人工确认」：引擎回调在工作线程里跑，
                # 因此走 worker 的"发信号 + 等答复"，弹框始终在主线程
                confirm=worker_.ask_confirm if options.require_confirm else None),
            label="循环美化", parent=self)
        worker.loopEvent.connect(self._on_event)
        worker.confirmRequested.connect(self._on_confirm_requested)
        worker.progress.connect(lambda done, total: W.report(
            self._task, f"循环美化 {done}/{total}", done, total))
        worker.message.connect(lambda text: W.note(self._task, text))
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        W.busy(self._task, "开始循环美化…")
        worker.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            # 若正卡在"等确认"，也要立刻解锁（按"不继续"处理）
            self._worker.answer_confirm(False)
            W.note(self._task, "已请求停止，等本轮结束后退出…")

    def _on_confirm_requested(self, report: object) -> None:
        """主线程弹「继续下一轮？」；点「否」循环就停在当前轮。"""
        worker = self._worker
        if worker is None:
            return
        index = int(getattr(report, "index", 0) or 0)
        changes = len(getattr(report, "changes", []) or [])
        score = getattr(getattr(report, "score", None), "overall", 0.0) or 0.0
        W.note(self._task, f"第 {index} 轮结束，等待确认…")
        answer = QMessageBox.question(
            self, "循环美化 · 每轮确认",
            f"第 {index} 轮完成：{changes} 处修改，当前质量 {float(score) * 100:.0f}%。\n"
            "继续下一轮吗？（点「否」保留当前结果）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        worker.answer_confirm(answer == QMessageBox.StandardButton.Yes)

    def _on_event(self, event) -> None:  # noqa: ANN001
        W.note(self._task, f"[第 {event.round} 轮] {event.message}" if event.round else event.message)
        if event.kind in ("round_end", "done"):
            self._refresh_rounds()

    def _on_done(self, result: object) -> None:
        self._worker = None
        self._start_button.setEnabled(self._ir is not None)
        self._stop_button.setEnabled(False)
        if not isinstance(result, LoopResult):
            W.idle(self._task, "循环美化结束")
            return
        self._result = result
        self._apply_button.setEnabled(True)
        self._export_button.setEnabled(True)
        self._rollback_button.setEnabled(True)
        self._summary.setText(result.summary())
        self._log.set_lines(result.log)
        self._refresh_rounds()
        self._toaster.success(result.summary())
        W.idle(self._task, result.summary())
        if self._auto_apply.isChecked():
            self.irChanged.emit(result.ir)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self._start_button.setEnabled(self._ir is not None)
        self._stop_button.setEnabled(False)
        self._toaster.error(f"循环美化失败：{message}")
        W.idle(self._task, f"循环美化失败：{message}")

    def _refresh_rounds(self) -> None:
        if self._result is None:
            return
        self._rounds_table.setRowCount(len(self._result.rounds))
        for row, report in enumerate(self._result.rounds):
            values = [
                f"第 {report.index} 轮",
                "、".join(TOOL_REGISTRY[name].label if name in TOOL_REGISTRY else name
                          for name in report.plan) or "（无）",
                f"{len(report.tool_calls)} 次",
                f"{report.change_count} 处",
                f"{report.score.overall * 100:.0f}",
                "是" if report.ai_used else "否",
                f"{report.duration_ms / 1000:.1f}s",
            ]
            for column, value in enumerate(values):
                self._rounds_table.setItem(row, column, QTableWidgetItem(value))

    def _show_selected_round(self) -> None:
        if self._result is None:
            return
        rows = sorted({index.row() for index in self._rounds_table.selectedIndexes()})
        if not rows:
            return
        report = self._result.rounds[min(rows[0], len(self._result.rounds) - 1)]
        self._diff.set_diff(report.diff)
        lines = [report.summary, report.score.summary(), ""]
        lines.extend(f"工具：{call.line()}" for call in report.tool_calls)
        lines.extend("")
        lines.extend(f"{change.line()}" for change in report.changes)
        self._log.set_lines(lines)

    def _rollback(self) -> None:
        if self._result is None:
            return
        rows = sorted({index.row() for index in self._rounds_table.selectedIndexes()})
        if not rows:
            self._toaster.info("先在「每轮结果」里选一轮")
            return
        report = self._result.rounds[min(rows[0], len(self._result.rounds) - 1)]
        snapshot = None
        rollback = getattr(self._result, "_rollback", None)
        if callable(rollback):
            snapshot = rollback(self._result, report.index)
        if snapshot is None and report.version_id:
            snapshot = self._library.rollback(report.version_id)
        if snapshot is None:
            self._toaster.error("这一轮没有可用快照")
            return
        self.irChanged.emit(snapshot)
        self.statusMessage.emit(f"已回滚到第 {report.index} 轮（记得点保存写回文件）")
        self._toaster.success(f"已回滚到第 {report.index} 轮")
        W.note(self._task, f"已回滚到第 {report.index} 轮")

    def _apply(self) -> None:
        if self._result is None:
            return
        self.irChanged.emit(self._result.ir)
        self._toaster.success("循环美化结果已送入查看器")
        W.note(self._task, "循环美化结果已应用到当前文档")

    def _export(self) -> None:
        if self._result is None:
            return
        target = self._export_target.currentData() or "docx"
        try:
            written = self._library.export(
                self._result.ir, target, name=f"{self._result.ir.title or '文档'}-美化")
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"导出失败：{error}")
            return
        message = f"已导出 {export_label(target)}：{written.path}"
        self._toaster.success(message)
        W.note(self._task, message)

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self._worker.answer_confirm(False)     # 卡在"等确认"时也要能退出来
            self._worker.wait(3000)

    def change_digest(self) -> str:
        """所有轮次的修改摘要（导出报告用）。"""
        if self._result is None:
            return ""
        lines: list[str] = []
        for report in self._result.rounds:
            lines.append(f"第 {report.index} 轮：{report.summary}｜{report.score.summary()}")
            lines.extend(f"  · {change.line()}" for change in report.changes)
        lines.append(quality_engine.summarize_diff(
            [line for report in self._result.rounds for line in report.diff]))
        return "\n".join(lines)


__all__ = ["LoopPanel", "LoopWorker"]
