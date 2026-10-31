"""文档板块 · 表格面板（`boards/document/sheet_page.py`）测试。

## 为什么这个页面必须有测试

它 622 行、此前**零测试覆盖**，却是"文档里改表格"的唯一入口：
填充、公式计算、分组统计都在这里。这里的错误不是崩溃，而是
**静默算错** —— 用户看到一串数字，以为是自己的数据。

本文件挑的是**不依赖完整 GUI 交互也能验证的逻辑**：

- `_column_values()`：取某一列的值。列名不存在、行比表头短（真实 Excel 极常见）
  时必须安全降级，否则统计会错位；
- `_current_table()`：当前表格的定位；
- `set_ir()`：换文档后状态要清干净，否则会把上一份文档的表格指到新文档上。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from modu_workbench.core.document.models import (
    BLOCK_PARAGRAPH,
    BLOCK_TABLE,
    Block,
    DocumentIR,
    TableData,
)


@pytest.fixture()
def library(tmp_path: Path):  # noqa: ANN201
    """最小可用的 DocumentLibrary（真实存储，隔离在 tmp_path）。"""
    from modu_workbench.core.document.library import DocumentLibrary
    from modu_workbench.core.document.storage import DocumentStorage

    storage = DocumentStorage(str(tmp_path / "doc.db"))
    lib = DocumentLibrary(storage, output_dir=str(tmp_path / "out"))
    yield lib
    storage.close()


@pytest.fixture()
def panel(qapp, library):  # noqa: ANN001, ANN201
    from modu_workbench.boards.document.sheet_page import SheetPanel

    widget = SheetPanel(library)
    yield widget
    widget.deleteLater()


def make_table(rows: list[list[str]], *, header: bool = True) -> TableData:
    return TableData(rows=rows, name="T", header=header)


def ir_with_table(table: TableData) -> DocumentIR:
    ir = DocumentIR(title="测试")
    ir.blocks = [
        Block(kind=BLOCK_PARAGRAPH, text="说明文字"),
        Block(kind=BLOCK_TABLE, text="", table=table),
    ]
    return ir


# --------------------------------------------------------------------------- 取列值


def test_column_values_returns_column(panel) -> None:  # noqa: ANN001
    """正常取列：返回该列每一行的值（跳过表头）。"""
    table = make_table([["姓名", "分数"], ["张三", "90"], ["李四", "80"]])
    assert panel._column_values(table, "分数") == ["90", "80"]


def test_column_values_missing_column_returns_empty(panel) -> None:  # noqa: ANN001
    """列名不存在时返回空列表，**不能抛异常**。

    用户可能手输列名；列名打错应该得到"没有数据"，而不是让面板崩掉。
    """
    table = make_table([["姓名", "分数"], ["张三", "90"]])
    assert panel._column_values(table, "不存在的列") == []


def test_column_values_handles_ragged_rows(panel) -> None:  # noqa: ANN001
    """行比表头短时缺失位置补空串。

    真实 Excel 里"合并单元格/空行"会造成行列不齐。若这里抛 IndexError，
    整张表的统计就废了。
    """
    table = make_table([["A", "B", "C"], ["1"], ["1", "2"], ["1", "2", "3"]])
    assert panel._column_values(table, "C") == ["", "", "3"]


def test_column_values_without_header(panel) -> None:  # noqa: ANN001
    """无表头表格：**首行是数据，不是列名**。

    ## 这条用例抓到过一个真 bug

    `set_ir()` 原来无条件用 `table.header_row()` 填列名下拉框。无表头的表
    （例如没有标题行的 CSV）首行其实是数据，于是：

    1. 下拉框把「第一行数据」显示成列名；
    2. 用户选中它之后，`_column_values()` 返回的 `body_rows()` **又包含这一行**；
    3. 结果统计/填充**多算一行**，而且用户看不出哪里错了。

    现在 `header=False` 时不再填列名（下拉框显示「（无表头）」），
    这条路径本来就是给无表头表准备的。
    """
    table = make_table([["1", "2"], ["3", "4"]], header=False)
    # `column_index` 仍会匹配到首行（它的定位用途不变），
    # 但面板**不会把首行当列名展示给用户**
    assert panel._column_values(table, "1") == ["1", "3"]


def test_headerless_table_shows_no_column_names(qapp, library) -> None:  # noqa: ANN001
    """无表头表格的列名下拉框必须是「（无表头）」，不能列出首行数据。"""
    from modu_workbench.boards.document.sheet_page import SheetPanel

    widget = SheetPanel(library)
    try:
        widget.set_ir(ir_with_table(make_table([["1", "2"], ["3", "4"]], header=False)))
        group = widget._stat_group.currentText()
        assert group == "（无表头）", f"无表头表却把首行当成了列名：{group!r}"
    finally:
        widget.deleteLater()



def test_column_values_empty_table(panel) -> None:  # noqa: ANN001
    """空表格返回空列表，不崩。"""
    assert panel._column_values(make_table([]), "任意") == []


# --------------------------------------------------------------------------- 当前表格


def test_current_table_is_none_without_ir(panel) -> None:  # noqa: ANN001
    """没有文档时没有"当前表格"。"""
    assert panel._current_table() is None


def test_current_table_found_after_set_ir(panel) -> None:  # noqa: ANN001
    """载入带表格的文档后能定位到表格。"""
    table = make_table([["A"], ["1"]])
    panel.set_ir(ir_with_table(table))
    current = panel._current_table()
    assert current is not None
    assert current.rows == table.rows


def test_current_table_none_for_document_without_tables(panel) -> None:  # noqa: ANN001
    """纯文字文档没有表格 —— 面板要给出"没有表格"而不是崩。"""
    ir = DocumentIR(title="纯文本")
    ir.blocks = [Block(kind=BLOCK_PARAGRAPH, text="只有文字")]
    panel.set_ir(ir)
    assert panel._current_table() is None


def test_set_ir_none_clears_state(panel) -> None:  # noqa: ANN001
    """`set_ir(None)` 要把状态清空（关闭文档后不能还指着上一份表格）。"""
    panel.set_ir(ir_with_table(make_table([["A"], ["1"]])))
    assert panel._current_table() is not None
    panel.set_ir(None)
    assert panel._current_table() is None


def test_set_ir_switches_between_documents(panel) -> None:  # noqa: ANN001
    """连续载入两份文档，必须指向**后一份**的表格。"""
    panel.set_ir(ir_with_table(make_table([["第一份"], ["1"]])))
    panel.set_ir(ir_with_table(make_table([["第二份"], ["2"]])))
    current = panel._current_table()
    assert current is not None
    assert current.rows[0] == ["第二份"]


# --------------------------------------------------------------------------- 公式计算


def test_calculate_on_table_range(panel, library) -> None:  # noqa: ANN001
    """表格区域求和能算对（公式引擎与表格联动的核心路径）。"""
    table = make_table([["项目", "金额"], ["A", "10"], ["B", "20"]])
    ir = ir_with_table(table)

    values = [library.calculate("=SUM(B2:B3)", ir, table_index=0)]
    assert values[0] == pytest.approx(30), f"SUM(B2:B3) 应为 30，实际 {values[0]}"


def test_invalid_formula_raises_formula_error(panel, library) -> None:  # noqa: ANN001
    """非法公式要抛 `FormulaError`（面板据此显示"公式错误"而不是崩）。"""
    from modu_workbench.core.document.sheet import FormulaError

    ir = ir_with_table(make_table([["A"], ["1"]]))
    with pytest.raises(FormulaError):
        library.calculate("=完全不存在的函数(1)", ir, table_index=0)
