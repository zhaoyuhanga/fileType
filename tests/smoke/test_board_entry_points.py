"""冒烟：板块页面的"入口动作"必须真的有反馈（回归用户反馈的"点了没反应"）。

背景：影视搜索页的状态助手依赖一个未创建的属性，导致点「搜索」直接抛
`AttributeError`，界面上完全没反应 —— 之前的测试只直接调 `_on_results()`，
没有覆盖「点按钮」这条真实路径，所以漏掉了。这里补上入口级冒烟。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from modu_workbench.boards.convert.board import ConvertBoardPage
from modu_workbench.boards.video import context as video_ctx
from modu_workbench.boards.video.search import VideoSearchPage
from modu_workbench.core.video import Episode, RemoteVideo
from modu_workbench.ui_kit.toast import Toaster


@pytest.fixture()
def toast_host() -> QWidget:
    host = QWidget()
    host.hide()
    return host


def settle(app: QApplication, predicate, timeout: float = 30.0) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return predicate()


class _FakeSearchWorker:
    """替身搜索线程：立刻回结果，避免测试依赖网络。"""

    instances: list["_FakeSearchWorker"] = []

    def __init__(self, keyword, kind, source_key, limit, page, parent=None, *, library=None):  # noqa: ANN001
        self.finishedResults = _Signal()
        self.failed = _Signal()
        self.finished = _Signal()
        self.started = False
        _FakeSearchWorker.instances.append(self)

    def start(self) -> None:
        self.started = True
        self.finishedResults.emit([_remote()], [])
        self.finished.emit()

    def isRunning(self) -> bool:  # noqa: N802
        return False


class _Signal:
    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot) -> None:  # noqa: ANN001
        self._slots.append(slot)

    def emit(self, *args) -> None:
        for slot in list(self._slots):
            slot(*args)


def _remote() -> RemoteVideo:
    return RemoteVideo(
        source="cms_360", remote_id="1", title="流浪地球", year="2019", kind="movie",
        episodes=[Episode(name="正片", url="https://cdn.example.com/1.m3u8", index=0)],
    )


def test_video_search_button_has_feedback(qapp: QApplication, toast_host: QWidget,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """点「搜索」→ 有状态文字、结果进入表格（此前这里直接抛 AttributeError）。"""
    monkeypatch.setattr("modu_workbench.boards.video.search.SearchWorker", _FakeSearchWorker)
    page = VideoSearchPage(video_ctx.video_library(), video_ctx.video_storage(), Toaster(toast_host))
    try:
        page._keyword.setText("流浪地球")           # noqa: SLF001
        page._start_search()                      # noqa: SLF001
        assert _FakeSearchWorker.instances[-1].started is True
        # 替身线程是同步完成的，因此这里只要求"有状态文字 + 结果进表格"
        assert page._status.text().strip(), "搜索后状态文字不能为空"     # noqa: SLF001
        assert page._table.rowCount() == 1        # noqa: SLF001
        assert page._table.isHidden() is False    # noqa: SLF001  表格已显示（窗口未 show，故用 isHidden）
        assert page._empty.isHidden() is True     # noqa: SLF001  空状态已收起
        assert "1 条结果" in page._status.text()   # noqa: SLF001
    finally:
        page.shutdown()


def test_video_search_empty_result_shows_empty_state(qapp: QApplication, toast_host: QWidget) -> None:
    page = VideoSearchPage(video_ctx.video_library(), video_ctx.video_storage(), Toaster(toast_host))
    try:
        page._on_results([], [])                  # noqa: SLF001
        assert page._empty.isHidden() is False    # noqa: SLF001
        assert page._table.isHidden() is True     # noqa: SLF001
        assert "0 条结果" in page._result_chip.text()   # noqa: SLF001
    finally:
        page.shutdown()


def test_convert_lists_actions_and_finishes_md_conversion(qapp: QApplication,
                                                          tmp_path: Path) -> None:
    """添加 md 文件后必须列出可用动作，且点「开始转换」有结果反馈。"""
    source = tmp_path / "示例.md"
    source.write_text("# 标题\n\n正文。\n\n| A | B |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")

    board = ConvertBoardPage(output_dir=str(tmp_path / "out"))
    try:
        board._append_paths([str(source)])        # noqa: SLF001
        qapp.processEvents()
        assert board._table.rowCount() == 1       # noqa: SLF001
        assert board._table.isHidden() is False   # noqa: SLF001
        actions = board._current_actions          # noqa: SLF001
        assert actions, "md 必须至少有一个可转换动作"
        board._select_action(actions[0])          # noqa: SLF001
        assert board._run_button.isEnabled() is True   # noqa: SLF001

        board._start()                            # noqa: SLF001
        assert settle(qapp, lambda: board._worker is None), "转换线程未结束"    # noqa: SLF001
        status = board._table.item(0, 3).text() if board._table.item(0, 3) else ""   # noqa: SLF001
        assert status, "转换结束后状态列必须有文字（不能点了没反应）"
        outputs = list((tmp_path / "out").glob("*"))
        assert outputs, f"转换没有产出文件；状态列={status}"
    finally:
        board._worker = None                      # noqa: SLF001


def test_convert_actions_for_each_format(qapp: QApplication, tmp_path: Path) -> None:
    """逐个格式检查"有没有可转换动作"，把缺动作的格式暴露出来。"""
    from modu_workbench.core.convert.formats import format_from_extension
    from modu_workbench.core.convert.registry import common_actions

    samples = {
        "txt": "纯文本", "md": "# 标题", "json": '{"a": 1}', "html": "<p>x</p>",
        "csv": "a,b\n1,2\n",
    }
    missing: list[str] = []
    for ext, content in samples.items():
        path = tmp_path / f"sample.{ext}"
        path.write_text(content, encoding="utf-8")
        fmt = format_from_extension(ext)
        assert fmt is not None, f"{ext} 未在格式表里注册"
        if not common_actions([fmt]):
            missing.append(f"{ext}({fmt})")
    assert not missing, f"以下格式没有任何可转换动作：{missing}"
