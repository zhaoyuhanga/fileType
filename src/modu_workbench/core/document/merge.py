"""墨软文档：两个文档合并（MR-DOC-304，P0 核心能力）。

分三层（对应 PRD 的"文档解析层 / AI 语义层 / 工具执行层"）：

- **解析层**：两个文档各自解析成 `DocumentIR`（`parser.py`）；
- **语义层**：术语统一、语义去重、过渡段生成、摘要 —— 有 AI 就调用 AI，
  没有 AI 就用本地规则（相似度去重、模板过渡句、抽取式摘要），所以**离线也能合并**；
- **执行层**：块拼接 / 章节插入 / 表格按行·列·主键合并 / 主从合并 / 对比合并，
  最后由 `writer.py` 写成真实文件。

合并模式（`models.MERGE_MODES`）：追加、章节、表格按行、表格按列、表格按主键、
主从、对比。七种模式都会产出 `MergeReport`（差异报告 + 摘要 + 冲突处理说明），
并且**标注哪些内容由 AI 生成或修改**（验收标准第 5 条）。
"""
from __future__ import annotations

import difflib
import re
from typing import Callable, Optional, Sequence

from . import quality
from .beautify import beautify, build_toc, template_options
from .models import (
    BLOCK_HEADING,
    BLOCK_PAGE_BREAK,
    BLOCK_PARAGRAPH,
    BLOCK_TABLE,
    MERGE_DIFF,
    MERGE_MODE_LABELS,
    MERGE_SECTION,
    MERGE_SLAVE,
    MERGE_TABLE_COLUMNS,
    MERGE_TABLE_ROWS,
    TABLE_MERGE_MODES,
    Block,
    Change,
    DiffLine,
    DocumentIR,
    MergeOptions,
    MergeReport,
    MergeResult,
    REVISION_INSERTED,
    REVISION_MODIFIED,
    TableData,
)

#: 过渡段模板（没有 AI 时用它生成章节衔接）
TRANSITION_TEMPLATE = "以下内容来自《{title}》。"
#: 主从合并时的附件标题模板
APPENDIX_TEMPLATE = "附件：{title}"

#: 引用标记（章节冲突扫描用）
_CITATION_RE = re.compile(r"[\[【]\s*(\d{1,3})\s*[\]】]")


# ---------------------------------------------------------------- 入口


def merge_documents(main: DocumentIR, other: DocumentIR, options: MergeOptions | None = None, *,
                    chat: Optional[Callable[[list[dict]], str]] = None) -> MergeResult:
    """把 `other` 合并进 `main`，返回（合并后的 IR, 差异报告）。

    `chat` 是大模型对话回调（`lambda messages: text`）：给了就走 AI 语义增强
    （术语表推断由调用方提供、过渡段与摘要由模型生成），不给就用本地规则。
    """
    options = options or MergeOptions()
    merged = main.clone()
    report = MergeReport(
        mode=options.mode,
        mode_label=MERGE_MODE_LABELS.get(options.mode, options.mode),
        main_title=main.title or main.path,
        other_title=other.title or other.path,
        main_path=main.path,
        other_path=other.path,
        target_format=options.target_format or main.format_key,
        ai_used=bool(chat),
    )

    if options.mode in TABLE_MERGE_MODES:
        _merge_tables(merged, other, options, report, chat)
    elif options.mode == MERGE_SECTION:
        _merge_section(merged, other, options, report, chat)
    elif options.mode == MERGE_SLAVE:
        _merge_slave(merged, other, options, report)
    elif options.mode == MERGE_DIFF:
        _merge_diff(merged, other, options, report, main)
    else:
        _merge_append(merged, other, options, report, chat)

    if options.unify_terms and options.term_map:
        _hits, changes = unify_terms(merged, options.term_map)
        report.changes.extend(changes)

    if options.dedupe:
        removed, changes = dedupe_blocks(merged, options.dedupe_threshold,
                                         mark_ai=options.mark_ai)
        report.deduped = removed
        report.changes.extend(changes)

    # 合并过程中的冲突（找不到章节、表格模式退化为追加…）要保留，
    # 只追加"两稿之间的风格/编号/元数据冲突"
    report.conflicts.extend(conflict_scan(main, other, options))

    if options.unify_style:
        # 合并选项里的"生成目录"必须真正生效：模板默认值（如"通用"）本身不含目录，
        # 直接 `beautify(template=...)` 会把 add_toc 吞掉
        base = template_options(options.template)
        base.template = options.template
        base.build_toc = bool(options.add_toc)
        base.toc_depth = int(options.toc_depth or 3)
        base.beautify_tables = True
        result = beautify(merged, base)
        merged = result.ir
        report.changes.extend(result.changes)
    elif options.add_toc:
        _entries, toc_changes = build_toc(merged, BeautifyOptions(toc_depth=options.toc_depth))
        report.changes.extend(toc_changes)

    if options.add_summary:
        report.summary = build_summary(merged, chat=chat)
        if report.summary:
            report.changes.append(Change(
                "合并摘要", "文档开头", report.summary.splitlines()[0][:60],
                ai_generated=bool(chat)))

    report.diff = quality.diff_irs(main, merged)
    merged.metadata["merged_from"] = [main.path or main.title, other.path or other.title]
    merged.metadata["merge_mode"] = options.mode
    if not merged.title:
        merged.title = main.title or "合并结果"
    return MergeResult(ir=merged, report=report)


# ---------------------------------------------------------------- 块级合并


def _paragraph(text: str, *, ai: bool = False, revision: str = "") -> Block:
    return Block(kind=BLOCK_PARAGRAPH, text=text, ai_generated=ai, revision=revision)


def _mark_blocks(blocks: Sequence[Block], *, ai: bool) -> list[Block]:
    marked: list[Block] = []
    for block in blocks:
        clone = block.clone()
        clone.revision = REVISION_INSERTED
        if ai:
            clone.ai_generated = True
        marked.append(clone)
    return marked


def _transition(other: DocumentIR, options: MergeOptions,
                chat: Optional[Callable[[list[dict]], str]]) -> Block:
    """过渡段：能用 AI 就用 AI 写一句衔接，否则用模板句。"""
    title = other.title or other.path or "副文档"
    if chat is not None:
        try:
            text = chat([
                {"role": "system", "content": "你是中文排版助手，只输出一句 30 字以内的过渡句。"},
                {"role": "user", "content": f"主文档《{{main}}》后面要接《{title}》，写一句过渡句。"},
            ]).strip()
            if text:
                return _paragraph(text, ai=True, revision=REVISION_INSERTED)
        except Exception:  # noqa: BLE001  AI 失败就退回模板句，合并不能因此中断
            pass
    return _paragraph(TRANSITION_TEMPLATE.format(title=title), ai=False, revision=REVISION_INSERTED)


def _merge_append(main: DocumentIR, other: DocumentIR, options: MergeOptions,
                  report: MergeReport,
                  chat: Optional[Callable[[list[dict]], str]] = None) -> None:
    if options.keep_revisions and options.add_transition:
        transition = _transition(other, options, chat)
        main.blocks.append(transition)
        report.changes.append(Change(
            "过渡段", "副文档前", transition.text,
            ai_generated=transition.ai_generated))
    main.blocks.append(Block(kind=BLOCK_PAGE_BREAK))
    if other.title and not any(block.text.strip() == other.title for block in main.headings()):
        main.blocks.append(Block(kind=BLOCK_HEADING, text=other.title, level=1,
                                 revision=REVISION_INSERTED))
    main.blocks.extend(_mark_blocks(other.blocks, ai=False))
    report.changes.append(Change(
        "追加合并", "主文档末尾",
        f"已追加 {len(other.blocks)} 个块（来自《{other.title or other.path}》）"))


def _heading_level(block: Block) -> int:
    return max(1, min(6, block.level or 1))


def _merge_section(main: DocumentIR, other: DocumentIR, options: MergeOptions,
                   report: MergeReport,
                   chat: Optional[Callable[[list[dict]], str]] = None) -> None:
    """章节合并：插到指定标题所在章节的末尾；找不到标题就追加并记冲突。"""
    title = (options.section_title or "").strip()
    insert_at = -1
    level = 1
    if title:
        for index, block in enumerate(main.blocks):
            if block.is_heading and title.lower() in block.text.strip().lower():
                insert_at = index
                level = _heading_level(block)
                break
    if insert_at < 0:
        report.conflicts.append(
            f"没有找到章节「{title}」：已改为追加到文档末尾（可先在主文档里补一个该标题）"
            if title else "没有指定章节标题：已追加到文档末尾")
        _merge_append(main, other, options, report, chat)
        return
    # 章节末尾 = 下一个同级或更高级标题之前
    end = len(main.blocks)
    for index in range(insert_at + 1, len(main.blocks)):
        block = main.blocks[index]
        if block.is_heading and _heading_level(block) <= level:
            end = index
            break
    inserted: list[Block] = []
    if options.add_transition:
        inserted.append(_transition(other, options, chat))
    if other.title:
        inserted.append(Block(kind=BLOCK_HEADING, text=other.title, level=min(6, level + 1),
                              revision=REVISION_INSERTED))
    inserted.extend(_mark_blocks(other.blocks, ai=False))
    for offset, block in enumerate(inserted):
        main.blocks.insert(end + offset, block)
    report.changes.append(Change(
        "章节合并", f"章节「{title}」末尾",
        f"已插入 {len(inserted)} 个块（来自《{other.title or other.path}》）"))


def _merge_slave(main: DocumentIR, other: DocumentIR, options: MergeOptions,
                 report: MergeReport) -> None:
    main.blocks.append(Block(kind=BLOCK_PAGE_BREAK))
    main.blocks.append(Block(
        kind=BLOCK_HEADING, text=APPENDIX_TEMPLATE.format(title=other.title or other.path),
        level=1, revision=REVISION_INSERTED))
    main.blocks.extend(_mark_blocks(other.blocks, ai=False))
    report.changes.append(Change(
        "主从合并", "附件区",
        f"副文档《{other.title or other.path}》已作为附件并入（{len(other.blocks)} 个块）"))


def _merge_diff(main: DocumentIR, other: DocumentIR, options: MergeOptions,
                report: MergeReport, original: DocumentIR) -> None:
    """对比合并：主文档保留，副文档的差异逐条插入并标注为修订。"""
    lines = quality.diff_irs(original, other)
    merged_blocks: list[Block] = []
    left = list(original.blocks)
    left_index = 0
    added = 0
    for line in lines:
        if line.kind == "same":
            if left_index < len(left):
                merged_blocks.append(left[left_index].clone())
                left_index += 1
            continue
        if line.kind == "removed":
            if left_index < len(left):
                clone = left[left_index].clone()
                clone.revision = REVISION_MODIFIED
                clone.meta["review"] = "副文档已删除此段"
                merged_blocks.append(clone)
                left_index += 1
            continue
        text = line.right or ""
        if not text.strip():
            continue
        prefix = "【修订】" if line.kind == "changed" else "【新增】"
        block = Block(kind=BLOCK_PARAGRAPH, text=f"{prefix}{text}",
                      revision=REVISION_INSERTED, ai_generated=False,
                      meta={"diff": line.kind, "before": line.left})
        if line.kind == "changed" and line.left:
            block.meta["before"] = line.left
        merged_blocks.append(block)
        added += 1
    main.blocks = merged_blocks
    report.changes.append(Change(
        "对比合并", "整篇",
        f"保留主文档正文，插入 {added} 处副文档差异（标注为【新增】/【修订】）"))


# ---------------------------------------------------------------- 表格合并


def _merge_tables(main: DocumentIR, other: DocumentIR, options: MergeOptions,
                  report: MergeReport,
                  chat: Optional[Callable[[list[dict]], str]] = None) -> None:
    main_table = main.first_table()
    other_table = other.first_table()
    if main_table is None or other_table is None:
        report.conflicts.append(
            "表格合并需要两边都有表格：已退化为追加合并"
            + ("（主文档没有表格）" if main_table is None else "（副文档没有表格）"))
        _merge_append(main, other, options, report, chat)
        return

    if options.mode == MERGE_TABLE_ROWS:
        merged, notes = merge_table_rows(main_table, other_table)
        label = "表格按行合并"
    elif options.mode == MERGE_TABLE_COLUMNS:
        merged, notes = merge_table_columns(main_table, other_table)
        label = "表格按列合并"
    else:
        merged, notes = merge_table_key(main_table, other_table, options.key_column)
        label = "表格按主键合并"

    replaced = False
    for index, block in enumerate(main.blocks):
        if block.kind == BLOCK_TABLE and block.table is not None:
            main.blocks[index] = Block(kind=BLOCK_TABLE, table=merged,
                                       style=dict(block.style), meta=dict(block.meta))
            replaced = True
            break
    if not replaced:
        main.blocks.append(Block(kind=BLOCK_TABLE, table=merged))
    for note in notes:
        report.changes.append(Change(label, f"表格「{merged.name or '未命名'}」", note))
    if other.blocks and len(other.tables()) > 1:
        report.conflicts.append(
            f"副文档有 {len(other.tables())} 张表，表格合并只处理了第一张"
            "（其余内容未并入，可用「追加合并」保留）")


def merge_table_rows(main: TableData, other: TableData) -> tuple[TableData, list[str]]:
    """按行合并：追加副表数据行，跳过与主表重复的行。"""
    merged = main.clone()
    notes: list[str] = []
    header = [cell.strip() for cell in merged.header_row()]
    other_header = [cell.strip() for cell in other.header_row()]
    if merged.header and other.header and header != other_header:
        notes.append(
            "两表表头不一致（" + "｜".join(header[:4]) + " vs " + "｜".join(other_header[:4])
            + "）：已按列位置强行对齐，请核对")
    existing = {tuple(cell.strip() for cell in row) for row in merged.rows}
    added = 0
    skipped = 0
    start = 1 if (other.header and len(other.rows) > 1) else 0
    for row in other.rows[start:]:
        padded = list(row) + [""] * (merged.width - len(row))
        key = tuple(cell.strip() for cell in padded[:merged.width])
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        merged.rows.append(padded)
        added += 1
    notes.append(f"追加 {added} 行" + (f"，跳过重复 {skipped} 行" if skipped else ""))
    return merged, notes


def merge_table_columns(main: TableData, other: TableData) -> tuple[TableData, list[str]]:
    """按列合并：把副表的列接到主表右侧（按行号对齐）。"""
    merged = main.clone()
    notes: list[str] = []
    main_width = main.width
    left = main.normalized()
    right = other.normalized()
    height = max(len(left), len(right))
    output: list[list[str]] = []
    for index in range(height):
        left_row = left[index] if index < len(left) else [""] * main_width
        right_row = right[index] if index < len(right) else [""] * other.width
        output.append(list(left_row) + list(right_row))
    merged.rows = output
    notes.append(f"新增 {other.width} 列（合计 {main_width + other.width} 列），按行号对齐")
    if len(left) != len(right):
        notes.append(f"两表行数不同（主 {len(left)} 行 / 副 {len(right)} 行）：不足部分补空")
    return merged, notes


def merge_table_key(main: TableData, other: TableData, key_column: str) -> tuple[TableData, list[str]]:
    """按主键合并：主键对齐，用副表补全主表空缺、并追加主表没有的主键行。"""
    merged = main.clone()
    notes: list[str] = []
    key = (key_column or "").strip()
    if not key:
        notes.append("没有指定主键列：已退化为按行追加（请指定主键以获得对齐效果）")
        merged_rows, extra = merge_table_rows(main, other)
        return merged_rows, notes + extra
    key_index = main.column_index(key)
    if key_index < 0:
        other_key = other.column_index(key)
        if other_key >= 0:
            notes.append(f"主表没有「{key}」列，副表有：已按副表列位置对齐")
            key_index = other_key
        else:
            notes.append(f"两表都没有「{key}」列：已退化为按列合并")
            return merge_table_columns(main, other)
    other_key = other.column_index(key)
    if other_key < 0:
        notes.append(f"副表没有「{key}」列：已退化为按行追加")
        merged_rows, extra = merge_table_rows(main, other)
        return merged_rows, notes + extra

    if not merged.header:
        merged.rows.insert(0, [f"列{index + 1}" for index in range(merged.width)])
        merged.header = True
    header = [cell.strip() for cell in merged.header_row()]
    other_header = [cell.strip() for cell in other.header_row()]
    offset = len(header)
    # 副表独有的列，接到主表右侧
    for index, name in enumerate(other_header):
        if name and name not in header:
            header.append(name)
            for row in merged.rows:
                row.append("")
    merged.rows[0] = header
    column_map: dict[str, int] = {}
    for index, name in enumerate(header):
        column_map.setdefault(name, index)

    def row_key(row: Sequence[str]) -> str:
        return (row[key_index] if key_index < len(row) else "").strip()

    index_by_key = {row_key(row): position for position, row in enumerate(merged.rows[1:], start=1)}
    filled = 0
    appended = 0
    start = 1 if (other.header and len(other.rows) > 1) else 0
    for row in other.rows[start:]:
        target = index_by_key.get(row_key(row))
        if target is None:
            new_row = [""] * len(header)
            for column, value in enumerate(row):
                name = other_header[column] if column < len(other_header) else ""
                position = column_map.get(name, column)
                if position < len(new_row):
                    new_row[position] = value
            merged.rows.append(new_row)
            index_by_key[row_key(new_row)] = len(merged.rows) - 1
            appended += 1
            continue
        current = merged.rows[target]
        for column, value in enumerate(row):
            name = other_header[column] if column < len(other_header) else ""
            position = column_map.get(name, column)
            if position < len(current) and not current[position].strip() and value.strip():
                current[position] = value
                filled += 1
    notes.append(f"按主键「{key}」补齐 {filled} 个空单元格，新增 {appended} 行")
    return merged, notes


# ---------------------------------------------------------------- 语义增强


def unify_terms(ir: DocumentIR, mapping: dict[str, str]) -> tuple[int, list[Change]]:
    """术语统一：把别名替换成标准名（人名/产品名/缩写/单位）。"""
    hits = 0
    changes: list[Change] = []
    for old, new in mapping.items():
        if not old or old == new:
            continue
        count = ir.replace_text(old, new)
        if count:
            hits += count
            changes.append(Change(
                "术语统一", f"全文", f"「{old}」→「{new}」（{count} 处）", ai_generated=False))
    return hits, changes


def dedupe_blocks(ir: DocumentIR, threshold: float = 0.9, *,
                  mark_ai: bool = True) -> tuple[int, list[Change]]:
    """语义去重：删除重复段落（先用归一化精确匹配，再用相似度）。

    只处理正文/列表/引用块，标题与表格保留（标题重复往往是有意为之，
    表格去重交给表格合并里的按行比对）。
    """
    changes: list[Change] = []
    kept: list[Block] = []
    normalized: list[str] = []
    removed = 0
    for block in ir.blocks:
        if block.kind not in (BLOCK_PARAGRAPH,):
            kept.append(block)
            continue
        text = re.sub(r"\s+", "", block.text or "")
        if len(text) < 8:
            kept.append(block)
            continue
        duplicate = ""
        if text in normalized:
            duplicate = text
        else:
            for existing in normalized:
                if difflib.SequenceMatcher(a=text, b=existing).ratio() >= threshold:
                    duplicate = existing
                    break
        if duplicate:
            removed += 1
            changes.append(Change(
                "语义去重", f"段落「{block.text.strip()[:20]}」",
                "与前面的段落重复，已删除", ai_generated=mark_ai,
                before=block.text, after=""))
            continue
        normalized.append(text)
        kept.append(block)
    if removed:
        ir.blocks = kept
    return removed, changes


def build_summary(ir: DocumentIR, *, chat: Optional[Callable[[list[dict]], str]] = None,
                  max_items: int = 8) -> str:
    """合并摘要：有 AI 用 AI，没有就用抽取式（每章首句）。"""
    text = ir.text(include_tables=False)
    if not text.strip():
        return ""
    if chat is not None:
        try:
            reply = chat([
                {"role": "system",
                 "content": "你是文档摘要助手：输出 5 条以内的中文要点，每条一行，不要客套话。"},
                {"role": "user", "content": text[:6000]},
            ]).strip()
            if reply:
                return reply
        except Exception:  # noqa: BLE001  摘要失败不影响合并结果
            pass
    return extractive_summary(ir, max_items=max_items)


def extractive_summary(ir: DocumentIR, *, max_items: int = 8) -> str:
    """抽取式摘要：每个标题下的第一句 + 文档首段。"""
    lines: list[str] = []
    for index, block in enumerate(ir.blocks):
        if block.kind == BLOCK_HEADING:
            following = ""
            for candidate in ir.blocks[index + 1:]:
                if candidate.kind == BLOCK_PARAGRAPH and candidate.text.strip():
                    following = _first_sentence(candidate.text)
                    break
                if candidate.kind == BLOCK_HEADING:
                    break
            lines.append(f"{block.text.strip()}：{following}".rstrip("："))
        elif not lines and block.kind == BLOCK_PARAGRAPH and block.text.strip():
            lines.append(_first_sentence(block.text))
        if len(lines) >= max_items:
            break
    return "\n".join(f"{index}. {line}" for index, line in enumerate(
        [item for item in lines if item.strip()], start=1))


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[。！？!?；;])", text.strip())
    return (parts[0] if parts else text.strip())[:80]


def conflict_scan(main: DocumentIR, other: DocumentIR,
                  options: MergeOptions | None = None) -> list[str]:
    """扫描四类冲突：样式、编号、引用、元数据；并说明合并时怎么处理的。"""
    options = options or MergeOptions()
    conflicts: list[str] = []
    main_style = main.styles or {}
    other_style = other.styles or {}
    if main_style.get("font_cn") and other_style.get("font_cn") \
            and main_style["font_cn"] != other_style["font_cn"]:
        conflicts.append(
            f"样式冲突：字体 {main_style['font_cn']} vs {other_style['font_cn']}"
            + ("（已按主文档模板统一）" if options.unify_style else "（未统一，建议开启「风格统一」）"))
    if main_style.get("body_size") and other_style.get("body_size") \
            and main_style["body_size"] != other_style["body_size"]:
        conflicts.append(
            f"字号冲突：{main_style['body_size']:g}pt vs {other_style['body_size']:g}pt"
            + ("（已统一）" if options.unify_style else ""))
    main_numbers = {quality.heading_numbering_style(block.text) for block in main.headings()}
    other_numbers = {quality.heading_numbering_style(block.text) for block in other.headings()}
    if (main_numbers - {"none"}) and (other_numbers - {"none"}) \
            and main_numbers != other_numbers:
        conflicts.append("编号冲突：两边标题编号风格不同，合并后已整体重排（开启「自动编号」）"
                         if options.unify_style else "编号冲突：两边标题编号风格不同，建议开启自动编号")
    main_citations = set(_CITATION_RE.findall(main.text()))
    other_citations = set(_CITATION_RE.findall(other.text()))
    if main_citations and other_citations and main_citations != other_citations:
        conflicts.append(
            f"引用冲突：引用编号不重叠（主 {len(main_citations)} 个 / 副 {len(other_citations)} 个），"
            "合并后请核对参考文献编号")
    if main.title and other.title and main.title != other.title:
        conflicts.append(f"元数据冲突：标题不同（已保留主文档《{main.title}》，副标题记为《{other.title}》）")
    return conflicts


# ---------------------------------------------------------------- 便捷入口


def diff_documents(left: DocumentIR, right: DocumentIR, *, limit: int = 400) -> list[DiffLine]:
    """两稿对比（界面「差异报告」直接用）。"""
    return quality.diff_irs(left, right, limit=limit)


def merge_preview_lines(result: MergeResult, *, limit: int = 40) -> list[str]:
    """给界面用的预览文本（报告 + 差异摘要）。"""
    lines = result.report.report_lines()
    if result.report.diff:
        lines.append("差异摘要：" + quality.summarize_diff(result.report.diff))
        lines.append("逐条差异（前 %d 条）：" % limit)
        lines.extend(f"  {line.render()}" for line in result.report.diff[:limit])
    return lines


def capability_text() -> str:
    return (
        "支持同格式与跨格式合并（Word/PDF/Excel/Markdown/TXT/HTML…），"
        "七种合并方式：追加、章节、表格按行、表格按列、表格按主键、主从、对比；"
        "并带语义去重、术语统一、风格统一、过渡段、目录、摘要、差异报告与版本快照。"
    )


__all__ = [
    "APPENDIX_TEMPLATE",
    "TRANSITION_TEMPLATE",
    "build_summary",
    "capability_text",
    "conflict_scan",
    "dedupe_blocks",
    "diff_documents",
    "extractive_summary",
    "merge_documents",
    "merge_preview_lines",
    "merge_table_columns",
    "merge_table_key",
    "merge_table_rows",
    "unify_terms",
]
