"""墨软文档板块的通用控件与后台线程。

约定（与其它板块一致）：
- 所有耗时操作（解析、美化、合并、循环美化、导出）都走 `DocumentWorker`（QThread），
  支持取消、进度与错误回报，界面永不阻塞；
- 页面通过模块级函数 `busy/note/idle/report` 操作底部统一任务条
  （不用 `self._xxx` 助手，避免"调用未定义助手"的低级错误）；
- 变更与差异统一用 `ChangeTable` / `DiffBrowser` 展示，保证"每一处修改都能看见"。
"""
from __future__ import annotations

import html as html_mod
import threading
from typing import Any, Callable, Optional, Sequence

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.document import Change, DiffLine
from modu_workbench.ui_kit.components import TaskBar

# ---------------------------------------------------------------- 任务条助手


def report(task_bar: Optional[TaskBar], message: str, done: int = 0, total: int = 0) -> None:
    if task_bar is None:
        return
    if done or total:
        task_bar.report(message, done, total)
    else:
        task_bar.note(message)


def busy(task_bar: Optional[TaskBar], message: str) -> None:
    if task_bar is not None:
        task_bar.busy(message)


def note(task_bar: Optional[TaskBar], message: str) -> None:
    if task_bar is not None:
        task_bar.note(message)


def idle(task_bar: Optional[TaskBar], message: str = "") -> None:
    if task_bar is not None:
        task_bar.idle(message)


def set_progress(task_bar: Optional[TaskBar], value: int) -> None:
    if task_bar is None:
        return
    bar = getattr(task_bar, "_progress", None)     # noqa: SLF001
    if bar is not None:
        bar.setRange(0, 100)
        bar.setValue(max(0, min(100, int(value))))


# ---------------------------------------------------------------- 后台线程


class DocumentWorker(QThread):
    """通用后台线程：跑一个可取消的 callable，回报进度与结果。

    `job(worker)` 里可以调用 `worker.report(...)` / `worker.emit_message(...)`
    以及检查 `worker.cancelled`。
    """

    progress = Signal(int, int)          # done, total
    message = Signal(str)
    done = Signal(object)                # 返回值
    failed = Signal(str)

    def __init__(self, job: Callable[["DocumentWorker"], Any], *, label: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._job = job
        self.label = label
        self.cancel = threading.Event()

    # ---------- 给任务用的 API ----------

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()

    def report(self, done: int, total: int, message: str = "") -> None:
        if message:
            self.message.emit(message)
        self.progress.emit(int(done), int(total))

    def emit_message(self, message: str) -> None:
        self.message.emit(message)

    def request_cancel(self) -> None:
        self.cancel.set()
        self.message.emit("正在停止…")

    # ---------- 线程体 ----------

    def run(self) -> None:  # noqa: D102
        try:
            value = self._job(self)
        except Exception as error:  # noqa: BLE001  任何异常都要回报给界面
            self.failed.emit(str(error) or error.__class__.__name__)
            return
        self.done.emit(value)


# ---------------------------------------------------------------- 变更表


class ChangeTable(QTableWidget):
    """修改明细表：类型 / 位置 / 说明 / 来源（AI 或本地）。"""

    HEADERS = ("类型", "位置", "说明", "来源")

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(0, len(self.HEADERS), parent)
        self.setHorizontalHeaderLabels(list(self.HEADERS))
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.setColumnWidth(1, 220)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setDefaultSectionSize(26)

    def set_changes(self, changes: Sequence[Change]) -> None:
        self.setRowCount(len(changes))
        for row, change in enumerate(changes):
            self.setItem(row, 0, QTableWidgetItem(change.kind))
            self.setItem(row, 1, QTableWidgetItem(change.location))
            detail = QTableWidgetItem(change.detail)
            if change.before or change.after:
                detail.setToolTip(f"修改前：{change.before}\n修改后：{change.after}")
            self.setItem(row, 2, detail)
            source = QTableWidgetItem("AI" if change.ai_generated else "本地")
            source.setForeground(QColor("#1452C4" if change.ai_generated else "#5a6478"))
            self.setItem(row, 3, source)


# ---------------------------------------------------------------- 差异视图


class DiffBrowser(QTextBrowser):
    """逐条差异（+ 新增 / − 删除 / ~ 修改）。"""

    COLORS = {
        "same": "#6b7590",
        "added": "#1c7c3c",
        "removed": "#b3261e",
        "changed": "#8a5a00",
    }

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)

    def set_diff(self, lines: Sequence[DiffLine], *, limit: int = 400) -> None:
        if not lines:
            self.setHtml("<p style='color:#6b7590'>暂无差异：两次结果完全一致。</p>")
            return
        rows: list[str] = []
        for line in lines[:limit]:
            color = self.COLORS.get(line.kind, "#1f2430")
            prefix = {"same": " ", "added": "+", "removed": "-", "changed": "~"}.get(line.kind, " ")
            text = line.render()
            rows.append(
                f"<div style='color:{color};white-space:pre-wrap'>{prefix} "
                f"{html_mod.escape(text)}</div>")
        if len(lines) > limit:
            rows.append(f"<div style='color:#6b7590'>…… 其余 {len(lines) - limit} 条已省略</div>")
        self.setHtml("".join(rows))


class ReportBrowser(QTextBrowser):
    """合并报告 / 循环日志（等宽展示，方便对照）。"""

    def set_lines(self, lines: Sequence[str]) -> None:
        if not lines:
            self.setHtml("<p style='color:#6b7590'>暂无内容。</p>")
            return
        self.setHtml("".join(
            f"<div style='white-space:pre-wrap'>{html_mod.escape(str(line))}</div>"
            for line in lines))


# ---------------------------------------------------------------- 版本历史


class VersionDialog(QDialog):
    """版本历史：查看快照并回滚。"""

    HEADERS = ("ID", "时间", "类型", "标签", "说明")

    def __init__(self, versions: Sequence[dict], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("版本历史")
        self.resize(720, 420)
        self._selected = 0

        layout = QVBoxLayout(self)
        hint = QLabel("每次打开、美化、合并与循环美化都会留一份快照；回滚只改当前编辑内容，"
                      "不会动磁盘上的原文件（需要时再点保存）。")
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._table = QTableWidget(len(versions), len(self.HEADERS))
        self._table.setHorizontalHeaderLabels(list(self.HEADERS))
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(3, 180)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for row, record in enumerate(versions):
            import time as _time

            created = _time.strftime("%Y-%m-%d %H:%M:%S",
                                     _time.localtime(float(record.get("created_at") or 0)))
            values = [str(record.get("id") or ""), created, str(record.get("kind") or ""),
                      str(record.get("label") or ""), str(record.get("note") or "")]
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(self._table, 1)

        buttons = QDialogButtonBox()
        self._restore = QPushButton("回滚到选中版本")
        self._restore.setObjectName("primaryButton")
        self._restore.setEnabled(bool(versions))
        self._restore.clicked.connect(self._accept)
        buttons.addButton(self._restore, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("关闭", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        self._table.doubleClicked.connect(lambda *_: self._accept())
        layout.addWidget(buttons)

    def _accept(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 0)
        self._selected = int(item.text()) if item else 0
        if self._selected:
            self.accept()

    @property
    def selected_version(self) -> int:
        return self._selected


def ask_text(parent: Optional[QWidget], title: str, label: str, default: str = "") -> str:
    """单行文本输入（返回空串表示取消）。"""
    text, ok = QInputDialog.getText(parent, title, label, text=default)
    return text.strip() if ok else ""


def file_row(parent: Optional[QWidget], label: str):  # noqa: ANN201
    """「标签 + 路径输入框 + 选择按钮」一行（合并页与计算页共用）。"""
    from PySide6.QtWidgets import QFileDialog, QLineEdit

    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(QLabel(label))
    field = QLineEdit()
    field.setPlaceholderText("拖入或选择一个文件")
    layout.addWidget(field, 1)
    button = QPushButton("选择…")
    layout.addWidget(button)

    def pick() -> None:
        path, _ = QFileDialog.getOpenFileName(parent, f"选择{label}", field.text())
        if path:
            field.setText(path)

    button.clicked.connect(pick)
    return holder, field


__all__ = [
    "ChangeTable",
    "DiffBrowser",
    "DocumentWorker",
    "ReportBrowser",
    "VersionDialog",
    "ask_text",
    "busy",
    "file_row",
    "idle",
    "note",
    "report",
    "set_progress",
]
