"""墨读转换板块 UI 冒烟测试（offscreen）：导入/动作/批量执行/文档查看器。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from modu_workbench.boards.convert_board import ConvertBoardPage
from modu_workbench.boards.doc_viewer import DocViewerDialog


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def out_dir(tmp_path: Path) -> Path:
    path = tmp_path / "out"
    path.mkdir()
    return path


def _sample_txt(tmp_path: Path) -> Path:
    path = tmp_path / "笔记.txt"
    path.write_text("第一行内容。\n第二行内容。", encoding="utf-8")
    return path


def test_page_imports_rows_and_lists_actions(qapp: QApplication, out_dir: Path, tmp_path: Path) -> None:
    page = ConvertBoardPage(output_dir=str(out_dir))
    try:
        page._append_paths([str(_sample_txt(tmp_path))])  # noqa: SLF001
        assert len(page._rows) == 1  # noqa: SLF001
        assert page._rows[0]["format"] == "txt"  # noqa: SLF001
        assert len(page._current_actions) >= 3  # noqa: SLF001
        assert page._run_button.isEnabled()  # noqa: SLF001
    finally:
        page.close()


def test_page_batch_run_async(qapp: QApplication, out_dir: Path, tmp_path: Path) -> None:
    page = ConvertBoardPage(output_dir=str(out_dir))
    try:
        sample = _sample_txt(tmp_path)
        page._append_paths([str(sample)])  # noqa: SLF001

        action = next(a for a in page._current_actions if a.target_format == "html")  # noqa: SLF001
        page._selected_action = action  # noqa: SLF001

        page._run_button.click()  # noqa: SLF001
        assert page._worker is not None  # noqa: SLF001

        loop = QEventLoop()
        page._worker.finished.connect(loop.quit)  # noqa: SLF001
        loop.exec()

        assert page._worker is None  # noqa: SLF001
        assert (out_dir / "笔记.html").exists()
        assert page._rows[0]["status"] == "成功"  # noqa: SLF001
    finally:
        page.close()


def test_doc_viewer_txt_and_json(qapp: QApplication, tmp_path: Path) -> None:
    txt = tmp_path / "readme.txt"
    txt.write_text("你好", encoding="utf-8")
    dialog = DocViewerDialog(str(txt))
    dialog.show()
    try:
        assert dialog._editor.toPlainText() == "你好"  # noqa: SLF001
        # 进入编辑
        dialog._toggle_mode()  # noqa: SLF001
        assert dialog._editor.isVisible()  # noqa: SLF001
    finally:
        dialog.close()

    json_file = tmp_path / "conf.json"
    json_file.write_text('{"a":1}', encoding="utf-8")
    dialog_json = DocViewerDialog(str(json_file))
    dialog_json.show()
    try:
        assert dialog_json._format_btn.isVisible()  # noqa: SLF001
    finally:
        dialog_json.close()
