"""墨软转换板块 UI 冒烟测试（offscreen）：导入/动作/批量执行/文档查看器。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from modu_workbench.boards.convert.board import ConvertBoardPage
from modu_workbench.boards.convert.doc_viewer import DocViewerDialog


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


def test_output_column_shows_file_name_with_full_path_tooltip(
    qapp: QApplication, out_dir: Path, tmp_path: Path
) -> None:
    """反馈：文件名与「详情/输出」挤在一起看不清 → 输出列只放文件名，完整路径进 tooltip。"""
    page = ConvertBoardPage(output_dir=str(out_dir))
    try:
        sample = _sample_txt(tmp_path)
        page._append_paths([str(sample)])  # noqa: SLF001
        action = next(a for a in page._current_actions if a.target_format == "html")  # noqa: SLF001
        page._selected_action = action  # noqa: SLF001
        page._run_button.click()  # noqa: SLF001
        loop = QEventLoop()
        page._worker.finished.connect(loop.quit)  # noqa: SLF001
        loop.exec()

        name_cell = page._table.item(0, 1)  # noqa: SLF001
        output_cell = page._table.item(0, 4)  # noqa: SLF001
        assert name_cell.text() == sample.name
        assert name_cell.toolTip() == str(sample), "文件名列悬停应给出完整路径"
        assert output_cell.text() == "笔记.html", "输出列只显示文件名，避免长路径挤成一团"
        assert output_cell.toolTip() == str(out_dir / "笔记.html")
    finally:
        page.close()


def test_output_column_keeps_failure_reason_verbatim(qapp: QApplication, out_dir: Path) -> None:
    """失败原因不是路径时要原样显示（不能被当成路径截断）。"""
    page = ConvertBoardPage(output_dir=str(out_dir))
    try:
        assert page._short_detail("「PDF 转 TXT」不适用于此格式") == "「PDF 转 TXT」不适用于此格式"  # noqa: SLF001
        assert page._short_detail(r"C:\out\报告.pdf") == "报告.pdf"  # noqa: SLF001
        assert page._short_detail("") == ""  # noqa: SLF001
    finally:
        page.close()


def test_default_output_dir_follows_documents_location(qapp: QApplication) -> None:
    """默认输出目录必须落在系统「文档」下（OneDrive 重定向时也要对）。

    中文 Windows 上「文档」常被重定向到 OneDrive，写死 `~/Documents` 会新建一个
    用户不会去看的目录 —— 这正是「转换成功但输出目录没数据」的头号原因。
    """
    from PySide6.QtCore import QStandardPaths

    from modu_workbench.boards.convert.board import default_output_dir

    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    assert documents, "系统应能给出「文档」目录"
    result = default_output_dir()
    assert result == Path(documents) / "墨软转换输出"
    assert result.is_dir()


def test_doc_viewer_txt_and_json(qapp: QApplication, tmp_path: Path) -> None:
    txt = tmp_path / "readme.txt"
    txt.write_text("你好", encoding="utf-8")
    dialog = DocViewerDialog(str(txt))
    dialog.show()
    try:
        assert dialog._editor.toPlainText() == "你好"  # noqa: SLF001
        # 进入编辑
        dialog._set_mode("edit")  # noqa: SLF001
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
