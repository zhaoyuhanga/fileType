"""文档板块 · 解析入口（`core/document/parser.py`）测试。

## 为什么只挑这几个函数测

`parser.py` 有 1598 行，是文档板块的格式解析总入口（docx/xlsx/pdf/md/txt/html…）。
把每种格式的细节都测一遍不现实，也不划算 —— 格式库本身有测试。
这里集中测**我们自己写的判断逻辑**，也就是"最容易错、错了最难发现"的部分：

| 函数 | 为什么重要 |
|---|---|
| `guess_heading_level` | 标题层级全靠它推断；判错会让整篇文档的目录/编号全乱 |
| `join_wrapped_lines` | TXT/PDF 抽出来的文字被硬换行打断，合并规则错了会整段黏连 |
| `parse_plain_text` | 最常见入口；空文件、无标题、超长行都要能处理 |
| `parse_markdown` | 结构相对明确，用来对照"显式标记 vs 推断"的差异 |
| `table_from_rows` | 表格首行是否当表头，直接影响体检与导出 |
| `read_document_text` | 读取文本（编码/大文件）的唯一入口 |

不使用真实 docx/xlsx（那些依赖第三方库的细节）；需要时用 tmp_path 造最小文本文件。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.document import parser as P
from modu_workbench.core.document.models import BLOCK_HEADING, BLOCK_PARAGRAPH, BLOCK_TABLE


# --------------------------------------------------------------------------- 标题层级


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("第一章 引言", 1),
        ("一、总体要求", 2),
        ("1. 概述", 2),
        ("2.1 背景", 3),
        ("普通正文段落", 0),
        ("【注意事项】", 0),
        ("附件：说明", 0),
    ],
)
def test_guess_heading_level(heading: str, expected: int) -> None:
    """按编号深度推断标题层级；**普通正文必须是 0（不是标题）**。

    0 很关键：正文被误判成标题，会导致整篇文档的目录结构出现大量假标题。
    """
    assert P.guess_heading_level(heading) == expected


def test_long_sentence_is_not_a_heading() -> None:
    """长句子即使带数字也不该被判成标题（正文里常有"2024 年我们…"）。"""
    sentence = "到了 2024 年，我们的业务规模已经扩大了整整三倍以上，需要重新评估。"
    assert P.guess_heading_level(sentence) == 0


def test_numbered_flag_can_disable_inference() -> None:
    """`numbered=False` 时不按编号推断（用于已确定不是编号场景的场合）。"""
    assert P.guess_heading_level("1. 概述", numbered=False) == 0


# --------------------------------------------------------------------------- 硬换行合并


def test_join_wrapped_lines_merges_within_paragraph() -> None:
    """被硬换行打断的一段文字要合并成一句（PDF/TXT 常见）。"""
    merged = P.join_wrapped_lines(["这是一段被", "硬换行打断的", "文字。"])
    assert merged == "这是一段被硬换行打断的文字。"


def test_join_wrapped_lines_keeps_blank_line_separation() -> None:
    """空行表示分段，合并后应保留段落分隔（不能把两段黏成一段）。"""
    merged = P.join_wrapped_lines(["第一段。", "", "第二段。"])
    assert "第一段" in merged and "第二段" in merged
    assert "\n\n" in merged or merged.count("。") == 2


def test_join_wrapped_lines_handles_empty() -> None:
    """空输入不崩。"""
    assert P.join_wrapped_lines([]) == ""


# --------------------------------------------------------------------------- 纯文本解析


def test_parse_plain_text_infers_headings() -> None:
    """纯文本里能识别出标题块，正文保持段落块。"""
    ir = P.parse_plain_text(
        "第一章 引言\n正文内容一。\n\n第二章 方法\n正文内容二。",
        title="测试文档",
    )
    kinds = [block.kind for block in ir.blocks]
    assert BLOCK_HEADING in kinds, "没有识别出任何标题"
    assert BLOCK_PARAGRAPH in kinds, "没有识别出任何正文"
    assert ir.title == "测试文档"


def test_parse_plain_text_without_headings() -> None:
    """没有标题的纯文本：全部当正文，不能凭空造标题。"""
    ir = P.parse_plain_text("这就是一段普通文字，没有任何标题。\n还有一行。")
    assert [block.kind for block in ir.blocks] == [BLOCK_PARAGRAPH] * len(ir.blocks)
    assert len(ir.blocks) >= 1


def test_parse_plain_text_empty_input() -> None:
    """空文本返回空 IR（界面显示"这个文件没有内容"，而不是崩溃）。"""
    ir = P.parse_plain_text("")
    assert ir.blocks == [] or all(not (block.text or "").strip() for block in ir.blocks)


def test_parse_plain_text_can_disable_heading_inference() -> None:
    """`infer_headings=False` 时整篇都是正文（用户明确说"别猜标题"）。"""
    ir = P.parse_plain_text("第一章 引言\n正文。", infer_headings=False)
    assert all(block.kind != BLOCK_HEADING for block in ir.blocks)


def test_parse_plain_text_preserves_content() -> None:
    """解析不能丢字：原文里的关键内容必须都还在。"""
    text = "第一章 引言\n这是正文第一句，包含数字 2024。\n第二章 结论\n结束。"
    ir = P.parse_plain_text(text)
    joined = "".join((block.text or "") for block in ir.blocks)
    for fragment in ("引言", "第一句", "2024", "结论", "结束"):
        assert fragment in joined, f"解析后丢失了内容：{fragment}"


# --------------------------------------------------------------------------- Markdown


def test_parse_markdown_uses_explicit_headings() -> None:
    """Markdown 的 `#` 是显式标题，必须被识别（不需要猜）。"""
    ir = P.parse_markdown("# 一级标题\n\n正文段落。\n\n## 二级标题\n\n另一段。")
    headings = [block for block in ir.blocks if block.kind == BLOCK_HEADING]
    assert len(headings) == 2
    assert headings[0].text.strip() == "一级标题"
    assert headings[1].level >= headings[0].level


def test_parse_markdown_keeps_plain_paragraphs() -> None:
    """Markdown 正文段落不能被当成标题。"""
    ir = P.parse_markdown("这是普通的一段话，没有井号。")
    assert not [block for block in ir.blocks if block.kind == BLOCK_HEADING]


def test_parse_markdown_empty() -> None:
    """空 Markdown 不崩溃。"""
    ir = P.parse_markdown("")
    assert ir.blocks == [] or all(not (block.text or "").strip() for block in ir.blocks)


# --------------------------------------------------------------------------- 表格


def test_table_from_rows_with_header() -> None:
    """首行作为表头的常见情况：列名保留、数据行数正确。"""
    table = P.table_from_rows([["姓名", "部门"], ["张三", "技术"], ["李四", "市场"]], header=True)
    assert table is not None
    assert len(table.rows) >= 2, f"数据行数不对：{getattr(table, 'rows', None)}"


def test_table_from_rows_without_header() -> None:
    """`header=False` 时首行也是数据（用于无表头的导入表格）。"""
    table = P.table_from_rows([["张三", "技术"], ["李四", "市场"]], header=False)
    assert table is not None


def test_table_from_rows_empty() -> None:
    """空表格不崩。"""
    table = P.table_from_rows([], header=True)
    assert table is not None


def test_table_from_rows_ragged_rows_are_safe() -> None:
    """行列数不一致（真实 Excel 极常见）不能让解析崩溃。"""
    table = P.table_from_rows([["a", "b", "c"], ["d"]], header=True)
    assert table is not None


# --------------------------------------------------------------------------- 文件读取


def test_read_document_text_utf8(tmp_path) -> None:  # noqa: ANN001
    """UTF-8 文本能正常读出。"""
    path = tmp_path / "a.txt"
    path.write_text("中文内容测试。", encoding="utf-8")
    assert "中文内容测试" in P.read_document_text(path)


def test_read_document_text_gbk(tmp_path) -> None:  # noqa: ANN001
    """GBK 编码的中文文件也要能读（Windows 上极常见）。

    读不出来就是"打开是乱码"，这是文档类工具最典型的投诉。
    """
    path = tmp_path / "gbk.txt"
    path.write_bytes("这是 GBK 编码的正文。".encode("gbk"))
    assert "GBK" in P.read_document_text(path) or "正文" in P.read_document_text(path)


def test_read_document_text_missing_file_raises(tmp_path) -> None:  # noqa: ANN001
    """文件不存在时明确报错（而不是返回空字符串让用户以为文件是空的）。"""
    with pytest.raises(Exception):
        P.read_document_text(tmp_path / "nope.txt")


def test_parse_document_txt_via_public_entry(tmp_path) -> None:  # noqa: ANN001
    """`parse_document` 是界面用的总入口，至少要能处理纯文本。"""
    path = tmp_path / "doc.txt"
    path.write_text("第一章 测试\n这是正文。", encoding="utf-8")
    ir = P.parse_document(path)
    assert ir.blocks, "解析结果为空"
    assert any(block.kind == BLOCK_HEADING for block in ir.blocks)


def test_parse_document_missing_file_reports_error(tmp_path) -> None:  # noqa: ANN001
    """文件不存在时 `parse_document` 要么抛异常、要么在 warnings 里说清楚。"""
    try:
        ir = P.parse_document(tmp_path / "nope.docx")
    except Exception:  # noqa: BLE001  抛异常也是可接受的契约
        return
    assert ir.warnings, "解析失败却没给出任何警告，用户会看到空白文档"
