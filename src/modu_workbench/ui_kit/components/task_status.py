"""页面 → 任务条的状态转发胶水（`TaskStatusMixin`）。

## 为什么需要它

乐库/影视的每个子页（搜索 / 详情 / 曲库 / 歌单 / 历史 / 转换…）都有一条底部任务条
（`ui_kit.components.taskbar.TaskBar`），页面里的 `_busy` / `_report` / `_idle` /
`_set_progress` 就是把"我这边在干什么"转发给它的四个薄方法。

这五个方法曾在乐库的 5 个页面里逐字复制 5 遍（影视还有 3 份 `_note_fallback`）。
复制带来的真实成本不是行数，而是**改一处忘一处**：修"未关注状态时不该丢提示"这类
问题时，只改其中一个页面，用户就会在另一个页面看到"点了没反应"。

## 宿主页面的契约

使用本混入的页面需要提供：

| 属性 | 必需 | 说明 |
|---|---|---|
| `_task` | 是 | 该页绑定的 `TaskBar`（可能为 `None`：页面还没装配好） |
| `_task_bar` | 否 | 另一处任务条引用；存在时 `_set_progress` 优先用它 |
| `_fallback_status` | 否 | 右上角小状态标签；进度/忙碌时同步一行文字，避免"只有底部在动" |

## 为什么不直接继承 `TaskBar`

`TaskBar` 是**控件**（有自己的布局与信号），而这里是**页面侧的行为**。
混入是纯 Python 类、不依赖 Qt，因此可以在无 Qt 的单元测试里用一个假宿主验证；
页面仍各自继承自己的 Qt 基类（`QWidget`），MRO 不受影响。
"""
from __future__ import annotations

__all__ = ["TaskStatusMixin"]


class TaskStatusMixin:
    """把页面的任务状态转发给 `TaskBar`（宿主要求见模块文档）。"""

    #: 宿主页面在用本混入时会赋值；这里只做类型提示，不初始化（避免覆盖宿主的装配结果）
    _task: object
    _task_bar: object
    _fallback_status: object

    def _note_fallback(self, message: str) -> None:
        """把状态写进右上角小标签（未接该标签时是静默，不算错误）。"""
        label = getattr(self, "_fallback_status", None)
        if label is not None:
            label.setText(message)  # type: ignore[attr-defined]

    def _report(self, message: str, done: int = 0, total: int = 0) -> None:
        """上报进度：给了 done/total 走进度，否则只记一条文字提示。"""
        self._note_fallback(message)
        if self._task is not None:
            if done or total:
                self._task.report(message, done, total)  # type: ignore[attr-defined]
            else:
                self._task.note(message)  # type: ignore[attr-defined]

    def _busy(self, message: str) -> None:
        """进入"进行中"（业务未知时长时用不确定进度）。"""
        self._note_fallback(message)
        if self._task is not None:
            self._task.busy(message)  # type: ignore[attr-defined]

    def _idle(self, message: str = "") -> None:
        """任务结束：有文字才更新，随后收起进度与取消。"""
        if message:
            self._note_fallback(message)
        if self._task is not None:
            self._task.idle(message)  # type: ignore[attr-defined]

    def _set_progress(self, value: int) -> None:
        """兼容旧写法：只更新进度数值；是否显示进度条由 report/busy/idle 决定。"""
        bar = (
            self._task_bar._progress  # type: ignore[attr-defined]
            if hasattr(self, "_task_bar")
            else (self._task._progress if self._task is not None else None)  # type: ignore[attr-defined]
        )
        if bar is not None:
            bar.setRange(0, 100)
            bar.setValue(int(value))
