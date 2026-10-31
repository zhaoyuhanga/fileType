"""墨软文档：两个文档合并（MR-DOC-304 测试）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from modu_workbench.core.document import (
    MERGE_MODES,
    DocumentLibrary,
    DocumentStorage,
    MergeOptions,
    TableData,
    conflict_scan,
    merge_documents,
    merge_preview_lines,
    merge_table_columns,
    merge_table_key,
    merge_table_rows,
    parse_document,
    parse_markdown,
)
from modu_workbench.core.document import merge as merge_engine

MAIN_MD = """# 年度报告

公司2024年实现收入 1,200,000 元。

## 1.1 数据

| 编号 | 项目 | 金额 |
| --- | --- | --- |
| A1 | 硬件 | 100 |
| A2 | 软件 | 200 |

## 1.2 结论

整体良好。
"""

OTHER_MD = """# 补充材料

## 一、市场

华东区增长明显。
华东区增长明显。

## 二、风险

供应链存在不确定性。
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- 模式


def test_append_merge_marks_revisions() -> None:
    main = parse_markdown(MAIN_MD, title="年度报告")
    other = parse_markdown(OTHER_MD, title="补充材料")
    result = merge_documents(main, other, MergeOptions(mode="append"))
    texts = [block.text for block in result.ir.blocks]
    assert "补充材料" in texts
    assert any(block.revision == "inserted" for block in result.ir.blocks)
    assert result.report.mode == "append"
    assert "追加" in result.report.mode_label
    assert result.report.diff, "合并后必须有差异记录"


def test_section_merge_inserts_into_named_section() -> None:
    main = parse_markdown(MAIN_MD, title="年度报告")
    other = parse_markdown(OTHER_MD, title="补充材料")
    result = merge_documents(main, other, MergeOptions(mode="section", section_title="数据"))
    index_other = next(index for index, block in enumerate(result.ir.blocks)
                       if block.text == "补充材料")
    index_conclusion = next(index for index, block in enumerate(result.ir.blocks)
                            if block.text == "1.2 结论")
    assert index_other < index_conclusion, "插入位置应在本章节末尾、下一章节之前"


def test_section_merge_falls_back_with_conflict() -> None:
    main = parse_markdown(MAIN_MD, title="年度报告")
    other = parse_markdown(OTHER_MD, title="补充材料")
    result = merge_documents(main, other, MergeOptions(mode="section", section_title="不存在的章节"))
    assert any("没有找到章节" in item for item in result.report.conflicts)
    assert any(block.text == "补充材料" for block in result.ir.blocks)


def test_master_slave_merge_uses_appendix_title() -> None:
    main = parse_markdown("# 主文档\n\n正文。", title="主文档")
    other = parse_markdown("# 附件说明\n\n补充。", title="附件说明")
    result = merge_documents(main, other, MergeOptions(mode="slave"))
    assert any(block.text.startswith("附件：") for block in result.ir.headings())


def test_diff_merge_keeps_both_versions() -> None:
    main = parse_markdown("# 稿子\n\n第一版内容。\n\n保留段落。", title="稿子")
    other = parse_markdown("# 稿子\n\n第二版内容。\n\n保留段落。", title="稿子")
    result = merge_documents(main, other, MergeOptions(mode="diff"))
    texts = " ".join(block.text for block in result.ir.blocks)
    assert "第一版内容" in texts and "第二版内容" in texts
    assert "【修订】" in texts or "【新增】" in texts


def test_all_modes_are_declared() -> None:
    keys = [key for key, _label, _note in MERGE_MODES]
    assert set(keys) == {"append", "section", "table_rows", "table_columns", "table_key",
                         "slave", "diff"}
    assert len(keys) == len(set(keys))


# ---------------------------------------------------------------- 表格合并


def test_merge_table_rows_skips_duplicates() -> None:
    main = TableData(rows=[["编号", "项目"], ["A1", "硬件"]], header=True)
    other = TableData(rows=[["编号", "项目"], ["A1", "硬件"], ["A2", "软件"]], header=True)
    merged, notes = merge_table_rows(main, other)
    assert len(merged.rows) == 3
    assert any("跳过重复 1 行" in note for note in notes)


def test_merge_table_rows_reports_header_mismatch() -> None:
    main = TableData(rows=[["编号", "项目"], ["A1", "硬件"]], header=True)
    other = TableData(rows=[["序号", "名称"], ["A2", "软件"]], header=True)
    merged, notes = merge_table_rows(main, other)
    assert any("表头不一致" in note for note in notes)
    assert len(merged.rows) == 3


def test_merge_table_columns_pads_rows() -> None:
    main = TableData(rows=[["A"], ["1"], ["2"]], header=True)
    other = TableData(rows=[["B"], ["x"]], header=True)
    merged, notes = merge_table_columns(main, other)
    assert merged.width == 2
    assert merged.rows[1] == ["1", "x"]
    assert merged.rows[2] == ["2", ""]
    assert any("行数不同" in note for note in notes)


def test_merge_table_key_fills_and_appends() -> None:
    main = TableData(rows=[["编号", "项目", "负责人"], ["A1", "硬件", ""], ["A2", "软件", ""]],
                     header=True)
    other = TableData(rows=[["编号", "负责人", "备注"], ["A1", "张三", "已确认"],
                            ["A3", "李四", ""]], header=True)
    merged, notes = merge_table_key(main, other, "编号")
    assert "备注" in merged.header_row()
    index_owner = merged.column_index("负责人")
    rows = {row[0]: row for row in merged.body_rows()}
    assert rows["A1"][index_owner] == "张三"
    assert "A3" in rows, "副表独有的主键应作为新行并入"
    assert any("补齐" in note for note in notes)


def test_merge_table_key_without_key_column_degrades() -> None:
    main = TableData(rows=[["编号", "项目"], ["A1", "硬件"]], header=True)
    other = TableData(rows=[["编号", "项目"], ["A2", "软件"]], header=True)
    merged, notes = merge_table_key(main, other, "")
    assert any("退化为按行追加" in note for note in notes)
    assert len(merged.rows) == 3


def test_table_merge_mode_requires_tables() -> None:
    main = parse_markdown("# 只有文字\n\n正文。", title="主")
    other = parse_markdown("# 另一篇\n\n正文。", title="副")
    result = merge_documents(main, other, MergeOptions(mode="table_rows"))
    assert any("需要两边都有表格" in item for item in result.report.conflicts)


def test_table_merge_replaces_first_table(tmp_path: Path) -> None:
    main = parse_markdown("| 编号 | 项目 |\n| --- | --- |\n| A1 | 硬件 |", title="主")
    other = parse_markdown("| 编号 | 项目 |\n| --- | --- |\n| A2 | 软件 |", title="副")
    result = merge_documents(main, other, MergeOptions(mode="table_rows"))
    tables = result.ir.tables()
    assert len(tables) == 1 and len(tables[0].rows) == 3


# ---------------------------------------------------------------- 语义增强


def test_dedupe_removes_repeated_paragraphs() -> None:
    main = parse_markdown("# 稿子\n\n重复段落内容足够长。\n\n重复段落内容足够长。", title="稿子")
    other = parse_markdown("# 别的\n\n正文。", title="别的")
    result = merge_documents(main, other, MergeOptions(mode="append", dedupe=True,
                                                      dedupe_threshold=0.95))
    texts = [block.text for block in result.ir.blocks]
    assert texts.count("重复段落内容足够长。") == 1
    assert result.report.deduped == 1
    assert any(change.kind == "语义去重" for change in result.report.changes)


def test_unify_terms_replaces_aliases() -> None:
    main = parse_markdown("# 稿子\n\n墨软工作台很好用。", title="稿子")
    other = parse_markdown("# 别的\n\n正文。", title="别的")
    result = merge_documents(main, other, MergeOptions(
        mode="append", unify_terms=True, term_map={"墨软工作台": "墨软·工作台"}))
    assert "墨软·工作台" in result.ir.text()
    assert any(change.kind == "术语统一" for change in result.report.changes)


def test_style_unification_and_toc() -> None:
    main = parse_markdown(MAIN_MD, title="年度报告")
    other = parse_markdown(OTHER_MD, title="补充材料")
    result = merge_documents(main, other, MergeOptions(
        mode="append", unify_style=True, template="report", add_toc=True, add_summary=False))
    assert any(block.kind == "toc" for block in result.ir.blocks)
    assert result.ir.styles.get("template") == "report"


def test_extractive_summary_without_ai() -> None:
    main = parse_markdown(MAIN_MD, title="年度报告")
    other = parse_markdown(OTHER_MD, title="补充材料")
    result = merge_documents(main, other, MergeOptions(mode="append", add_summary=True))
    assert result.report.summary
    assert result.report.ai_used is False
    assert "1." in result.report.summary


def test_conflict_scan_reports_style_number_and_metadata() -> None:
    main = parse_markdown("# 年度报告\n\n第一章 概述\n\n正文。", title="年度报告")
    main.styles["font_cn"] = "宋体"
    main.styles["body_size"] = 12.0
    other = parse_markdown("# 补充\n\n一、概述\n\n正文。", title="补充")
    other.styles["font_cn"] = "黑体"
    other.styles["body_size"] = 14.0
    conflicts = conflict_scan(main, other, MergeOptions(unify_style=True))
    joined = " ".join(conflicts)
    assert "样式冲突" in joined
    assert "元数据冲突" in joined


def test_merge_with_ai_chat_callback() -> None:
    main = parse_markdown("# 主\n\n正文。", title="主")
    other = parse_markdown("# 副\n\n补充。", title="副")
    calls: list[list[dict]] = []

    def chat(messages: list[dict]) -> str:
        calls.append(messages)
        prompt = messages[-1]["content"]
        if "摘要" in messages[0]["content"]:
            return "1. 要点一"
        return "接下来是补充材料的内容。"

    result = merge_documents(main, other, MergeOptions(mode="append", add_summary=True), chat=chat)
    assert result.report.ai_used is True
    assert result.report.summary == "1. 要点一"
    texts = [block.text for block in result.ir.blocks]
    assert any("接下来是补充材料" in text for text in texts)
    assert any(change.ai_generated for change in result.report.changes)
    assert calls, "应当真的调用过模型"


def test_ai_failure_falls_back_to_local_rules() -> None:
    main = parse_markdown("# 主\n\n正文。", title="主")
    other = parse_markdown("# 副\n\n补充。", title="副")

    def broken(messages: list[dict]) -> str:  # noqa: ARG001
        raise RuntimeError("模型不可用")

    result = merge_documents(main, other, MergeOptions(mode="append", add_summary=True),
                             chat=broken)
    assert result.report.summary, "AI 失败也必须给出本地摘要"
    assert any(block.text.startswith("以下内容来自") for block in result.ir.blocks)


def test_merge_preview_lines() -> None:
    main = parse_markdown("# 主\n\n正文。", title="主")
    other = parse_markdown("# 副\n\n补充。", title="副")
    result = merge_documents(main, other, MergeOptions(mode="append"))
    lines = merge_preview_lines(result)
    assert any("合并方式" in line for line in lines)
    assert any("差异摘要" in line for line in lines)


def test_capability_text_mentions_modes() -> None:
    text = merge_engine.capability_text()
    assert "追加" in text and "按主键" in text


# ---------------------------------------------------------------- 库集成


def test_library_merge_paths_and_rollback(tmp_path: Path) -> None:
    store = DocumentStorage(tmp_path / "modu.db")
    library = DocumentLibrary(store, output_dir=tmp_path / "out")
    main_path = _write(tmp_path, "主.md", MAIN_MD)
    other_path = _write(tmp_path, "副.md", OTHER_MD)

    result = library.merge_paths(main_path, other_path, MergeOptions(mode="append"))
    assert result.report.changes
    assert store.list_merge_reports(), "合并报告必须落库"

    document_id = library.document_id_of(result.ir)
    versions = store.list_versions(document_id)
    assert any(record["kind"] == "merge" for record in versions)

    exported = library.export(result.ir, "docx", name="合并结果")
    assert exported.path.is_file()

    audit_actions = [record["action"] for record in library.audit_records()]
    assert "merge" in audit_actions and "export" in audit_actions


def test_cross_format_merge_markdown_and_csv(tmp_path: Path) -> None:
    """跨格式合并：Markdown 主文档 + CSV 副文档（解析成 IR 后合并）。"""
    main_path = _write(tmp_path, "报告.md", MAIN_MD)
    csv_path = _write(tmp_path, "数据.csv", "编号,项目\nB1,服务\n")
    result = merge_documents(parse_document(main_path), parse_document(csv_path),
                             MergeOptions(mode="append"))
    assert result.ir.tables(), "CSV 的表格应当并入结果"
    assert len(result.ir.tables()) == 2
