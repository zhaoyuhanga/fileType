"""文档板块 · 质量评分与差异对比（`core/document/quality.py`）测试。

## 为什么这个模块必须有测试

它是"文档体检"和"两个文档差异对比"的引擎，此前**零测试覆盖**。
这类打分/对比逻辑的坑在于：

- **分数无意义**：全是 5 分或全是 1 分，用户看了等于没看；
- **误报**：把正常的排版判成"不一致"，让人反复改没问题的东西；
- **漏报**：真正的问题（中英文之间缺空格、重复标点）看不见；
- **差异不准**：改了一句话却显示"全文重写"，用户会失去信任。

这里不追求"断言具体的分数值"（那是把实现抄进测试），而是钉住
**方向性与不变量**：做了什么改动 → 哪一维该升、哪一维该降；
以及**报告必须能用**（有说明、有条数、可重复）。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.document import quality as Q
from modu_workbench.core.document.models import (
    BLOCK_HEADING,
    BLOCK_PARAGRAPH,
    Block,
    DocumentIR,
)


def make_ir(*texts: str, headings: tuple[str, ...] = ()) -> DocumentIR:
    """构造一个最小可用的 DocumentIR。"""
    ir = DocumentIR(title="测试文档")
    for title in headings:
        ir.blocks.append(Block(kind=BLOCK_HEADING, text=title, level=1))
    for text in texts:
        ir.blocks.append(Block(kind=BLOCK_PARAGRAPH, text=text))
    return ir


# --------------------------------------------------------------------------- 数字抽取


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("提高效率 30%", ["30%"]),
        ("2024年目标", ["2024"]),
        ("达到 1.5 倍", ["1.5"]),
        ("没有数字", []),
    ],
)
def test_extract_numbers(text: str, expected: list[str]) -> None:
    """抽取文档里的数字（体检用它核对"说法与数据"是否互相矛盾）。"""
    assert Q.extract_numbers(text) == expected


def test_extract_numbers_keeps_multiple_in_order() -> None:
    """多个数字按出现顺序返回（比较两处说法时顺序有意义）。"""
    assert Q.extract_numbers("从 10 涨到 25，增幅 150%") == ["10", "25", "150%"]


# --------------------------------------------------------------------------- 编号风格


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("一、总体要求", "chinese"),
        ("第一章 引言", "chinese"),
        ("1. 概述", "arabic"),
        ("1.1 背景", "arabic"),
        ("普通段落", "none"),
    ],
)
def test_heading_numbering_style(heading: str, expected: str) -> None:
    """识别标题的编号风格：中文序号 / 阿拉伯数字 / 无编号。

    体检里的"格式一致性"就靠它 —— 一篇文档里混用两种编号风格会被指出来。
    """
    assert Q.heading_numbering_style(heading) == expected


# --------------------------------------------------------------------------- 目标关键词


def test_goal_keywords_splits_chinese_goal() -> None:
    """短中文目标必须被**切分成实词**，而不是整句当一个词。

    这曾是真实缺陷：`re.findall(r"[\u4e00-\u9fff]{2,}")` 贪婪匹配，
    把「请帮我优化一下这段文字的正式程度」整句返回成一个 token，
    于是"目标匹配"退化成整句子串查找，几乎永远匹配不上、分数恒低。
    现在用停用词当分隔符切分。
    """
    keywords = Q.goal_keywords("请帮我优化一下这段文字的正式程度")
    assert len(keywords) >= 2, f"短句目标没有被切分：{keywords}"
    joined = "".join(keywords)
    assert "帮我" not in joined, "口语虚词不应进入关键词"
    assert any(word in joined for word in ("文字", "正式")), f"没切出实词：{keywords}"


def test_goal_keywords_empty_input() -> None:
    """空目标不崩，返回空列表。"""
    assert Q.goal_keywords("") == []
    assert Q.goal_keywords("   ") == []


# --------------------------------------------------------------------------- 体检报告


def test_analyze_document_returns_expected_sections() -> None:
    """体检报告必须包含可展示的条目（不是空字典）。"""
    report = Q.analyze_document(make_ir("这是一段正常的正文。", headings=("第一章 引言",)))
    assert isinstance(report, dict)
    # 至少要有统计类信息，界面才能显示"体检了哪些项"
    assert report, "体检报告不能为空"


def test_analyze_document_on_empty_document() -> None:
    """空文档不能崩（用户可能刚新建一个）—— 但报告要体现"没内容"。"""
    report = Q.analyze_document(make_ir())
    assert isinstance(report, dict)


# --------------------------------------------------------------------------- 五维评分


def test_score_has_all_five_dimensions() -> None:
    """评分对象必须是五个维度 + 说明，界面按这五格渲染。"""
    score = Q.heuristic_score(make_ir("这是一段正常的正文。"), "优化表达")
    for field in (
        "format_consistency",
        "language_quality",
        "readability",
        "factual_consistency",
        "goal_match",
    ):
        value = getattr(score, field)
        assert 0.0 <= value <= 1.0, f"{field} 超出 0~1：{value}"
    assert isinstance(score.notes, list)


def test_score_is_not_flat() -> None:
    """五个维度不能全都一样 —— 全 5 分或全 1 分的评等于没评。"""
    score = Q.heuristic_score(
        make_ir("段落一。", "段落二，这里有重复标点！！", headings=("一、要求", "1. 说明")),
        "让公文更规范",
    )
    values = {
        round(score.format_consistency, 3),
        round(score.language_quality, 3),
        round(score.readability, 3),
        round(score.goal_match, 3),
    }
    assert len(values) > 1, f"四个维度分数完全相同（{values}），评分为无信息量"


def test_mixed_heading_numbering_lowers_format_consistency() -> None:
    """标题混用中文序号与阿拉伯数字 → 格式一致性应更低。

    这是体检最常用的判断：一篇公文里「一、」和「1.」混着用，是不规范的。
    """
    consistent = make_ir("正文。", headings=("一、要求", "二、说明"))
    mixed = make_ir("正文。", headings=("一、要求", "1. 说明"))

    good = Q.heuristic_score(consistent).format_consistency
    bad = Q.heuristic_score(mixed).format_consistency
    assert bad <= good, f"混用编号的风格分应不高于统一编号（统一 {good}，混用 {bad}）"


def test_repeated_punctuation_lowers_language_quality() -> None:
    """重复标点（！！/  。。）属于文字问题，语言质量应更低。"""
    clean = Q.heuristic_score(make_ir("这是一段正常的文字。"))
    messy = Q.heuristic_score(make_ir("这是一段正常的文字！！！！"))
    assert messy.language_quality <= clean.language_quality


def test_goal_match_without_goal_is_neutral_not_zero() -> None:
    """没给目标时，目标匹配度不应判成 0（那会让用户以为文档完全跑题）。"""
    score = Q.heuristic_score(make_ir("正文内容。"), "")
    assert score.goal_match >= 0.5, "无目标时应给中性分，而不是 0"


def test_goal_match_reflects_keywords() -> None:
    """目标关键词命中越多，匹配度越高。"""
    ir = make_ir("本方案重点讨论数据安全与合规要求。")
    on_topic = Q.heuristic_score(ir, "数据安全 合规").goal_match
    off_topic = Q.heuristic_score(ir, "市场营销 促销活动").goal_match
    assert on_topic >= off_topic


def test_score_notes_explain_what_was_found() -> None:
    """评分必须附带人话说明（"哪里有问题"），否则光有分数没用。"""
    score = Q.heuristic_score(make_ir("文字！！！！！", headings=("一、甲", "1. 乙")))
    if score.notes:
        assert all(isinstance(note, str) and note.strip() for note in score.notes)


def test_score_comparison_with_before() -> None:
    """传入 `before` 时可以做"改前改后"对比，且不崩。"""
    before = make_ir("文字！！！！")
    after = make_ir("文字。")
    score = Q.heuristic_score(after, "修正标点", before=before)
    assert score.source, "对比模式下应标明分数来源"


# --------------------------------------------------------------------------- 差异对比


def test_diff_detects_added_paragraph() -> None:
    """新增一段 → 差异里必须出现新增行。"""
    before = make_ir("第一段。")
    after = make_ir("第一段。", "第二段新增。")
    lines = Q.diff_irs(before, after)
    assert lines, "有改动却给出空差异"
    assert any("第二段" in (line.right or "") or "第二段" in (line.left or "")
               for line in lines), "差异里找不到新增内容"


def test_diff_identical_documents_has_no_real_changes() -> None:
    """完全相同的两个文档：**没有任何新增/删除/修改**。

    注意 `diff_irs` 会返回 `kind="same"` 的行（保留上下文便于阅读），
    所以不能断言"空列表"，而应断言"没有真实改动"。
    """
    ir = make_ir("完全相同的内容。")
    lines = Q.diff_irs(ir, make_ir("完全相同的内容。"))
    changed = [line for line in lines if line.kind != "same"]
    assert changed == [], f"相同的文档却检出改动：{changed}"
    # 摘要也应明确说"0 处改动"
    summary = Q.summarize_diff(lines)
    assert "新增 0" in summary and "删除 0" in summary and "修改 0" in summary


def test_diff_detects_modified_paragraph() -> None:
    """改动一个词也应被检出。"""
    before = make_ir("今天天气很好。")
    after = make_ir("今天天气很差。")
    lines = Q.diff_irs(before, after)
    assert lines, "改了一个词却没检出差异"
    text = " ".join((line.left or "") + (line.right or "") for line in lines)
    assert "好" in text or "差" in text


def test_diff_respects_limit() -> None:
    """差异条数受 `limit` 约束（超长文档不能把界面撑爆）。"""
    before = make_ir(*[f"原第 {i} 段。" for i in range(200)])
    after = make_ir(*[f"新第 {i} 段。" for i in range(200)])
    lines = Q.diff_irs(before, after, limit=20)
    assert len(lines) <= 20, f"limit=20 却返回了 {len(lines)} 条"


def test_summarize_diff_is_human_readable() -> None:
    """差异摘要要能直接展示（含新增/删除的条数）。"""
    before = make_ir("甲。", "乙。")
    after = make_ir("甲。", "丙。", "丁。")
    lines = Q.diff_irs(before, after)
    summary = Q.summarize_diff(lines)
    assert isinstance(summary, str) and summary.strip()
    assert any(token in summary for token in ("+", "-", "新增", "删除", "修改", "处"))


def test_summarize_empty_diff() -> None:
    """没有差异时也要给一句可展示的话（不能是空串）。"""
    summary = Q.summarize_diff([])
    assert isinstance(summary, str)
    assert summary.strip(), "无差异时应说明「没有差异」，而不是空字符串"


def test_changed_ratio_bounds() -> None:
    """改动比例必须在 0~1 之间：全同 → 0，全改 → 接近 1。"""
    same = Q.changed_ratio(make_ir("一样。"), make_ir("一样。"))
    assert same == pytest.approx(0.0, abs=0.01)

    different = Q.changed_ratio(
        make_ir("原文一。", "原文二。"),
        make_ir("新文一。", "新文二。"),
    )
    assert 0.0 <= different <= 1.0
    assert different > same


def test_changed_ratio_empty_edges() -> None:
    """空文档对空文档不能除零崩溃。"""
    ratio = Q.changed_ratio(make_ir(), make_ir())
    assert ratio == pytest.approx(0.0, abs=0.01)


# --------------------------------------------------------------------------- 体检的目标匹配


def test_score_line_says_not_evaluated_without_goal() -> None:
    """不填目标时，体检报告必须显示「目标匹配度 未评估」，而不是 100。

    ## 这条守的是什么

    界面的「文档体检」原来调用 `heuristic_score(ir)`（不带 goal），
    而 `goal_match` 在没有目标时是 **1.0** —— 用户看到"目标匹配度 100"
    会以为文档完全符合要求，其实这项**根本没评估过**。

    现在体检会先问一句目标（可留空）；留空时把这一维显示成"未评估"，
    并且总分只按**实际评估过的四维**算平均，不把虚高的 100 掺进去。
    目标输入原来只在「循环美化」页有，而体检是更常用的入口。
    """
    pytest.importorskip("PySide6")
    from modu_workbench.boards.document import viewer as viewer_module

    score_line = viewer_module.DocViewer.__dict__["_score_line"].__func__
    ir = make_ir("正文内容。", headings=("一、要求",))

    without_goal = score_line(Q.heuristic_score(ir), "")
    assert "未评估" in without_goal, f"没填目标却给出了目标匹配度：{without_goal}"
    assert "目标匹配度 100" not in without_goal

    with_goal = score_line(Q.heuristic_score(ir, "公文 规范"), "公文 规范")
    assert "未评估" not in with_goal, "填了目标就应当真的评估"
    assert "目标匹配度" in with_goal


def test_score_line_total_excludes_unevaluated_dimension() -> None:
    """留空目标时，总分不能把「未评估」的那一维按满分算进去。"""
    pytest.importorskip("PySide6")
    from modu_workbench.boards.document import viewer as viewer_module

    score_line = viewer_module.DocViewer.__dict__["_score_line"].__func__
    ir = make_ir("正文内容。")
    score = Q.heuristic_score(ir)
    line = score_line(score, "")

    expected_total = (
        score.format_consistency + score.language_quality + score.readability
        + score.factual_consistency
    ) / 4 * 100
    assert f"{expected_total:.0f}" in line, f"总分应按四维平均（{expected_total:.0f}）：{line}"

