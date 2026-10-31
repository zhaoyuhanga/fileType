"""墨软文档：自动填充 / 智能填充 / 公式引擎 / 统计（MR-DOC-302 测试）。"""
from __future__ import annotations

import pytest

from modu_workbench.core.document import (
    CUSTOM_LISTS,
    FUNCTION_NAMES,
    FormulaEngine,
    FormulaError,
    TableData,
    apply_pattern,
    autofill,
    cell_value,
    chart_series,
    check_formulas,
    column_index,
    column_letters,
    convert_units,
    detect_series,
    evaluate_formula,
    fill_series,
    group_by,
    infer_pattern,
    infer_patterns,
    parse_cell_ref,
    pivot,
    smart_fill,
    summarize_table,
    translate_formula,
    units_of,
)


@pytest.fixture()
def sheet() -> TableData:
    return TableData(rows=[
        ["项目", "数量", "单价", "金额", "状态"],
        ["A", "3", "10", "=B2*C2", '=IF(D2>25,"高","低")'],
        ["B", "2", "20", "=B3*C3", '=IF(D3>25,"高","低")'],
        ["C", "5", "5", "=B4*C4", '=IF(D4>25,"高","低")'],
    ], name="明细", header=True)


# ---------------------------------------------------------------- 引用


def test_column_index_roundtrip() -> None:
    assert column_index("A") == 0
    assert column_index("AA") == 26
    for index in (0, 1, 25, 26, 701):
        assert column_index(column_letters(index)) == index


def test_parse_cell_ref_with_sheet_and_absolute() -> None:
    reference = parse_cell_ref("'我的 表'!$B$3")
    assert reference is not None
    assert (reference.sheet, reference.row, reference.column) == ("我的 表", 2, 1)
    assert reference.absolute_row and reference.absolute_column
    assert reference.text(with_sheet=True).endswith("$B$3")
    assert parse_cell_ref("不是引用") is None


# ---------------------------------------------------------------- 公式引擎


def test_formula_arithmetic_and_functions(sheet: TableData) -> None:
    assert float(evaluate_formula("=SUM(D2:D4)", sheet, sheet_name="明细")) == 95.0
    assert float(evaluate_formula("=AVERAGE(D2:D4)", sheet, sheet_name="明细")) > 30
    assert float(evaluate_formula("=MAX(B2:B4)*2", sheet, sheet_name="明细")) == 10.0
    assert evaluate_formula('=IF(D2>25,"高","低")', sheet, sheet_name="明细") == "高"
    assert evaluate_formula('=COUNTIF(B2:B4,">2")', sheet, sheet_name="明细") == 2
    assert float(evaluate_formula('=SUMIF(B2:B4,">2",D2:D4)', sheet, sheet_name="明细")) == 55.0
    assert evaluate_formula('=CONCATENATE(A2,"-",C2)', sheet, sheet_name="明细") == "A-10"
    assert float(evaluate_formula("=ROUND(10/3,2)", sheet, sheet_name="明细")) == 3.33
    assert evaluate_formula('=UPPER("ab")', sheet, sheet_name="明细") == "AB"


def test_formula_array_and_cross_sheet(sheet: TableData) -> None:
    other = TableData(rows=[["21"]], name="汇率")
    assert float(evaluate_formula("{=SUM(B2:B4*C2:C4)}", sheet, sheet_name="明细")) == 95.0
    assert float(evaluate_formula("=汇率!A1*2", sheet, sheet_name="明细",
                                 tables=[sheet, other])) == 42.0


def test_formula_vlookup_and_index(sheet: TableData) -> None:
    assert float(evaluate_formula('=VLOOKUP("B",A2:D4,4,FALSE)', sheet, sheet_name="明细")) == 40.0
    assert evaluate_formula("=INDEX(A2:A4,2)", sheet, sheet_name="明细") == "B"
    with pytest.raises(FormulaError) as info:
        evaluate_formula('=VLOOKUP("Z",A2:D4,4,FALSE)', sheet, sheet_name="明细")
    assert info.value.code == "#N/A"


def test_formula_errors_are_explicit(sheet: TableData) -> None:
    with pytest.raises(FormulaError) as info:
        evaluate_formula("=1/0", sheet, sheet_name="明细")
    assert info.value.code == "#DIV/0!"
    with pytest.raises(FormulaError) as info:
        evaluate_formula("=不存在函数(1)", sheet, sheet_name="明细")
    assert info.value.code == "#NAME?"
    with pytest.raises(FormulaError) as info:
        evaluate_formula('="文本"+1', sheet, sheet_name="明细")
    assert info.value.code == "#VALUE!"


def test_circular_reference_detected() -> None:
    table = TableData(rows=[["=A2"], ["=A1"]], name="S")
    issues = check_formulas([table])
    assert len(issues) == 2
    assert all(item["code"] == "#CIRC!" for item in issues)


def test_formula_engine_keeps_cell_cache() -> None:
    engine = FormulaEngine({"S": [["1", "2", "=A1+B1"]]})
    assert float(engine.value("S", 0, 2)) == 3.0
    assert float(engine.value("S", 0, 2)) == 3.0      # 第二次走缓存
    assert engine.sheet_names() == ["S"]


def test_function_catalog_is_reachable() -> None:
    for name in ("SUM", "AVERAGE", "VLOOKUP", "COUNTIF", "IF", "TODAY"):
        assert name in FUNCTION_NAMES


def test_cell_value_parsing() -> None:
    assert cell_value("12") == 12.0
    assert cell_value("TRUE") is True
    assert cell_value("文字") == "文字"
    assert cell_value("") == ""


# ---------------------------------------------------------------- 自动填充


def test_detect_and_fill_series() -> None:
    filled, spec = autofill(["1", "3", "5"], 3)
    assert spec.kind == "number"
    assert filled == ["7", "9", "11"]

    filled, spec = autofill(["2024-01-01", "2024-01-02"], 2)
    assert spec.kind == "date"
    assert filled == ["2024-01-03", "2024-01-04"]

    filled, spec = autofill(["周五"], 3)
    assert spec.kind == "weekday_name"
    assert filled == ["周六", "周日", "周一"]

    filled, spec = autofill(["一", "二"], 3)
    assert spec.kind == "custom"
    assert filled == ["三", "四", "五"]

    filled, spec = autofill(["第1项", "第2项"], 2)
    assert spec.kind == "text_number"
    assert filled == ["第3项", "第4项"]

    filled, spec = autofill(["2024-01", "2024-02"], 2)
    assert filled == ["2024-03", "2024-04"]

    filled, spec = autofill(["=B1*C1", "=B2*C2"], 2)
    assert spec.kind == "formula"
    assert filled == ["=B3*C3", "=B4*C4"]

    filled, spec = autofill(["任意值"], 2)
    assert spec.kind == "copy"
    assert filled == ["任意值", "任意值"]


def test_workday_series_skips_weekend() -> None:
    # 周五 → 周一 的样本体现"跳过周末"，据此推断工作日序列
    filled, spec = autofill(["2024-03-08", "2024-03-11"], 3)
    assert spec.unit == "workday"
    assert filled == ["2024-03-12", "2024-03-13", "2024-03-14"]

    # 连续工作日（周四 → 周五）按自然日填充，不会莫名跳周末
    filled, spec = autofill(["2024-03-07", "2024-03-08"], 2)
    assert spec.unit == "day"
    assert filled == ["2024-03-09", "2024-03-10"]


def test_custom_list_and_detect_series() -> None:
    assert detect_series(["甲", "乙"]).kind == "custom"
    assert "天干" in CUSTOM_LISTS
    filled = fill_series(["甲", "乙"], 2)
    assert filled == ["丙", "丁"]


def test_translate_formula_keeps_absolute() -> None:
    assert translate_formula("=SUM($A$1,B2)", 1, 1) == "=SUM($A$1,C3)"
    assert translate_formula("=B2", 0, 1) == "=C2"
    assert translate_formula("=\"B2\"&B2", 1, 0) == '="B2"&B3'    # 字符串常量里的不动
    assert translate_formula("普通文本", 1, 1) == "普通文本"


# ---------------------------------------------------------------- 智能填充


def test_infer_pattern_split_name() -> None:
    surname = infer_pattern(["张三", "李四"], ["张", "李"])
    assert surname is not None and surname.kind == "cn_surname"
    assert apply_pattern(surname, "王五") == "王"

    given = infer_pattern(["张三", "李四"], ["三", "四"])
    assert given is not None and given.kind == "cn_given"
    assert apply_pattern(given, "王五") == "五"


def test_infer_pattern_extract_and_prefix() -> None:
    spec = infer_pattern(["订单-20240101", "订单-20240202"], ["20240101", "20240202"])
    assert spec is not None
    assert apply_pattern(spec, "订单-20240303") == "20240303"

    prefix = infer_pattern(["SKU-001", "SKU-002"], ["001", "002"])
    assert prefix is not None and apply_pattern(prefix, "SKU-010") == "010"

    suffix = infer_pattern(["001-a", "002-a"], ["001", "002"])
    assert suffix is not None and apply_pattern(suffix, "003-a") == "003"


def test_infer_pattern_classify_and_smart_fill() -> None:
    examples = ["苹果手机", "华为手机", "美的空调"]
    outputs = ["手机", "手机", "空调"]
    spec = infer_pattern(examples, outputs)
    assert spec is not None and spec.kind == "classify"
    assert apply_pattern(spec, "小米手机") == "手机"
    assert apply_pattern(spec, "格力空调") == "空调"
    assert apply_pattern(spec, "未知品类") == ""

    specs = infer_patterns(examples, [outputs])
    columns = smart_fill(["OPPO 手机"], specs)
    assert columns == [["手机"]]


def test_infer_pattern_pad_and_case() -> None:
    pad = infer_pattern(["1", "2"], ["0001", "0002"])
    assert pad is not None and apply_pattern(pad, "37") == "0037"
    upper = infer_pattern(["abc", "def"], ["ABC", "DEF"])
    assert upper is not None and apply_pattern(upper, "xyz") == "XYZ"


def test_infer_pattern_returns_none_for_unknown() -> None:
    assert infer_pattern(["a"], ["完全无关的内容"]) is None or infer_pattern(
        ["a"], ["完全无关的内容"]).kind != "copy"


# ---------------------------------------------------------------- 统计


def test_summarize_and_group_by(sheet: TableData) -> None:
    reports = summarize_table(sheet)
    amount = next(item for item in reports if item["column"] == "金额")
    assert amount["numeric"] == 3 and amount["sum"] == 95.0

    rows = dict(group_by(sheet, "状态"))
    assert rows == {"低": 1, "高": 2}


def test_pivot_and_chart(sheet: TableData) -> None:
    table = pivot(sheet, "项目", "状态", "金额")
    assert table.header_row() == ["项目", "高", "低", "合计"]
    assert len(table.rows) == 4

    data = chart_series(sheet, "项目", "金额")
    assert data["labels"] == ["A", "B", "C"]
    assert data["values"] == [30.0, 40.0, 25.0]


def test_group_by_unknown_column(sheet: TableData) -> None:
    with pytest.raises(ValueError):
        group_by(sheet, "不存在的列")


# ---------------------------------------------------------------- 排序与筛选


def test_sort_table_numeric_and_text(sheet: TableData) -> None:
    from modu_workbench.core.document import sort_table

    ascending = sort_table(sheet, "数量")
    assert [row[0] for row in ascending.body_rows()] == ["B", "A", "C"]     # 2、3、5

    descending = sort_table(sheet, "数量", descending=True)
    assert [row[0] for row in descending.body_rows()] == ["C", "A", "B"]
    assert "降序" in descending.meta["sorted_by"]

    text_sorted = sort_table(sheet, "项目")
    assert [row[0] for row in text_sorted.body_rows()] == ["A", "B", "C"]
    assert text_sorted.header_row() == sheet.header_row(), "表头必须留在第一行"

    with pytest.raises(ValueError):
        sort_table(sheet, "没有这列")


def test_filter_table_modes(sheet: TableData) -> None:
    from modu_workbench.core.document import filter_table

    contains = filter_table(sheet, "项目", "A", mode="contains")
    assert len(contains.body_rows()) == 1
    assert contains.meta["filtered_out"] == 2

    greater = filter_table(sheet, "数量", "2", mode="gt")
    assert [row[0] for row in greater.body_rows()] == ["A", "C"]

    equals = filter_table(sheet, "状态", "高", mode="equals")
    assert len(equals.body_rows()) == 2

    not_empty = filter_table(sheet, "项目", "", mode="not_empty")
    assert len(not_empty.body_rows()) == 3

    with pytest.raises(ValueError):
        filter_table(sheet, "项目", "A", mode="不支持")


def test_distinct_values(sheet: TableData) -> None:
    from modu_workbench.core.document import distinct_values

    assert distinct_values(sheet, "状态") == ["高", "低"]
    with pytest.raises(ValueError):
        distinct_values(sheet, "没有这列")


# ---------------------------------------------------------------- 单位换算


def test_convert_units() -> None:
    assert convert_units(100, "厘米", "米") == pytest.approx(1.0)
    assert convert_units(1, "GB", "MB") == pytest.approx(1024.0)
    assert convert_units(0, "摄氏度", "华氏度") == pytest.approx(32.0)
    assert convert_units(100, "摄氏度", "开尔文") == pytest.approx(373.15)
    assert units_of("长度")[0] == "毫米"
    assert "摄氏度" in units_of("温度")
    with pytest.raises(ValueError):
        convert_units(1, "米", "千克")


# ---------------------------------------------------------------- AI 公式


def test_ai_formula_helpers_parse_reply() -> None:
    from modu_workbench.core.document import explain_formula, suggest_formula
    from modu_workbench.core.document.sheet import parse_formula_reply

    table = TableData(rows=[["名称", "数量", "单价"], ["A", "1", "2"]], name="S")
    assert parse_formula_reply("```excel\n=SUM(B2:B9)\n```") == "=SUM(B2:B9)"
    assert parse_formula_reply("建议使用 =B2*C2 这条公式") == "=B2*C2"

    captured: list[list[dict]] = []

    def fake_chat(messages: list[dict]) -> str:
        captured.append(messages)
        return "=B2*C2"

    assert suggest_formula(fake_chat, "单价乘数量", table, cell="D2") == "=B2*C2"
    assert "数量" in captured[0][1]["content"]
    assert explain_formula(fake_chat, "=SUM(A1:A3)") == "=B2*C2"
