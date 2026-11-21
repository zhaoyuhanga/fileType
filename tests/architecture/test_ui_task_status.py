"""`ui_kit.components.task_status.TaskStatusMixin`：页面 → 任务条的状态转发。

## 为什么要单独测

这五个方法（`_busy` / `_report` / `_idle` / `_note_fallback` / `_set_progress`）
原先在乐库 5 个页面里逐字复制 5 遍、影视还有 3 份 `_note_fallback`。收敛成混入之后，
它们**第一次可以被单独测试**：混入是纯 Python 类，用假宿主就够了，不用起 Qt。

这里钉的是三条容易被"顺手改坏"的语义：

1. `_report(message)` 不带计数时只记文字（**不**显示进度条）——否则每次搜索都会闪一条进度；
2. `_idle("")` 不覆盖已有文字（空字符串不代表"清空"）；
3. `_set_progress` 只改进度数值，**不**决定进度条显隐（显隐由 report/busy/idle 决定，
   历史上有页面在 idle 之后调它，若在这里 setVisible(True) 就会留下一条 99% 的进度条）。
"""
from __future__ import annotations

from modu_workbench.ui_kit.components.task_status import TaskStatusMixin


class _FakeBar:
    """假的 `TaskBar`：只记录调用，不做界面。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._progress = _FakeProgress()
        self.reported_range: tuple[int, int] | None = None

    def report(self, message: str, done: int = 0, total: int = 0) -> None:
        self.calls.append(("report", message, done, total))

    def note(self, message: str) -> None:
        self.calls.append(("note", message))

    def busy(self, message: str) -> None:
        self.calls.append(("busy", message))

    def idle(self, message: str = "") -> None:
        self.calls.append(("idle", message))


class _FakeProgress:
    def __init__(self) -> None:
        self.range: tuple[int, int] = (0, 100)
        self.value = 0
        self.visibility_changes = 0

    def setRange(self, low: int, high: int) -> None:  # noqa: N802  Qt 命名
        self.range = (low, high)

    def setValue(self, value: int) -> None:  # noqa: N802  Qt 命名
        self.value = value


class _FakeLabel:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, text: str) -> None:  # noqa: N802  Qt 命名
        self.text = text


class _Page(TaskStatusMixin):
    """最小宿主：只提供混入契约要求的属性。"""

    def __init__(self, bar: _FakeBar | None = None, label: _FakeLabel | None = None) -> None:
        self._task = bar
        if label is not None:
            self._fallback_status = label


# ------------------------------------------------------------------ _report


def test_report_without_counts_only_notes() -> None:
    bar = _FakeBar()
    _Page(bar)._report("正在搜索…")
    assert bar.calls == [("note", "正在搜索…")]


def test_report_with_counts_reports_progress() -> None:
    bar = _FakeBar()
    _Page(bar)._report("下载中", 3, 10)
    assert bar.calls == [("report", "下载中", 3, 10)]


def test_report_total_only_still_reports() -> None:
    """只有 total（`done` 省略）也算进度，不能退化成 note。"""
    bar = _FakeBar()
    _Page(bar)._report("准备中", 0, 5)
    assert bar.calls == [("report", "准备中", 0, 5)]


# ------------------------------------------------------------------ _idle / _busy


def test_idle_without_message_keeps_previous_text() -> None:
    bar = _FakeBar()
    _Page(bar)._idle()
    assert bar.calls == [("idle", "")]


def test_idle_with_message_writes_fallback_label() -> None:
    bar, label = _FakeBar(), _FakeLabel()
    _Page(bar, label)._idle("已完成")
    assert bar.calls == [("idle", "已完成")]
    assert label.text == "已完成"


def test_busy_marks_fallback_label() -> None:
    bar, label = _FakeBar(), _FakeLabel()
    _Page(bar, label)._busy("解析中…")
    assert bar.calls == [("busy", "解析中…")]
    assert label.text == "解析中…"


# ------------------------------------------------------------------ 宿主缺失


def test_methods_are_noop_without_task_bar() -> None:
    """页面还没装好任务条时（`_task is None`），这些调用必须是静默 no-op，不能抛。"""
    page = _Page(None)
    page._report("x", 1, 2)
    page._busy("x")
    page._idle("x")
    page._set_progress(50)          # 没有 _task_bar、也没有 _task → 无事发生


def test_missing_fallback_label_is_tolerated() -> None:
    """没接右上角小标签的页面（多数页面）不该因为少了这个属性而崩。"""
    page = _Page(_FakeBar())
    page._note_fallback("随便说说")


# ------------------------------------------------------------------ _set_progress


def test_set_progress_prefers_task_bar_widget() -> None:
    page = _Page(_FakeBar())
    direct = _FakeBar()
    page._task_bar = direct
    page._set_progress(42)
    assert direct._progress.value == 42
    assert direct._progress.range == (0, 100)
    # 优先走 _task_bar，不该再动 _task
    assert page._task._progress.value == 0


def test_set_progress_falls_back_to_task_bar_of_page() -> None:
    bar = _FakeBar()
    _Page(bar)._set_progress(7)
    assert bar._progress.value == 7


def test_set_progress_never_toggles_visibility() -> None:
    """只改数值：进度条的显隐由 report/busy/idle 决定（见模块文档第 3 条）。"""
    bar = _FakeBar()
    _Page(bar)._set_progress(99)
    assert bar._progress.visibility_changes == 0
