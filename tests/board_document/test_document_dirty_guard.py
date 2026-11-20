"""文档板块「未保存」保护：只读操作不清脏标记、关标签先问保存。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from modu_workbench.boards.document.board import DocumentBoardPage
from modu_workbench.core.document import DocumentLibrary, DocumentStorage

MD = "# 年度报告\n\n正文内容。\n"


@pytest.fixture()
def board(qapp: QApplication, tmp_path: Path) -> DocumentBoardPage:
    library = DocumentLibrary(DocumentStorage(tmp_path / "modu.db"),
                              output_dir=tmp_path / "out")
    page = DocumentBoardPage(library=library)
    page.resize(1100, 760)
    page.show()
    qapp.processEvents()
    try:
        yield page
    finally:
        page.shutdown()
        page.close()
        page.setParent(None)
        page.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


@pytest.fixture()
def sample(tmp_path: Path) -> Path:
    path = tmp_path / "报告.md"
    path.write_text(MD, encoding="utf-8")
    return path


def _open(board: DocumentBoardPage, qapp: QApplication, path: Path) -> None:
    board._add_paths([str(path)])  # noqa: SLF001
    qapp.processEvents()
    board._open_path(str(path))  # noqa: SLF001
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        qapp.processEvents()
        if board._worker is None:  # noqa: SLF001
            return
        time.sleep(0.02)
    raise AssertionError("解析线程未结束")


def _type(board: DocumentBoardPage, text: str) -> None:
    viewer = board._viewer  # noqa: SLF001
    viewer._edit_button.setChecked(True)  # noqa: SLF001
    viewer._toggle_edit()  # noqa: SLF001
    viewer._editor.setPlainText(text)  # noqa: SLF001


def test_read_only_actions_do_not_clear_dirty(
    board: DocumentBoardPage, qapp: QApplication, sample: Path
) -> None:
    """查找/体检/版本历史读内容时不能顺手把「未保存」清掉（否则编辑静默丢失）。"""
    _open(board, qapp, sample)
    _type(board, "# 改过的标题\n\n改过的正文。")
    viewer = board._viewer  # noqa: SLF001
    assert viewer.is_dirty()

    viewer.current_ir()  # 查找/体检/版本历史都走这里
    assert viewer.is_dirty(), "读一次内容就把脏标记清掉，自动保存与关闭确认都会失效"

    viewer._find_input.setText("改过")  # noqa: SLF001
    viewer._find_next()  # noqa: SLF001
    assert viewer.is_dirty()
    assert sample.read_text(encoding="utf-8") == MD, "只读操作不应写盘"


def test_close_tab_asks_before_discarding_edits(
    board: DocumentBoardPage, qapp: QApplication, sample: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open(board, qapp, sample)
    _type(board, "# 未保存\n\n正文。")
    assert board._doc_tabs.count() == 1  # noqa: SLF001

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )
    board._request_close_tab(0)  # noqa: SLF001
    assert board._doc_tabs.count() == 1, "选「取消」时标签必须保留"  # noqa: SLF001

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Save,
    )
    board._request_close_tab(0)  # noqa: SLF001
    assert board._doc_tabs.count() == 0  # noqa: SLF001
    assert "未保存" in sample.read_text(encoding="utf-8"), "选「保存」应先写盘再关标签"
