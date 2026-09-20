"""墨软文档：文档分析与质量评估（MR-DOC-305 第 2、5 步）。

两件事：
1. `analyze_document(ir)` —— 结构/语言/表格体检，产出可展示的问题清单，
   循环美化引擎用它生成"下一步做什么"的计划，界面用它做"文档体检"面板；
2. `heuristic_score(ir, goal)` —— **本地规则**的质量评分（格式一致性 / 语言质量 /
   可读性 / 事实一致性 / 目标匹配度）。AI 可用时由 `ai.evaluate` 覆盖，
   AI 不可用时评分仍然工作 —— 这是"离线也能用"的底线。
"""
from __future__ import annotations

import difflib
import re
from typing import Iterable, Optional

from .models import (
    BLOCK_CODE,
    BLOCK_HEADING,
    BLOCK_PARAGRAPH,
    BLOCK_TABLE,
    DiffLine,
    DocumentIR,
    QualityScore,
)

#: 中文正文里的英文标点（常见于复制粘贴），逐个给出提示
_ASCII_PUNCT_IN_CN = re.compile(r"[\u4e00-\u9fff][,;:!?]|[,;:!?][\u4e00-\u9fff]")
_REPEATED_PUNCT = re.compile(r"[，。！？；：]{2,}")
_SPACES_AROUND_CN = re.compile(r"[\u4e00-\u9fff]\s+[\u4e00-\u9fff]")
_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*%?")
_GOAL_STOPWORDS = {
    "把", "的", "了", "和", "与", "并", "为", "成", "这份", "文档", "内容", "进行", "一下",
    "请", "让", "文档", "优化", "美化", "统一", "生成", "整理", "一下", "更加", "更",
}


def _text_blocks(ir: DocumentIR) -> list[str]:
    return [block.text.strip() for block in ir.blocks
            if block.text and block.kind != BLOCK_TABLE and block.kind != BLOCK_CODE]


def _language_of(text: str) -> str:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if cjk == 0 and latin == 0:
        return "none"
    if cjk >= latin:
        return "zh" if cjk else "en"
    return "en"


def extract_numbers(text: str) -> list[str]:
    """抽取文本里的数字（用于事实一致性：改文不该改数字）。"""
    return [item.rstrip(".") for item in _NUMBER_RE.findall(text or "")]


def heading_numbering_style(text: str) -> str:
    """判断标题的编号风格：arabic / chinese / legal / none。"""
    stripped = (text or "").strip()
    if re.match(r"^第[一二三四五六七八九十百千零〇\d]+[章篇部]", stripped):
        return "chinese"
    if re.match(r"^第[一二三四五六七八九十百千零〇\d]+条", stripped):
        return "legal"
    if re.match(r"^\d+(\.\d+)*[\.、]?\s", stripped):
        return "arabic"
    if re.match(r"^[一二三四五六七八九十]+、", stripped):
        return "chinese"
    return "none"


def analyze_document(ir: DocumentIR) -> dict:
    """文档体检：结构 + 语言 + 表格，返回可直接展示的字典。"""
    headings = ir.headings()
    paragraphs = [block for block in ir.blocks if block.kind == BLOCK_PARAGRAPH and block.text.strip()]
    tables = ir.tables()
    text = ir.text()

    levels = [block.level or 1 for block in headings]
    level_counts: dict[int, int] = {}
    for level in levels:
        level_counts[level] = level_counts.get(level, 0) + 1

    issues: list[str] = []
    # 1) 标题层级跳级（1 → 3 这类，导出 Word 后目录会错乱）
    jumps = [
        f"第 {index + 1} 个标题从 {levels[index - 1]} 级跳到 {levels[index]} 级"
        for index in range(1, len(levels)) if levels[index] - levels[index - 1] > 1
    ]
    if jumps:
        issues.append("标题层级有跳级：" + "；".join(jumps[:3]))

    # 2) 编号风格不统一
    styles = {heading_numbering_style(block.text) for block in headings}
    numbered = styles - {"none"}
    if len(numbered) > 1:
        issues.append("标题编号风格不统一（同时存在 " + "、".join(sorted(numbered)) + "）")
    if not headings:
        issues.append("没有识别到标题：建议补上「第一章 / 一、/ 1.1」这类编号，方便生成目录")

    # 3) 段落过长
    long_paragraphs = [block for block in paragraphs if len(block.text) > 300]
    if long_paragraphs:
        issues.append(f"有 {len(long_paragraphs)} 个段落超过 300 字，建议拆分")

    # 4) 语言问题（中文里的英文标点、重复标点、中文字之间的空格）
    language_hint = _language_of(text)
    punctuation_hits = len(_ASCII_PUNCT_IN_CN.findall(text))
    if language_hint == "zh" and punctuation_hits:
        issues.append(f"中文里混用了英文标点 {punctuation_hits} 处")
    if _REPEATED_PUNCT.search(text):
        issues.append("存在连续重复的标点（如「，，」「。。」）")
    spaced = len(_SPACES_AROUND_CN.findall(text))
    if language_hint == "zh" and spaced > 2:
        issues.append(f"中文字之间存在多余空格 {spaced} 处")

    # 5) 重复段落（复制粘贴常见）
    normalized = [re.sub(r"\s+", "", item) for item in _text_blocks(ir) if len(item) >= 12]
    duplicates = len(normalized) - len(set(normalized))
    if duplicates:
        issues.append(f"发现 {duplicates} 处完全重复的段落")

    # 6) 表格问题
    table_issues: list[str] = []
    for index, table in enumerate(tables, start=1):
        name = table.name or f"表{index}"
        if not table.rows:
            table_issues.append(f"{name}：空表")
            continue
        if not any(cell.strip() for cell in table.header_row()):
            table_issues.append(f"{name}：表头为空")
        width = table.width
        if any(len(row) != width for row in table.rows):
            table_issues.append(f"{name}：各行列数不一致（导出后会出现错位）")
        empty_cells = sum(
            1 for row in table.normalized() for cell in row if not cell.strip())
        if empty_cells > max(4, len(table.rows) * width * 0.3):
            table_issues.append(f"{name}：空单元格较多（{empty_cells} 个）")
    issues.extend(table_issues)

    sentences = [item for item in _SENTENCE_SPLIT.split(text) if item.strip()]
    lengths = [len(item) for item in sentences] or [0]
    return {
        "blocks": len(ir.blocks),
        "headings": len(headings),
        "paragraphs": len(paragraphs),
        "tables": len(tables),
        "level_counts": level_counts,
        "numbering_styles": sorted(styles),
        "language": language_hint,
        "avg_sentence_length": round(sum(lengths) / len(lengths), 1),
        "long_sentences": len([item for item in lengths if item > 60]),
        "long_paragraphs": len(long_paragraphs),
        "duplicate_paragraphs": duplicates,
        "punctuation_issues": punctuation_hits,
        "table_issues": table_issues,
        "empty_cells": sum(
            1 for table in tables for row in table.normalized() for cell in row if not cell.strip()),
        "numbers": extract_numbers(text)[:200],
        "chars": len(text),
        "issues": issues,
    }


def goal_keywords(goal: str) -> list[str]:
    """目标里的关键词（去掉"美化/优化"这类指令词后的实词）。"""
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", goal or "")
    keywords: list[str] = []
    for token in tokens:
        if token in _GOAL_STOPWORDS:
            continue
        if token not in keywords:
            keywords.append(token)
    return keywords


def heuristic_score(ir: DocumentIR, goal: str = "", *,
                    before: Optional[DocumentIR] = None) -> QualityScore:
    """本地规则评分（AI 不可用时的兜底，也是循环引擎的每轮基线）。"""
    analysis = analyze_document(ir)
    notes: list[str] = []
    text = ir.text()
    language = analysis["language"]

    # ---- 格式一致性：标题层级、编号统一、表格规整、样式已统一 ----
    format_score = 1.0
    jump_penalty = 0.12 * len([item for item in analysis["issues"] if "跳级" in item])
    format_score -= jump_penalty
    if len(set(analysis["numbering_styles"]) - {"none"}) > 1:
        format_score -= 0.12
    if analysis["headings"] == 0:
        format_score -= 0.10
    if analysis["table_issues"]:
        format_score -= min(0.24, 0.08 * len(analysis["table_issues"]))
    unstyled = len([block for block in ir.blocks if block.text.strip() and not block.style])
    styled_ratio = 1 - (unstyled / max(1, len(ir.blocks)))
    format_score = 0.6 * format_score + 0.4 * styled_ratio
    if styled_ratio < 0.9:
        notes.append("部分段落还没有统一样式（先做一次「一键美化」）")

    # ---- 语言质量：标点、重复标点、重复段落 ----
    language_score = 1.0
    if language == "zh":
        language_score -= min(0.3, 0.04 * analysis["punctuation_issues"])
    if _REPEATED_PUNCT.search(text):
        language_score -= 0.08
    language_score -= min(0.3, 0.06 * analysis["duplicate_paragraphs"])
    if analysis["duplicate_paragraphs"]:
        notes.append(f"有 {analysis['duplicate_paragraphs']} 处重复段落可去重")

    # ---- 可读性：平均句长、超长句、超长段 ----
    average = float(analysis["avg_sentence_length"] or 0)
    if average <= 0:
        readability = 0.5
    elif average <= 35:
        readability = 1.0 - abs(average - 22) / 100.0
    else:
        readability = max(0.3, 1.0 - (average - 35) / 60.0)
    readability -= min(0.2, 0.05 * analysis["long_sentences"])
    readability -= min(0.2, 0.05 * analysis["long_paragraphs"])
    if analysis["long_sentences"]:
        notes.append(f"{analysis['long_sentences']} 个句子偏长（>60 字），建议断句")

    # ---- 事实一致性：数字不该被改写 ----
    factual = 1.0
    if before is not None:
        left = extract_numbers(before.text())
        right = extract_numbers(text)
        if left:
            changed = len([item for item in left if item not in right])
            factual = max(0.0, 1.0 - changed / max(1, len(left)))
            if changed:
                notes.append(f"有 {changed} 个数字在改写后消失，请核对事实")

    # ---- 目标匹配度：目标关键词覆盖率 ----
    keywords = goal_keywords(goal)
    if not keywords:
        goal_match = 1.0
    else:
        joined = re.sub(r"\s+", "", text) + re.sub(r"\s+", "", ir.title)
        hit = len([keyword for keyword in keywords if keyword in joined])
        goal_match = hit / len(keywords)
        if goal_match < 1.0:
            missing = [keyword for keyword in keywords if keyword not in joined]
            notes.append("目标关键词未覆盖：" + "、".join(missing[:5]))

    return QualityScore(
        format_consistency=_clamp(format_score),
        language_quality=_clamp(language_score),
        readability=_clamp(readability),
        factual_consistency=_clamp(factual),
        goal_match=_clamp(goal_match),
        notes=notes,
        source="heuristic",
    )


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


# ---------------------------------------------------------------- 对比


def _signature(block) -> str:  # noqa: ANN001
    if block.kind == BLOCK_HEADING:
        return f"H{block.level or 1}: {block.text.strip()}"
    if block.kind == BLOCK_TABLE and block.table is not None:
        rows = block.table.normalized()
        head = " / ".join(rows[0]) if rows else ""
        return f"T[{block.table.name}]({len(rows)}行): {head}"
    return f"{block.kind}: {re.sub(r'\\s+', '', block.text)[:200]}"


def diff_irs(before: DocumentIR, after: DocumentIR, *, limit: int = 400) -> list[DiffLine]:
    """结构化 diff（按块）：相同 / 新增 / 删除 / 修改。"""
    left = [_signature(block) for block in before.blocks]
    right = [_signature(block) for block in after.blocks]
    matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
    lines: list[DiffLine] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                if len(lines) < limit:
                    lines.append(DiffLine("same", left[i1 + offset], right[j1 + offset]))
            continue
        if tag == "delete":
            for index in range(i1, i2):
                if len(lines) < limit:
                    lines.append(DiffLine("removed", left[index], "", index))
            continue
        if tag == "insert":
            for index in range(j1, j2):
                if len(lines) < limit:
                    lines.append(DiffLine("added", "", right[index], index))
            continue
        # replace：左右等长时配对成"修改"，否则按删+增展示
        left_count, right_count = i2 - i1, j2 - j1
        if left_count == right_count:
            for offset in range(left_count):
                if len(lines) < limit:
                    lines.append(DiffLine("changed", left[i1 + offset], right[j1 + offset], i1 + offset))
        else:
            for index in range(i1, i2):
                if len(lines) < limit:
                    lines.append(DiffLine("removed", left[index], "", index))
            for index in range(j1, j2):
                if len(lines) < limit:
                    lines.append(DiffLine("added", "", right[index], index))
    return lines


def summarize_diff(lines: Iterable[DiffLine]) -> str:
    counts = {"added": 0, "removed": 0, "changed": 0}
    for line in lines:
        if line.kind in counts:
            counts[line.kind] += 1
    return f"新增 {counts['added']} · 删除 {counts['removed']} · 修改 {counts['changed']}"


def changed_ratio(before: DocumentIR, after: DocumentIR) -> float:
    """变化比例（用于"本轮无可改进项"的停止条件）。"""
    lines = diff_irs(before, after)
    total = max(1, len(lines))
    changed = len([line for line in lines if line.kind != "same"])
    return changed / total


__all__ = [
    "analyze_document",
    "changed_ratio",
    "diff_irs",
    "extract_numbers",
    "goal_keywords",
    "heading_numbering_style",
    "heuristic_score",
    "summarize_diff",
]
