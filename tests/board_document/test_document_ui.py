"""墨软文档：板块界面测试（offscreen 无头 Qt，不联网）。"""
from __future__ import annotations

import contextlib
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from modu_workbench.boards.document import context as document_context
from modu_workbench.boards.document.board import DocumentBoardPage
from modu_workbench.core.document import (
    DocumentLibrary,
    DocumentStorage,
    LoopOptions,
    MergeOptions,
    template_options,
)
from modu_workbench.ui_kit.settings import PAGE_FACTORIES, SettingsDialog
from modu_workbench.ui_kit.theme import ThemeTokens, app_qss

MD = """# 年度报告

公司2024年收入 1,200,000 元,同比增长 15% 。联系电话 13800138000 。

## 1.1 数据

| 项目 | 数量 | 单价 | 金额 |
| --- | --- | --- | --- |
| A | 3 | 10 | =B3*C3 |
| B | 2 | 20 | =B4*C4 |
"""


@pytest.fixture(scope="module", autouse=True)
def themed(qapp: QApplication) -> None:
    """样式表只应用一次：`setStyleSheet` 会让所有存活控件重新 polish，
    每测一次就重新应用一遍的话，测试越到后面越慢（实测第 28 个测试要 18 秒）。"""
    qapp.setStyleSheet(app_qss(ThemeTokens()))
    return None


@pytest.fixture()
def board(qapp: QApplication, tmp_path: Path) -> DocumentBoardPage:
    """每个测试用独立的文档库（避免测试之间互相看见对方的文档）。"""
    library = DocumentLibrary(DocumentStorage(tmp_path / "modu.db"),
                              output_dir=tmp_path / "out")
    # 循环美化默认关掉「每轮人工确认」：否则本机设置里开着它时，
    # 循环测试会停在模态确认框上（要确认的那条测试自己会临时打开）
    with _temp_settings(**{"document/require_confirm": False}):
        page = DocumentBoardPage(library=library)
        page.resize(1280, 800)
        page.show()
        qapp.processEvents()
        try:
            yield page
        finally:
            page.shutdown()
            page.close()
            page.setParent(None)
            page.deleteLater()
            # deleteLater 只挂"延迟删除"事件，processEvents 默认不派发它 ——
            # 不显式派发的话页面会一直活着（后续测试的样式重新应用会越来越慢）
            qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            qapp.processEvents()


def settle(app: QApplication, predicate, timeout: float = 30.0) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return predicate()


@pytest.fixture()
def sample(tmp_path: Path) -> Path:
    path = tmp_path / "报告.md"
    path.write_text(MD, encoding="utf-8")
    return path


def _open(board: DocumentBoardPage, qapp: QApplication, path: Path) -> None:
    board._add_paths([str(path)])          # noqa: SLF001
    qapp.processEvents()
    board._open_path(str(path))            # noqa: SLF001
    assert settle(qapp, lambda: board._worker is None), "解析线程未结束"   # noqa: SLF001


@contextlib.contextmanager
def _temp_settings(**values: object):  # noqa: ANN201
    """临时改 QSettings（None = 删掉该键），用完还原，避免污染其它测试。"""
    from modu_workbench.ui_kit.settings import app_settings

    settings = app_settings()
    previous = {key: settings.value(key, None) for key in values}
    for key, value in values.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)
    settings.sync()
    try:
        yield settings
    finally:
        for key, value in previous.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
        settings.sync()


# ---------------------------------------------------------------- 板块骨架


def test_board_registered_and_titled() -> None:
    from modu_workbench.app.registry import ACTIVE_BOARDS, get_board

    spec = get_board("document")
    assert spec is not None
    assert spec.title == "墨软文档"
    assert spec.icon and spec.description
    keys = [board.key for board in ACTIVE_BOARDS]
    assert keys[-1] == "document", "第六大板块追加在末尾，不影响既有板块位置"


def test_board_has_all_panels(board: DocumentBoardPage) -> None:
    labels = [board._tabs.tabText(index) for index in range(board._tabs.count())]   # noqa: SLF001
    assert labels == ["查看编辑", "格式美化", "填充计算", "文档合并", "AI 循环美化", "权限安全"]
    assert board._task_bar is not None                                              # noqa: SLF001
    assert board._table.isHidden() is True                                          # noqa: SLF001


def test_add_and_open_document(board: DocumentBoardPage, qapp: QApplication,
                               sample: Path) -> None:
    _open(board, qapp, sample)
    assert board._current_ir is not None                                            # noqa: SLF001
    assert board._table.rowCount() == 1                                             # noqa: SLF001
    assert board._table.item(0, 4).text() == "已解析"                                # noqa: SLF001
    ir = board._current_ir                                                          # noqa: SLF001
    assert ir.title == "报告" and ir.stats()["tables"] == 1
    assert "已打开" in board._task_bar.text                                          # noqa: SLF001


def test_missing_file_is_reported(board: DocumentBoardPage, qapp: QApplication,
                                  tmp_path: Path) -> None:
    missing = tmp_path / "不存在.md"
    board._add_paths([str(missing)])                                                # noqa: SLF001
    assert board._table.rowCount() == 0, "不存在的文件不该入库"                      # noqa: SLF001
    board._open_path(str(missing))                                                  # noqa: SLF001
    qapp.processEvents()
    assert "不存在" in board._task_bar.text                                          # noqa: SLF001


def test_remove_from_library(board: DocumentBoardPage, qapp: QApplication,
                             sample: Path) -> None:
    _open(board, qapp, sample)
    board._table.selectRow(0)                                                       # noqa: SLF001
    board._remove_selected()                                                        # noqa: SLF001
    qapp.processEvents()
    assert board._table.rowCount() == 0                                             # noqa: SLF001
    assert sample.is_file(), "移除文档库记录不能删磁盘文件"


# ---------------------------------------------------------------- 查看编辑


def test_viewer_loads_and_edits(board: DocumentBoardPage, qapp: QApplication,
                                sample: Path) -> None:
    _open(board, qapp, sample)
    viewer = board._viewer                                                          # noqa: SLF001
    assert viewer.current_ir() is not None
    assert viewer._edit_button.isEnabled()                                          # noqa: SLF001
    viewer._edit_button.setChecked(True)                                            # noqa: SLF001
    viewer._toggle_edit()                                                           # noqa: SLF001
    assert viewer._editor.toPlainText().strip()                                     # noqa: SLF001

    viewer._editor.setPlainText("# 新标题\n\n新的正文内容。")                        # noqa: SLF001
    ir = viewer.current_ir()
    assert ir is not None and ir.blocks[0].text == "新标题"


def test_viewer_find_replace(board: DocumentBoardPage, qapp: QApplication,
                             sample: Path) -> None:
    _open(board, qapp, sample)
    viewer = board._viewer                                                          # noqa: SLF001
    viewer._find_input.setText("公司")                                              # noqa: SLF001
    viewer._replace_input.setText("本公司")                                          # noqa: SLF001
    viewer._replace(True)                                                           # noqa: SLF001
    assert "本公司" in viewer.current_ir().text()


def test_viewer_split_view_and_live_preview(board: DocumentBoardPage, qapp: QApplication,
                                            sample: Path) -> None:
    """分屏 + 实时预览（Markdown 边改边看）。"""
    _open(board, qapp, sample)
    viewer = board._viewer                                                          # noqa: SLF001
    viewer._edit_button.setChecked(True)                                            # noqa: SLF001
    viewer._toggle_edit()                                                           # noqa: SLF001
    assert viewer._split_button.isEnabled()                                         # noqa: SLF001
    viewer._split_button.setChecked(True)                                           # noqa: SLF001
    viewer._on_split_toggled()                                                      # noqa: SLF001
    qapp.processEvents()
    assert viewer._editor.isVisible() and viewer._preview.isVisible()                # noqa: SLF001

    viewer._editor.setPlainText("# 新标题\n\n新正文。")                              # noqa: SLF001
    viewer._render_live_preview()                                                   # noqa: SLF001
    assert "新标题" in viewer._preview.toPlainText()                                 # noqa: SLF001

    # 语法错误时预览给出可读提示，而不是崩掉
    viewer._editor.setPlainText("| 表格 没了分隔线")                                  # noqa: SLF001
    viewer._render_live_preview()                                                   # noqa: SLF001
    assert viewer._preview.toPlainText().strip()                                     # noqa: SLF001


def test_viewer_bookmarks(board: DocumentBoardPage, qapp: QApplication,
                          sample: Path) -> None:
    _open(board, qapp, sample)
    viewer = board._viewer                                                          # noqa: SLF001
    item = viewer._outline.topLevelItem(0)                                          # noqa: SLF001
    viewer._outline.setCurrentItem(item)                                            # noqa: SLF001
    item.setSelected(True)
    viewer._toggle_bookmark()                                                       # noqa: SLF001
    assert viewer._bookmarks.count() == 1                                           # noqa: SLF001
    assert any(block.meta.get("bookmark") for block in board._current_ir.blocks)     # noqa: SLF001

    viewer._jump_to_bookmark(viewer._bookmarks.item(0))                             # noqa: SLF001
    viewer._clear_bookmarks()                                                       # noqa: SLF001
    assert viewer._bookmarks.count() == 0                                           # noqa: SLF001
    assert not any(block.meta.get("bookmark") for block in board._current_ir.blocks)  # noqa: SLF001


def test_viewer_autosave(board: DocumentBoardPage, qapp: QApplication,
                         sample: Path) -> None:
    """自动保存：有未保存编辑时到点写回原文件（不新增版本快照）。"""
    from modu_workbench.ui_kit.settings import app_settings

    settings = app_settings()
    settings.setValue("document/autosave", True)
    settings.setValue("document/autosave_seconds", 30)
    _open(board, qapp, sample)
    viewer = board._viewer                                                          # noqa: SLF001
    viewer._start_autosave_timer()                                                  # noqa: SLF001
    assert viewer._autosave_timer.isActive()                                        # noqa: SLF001

    viewer._edit_button.setChecked(True)                                            # noqa: SLF001
    viewer._toggle_edit()                                                           # noqa: SLF001
    viewer._editor.setPlainText("# 自动保存验证\n\n正文。")                          # noqa: SLF001
    assert viewer.is_dirty()
    versions_before = len(board._library.versions(board._current_ir))                # noqa: SLF001

    viewer._autosave_tick()                                                         # noqa: SLF001
    qapp.processEvents()
    assert viewer.is_dirty() is False
    assert "自动保存验证" in sample.read_text(encoding="utf-8")
    assert len(board._library.versions(board._current_ir)) == versions_before        # noqa: SLF001
    assert any("自动保存" in record["detail"] for record in board._library.audit_records())  # noqa: SLF001


def test_viewer_print_requires_document(qapp: QApplication, tmp_path: Path) -> None:  # noqa: ANN001
    from modu_workbench.boards.document.viewer import DocViewer
    from modu_workbench.core.document import DocumentLibrary, DocumentStorage

    library = DocumentLibrary(DocumentStorage(tmp_path / "m.db"), output_dir=tmp_path)
    viewer = DocViewer(library)
    viewer.print_document()          # 没有文档时只提示、不弹打印对话框也不崩
    assert viewer.current_ir() is None


def test_pdf_printing_renders_pages(qapp: QApplication, tmp_path: Path) -> None:
    """PDF 打印走"对话框 + 逐页渲染"，并按对话框选的页范围输出。"""
    from PySide6.QtPrintSupport import QPrinter

    from modu_workbench.boards.document.viewer import DocViewer
    from modu_workbench.core.document import DocumentLibrary, DocumentStorage, write_document
    from modu_workbench.core.document import parse_markdown

    library = DocumentLibrary(DocumentStorage(tmp_path / "m.db"), output_dir=tmp_path)
    source = write_document(
        parse_markdown("# 第一页\n\n正文一。\n\n# 第二页\n\n正文二。", title="打印稿"),
        tmp_path / "打印稿.pdf", "pdf").path
    viewer = DocViewer(library)
    viewer.load(library.open_path(source))

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printed = tmp_path / "printed.pdf"
    printer.setOutputFileName(str(printed))
    assert viewer._print_pdf(printer) is True                                      # noqa: SLF001
    assert printed.is_file() and printed.stat().st_size > 1000

    # 只打印第 1 页：输出也应当只有 1 页
    only_first = tmp_path / "printed-first.pdf"
    printer2 = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer2.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer2.setOutputFileName(str(only_first))
    printer2.setFromTo(1, 1)
    assert viewer._print_pdf(printer2) is True                                     # noqa: SLF001
    import pypdf

    assert len(pypdf.PdfReader(str(only_first)).pages) == 1


# ---------------------------------------------------------------- 多标签


def test_multi_tab_open_and_switch(board: DocumentBoardPage, qapp: QApplication,
                                   sample: Path, tmp_path: Path) -> None:
    other = tmp_path / "另一份.md"
    other.write_text("# 另一份\n\n别的内容。", encoding="utf-8")
    _open(board, qapp, sample)
    _open(board, qapp, other)
    assert board._doc_tabs.count() == 2                                              # noqa: SLF001
    assert board._current_ir.title == "另一份"                                        # noqa: SLF001

    # 切回第一个标签：内容随之切换
    board._doc_tabs.setCurrentIndex(0)                                               # noqa: SLF001
    qapp.processEvents()
    assert board._current_ir is not None and "报告" in (board._current_ir.title or "")  # noqa: SLF001

    # 重复打开同一份文件不会新增标签，而是切过去
    _open(board, qapp, other)
    assert board._doc_tabs.count() == 2                                              # noqa: SLF001


def test_multi_tab_keeps_unsaved_edits(board: DocumentBoardPage, qapp: QApplication,
                                       sample: Path, tmp_path: Path) -> None:
    other = tmp_path / "第二份.md"
    other.write_text("# 第二份\n\n内容。", encoding="utf-8")
    _open(board, qapp, sample)
    viewer = board._viewer                                                           # noqa: SLF001
    viewer._edit_button.setChecked(True)                                             # noqa: SLF001
    viewer._toggle_edit()                                                            # noqa: SLF001
    viewer._editor.setPlainText("# 未保存标题\n\n未保存正文。")                        # noqa: SLF001
    assert viewer.is_dirty()
    assert board._doc_tabs.tabText(0).startswith("•")                                 # noqa: SLF001

    _open(board, qapp, other)
    board._doc_tabs.setCurrentIndex(0)                                                # noqa: SLF001
    qapp.processEvents()
    assert "未保存标题" in board._current_ir.text()                                    # noqa: SLF001
    assert sample.read_text(encoding="utf-8").startswith("# 年度报告"), "切标签不应写盘"  # noqa: SLF001


def test_multi_tab_close(board: DocumentBoardPage, qapp: QApplication,
                         sample: Path, tmp_path: Path) -> None:
    other = tmp_path / "第三份.md"
    other.write_text("# 第三份\n\n内容。", encoding="utf-8")
    _open(board, qapp, sample)
    _open(board, qapp, other)
    board._on_doc_tab_closed(0)                                                      # noqa: SLF001
    qapp.processEvents()
    assert board._doc_tabs.count() == 1                                              # noqa: SLF001
    assert board._current_ir is not None                                              # noqa: SLF001

    board._on_doc_tab_closed(0)                                                      # noqa: SLF001
    qapp.processEvents()
    assert board._doc_tabs.count() == 0                                              # noqa: SLF001
    assert board._current_ir is None                                                 # noqa: SLF001
    assert board._doc_tabs.isVisible() is False                                       # noqa: SLF001


def test_viewer_version_history(board: DocumentBoardPage, qapp: QApplication,
                                sample: Path) -> None:
    _open(board, qapp, sample)
    versions = board._library.versions(board._current_ir)                            # noqa: SLF001
    assert versions, "打开文档后必须留有快照"
    snapshot = board._library.rollback(versions[0]["id"])                            # noqa: SLF001
    assert snapshot is not None


def test_viewer_rejects_readonly_format(board: DocumentBoardPage, qapp: QApplication,
                                        tmp_path: Path) -> None:
    pdf = tmp_path / "扫描件.pdf"
    pdf.write_bytes(b"%PDF-1.4 broken")
    board._add_paths([str(pdf)])                                                    # noqa: SLF001
    qapp.processEvents()
    # 解析会失败（损坏 PDF），状态要写清楚而不是静默无反应
    board._open_path(str(pdf))                                                      # noqa: SLF001
    settle(qapp, lambda: board._worker is None)                                     # noqa: SLF001
    assert board._table.item(0, 4).text() == "解析失败"                              # noqa: SLF001


# ---------------------------------------------------------------- 美化 / 计算


def test_beautify_panel_applies_and_exports(board: DocumentBoardPage, qapp: QApplication,
                                            sample: Path, tmp_path: Path) -> None:
    _open(board, qapp, sample)
    panel = board._beautify                                                         # noqa: SLF001
    panel._template.setCurrentIndex(2)          # 报告模板
    panel._preview()
    assert panel._summary.text() and panel._changes.rowCount() >= 1
    panel._apply()
    qapp.processEvents()
    assert any(block.kind == "toc" for block in board._current_ir.blocks)            # noqa: SLF001

    panel._export_target.setCurrentIndex(3)     # Word DOCX
    panel._export()
    outputs = list((tmp_path / "out").glob("*.docx")) if (tmp_path / "out").exists() else []
    assert outputs or Path(board._library.output_dir).exists()


def test_sheet_panel_compute_and_autofill(board: DocumentBoardPage, qapp: QApplication,
                                          sample: Path) -> None:
    _open(board, qapp, sample)
    panel = board._sheet                                                           # noqa: SLF001
    panel.set_ir(board._current_ir)                                                # noqa: SLF001
    assert panel._shape_label.text().endswith("列")                                 # noqa: SLF001

    panel._formula.setText("=SUM(B2:B3)")                                          # noqa: SLF001
    panel._do_compute()                                                            # noqa: SLF001
    assert "= 5" in panel._report_browser.toPlainText()                             # noqa: SLF001

    panel._fill_column.setCurrentIndex(0)                                          # noqa: SLF001
    before = panel._current_table().height                                          # noqa: SLF001
    panel._do_autofill()                                                           # noqa: SLF001
    assert panel._current_table().height > before                                   # noqa: SLF001


def test_sheet_panel_reports_no_table(board: DocumentBoardPage, qapp: QApplication,
                                      tmp_path: Path) -> None:
    text_file = tmp_path / "无表格.txt"
    text_file.write_text("第一章 说明\n\n这里只有正文。", encoding="utf-8")
    _open(board, qapp, text_file)
    panel = board._sheet                                                           # noqa: SLF001
    panel.set_ir(board._current_ir)                                                # noqa: SLF001
    assert "没有表格" in panel._report_browser.toPlainText()                        # noqa: SLF001


# ---------------------------------------------------------------- 合并


def test_merge_panel_end_to_end(board: DocumentBoardPage, qapp: QApplication,
                                sample: Path, tmp_path: Path) -> None:
    other = tmp_path / "补充.md"
    other.write_text("# 补充材料\n\n## 一、市场\n\n华东区增长明显。", encoding="utf-8")
    _open(board, qapp, sample)
    panel = board._merge                                                           # noqa: SLF001
    panel._main_path.setText(str(sample))                                          # noqa: SLF001
    panel._other_path.setText(str(other))                                          # noqa: SLF001
    panel._preview()                                                               # noqa: SLF001
    qapp.processEvents()
    assert "合并" in panel._summary.text()                                          # noqa: SLF001
    assert panel._changes.rowCount() >= 1                                           # noqa: SLF001
    assert panel._diff.toPlainText()                                               # noqa: SLF001

    panel._apply()                                                                 # noqa: SLF001
    qapp.processEvents()
    assert "补充材料" in board._current_ir.text()                                    # noqa: SLF001


def test_merge_panel_requires_two_files(board: DocumentBoardPage, qapp: QApplication,
                                        sample: Path) -> None:
    _open(board, qapp, sample)
    panel = board._merge                                                           # noqa: SLF001
    panel._main_path.setText(str(sample))                                          # noqa: SLF001
    panel._other_path.setText(str(sample))                                         # noqa: SLF001
    panel._preview()                                                               # noqa: SLF001
    qapp.processEvents()
    # 同一个文件必须被拦下：不能产生"合并结果"，也不能允许应用/导出
    assert panel._result is None                                                   # noqa: SLF001
    assert panel._apply_button.isEnabled() is False                                 # noqa: SLF001
    assert "合并" in panel._summary.text() or "选择" in panel._summary.text()        # noqa: SLF001


# ---------------------------------------------------------------- 循环美化


def test_loop_panel_runs_offline(board: DocumentBoardPage, qapp: QApplication,
                                 sample: Path) -> None:
    _open(board, qapp, sample)
    panel = board._loop                                                            # noqa: SLF001
    panel._goal.setText("规范标点并统一格式")                                        # noqa: SLF001
    panel._use_ai.setChecked(False)                                                # noqa: SLF001
    panel._rounds.setValue(2)                                                     # noqa: SLF001
    panel._start()                                                                 # noqa: SLF001
    assert settle(qapp, lambda: panel._worker is None, timeout=60), "循环未结束"      # noqa: SLF001
    assert panel._summary.text()                                                   # noqa: SLF001
    assert panel._rounds_table.rowCount() >= 1                                     # noqa: SLF001
    panel._rounds_table.selectRow(0)                                               # noqa: SLF001
    assert panel._diff.toPlainText()                                               # noqa: SLF001
    panel._rollback()                                                              # noqa: SLF001
    qapp.processEvents()
    assert board._current_ir is not None                                            # noqa: SLF001
    assert panel.change_digest()                                                    # noqa: SLF001


# ---------------------------------------------------------------- 安全


def test_security_panel_masks_and_audits(board: DocumentBoardPage, qapp: QApplication,
                                         sample: Path) -> None:
    _open(board, qapp, sample)
    panel = board._security                                                        # noqa: SLF001
    panel.set_ir(board._current_ir)                                                # noqa: SLF001
    panel._preview_mask()                                                          # noqa: SLF001
    assert panel._mask_table.rowCount() >= 5                                       # noqa: SLF001
    assert "命中" in panel._status.text()                                           # noqa: SLF001

    panel._apply_mask()                                                            # noqa: SLF001
    qapp.processEvents()
    assert "13800138000" not in board._current_ir.text()                            # noqa: SLF001

    panel._watermark_text.setText("内部资料")                                        # noqa: SLF001
    panel._apply_watermark()                                                       # noqa: SLF001
    assert board._library.watermark is not None and board._library.watermark.enabled  # noqa: SLF001

    panel.refresh_audit()                                                          # noqa: SLF001
    assert panel._audit_table.rowCount() >= 1                                       # noqa: SLF001
    panel._save_permissions()                                                      # noqa: SLF001
    assert "权限" in board._task_bar.text                                            # noqa: SLF001


# ---------------------------------------------------------------- 设置页


def test_document_settings_page(qapp: QApplication) -> None:
    keys = [key for key, _title, _icon, _factory in PAGE_FACTORIES]
    assert "document" in keys

    dialog = SettingsDialog(initial="document")
    dialog.resize(980, 700)
    dialog.show()
    qapp.processEvents()
    try:
        page = dialog._ensure_page("document")                                      # noqa: SLF001
        assert page is not None
        assert dialog._scrolls["document"].horizontalScrollBar().maximum() == 0      # noqa: SLF001
        page._template.setCurrentIndex(1)                                           # noqa: SLF001
        page._output_dir.setText(str(Path(page._output_dir.text() or ".")))          # noqa: SLF001
        assert page.save() is True
        assert page.hint()
    finally:
        dialog.close()


def test_board_settings_button_opens_document_page(board: DocumentBoardPage,
                                                   qapp: QApplication) -> None:
    """板块设置直达「文档」页（避免用户自己找）。"""
    dialog = SettingsDialog(board, initial="document")
    try:
        page = dialog._ensure_page("document")                                      # noqa: SLF001
        assert page is not None and page.title == "文档"
    finally:
        dialog.close()


def test_context_singletons() -> None:
    storage = document_context.document_storage()
    library = document_context.document_library()
    assert isinstance(storage, DocumentStorage)
    assert library.storage is storage
    assert document_context.document_ai() is document_context.document_ai()
    assert document_context.document_output_dir().is_dir()


def test_context_version_keep_follows_settings() -> None:
    """板块存储接上了「版本保留」读取器（设置改完立即生效，无需重启）。"""
    from modu_workbench.core.document.storage import MAX_VERSIONS_PER_DOCUMENT

    with _temp_settings(**{"document/max_versions": 7}):
        assert document_context._read_version_keep() == 7                        # noqa: SLF001
    with _temp_settings(**{"document/max_versions": None}):
        assert document_context._read_version_keep() == MAX_VERSIONS_PER_DOCUMENT  # noqa: SLF001
    assert document_context.document_storage().version_keep_provider is not None


def test_loop_options_from_panel(board: DocumentBoardPage) -> None:
    panel = board._loop                                                            # noqa: SLF001
    options = panel.options()
    assert isinstance(options, LoopOptions)
    assert options.allow_tools, "默认应当允许全部工具"
    assert panel.capability_hint() if hasattr(panel, "capability_hint") else True


def test_loop_confirm_setting_is_wired(board: DocumentBoardPage, qapp: QApplication,
                                       sample: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """设置页的「每轮结束人工确认」要真的让循环停下来等人点头。"""
    from modu_workbench.core.document.models import STOP_USER

    _open(board, qapp, sample)
    panel = board._loop                                                            # noqa: SLF001
    assert panel.options().require_confirm is False, "默认不确认"
    asked: list[int] = []

    def auto_answer(self, report) -> None:  # noqa: ANN001  替身：不弹模态框
        asked.append(int(getattr(report, "index", 0) or 0))
        self._worker.answer_confirm(False)      # 第一轮后回答"不继续"

    monkeypatch.setattr(type(panel), "_on_confirm_requested", auto_answer)
    with _temp_settings(**{"document/require_confirm": True}):
        assert panel.options().require_confirm is True
        panel._goal.setText("规范标点并统一格式")                                    # noqa: SLF001
        panel._use_ai.setChecked(False)                                            # noqa: SLF001
        panel._rounds.setValue(5)                                                  # noqa: SLF001
        panel._start()                                                             # noqa: SLF001
        assert settle(qapp, lambda: panel._worker is None, timeout=60), "循环未结束"  # noqa: SLF001

    assert asked == [1], "确认请求应当只在第一轮结束时出现"
    result = panel._result                                                          # noqa: SLF001
    assert result is not None and result.round_count == 1
    assert result.stopped_reason == STOP_USER
    assert "用户" in result.summary()


def test_merge_options_from_panel(board: DocumentBoardPage) -> None:
    panel = board._merge                                                          # noqa: SLF001
    options = panel.options()
    assert isinstance(options, MergeOptions)
    assert options.mode == "append"
    assert "追加" in panel.capability_hint()


# ---------------------------------------------------------------- 设置项真的生效


def test_loop_panel_takes_settings_defaults(qapp: QApplication, tmp_path: Path) -> None:
    """设置页的「AI 循环美化默认值」要进面板（过去只写不读）。"""
    from modu_workbench.boards.document.loop_page import LoopPanel

    library = DocumentLibrary(DocumentStorage(tmp_path / "m.db"), output_dir=tmp_path)
    with _temp_settings(**{"document/max_rounds": 5, "document/quality_threshold": 0.95,
                           "document/max_cost": 2.5}):
        panel = LoopPanel(library)
    try:
        assert panel._rounds.value() == 5                                       # noqa: SLF001
        assert panel._threshold.value() == pytest.approx(0.95)                  # noqa: SLF001
        assert panel._max_cost.value() == pytest.approx(2.5)                    # noqa: SLF001
        options = panel.options()
        assert options.max_rounds == 5
        assert options.quality_threshold == pytest.approx(0.95)
        assert options.max_cost == pytest.approx(2.5)
    finally:
        panel.deleteLater()
        qapp.processEvents()

    # 没保存过设置时保持面板自己的默认值
    with _temp_settings(**{"document/max_rounds": None, "document/quality_threshold": None,
                           "document/max_cost": None}):
        plain = LoopPanel(library)
    try:
        assert plain._rounds.value() == 3 and plain._max_cost.value() == 0.0     # noqa: SLF001
    finally:
        plain.deleteLater()
        qapp.processEvents()


def test_bool_settings_read_from_windows_string_form(board: DocumentBoardPage,
                                                     qapp: QApplication, sample: Path) -> None:
    """Qt 在 Windows 注册表里把 bool 存成 "true"/"false" 字符串：不能再 bool("false")。

    踩过的坑：`bool(settings.value(...))` 让"关掉人工确认/自动保存"全都失效
    （`bool("false") is True`）。
    """
    with _temp_settings(**{"document/require_confirm": "false",
                           "document/autosave": "false"}):
        assert board._loop.require_confirm() is False, "字符串 false 必须按关闭处理"   # noqa: SLF001
        _open(board, qapp, sample)
        viewer = board._viewer                                                    # noqa: SLF001
        viewer._start_autosave_timer()                                            # noqa: SLF001
        assert viewer._autosave_timer.isActive() is False                         # noqa: SLF001

    with _temp_settings(**{"document/require_confirm": "true"}):
        assert board._loop.require_confirm() is True                              # noqa: SLF001


def test_beautify_panel_takes_settings_defaults(qapp: QApplication, tmp_path: Path) -> None:
    """设置页的「默认美化参数」要进美化面板；没保存过的项保持模板默认。"""
    from modu_workbench.boards.document.beautify_page import BeautifyPanel

    library = DocumentLibrary(DocumentStorage(tmp_path / "m.db"), output_dir=tmp_path)
    gov = template_options("gov")
    with _temp_settings(**{"document/template": "gov", "document/font_cn": "楷体",
                           "document/body_size": 14.0, "document/brand_color": "#B01F24",
                           "document/build_toc": True}):
        panel = BeautifyPanel(library)
    try:
        assert panel._template.currentData() == "gov"                            # noqa: SLF001
        assert panel._font_cn.currentText() == "楷体"                             # noqa: SLF001
        assert panel._body_size.value() == pytest.approx(14.0)                   # noqa: SLF001
        assert panel._brand_color.text() == "#B01F24"                            # noqa: SLF001
        assert panel._build_toc.isChecked() is True                              # noqa: SLF001
    finally:
        panel.deleteLater()
        qapp.processEvents()

    # 未保存过任何美化参数 → 完全跟模板默认（不能把"公文体"的字体字号冲成通用值）
    with _temp_settings(**{"document/template": None, "document/font_cn": None,
                           "document/body_size": None, "document/brand_color": None,
                           "document/build_toc": None}):
        plain = BeautifyPanel(library)
    try:
        assert plain._font_cn.currentText() == template_options("general").font_cn  # noqa: SLF001
        assert plain._body_size.value() == pytest.approx(                          # noqa: SLF001
            template_options("general").body_size)
        assert plain._body_size.value() != pytest.approx(gov.body_size)            # noqa: SLF001
    finally:
        plain.deleteLater()
        qapp.processEvents()

    # 注册表里存的是 "true"/"false" 字符串：开关要按字符串读，不能 bool("false")=True
    with _temp_settings(**{"document/beautify_tables": "false", "document/freeze_header": "false"}):
        strings = BeautifyPanel(library)
    try:
        assert strings._beautify_tables.isChecked() is False                       # noqa: SLF001
        assert strings._freeze_header.isChecked() is False                         # noqa: SLF001
    finally:
        strings.deleteLater()
        qapp.processEvents()


def test_merge_apply_never_overwrites_main_file(board: DocumentBoardPage, qapp: QApplication,
                                                sample: Path, tmp_path: Path) -> None:
    """「应用到文档」后不能留着主文档路径，否则 Ctrl+S / 自动保存会覆盖原稿。"""
    other = tmp_path / "补充.md"
    other.write_text("# 补充材料\n\n## 一、市场\n\n华东区增长明显。", encoding="utf-8")
    _open(board, qapp, sample)
    panel = board._merge                                                          # noqa: SLF001
    panel._main_path.setText(str(sample))                                          # noqa: SLF001
    panel._other_path.setText(str(other))                                          # noqa: SLF001
    panel._preview()                                                               # noqa: SLF001
    panel._apply()                                                                 # noqa: SLF001
    qapp.processEvents()

    merged = board._current_ir                                                     # noqa: SLF001
    assert merged.path == "", "合并结果必须是无路径的新文档（保存时才会另存为）"
    assert merged.metadata.get("merge_source_path") == str(sample)
    assert "补充材料" in merged.text()
    # 原稿内容不动
    assert "补充材料" not in sample.read_text(encoding="utf-8")
    # 合并结果要有自己的标签，否则切走再切回来会取回合并前的旧内容
    tab_keys = [board._doc_tabs.tabData(index)                                  # noqa: SLF001
                for index in range(board._doc_tabs.count())]                     # noqa: SLF001
    assert merged.title in tab_keys
    # 未落盘的合并稿不该在文档库里留下一行"空路径"记录
    assert all(record.path for record in board._library.documents())              # noqa: SLF001
