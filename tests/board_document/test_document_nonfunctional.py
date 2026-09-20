"""墨软文档：非功能需求（需求 6.9）测试 —— 性能 / 可靠 / 易用与可访问。

性能断言用的是"宽松预算"（本机实测的 3~5 倍），目的是挡住"某次改动把大文档拖成分钟级"
这类回归，而不是做微基准；每条都打印实际耗时，便于人工对比。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from modu_workbench.core.document import (
    MAX_TABLE_ROWS,
    BeautifyOptions,
    DocumentLibrary,
    DocumentStorage,
    beautify_document,
    parse_document,
    parse_markdown,
    write_document,
)
from modu_workbench.core.document.storage import MAX_VERSIONS_PER_DOCUMENT


def _big_markdown(paragraphs: int = 8000, *, heading_every: int = 20) -> str:
    """造一份"几 MB 级"的 Markdown（每 20 段插一个二级标题）。"""
    lines: list[str] = ["# 大型文档压测", ""]
    for index in range(1, paragraphs + 1):
        if index % heading_every == 0:
            lines.append(f"## 1.{index // heading_every} 小节标题")
            lines.append("")
        lines.append(
            f"第 {index} 段正文：本段用于验证大文档的解析与排版性能，"
            "内容需要在语义上足够长以便接近真实文档的体量，因此这里重复了一些说明文字。"
        )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 性能


def test_parse_and_beautify_large_markdown(tmp_path: Path) -> None:
    source = tmp_path / "大文档.md"
    text = _big_markdown()
    source.write_text(text, encoding="utf-8")
    size_mb = source.stat().st_size / 1024 / 1024
    assert size_mb > 1.0, f"样本太小（{size_mb:.2f} MB），起不到压测作用"

    started = time.perf_counter()
    document = parse_document(source)
    parse_seconds = time.perf_counter() - started
    print(f"\n[性能] 解析 {size_mb:.2f} MB：{parse_seconds:.2f}s（{len(document.blocks)} 块）")
    assert parse_seconds < 10.0, f"解析过慢：{parse_seconds:.2f}s"

    started = time.perf_counter()
    result = beautify_document(document, BeautifyOptions(template="report", build_toc=True))
    beautify_seconds = time.perf_counter() - started
    print(f"[性能] 美化（模板+目录）：{beautify_seconds:.2f}s（{result.change_count} 处修改）")
    assert beautify_seconds < 12.0, f"美化过慢：{beautify_seconds:.2f}s"
    assert result.ir.stats()["headings"] > 100


def test_export_large_document_within_budget(tmp_path: Path) -> None:
    document = parse_markdown(_big_markdown(2000), title="大型文档")
    started = time.perf_counter()
    written = write_document(document, tmp_path / "大.docx", "docx")
    seconds = time.perf_counter() - started
    print(f"\n[性能] 导出 Word：{seconds:.2f}s（{written.path.stat().st_size / 1024:.0f} KB）")
    assert written.path.is_file() and seconds < 30.0

    started = time.perf_counter()
    html = write_document(document, tmp_path / "大.html", "html")
    seconds = time.perf_counter() - started
    print(f"[性能] 导出 HTML：{seconds:.2f}s")
    assert html.path.stat().st_size > 10_000 and seconds < 15.0


def test_large_csv_is_truncated_with_warning(tmp_path: Path) -> None:
    csv_path = tmp_path / "大表.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        handle.write("编号,名称,金额\n")
        for index in range(MAX_TABLE_ROWS + 500):
            handle.write(f"{index},项目{index},{index * 3}\n")

    started = time.perf_counter()
    document = parse_document(csv_path)
    seconds = time.perf_counter() - started
    table = document.first_table()
    print(f"\n[性能] 解析 {MAX_TABLE_ROWS + 501} 行 CSV：{seconds:.2f}s（保留 {table.height} 行）")
    assert seconds < 15.0
    assert table.height <= MAX_TABLE_ROWS + 1, "应当按上限截断预览"
    assert document.warnings and "截断" in document.warnings[0]


def test_viewer_preview_is_capped_for_huge_documents(qapp, tmp_path: Path) -> None:  # noqa: ANN001
    from modu_workbench.boards.document.viewer import (
        PREVIEW_BLOCK_LIMIT,
        DocViewer,
    )
    from modu_workbench.core.document import BLOCK_PARAGRAPH, Block, DocumentIR

    library = DocumentLibrary(DocumentStorage(tmp_path / "modu.db"), output_dir=tmp_path)
    viewer = DocViewer(library)
    try:
        small = parse_markdown("# 标题\n\n正文。", title="小文档")
        assert viewer.is_preview_capped(small) is False
        assert "预览只渲染" not in viewer._preview_html(small)     # noqa: SLF001

        huge = DocumentIR(
            blocks=[Block(kind=BLOCK_PARAGRAPH, text=f"第 {index} 段正文内容。")
                    for index in range(PREVIEW_BLOCK_LIMIT + 50)],
            title="大文档", format_key="markdown")
        assert viewer.is_preview_capped(huge) is True
        html = viewer._preview_html(huge)                          # noqa: SLF001
        assert "预览只渲染前" in html
        assert html.count("<p>") <= PREVIEW_BLOCK_LIMIT + 5

        # 保护只作用于预览：导出/保存仍是全文
        viewer.load(huge)
        assert len(viewer.current_ir().blocks) == PREVIEW_BLOCK_LIMIT + 50
        assert viewer._preview.toPlainText().count("第") >= PREVIEW_BLOCK_LIMIT  # noqa: SLF001
    finally:
        viewer._set_dirty(False)                                   # noqa: SLF001
        viewer._autosave_timer.stop()                              # noqa: SLF001
        viewer.close()


# ---------------------------------------------------------------- 可靠


def test_version_snapshots_are_pruned(tmp_path: Path) -> None:
    store = DocumentStorage(tmp_path / "modu.db")
    library = DocumentLibrary(store, output_dir=tmp_path / "out")
    source = tmp_path / "a.md"
    source.write_text("# 标题\n\n正文。", encoding="utf-8")
    document = library.open_path(source)

    for index in range(MAX_VERSIONS_PER_DOCUMENT + 8):
        document.blocks[-1].text = f"第 {index} 次修改"
        library.snapshot(document, label=f"第 {index} 次")
    versions = store.list_versions(library.document_id_of(document), limit=200)
    assert len(versions) <= MAX_VERSIONS_PER_DOCUMENT, "版本快照必须按上限清理，避免库无限增长"


def test_rollback_restores_content(tmp_path: Path) -> None:
    store = DocumentStorage(tmp_path / "modu.db")
    library = DocumentLibrary(store, output_dir=tmp_path / "out")
    source = tmp_path / "a.md"
    source.write_text("# 标题\n\n原始正文。", encoding="utf-8")
    document = library.open_path(source)
    version_id = library.snapshot(document, label="原始")

    document.blocks[-1].text = "被改坏的正文"
    restored = library.rollback(version_id)
    assert restored is not None and "原始正文" in restored.text()
    assert any(record["action"] == "rollback" for record in library.audit_records())


# ---------------------------------------------------------------- 易用 / 可访问


def test_board_shortcuts_and_accessible_names(qapp, tmp_path: Path) -> None:  # noqa: ANN001
    from modu_workbench.boards.document.board import DocumentBoardPage

    library = DocumentLibrary(DocumentStorage(tmp_path / "modu.db"), output_dir=tmp_path / "out")
    page = DocumentBoardPage(library=library)
    page.resize(1280, 800)
    page.show()
    qapp.processEvents()
    try:
        keys = page.shortcut_keys()
        expected = {
            "add_files": "Ctrl+O", "add_folder": "Ctrl+Shift+O", "open_selected": "Ctrl+Return",
            "save": "Ctrl+S", "save_as": "Ctrl+Shift+S", "print": "Ctrl+P",
            "close_tab": "Ctrl+W", "focus_search": "Ctrl+L", "find": "Ctrl+F",
            "refresh": "F5", "pdf_tools": "Ctrl+Shift+P", "ocr": "Ctrl+Shift+R",
            "remove": "Ctrl+Del",
        }
        for name, sequence in expected.items():
            assert keys.get(name) == sequence, f"{name} 的快捷键应为 {sequence}，实际 {keys.get(name)}"
        for index in range(1, 7):
            assert keys.get(f"panel_{index}") == f"Alt+{index}"

        # 面板切换快捷键真的切页签
        page._shortcuts["panel_3"].activated.emit()                 # noqa: SLF001
        qapp.processEvents()
        assert page._tabs.currentIndex() == 2                       # noqa: SLF001

        # 搜索快捷键：聚焦并全选（离屏环境窗口未必被激活，因此断言"全选生效"这条硬证据）
        page._search.setText("报告")                                 # noqa: SLF001
        page._shortcuts["focus_search"].activated.emit()             # noqa: SLF001
        qapp.processEvents()
        assert page._search.selectedText() == "报告"                  # noqa: SLF001

        for widget in (page, page._table, page._search, page._tabs,    # noqa: SLF001
                       page._doc_tabs, page._viewer, page._task_bar):  # noqa: SLF001
            assert widget.accessibleName().strip(), f"{type(widget).__name__} 缺少可访问名"
    finally:
        page.shutdown()
        page.close()


def test_ctrl_s_saves_current_document(qapp, tmp_path: Path) -> None:  # noqa: ANN001
    from modu_workbench.boards.document.board import DocumentBoardPage

    source = tmp_path / "报告.md"
    source.write_text("# 报告\n\n原始内容。", encoding="utf-8")
    library = DocumentLibrary(DocumentStorage(tmp_path / "modu.db"), output_dir=tmp_path / "out")
    page = DocumentBoardPage(library=library)
    try:
        page._add_paths([str(source)])                              # noqa: SLF001
        page._open_path(str(source))                                # noqa: SLF001
        deadline = time.monotonic() + 30
        while page._worker is not None and time.monotonic() < deadline:   # noqa: SLF001
            qapp.processEvents()
            time.sleep(0.02)

        viewer = page._viewer                                       # noqa: SLF001
        viewer._edit_button.setChecked(True)                        # noqa: SLF001
        viewer._toggle_edit()                                       # noqa: SLF001
        viewer._editor.setPlainText("# 报告\n\n快捷键保存的内容。")   # noqa: SLF001

        page._shortcuts["save"].activated.emit()                    # noqa: SLF001
        qapp.processEvents()
        assert "快捷键保存的内容" in source.read_text(encoding="utf-8")
        assert viewer.is_dirty() is False
    finally:
        page.shutdown()
        page.close()
