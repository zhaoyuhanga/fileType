"""底部任务条：状态文字 + 进度 + 取消（空闲时自动收起进度与取消）。

为什么需要它：各板块此前各自在页面里塞「状态标签 + 进度条 + 取消按钮」，
于是同一个屏幕上出现两条状态栏、进度条常驻显示（0%）、取消按钮永远可点 —— 这正是
v1.0.0 要修掉的"冗余布局"。

约定：
- 状态文字常驻（用户随时知道发生了什么），进度与取消只在任务进行中出现；
- 任务结束调用 `idle("已完成…")` 收起进度条。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

from ..tokens import ROW_GAP, SPACE

__all__ = ["TaskBar"]


class TaskBar(QWidget):
    """统一任务条（放在页面/板块底部，所有子页共用一条）。"""

    cancelled = Signal()

    def __init__(self, text: str = "就绪", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("bottomBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"])
        layout.setSpacing(ROW_GAP)

        self._label = QLabel(text)
        self._label.setObjectName("readerStatus")
        self._label.setWordWrap(True)
        layout.addWidget(self._label, 1)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedWidth(220)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._cancel = QPushButton("取消")
        self._cancel.setObjectName("dangerButton")
        self._cancel.setVisible(False)
        self._cancel.clicked.connect(self.cancelled)
        layout.addWidget(self._cancel)

    # ---------- 对外 API ----------

    def report(self, message: str, done: int = 0, total: int = 0) -> None:
        """上报进度：total > 0 显示百分比，否则显示不确定态（忙碌）。"""
        self._label.setText(message)
        if total > 0:
            self._progress.setRange(0, 100)
            self._progress.setValue(int(done * 100 / total))
        else:
            self._progress.setRange(0, 0)      # 不确定进度（搜索/解析这类未知时长）
        self._progress.setVisible(True)
        self._cancel.setVisible(True)

    def idle(self, message: str = "") -> None:
        """任务结束：收起进度条与取消按钮。"""
        if message:
            self._label.setText(message)
        self._progress.setVisible(False)
        self._cancel.setVisible(False)

    def set_text(self, message: str) -> None:
        self._label.setText(message)

    def set_cancel_enabled(self, enabled: bool) -> None:
        self._cancel.setEnabled(enabled)

    @property
    def text(self) -> str:
        return self._label.text()

    @property
    def is_busy(self) -> bool:
        return self._progress.isVisible()
