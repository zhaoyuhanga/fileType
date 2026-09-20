"""墨软文档：工具注册表（MR-DOC-303 第 5 条"支持工具调用"）。

这里是"工具执行层"：每个工具都是**真做事**的函数（不是给模型的占位符），
AI 可以通过 JSON 协议调用它们，界面上的按钮走的也是同一套实现 ——
因此"AI 能做的"和"手动能做的"永远一致。

工具覆盖（对应需求点名的能力）：
- 读取 / 解析 / 写入 / 导出（`read_document` / `document_info` / `export_document`）
- 查找替换 / 术语统一 / 标点规范（`find_replace` / `unify_terms` / `normalize_text`）
- 排版与格式（`beautify` / `number_headings` / `build_toc` / `beautify_table`）
- 计算与表格（`compute_formula` / `check_calculation` / `autofill` / `smart_fill` /
  `statistics` / `pivot_table`）
- AI 文本（`polish` / `summarize` / `translate` / `expand` / `condense` / `proofread` / `rewrite`）
- 合并 / 拆分 / 对比（`merge_documents` / `split_document` / `diff_documents`）
- OCR / 脱敏 / 水印（`ocr` / `mask_document` / `set_watermark`）
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from . import merge as merge_engine
from . import pdf_tools
from . import quality
from . import security
from . import sheet as sheet_engine
from . import templates
from . import writer as writer_engine
from .beautify import beautify as apply_beautify
from .beautify import beautify_tables, build_toc, infer_heading_levels, number_headings
from .beautify import template_label, template_options
from .templates import TemplateError, save_template
from .formats import EXPORT_TARGET_KEYS, export_label
from .models import (
    BLOCK_CODE,
    BLOCK_PARAGRAPH,
    BLOCK_TABLE,
    BeautifyOptions,
    Block,
    Change,
    DocumentIR,
    TableData,
    ToolCallRecord,
)
from .parser import ParseError, parse_document

TOOL_CATEGORIES = {
    "read": "读取与解析",
    "write": "写入与导出",
    "edit": "编辑与排版",
    "sheet": "表格与计算",
    "ai": "AI 文本",
    "merge": "合并与拆分",
    "pdf": "PDF 页面操作",
    "secure": "安全与识别",
}


@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: dict = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    label: str
    description: str
    parameters: dict[str, str] = field(default_factory=dict)
    category: str = "edit"
    needs_ai: bool = False
    handler: Optional[Callable[["ToolContext", dict], ToolResult]] = None

    def signature(self) -> str:
        args = ", ".join(f"{key}: {note}" for key, note in self.parameters.items())
        return f"{self.name}({args})"


@dataclass
class ToolContext:
    """工具执行上下文：当前文档 + 依赖（库/AI）+ 输出目录 + 调用日志。"""

    ir: DocumentIR
    library: Any = None
    ai: Any = None
    output_dir: str = ""
    options: BeautifyOptions = field(default_factory=BeautifyOptions)
    watermark: Optional[writer_engine.WatermarkOptions] = None
    document_id: int = 0
    log: list[ToolCallRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    def note(self, message: str) -> None:
        if message:
            self.notes.append(message)


TOOL_REGISTRY: dict[str, ToolSpec] = {}
TOOL_ORDER: list[str] = []


def register_tool(spec: ToolSpec) -> ToolSpec:
    TOOL_REGISTRY[spec.name] = spec
    if spec.name not in TOOL_ORDER:
        TOOL_ORDER.append(spec.name)
    return spec


def tool(name: str) -> Optional[ToolSpec]:
    return TOOL_REGISTRY.get(name)


def tool_names() -> list[str]:
    return list(TOOL_ORDER)


def tools_in_category(category: str) -> list[ToolSpec]:
    return [TOOL_REGISTRY[name] for name in TOOL_ORDER
            if TOOL_REGISTRY[name].category == category]


def catalog_lines(*, only: Sequence[str] | None = None, with_params: bool = True) -> list[str]:
    """工具清单（给 AI 的提示词与界面"可用工具"面板共用）。"""
    names = list(only) if only else tool_names()
    lines: list[str] = []
    for name in names:
        spec = TOOL_REGISTRY.get(name)
        if spec is None:
            continue
        head = f"- {spec.signature() if with_params else spec.name}：{spec.description}"
        if spec.needs_ai:
            head += "（需要已配置大模型）"
        lines.append(head)
    return lines


def call_tool(name: str, context: ToolContext, args: Optional[dict] = None, *,
              round_index: int = 0) -> ToolResult:
    """执行工具并记录调用日志（AI 与界面都走这里）。

    有板块库时会同时写一条审计记录（需求 6.8：工具调用要可追溯）。
    """
    spec = TOOL_REGISTRY.get(name)
    started = time.monotonic()
    if spec is None or spec.handler is None:
        result = ToolResult(False, f"未知工具：{name}（可用：{'、'.join(tool_names())}）")
    else:
        try:
            result = spec.handler(context, dict(args or {}))
        except Exception as error:  # noqa: BLE001  工具失败不能中断整条流程
            result = ToolResult(False, f"{spec.label}失败：{error}")
    duration = int((time.monotonic() - started) * 1000)
    context.log.append(ToolCallRecord(
        tool=name, args=_short_args(args or {}), ok=result.ok, detail=result.summary[:200],
        duration_ms=duration, round_index=round_index))
    if context.library is not None:
        label = spec.label if spec else name
        try:
            context.library.audit("tool", context.ir.title or context.ir.path or name,
                                  f"{label}：{result.summary[:160]}",
                                  actor=f"round-{round_index}" if round_index else "local")
        except Exception:  # noqa: BLE001  审计失败不影响工具结果
            pass
    return result


def _short_args(args: dict) -> dict:
    short: dict = {}
    for key, value in args.items():
        text = str(value)
        short[key] = text if len(text) <= 40 else text[:37] + "…"
    return short


# ---------------------------------------------------------------- 读取与解析


def _tool_read_document(context: ToolContext, args: dict) -> ToolResult:
    path = str(args.get("path") or "").strip()
    if not path:
        return ToolResult(False, "缺少参数 path")
    try:
        ir = parse_document(path, ocr=bool(args.get("ocr")))
    except ParseError as error:
        return ToolResult(False, f"解析失败：{error}")
    context.ir = ir
    if context.library is not None:
        context.document_id = context.library.register(ir)
    stats = ir.stats()
    context.note(f"已读取《{ir.title}》：{stats['blocks']} 个块 / {stats['tables']} 张表")
    return ToolResult(True, f"已读取《{ir.title}》（{ir.format_key}）", {"ir": ir, "stats": stats})


def _tool_document_info(context: ToolContext, args: dict) -> ToolResult:  # noqa: ARG001
    analysis = quality.analyze_document(context.ir)
    score = quality.heuristic_score(context.ir)
    return ToolResult(
        True,
        f"{analysis['blocks']} 块 / {analysis['headings']} 标题 / {analysis['tables']} 表 · "
        f"质量 {score.overall * 100:.0f}",
        {"analysis": analysis, "score": score.to_dict()})


# ---------------------------------------------------------------- 写入与导出


def _tool_export_document(context: ToolContext, args: dict) -> ToolResult:
    target = str(args.get("target") or context.ir.format_key or "docx").lower()
    if target not in EXPORT_TARGET_KEYS:
        return ToolResult(False, f"不支持的导出目标：{target}")
    output_dir = Path(str(args.get("output_dir") or context.output_dir or "."))
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = str(args.get("name") or context.ir.title or "导出文档")
    try:
        result = writer_engine.write_document(
            context.ir, output_dir / f"{stem}.{target}", target,
            options=context.options, watermark=context.watermark)
    except writer_engine.WriteError as error:
        return ToolResult(False, f"导出失败：{error}")
    context.note(f"已导出 {export_label(target)}：{result.path}")
    return ToolResult(True, f"已导出 {export_label(target)} → {result.path.name}",
                      {"path": str(result.path), "notes": result.notes})


def _tool_write_document(context: ToolContext, args: dict) -> ToolResult:
    path = str(args.get("path") or "").strip()
    if not path:
        return ToolResult(False, "缺少参数 path")
    target = str(args.get("target") or Path(path).suffix.lstrip(".") or context.ir.format_key)
    try:
        result = writer_engine.write_document(
            context.ir, path, target, options=context.options,
            watermark=context.watermark, overwrite=bool(args.get("overwrite")))
    except writer_engine.WriteError as error:
        return ToolResult(False, f"写入失败：{error}")
    return ToolResult(True, f"已写入 {result.path.name}", {"path": str(result.path)})


# ---------------------------------------------------------------- 编辑与排版


def _tool_find_replace(context: ToolContext, args: dict) -> ToolResult:
    pattern = str(args.get("pattern") or "")
    if not pattern:
        return ToolResult(False, "缺少参数 pattern")
    replacement = str(args.get("replacement") or "")
    hits = context.ir.replace_text(
        pattern, replacement, regex=bool(args.get("regex")),
        count=int(args.get("count") or 0), mark_ai=bool(args.get("mark_ai")))
    return ToolResult(bool(hits), f"替换 {hits} 处「{pattern}」", {"hits": hits})


def _tool_unify_terms(context: ToolContext, args: dict) -> ToolResult:
    mapping = args.get("mapping") or args.get("terms") or {}
    if not isinstance(mapping, dict) or not mapping:
        return ToolResult(False, "缺少参数 mapping（形如 {\"旧\":\"新\"}）")
    hits, _changes = merge_engine.unify_terms(context.ir, {str(k): str(v) for k, v in mapping.items()})
    return ToolResult(bool(hits), f"术语统一 {hits} 处", {"hits": hits})


def _tool_normalize_text(context: ToolContext, args: dict) -> ToolResult:
    """本地文本规范（不需要 AI）：中英标点、多余空格、重复标点、句末句号。"""
    from .ai import local_polish

    hits = 0
    changes: list[Change] = context.extras.setdefault("changes", [])
    for index, block in enumerate(context.ir.blocks):
        if not block.is_textual or not block.text.strip():
            continue
        cleaned = local_polish(block.text)
        if cleaned == block.text:
            continue
        hits += 1
        changes.append(Change("标点规范", f"块 {index + 1}", "已规范标点与空格",
                              before=block.text[:40], after=cleaned[:40]))
        if args.get("mark_ai"):
            block.ai_generated = True
        block.text = cleaned
    return ToolResult(bool(hits), f"已规范 {hits} 段文本", {"hits": hits})


def _tool_dedupe_paragraphs(context: ToolContext, args: dict) -> ToolResult:
    """语义去重：删除重复段落（相似度可调），并记录被删内容便于回滚。"""
    removed, changes = merge_engine.dedupe_blocks(
        context.ir, float(args.get("threshold") or 0.9), mark_ai=bool(args.get("mark_ai")))
    context.extras.setdefault("changes", []).extend(changes)
    return ToolResult(bool(removed), f"已删除 {removed} 段重复内容", {"removed": removed})


def _tool_beautify(context: ToolContext, args: dict) -> ToolResult:
    template = str(args.get("template") or context.options.template or "general")
    result = apply_beautify(context.ir, context.options, template=template)
    context.ir = result.ir
    context.extras.setdefault("changes", []).extend(result.changes)
    return ToolResult(True, f"已按「{template_label(template)}」美化"
                            f"（{result.change_count} 处修改）",
                      {"changes": [change.to_dict() for change in result.changes],
                       "toc_entries": result.toc_entries})


def _tool_number_headings(context: ToolContext, args: dict) -> ToolResult:
    options = context.options
    if args.get("style"):
        options.number_style = str(args["style"])
    changes = number_headings(context.ir, options)
    return ToolResult(bool(changes), f"已重排 {len(changes)} 个标题编号",
                      {"count": len(changes)})


def _tool_set_heading_levels(context: ToolContext, args: dict) -> ToolResult:  # noqa: ARG001
    changes = infer_heading_levels(context.ir, context.options)
    return ToolResult(bool(changes), f"识别出 {len(changes)} 个标题层级",
                      {"count": len(changes)})


def _tool_build_toc(context: ToolContext, args: dict) -> ToolResult:
    options = context.options
    if args.get("depth"):
        options.toc_depth = max(1, min(6, int(args["depth"])))
    entries, changes = build_toc(context.ir, options)
    return ToolResult(bool(entries), f"目录已生成（{entries} 条）", {"entries": entries})


def _tool_beautify_table(context: ToolContext, args: dict) -> ToolResult:  # noqa: ARG001
    changes = beautify_tables(context.ir, context.options)
    tables = context.ir.table_count()
    return ToolResult(bool(changes), f"已美化 {tables} 张表格", {"tables": tables})


# ---------------------------------------------------------------- 表格与计算


def _pick_table(context: ToolContext, args: dict) -> Optional[TableData]:
    tables = context.ir.tables()
    if not tables:
        return None
    index = int(args.get("table") or 0)
    return tables[index] if 0 <= index < len(tables) else tables[0]


def _tool_compute_formula(context: ToolContext, args: dict) -> ToolResult:
    formula = str(args.get("formula") or "").strip()
    if not formula:
        return ToolResult(False, "缺少参数 formula")
    tables = context.ir.tables()
    if not tables:
        return ToolResult(False, "当前文档没有表格，无法计算")
    try:
        value = sheet_engine.evaluate_formula(
            formula, tables[0], sheet_name=sheet_engine.sheet_name_of(tables[0]), tables=tables)
    except sheet_engine.FormulaError as error:
        return ToolResult(False, f"公式错误：{error}")
    cell = str(args.get("cell") or "").strip()
    if cell:
        written = _write_cell(tables[0], cell, sheet_engine.to_text(value))
        if not written:
            return ToolResult(True, f"结果 {sheet_engine.to_text(value)}（未找到单元格 {cell}）",
                              {"value": value})
    return ToolResult(True, f"计算结果：{sheet_engine.to_text(value)}", {"value": value})


def _write_cell(table: TableData, address: str, text: str) -> bool:
    reference = sheet_engine.parse_cell_ref(address, default_sheet=table.name)
    if reference is None:
        return False
    row = reference.row
    column = reference.column
    while len(table.rows) <= row:
        table.rows.append([])
    line = table.rows[row]
    while len(line) <= column:
        line.append("")
    line[column] = text
    return True


def _tool_check_calculation(context: ToolContext, args: dict) -> ToolResult:  # noqa: ARG001
    issues = sheet_engine.check_formulas(context.ir.tables())
    if not issues:
        return ToolResult(True, "公式检查通过：没有错误或循环引用", {"issues": []})
    detail = "；".join(f"{item['cell']} {item['code']}" for item in issues[:5])
    return ToolResult(True, f"发现 {len(issues)} 处公式问题：{detail}", {"issues": issues})


def _tool_autofill(context: ToolContext, args: dict) -> ToolResult:
    table = _pick_table(context, args)
    if table is None:
        return ToolResult(False, "当前文档没有表格")
    column = str(args.get("column") or "").strip()
    count = int(args.get("count") or 3)
    index = table.column_index(column) if column else -1
    if index < 0:
        return ToolResult(False, f"找不到列：{column or '（未指定）'}")
    values = [row[index] if index < len(row) else "" for row in table.body_rows()]
    custom = args.get("custom_list")
    filled, spec = sheet_engine.autofill(
        values, count, custom_list=[str(item) for item in custom] if custom else None)
    start = len(table.rows) - len(table.body_rows())
    for offset, value in enumerate(filled):
        row_index = start + len(values) + offset
        while len(table.rows) <= row_index:
            table.rows.append([""] * table.width)
        row = table.rows[row_index]
        while len(row) <= index:
            row.append("")
        row[index] = value
    return ToolResult(True, f"已填充 {len(filled)} 行（{spec.label}）",
                      {"series": spec.to_dict(), "values": filled})


def _tool_smart_fill(context: ToolContext, args: dict) -> ToolResult:
    table = _pick_table(context, args)
    if table is None:
        return ToolResult(False, "当前文档没有表格")
    examples = args.get("examples") or []
    targets = args.get("targets") or []
    if not isinstance(examples, list) or not isinstance(targets, list):
        return ToolResult(False, "examples / targets 必须是数组")
    new_values = [str(item) for item in (args.get("values") or [])]
    if new_values:
        specs = sheet_engine.infer_patterns(
            [str(item) for item in examples],
            [[str(item) for item in column] for column in targets])
        columns = sheet_engine.smart_fill(new_values, specs)
        labels = [spec.label if spec else "无法识别" for spec in specs]
        return ToolResult(any(specs), "智能填充：" + "、".join(labels),
                          {"columns": columns, "patterns": [s.to_dict() if s else None for s in specs]})
    return ToolResult(False, "缺少参数 values（要填充的新数据）")


def _tool_statistics(context: ToolContext, args: dict) -> ToolResult:
    table = _pick_table(context, args)
    if table is None:
        return ToolResult(False, "当前文档没有表格")
    group = str(args.get("group_column") or "")
    value = str(args.get("value_column") or "")
    how = str(args.get("how") or "sum")
    if group:
        try:
            rows = sheet_engine.group_by(table, group, value_column=value, how=how)
        except ValueError as error:
            return ToolResult(False, str(error))
        text = "；".join(f"{key}={sheet_engine.to_text(item)}" for key, item in rows[:10])
        return ToolResult(True, f"按「{group}」{how}：{text}", {"rows": rows})
    reports = sheet_engine.summarize_table(table)
    parts = [f"{item['column']} 合计 {item['sum']:g}" for item in reports[:6]]
    return ToolResult(True, "统计：" + "；".join(parts), {"columns": reports})


def _tool_pivot_table(context: ToolContext, args: dict) -> ToolResult:
    table = _pick_table(context, args)
    if table is None:
        return ToolResult(False, "当前文档没有表格")
    try:
        result = sheet_engine.pivot(
            table, str(args.get("index_column") or ""), str(args.get("column_column") or ""),
            str(args.get("value_column") or ""), how=str(args.get("how") or "sum"))
    except ValueError as error:
        return ToolResult(False, str(error))
    context.ir.blocks.append(Block(kind=BLOCK_TABLE, table=result, ai_generated=False))
    return ToolResult(True, f"已生成透视表（{len(result.rows)} 行）", {"rows": result.rows})


# ---------------------------------------------------------------- AI 文本


def _ai_or_fail(context: ToolContext, scene: str, text: str, *, extra: str = "") -> ToolResult:
    if context.ai is None or not context.ai.available:
        return ToolResult(False, f"{scene}需要先配置大模型（设置 → 大模型）")
    try:
        result = context.ai.transform(text, scene, extra=extra, document_id=context.document_id)
    except Exception as error:  # noqa: BLE001
        return ToolResult(False, f"{scene}失败：{error}")
    return ToolResult(True, f"{scene}完成（{len(result)} 字）", {"text": result})


#: AI 场景 → 中文名（写入 Change 与日志时用）
_AI_SCENE_LABELS = {
    "polish": "AI 润色", "proofread": "AI 纠错", "rewrite": "AI 改写",
    "translate": "AI 翻译", "condense": "AI 缩写", "expand": "AI 扩写",
}


def _ai_transform_blocks(context: ToolContext, scene: str, *, extra: str = "",
                         limit: int = 8, include_headings: bool = False,
                         minimum: int = 4) -> ToolResult:
    """把 AI 变换**真正写回文档**（逐段调用，保留标题/表格结构）。

    为什么不整篇丢给模型：整篇改写会让"哪一段被改了"无法追溯，
    也容易丢掉表格与标题层级。逐段处理换来的是可预览、可回滚的逐条 Change。
    每轮只处理 `limit` 段（避免一次点下去打几十个请求），再跑一轮就覆盖后续段落。
    """
    label = _AI_SCENE_LABELS.get(scene, scene)
    if context.ai is None or not context.ai.available:
        return ToolResult(False, f"{label}需要先配置大模型（设置 → 大模型）")
    targets = [
        block for block in context.ir.blocks
        if block.is_textual and block.text.strip() and block.kind != BLOCK_CODE
        and (include_headings or not block.is_heading)
    ]
    if not targets:
        return ToolResult(False, "没有可处理的正文段落")
    selected = [block for block in targets if len(block.text.strip()) >= minimum][:limit] or targets[:limit]
    changes: list[Change] = context.extras.setdefault("changes", [])
    done = 0
    failed = ""
    for block in selected:
        try:
            new_text = context.ai.transform(block.text, scene, extra=extra,
                                            document_id=context.document_id)
        except Exception as error:  # noqa: BLE001  单段失败不影响其余段
            failed = str(error)
            continue
        new_text = (new_text or "").strip()
        if not new_text or new_text == block.text.strip():
            continue
        changes.append(Change(
            label, f"段落「{block.text.strip()[:16]}」",
            f"已{_AI_SCENE_LABELS.get(scene, scene)[3:]}（{len(block.text)} → {len(new_text)} 字）",
            ai_generated=True, before=block.text[:60], after=new_text[:60]))
        block.text = new_text
        block.ai_generated = True
        done += 1
    remaining = len(targets) - len(selected)
    summary = f"{label}：改写 {done}/{len(selected)} 段"
    if remaining > 0:
        summary += f"（还有 {remaining} 段，再跑一轮即可覆盖）"
        context.note(summary)
    if failed and not done:
        return ToolResult(False, f"{label}失败：{failed}")
    return ToolResult(bool(done), summary, {"changed": done, "remaining": remaining})


def _tool_polish(context: ToolContext, args: dict) -> ToolResult:
    return _ai_transform_blocks(context, "polish",
                                extra=str(args.get("requirement") or ""))


def _tool_summarize(context: ToolContext, args: dict) -> ToolResult:
    result = _ai_or_fail(context, "summary", context.ir.text(),
                         extra=str(args.get("length") or "5 条要点"))
    if result.ok:
        position = 1 if context.ir.blocks and context.ir.blocks[0].is_heading else 0
        context.ir.blocks.insert(position, Block(
            kind=BLOCK_PARAGRAPH, text=f"摘要：{result.data['text']}", ai_generated=True))
    return result


def _tool_translate(context: ToolContext, args: dict) -> ToolResult:
    language = str(args.get("target_language") or "英文")
    return _ai_transform_blocks(context, "translate", extra=language,
                                include_headings=True, minimum=2)


def _tool_expand(context: ToolContext, args: dict) -> ToolResult:
    return _ai_transform_blocks(context, "expand", extra=str(args.get("requirement") or ""),
                                limit=4)


def _tool_condense(context: ToolContext, args: dict) -> ToolResult:
    return _ai_transform_blocks(context, "condense", extra=str(args.get("ratio") or ""),
                                limit=6)


def _tool_proofread(context: ToolContext, args: dict) -> ToolResult:
    return _ai_transform_blocks(context, "proofread",
                                extra=str(args.get("requirement") or ""))


def _tool_rewrite(context: ToolContext, args: dict) -> ToolResult:
    return _ai_transform_blocks(context, "rewrite", extra=str(args.get("style") or "正式书面"),
                                include_headings=True)


def _tool_quality_check(context: ToolContext, args: dict) -> ToolResult:
    goal = str(args.get("goal") or "")
    if args.get("use_ai") and context.ai is not None and context.ai.available:
        score = context.ai.evaluate(context.ir, goal)
    else:
        score = quality.heuristic_score(context.ir, goal)
    return ToolResult(True, score.summary(), {"score": score.to_dict()})


# ---------------------------------------------------------------- 合并与拆分


def _tool_merge_documents(context: ToolContext, args: dict) -> ToolResult:
    path = str(args.get("path") or args.get("other_path") or "").strip()
    if not path:
        return ToolResult(False, "缺少参数 path（副文档路径）")
    try:
        other = parse_document(path)
    except ParseError as error:
        return ToolResult(False, f"解析副文档失败：{error}")
    options = merge_engine.MergeOptions(
        mode=str(args.get("mode") or merge_engine.MERGE_APPEND),
        section_title=str(args.get("section_title") or ""),
        key_column=str(args.get("key_column") or ""),
    )
    chat = context.ai.chat_callback() if (context.ai is not None and context.ai.available) else None
    result = merge_engine.merge_documents(context.ir, other, options, chat=chat)
    context.ir = result.ir
    context.extras["merge_report"] = result.report
    return ToolResult(True, f"已合并《{other.title}》：{len(result.report.changes)} 处变更",
                      {"report": result.report.to_dict()})


def _tool_split_document(context: ToolContext, args: dict) -> ToolResult:
    parts = max(2, int(args.get("parts") or 2))
    output_dir = Path(str(args.get("output_dir") or context.output_dir or "."))
    chunks = _split_blocks(context.ir, parts)
    target = str(args.get("target") or context.ir.format_key or "docx")
    if target not in EXPORT_TARGET_KEYS:
        target = "docx"
    written: list[str] = []
    for index, blocks in enumerate(chunks, start=1):
        piece = DocumentIR(blocks=blocks, title=f"{context.ir.title or '文档'} 第{index}部分",
                           format_key=context.ir.format_key, styles=dict(context.ir.styles))
        try:
            result = writer_engine.write_document(
                piece, output_dir / f"{piece.title}.{target}", target,
                options=context.options, watermark=context.watermark)
        except writer_engine.WriteError as error:
            return ToolResult(False, f"拆分写出失败：{error}")
        written.append(str(result.path))
    return ToolResult(True, f"已拆分为 {len(written)} 个文件", {"paths": written})


def _split_blocks(ir: DocumentIR, parts: int) -> list[list[Block]]:
    """按标题均分（没有标题时按块数均分）。"""
    heading_indexes = [index for index, block in enumerate(ir.blocks) if block.is_heading]
    if len(heading_indexes) >= parts:
        step = max(1, len(heading_indexes) // parts)
        boundaries = [heading_indexes[min(len(heading_indexes) - 1, index * step)]
                      for index in range(parts)]
    else:
        boundaries = [index * max(1, len(ir.blocks) // parts) for index in range(parts)]
    chunks: list[list[Block]] = []
    for position, start in enumerate(boundaries):
        end = boundaries[position + 1] if position + 1 < len(boundaries) else len(ir.blocks)
        chunks.append([block.clone() for block in ir.blocks[start:end]])
    return [chunk for chunk in chunks if chunk]


def _tool_diff_documents(context: ToolContext, args: dict) -> ToolResult:
    path = str(args.get("path") or args.get("other_path") or "").strip()
    if not path:
        return ToolResult(False, "缺少参数 path")
    try:
        other = parse_document(path)
    except ParseError as error:
        return ToolResult(False, f"解析对比文档失败：{error}")
    lines = quality.diff_irs(context.ir, other)
    context.extras["diff"] = lines
    return ToolResult(True, "差异：" + quality.summarize_diff(lines),
                      {"diff": [line.to_dict() for line in lines]})


# ---------------------------------------------------------------- PDF 页面操作


def _pdf_source(context: ToolContext, args: dict) -> str:
    return str(args.get("path") or context.ir.path or "").strip()


def _pdf_target(context: ToolContext, args: dict, source: str, suffix: str,
                *, target_dir: str = "") -> Path:
    """给 PDF 产物生成"不覆盖已有文件"的路径。"""
    folder = Path(target_dir or args.get("output_dir") or context.output_dir or ".")
    folder.mkdir(parents=True, exist_ok=True)
    name = str(args.get("name") or "").strip()
    stem = name or f"{Path(source).stem}-{suffix}"
    return writer_engine.unique_output_path(folder / f"{stem}.pdf")


def _tool_pdf_info(context: ToolContext, args: dict) -> ToolResult:  # noqa: ARG001
    source = _pdf_source(context, args)
    if not source:
        return ToolResult(False, "缺少参数 path")
    try:
        info = pdf_tools.pdf_info(source, password=str(args.get("password") or ""))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    return ToolResult(True, f"{Path(source).name}：{info.summary()}",
                      {"info": info.__dict__})


def _tool_pdf_split(context: ToolContext, args: dict) -> ToolResult:
    source = _pdf_source(context, args)
    if not source:
        return ToolResult(False, "缺少参数 path")
    folder = Path(str(args.get("output_dir") or context.output_dir or "."))
    try:
        produced = pdf_tools.split_pdf(
            source, folder, mode=str(args.get("mode") or "each"),
            ranges=str(args.get("ranges") or ""), every=int(args.get("every") or 1),
            password=str(args.get("password") or ""))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    context.note(f"PDF 拆分：{len(produced)} 个文件 → {folder}")
    return ToolResult(True, f"已拆分为 {len(produced)} 个 PDF（{folder}）",
                      {"paths": [str(item) for item in produced]})


def _tool_pdf_merge(context: ToolContext, args: dict) -> ToolResult:
    paths = args.get("paths") or []
    if isinstance(paths, str):
        paths = [item.strip() for item in paths.split(";") if item.strip()]
    sources = [str(item) for item in paths if str(item).strip()]
    if len(sources) < 2:
        return ToolResult(False, "合并至少需要两个 PDF（paths 数组）")
    target = _pdf_target(context, args, sources[0], "合并")
    try:
        produced = pdf_tools.merge_pdfs(
            sources, target, bookmarks=bool(args.get("bookmarks", True)),
            password=str(args.get("password") or ""))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    context.note(f"PDF 合并：{len(sources)} 份 → {produced}")
    return ToolResult(True, f"已合并 {len(sources)} 份 PDF → {produced.name}",
                      {"path": str(produced)})


def _tool_pdf_extract_pages(context: ToolContext, args: dict) -> ToolResult:
    source = _pdf_source(context, args)
    if not source:
        return ToolResult(False, "缺少参数 path")
    target = _pdf_target(context, args, source, "抽取")
    try:
        produced = pdf_tools.extract_pages(source, target,
                                          pages=str(args.get("pages") or ""),
                                          password=str(args.get("password") or ""))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    return ToolResult(True, f"已抽取页面 → {produced.name}", {"path": str(produced)})


def _tool_pdf_compress(context: ToolContext, args: dict) -> ToolResult:
    source = _pdf_source(context, args)
    if not source:
        return ToolResult(False, "缺少参数 path")
    target = _pdf_target(context, args, source, "压缩")
    try:
        produced = pdf_tools.compress_pdf(source, target,
                                         level=str(args.get("level") or "medium"),
                                         password=str(args.get("password") or ""))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    before = Path(source).stat().st_size
    after = produced.stat().st_size
    return ToolResult(True, f"已压缩：{before / 1024:.0f} KB → {after / 1024:.0f} KB"
                            + ("（这份 PDF 已经比较紧凑）" if after >= before * 0.98 else ""),
                      {"path": str(produced), "size": after})


def _tool_pdf_encrypt(context: ToolContext, args: dict) -> ToolResult:
    source = _pdf_source(context, args)
    if not source:
        return ToolResult(False, "缺少参数 path")
    password = str(args.get("user_password") or args.get("password_new") or "")
    if not password:
        return ToolResult(False, "请提供 user_password（打开密码）")
    target = _pdf_target(context, args, source, "已加密")
    try:
        produced = pdf_tools.encrypt_pdf(
            source, target, user_password=password,
            owner_password=str(args.get("owner_password") or ""),
            allow_printing=bool(args.get("allow_printing", True)),
            allow_copying=bool(args.get("allow_copying", False)),
            allow_modifying=bool(args.get("allow_modifying", False)),
            password=str(args.get("password") or ""))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    return ToolResult(True, f"已加密 → {produced.name}（打印"
                            f"{'允许' if args.get('allow_printing', True) else '禁止'}，复制"
                            f"{'允许' if args.get('allow_copying') else '禁止'}）",
                      {"path": str(produced)})


def _tool_pdf_decrypt(context: ToolContext, args: dict) -> ToolResult:
    source = _pdf_source(context, args)
    if not source:
        return ToolResult(False, "缺少参数 path")
    target = _pdf_target(context, args, source, "已解密")
    try:
        produced = pdf_tools.decrypt_pdf(source, target,
                                        password=str(args.get("password") or ""))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    return ToolResult(True, f"已解密并另存 → {produced.name}", {"path": str(produced)})


def _tool_pdf_rotate(context: ToolContext, args: dict) -> ToolResult:
    source = _pdf_source(context, args)
    if not source:
        return ToolResult(False, "缺少参数 path")
    target = _pdf_target(context, args, source, "旋转")
    try:
        produced = pdf_tools.rotate_pdf(source, target, angle=int(args.get("angle") or 90),
                                       pages=str(args.get("pages") or ""),
                                       password=str(args.get("password") or ""))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    return ToolResult(True, f"已旋转 {args.get('angle') or 90}° → {produced.name}",
                      {"path": str(produced)})


def _tool_pdf_watermark(context: ToolContext, args: dict) -> ToolResult:
    source = _pdf_source(context, args)
    if not source:
        return ToolResult(False, "缺少参数 path")
    text = str(args.get("text") or "")
    footer = str(args.get("footer") or "")
    tracking = str(args.get("tracking_id") or "")
    if not (text or footer or tracking):
        watermark = context.watermark
        if watermark is None or not watermark.enabled:
            return ToolResult(False, "请提供 text（水印文字），或先在「权限安全」页设置导出水印")
        text, footer, tracking = watermark.text, watermark.footer, watermark.tracking_id
    target = _pdf_target(context, args, source, "水印")
    try:
        produced = pdf_tools.watermark_pdf(source, target, text=text, footer=footer,
                                          tracking_id=tracking,
                                          password=str(args.get("password") or ""))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    return ToolResult(True, f"已加逐页水印 → {produced.name}", {"path": str(produced)})


def _tool_pdf_form(context: ToolContext, args: dict) -> ToolResult:
    source = _pdf_source(context, args)
    if not source:
        return ToolResult(False, "缺少参数 path")
    values = args.get("values") or {}
    if not isinstance(values, dict):
        return ToolResult(False, "values 必须是 {字段名: 值} 对象")
    try:
        if not values:
            fields = pdf_tools.list_form_fields(source, password=str(args.get("password") or ""))
            if not fields:
                return ToolResult(False, "这份 PDF 没有可填写的表单域")
            summary = "；".join(f"{item['name']}（{item.get('type', '')}）" for item in fields[:10])
            return ToolResult(True, f"共 {len(fields)} 个表单域：{summary}", {"fields": fields})
        target = _pdf_target(context, args, source, "已填表")
        produced = pdf_tools.fill_form(source, target, {str(k): str(v) for k, v in values.items()},
                                       password=str(args.get("password") or ""),
                                       flatten=bool(args.get("flatten")))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    return ToolResult(True, f"已填写 {len(values)} 个字段 → {produced.name}",
                      {"path": str(produced)})


def _tool_pdf_to_images(context: ToolContext, args: dict) -> ToolResult:
    source = _pdf_source(context, args)
    if not source:
        return ToolResult(False, "缺少参数 path")
    folder = Path(str(args.get("output_dir") or context.output_dir or "."))
    try:
        produced = pdf_tools.to_images(source, folder, pages=str(args.get("pages") or ""),
                                       dpi=int(args.get("dpi") or 150))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    return ToolResult(True, f"已导出 {len(produced)} 张图片 → {folder}（可用于 OCR）",
                      {"paths": [str(item) for item in produced]})


def _tool_add_comment(context: ToolContext, args: dict) -> ToolResult:
    """给某个块加批注（`index` 为块序号，从 0 开始；省略时按文字查找）。"""
    text = str(args.get("text") or "").strip()
    if not text:
        return ToolResult(False, "缺少参数 text（批注内容）")
    index = args.get("index")
    if index is None:
        anchor = str(args.get("anchor") or "").strip()
        if not anchor:
            return ToolResult(False, "请给出 index（块序号）或 anchor（要批注的文字片段）")
        index = next((position for position, block in enumerate(context.ir.blocks)
                      if anchor in (block.text or "")), -1)
        if int(index) < 0:
            return ToolResult(False, f"没有找到包含「{anchor}」的段落")
    if context.library is not None:
        ok = context.library.add_comment(context.ir, int(index), text,
                                        author=str(args.get("author") or "local"))
    else:
        ok = 0 <= int(index) < len(context.ir.blocks)
        if ok:
            context.ir.blocks[int(index)].note = text
    if not ok:
        return ToolResult(False, f"块序号无效：{index}（共 {len(context.ir.blocks)} 块）")
    return ToolResult(True, f"已加批注（块 {int(index) + 1}）：{text[:40]}",
                      {"index": int(index)})

def _tool_list_comments(context: ToolContext, args: dict) -> ToolResult:  # noqa: ARG001
    comments = [
        {"index": index, "anchor": (block.text or "").strip()[:40], "note": block.note,
         "author": str(block.meta.get("comment_author") or "local")}
        for index, block in enumerate(context.ir.blocks) if block.note
    ]
    if not comments:
        return ToolResult(True, "当前文档没有批注", {"comments": []})
    summary = "；".join(f"#{item['index'] + 1} {item['note'][:20]}" for item in comments[:6])
    return ToolResult(True, f"共 {len(comments)} 条批注：{summary}", {"comments": comments})


def _tool_annotate_pdf(context: ToolContext, args: dict) -> ToolResult:
    """把批注写入 PDF（真实 FreeText 标注）；不给 notes 时按文档批注+页码映射。"""
    source = _pdf_source(context, args)
    if not source:
        return ToolResult(False, "缺少参数 path")
    notes = args.get("notes")
    if not notes:
        # 没显式给批注时：用当前文档的批注，页码取块上记录的 page（没有就放在第 1 页）
        notes = []
        for block in context.ir.blocks:
            if block.note:
                notes.append({"page": int(block.meta.get("page") or 1), "text": block.note})
    if not notes:
        return ToolResult(False, "没有可写入的批注（请给 notes 或先给文档加批注）")
    target = _pdf_target(context, args, source, "批注")
    try:
        produced = pdf_tools.annotate_pdf(source, target, notes,
                                         password=str(args.get("password") or ""))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    return ToolResult(True, f"已写入 {len(notes)} 条 PDF 批注 → {produced.name}",
                      {"path": str(produced)})


def _tool_pdf_list_annotations(context: ToolContext, args: dict) -> ToolResult:
    source = _pdf_source(context, args)
    if not source:
        return ToolResult(False, "缺少参数 path")
    try:
        found = pdf_tools.list_annotations(source, password=str(args.get("password") or ""))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    if not found:
        return ToolResult(True, "这份 PDF 没有批注", {"annotations": []})
    summary = "；".join(f"第 {item['page']} 页：{item['text'][:20]}" for item in found[:6])
    return ToolResult(True, f"共 {len(found)} 条批注：{summary}", {"annotations": found})


def _tool_export_images(context: ToolContext, args: dict) -> ToolResult:
    """把文档导出为 PNG（Office 文档先经 LibreOffice 转 PDF；PDF 需 PyMuPDF）。"""
    path = str(args.get("path") or context.ir.path or "").strip()
    if not path:
        return ToolResult(False, "导出图片需要一份磁盘上的源文件（请给 path）")
    folder = Path(str(args.get("output_dir") or context.output_dir or "."))
    try:
        produced = pdf_tools.document_to_images(
            path, folder, pages=str(args.get("pages") or ""), dpi=int(args.get("dpi") or 150))
    except pdf_tools.PdfError as error:
        return ToolResult(False, str(error))
    context.note(f"导出图片：{len(produced)} 张 → {folder}")
    return ToolResult(True, f"已导出 {len(produced)} 张图片 → {folder}",
                      {"paths": [str(item) for item in produced]})


def _tool_register_template(context: ToolContext, args: dict) -> ToolResult:
    """把当前美化参数保存为本地模板。"""
    name = str(args.get("name") or "").strip()
    if not name:
        return ToolResult(False, "缺少参数 name（模板名）")
    options = context.options
    if args.get("template"):
        options = template_options(str(args["template"]))
    try:
        path = save_template(name, options, note=str(args.get("note") or ""))
    except TemplateError as error:
        return ToolResult(False, str(error))
    return ToolResult(True, f"已保存模板「{name}」→ {path.name}", {"path": str(path)})


def _tool_apply_template(context: ToolContext, args: dict) -> ToolResult:
    """套用内置或本地模板（`template` 可为 key、模板名或 `user:<路径>`）。"""
    value = str(args.get("template") or args.get("name") or "").strip()
    if not value:
        return ToolResult(False, "缺少参数 template")
    try:
        options, label = templates.resolve_choice(value)
    except TemplateError as error:
        return ToolResult(False, str(error))
    result = apply_beautify(context.ir, options)
    context.ir = result.ir
    context.extras.setdefault("changes", []).extend(result.changes)
    return ToolResult(True, f"已套用模板「{label}」（{result.change_count} 处修改）",
                      {"changes": [change.to_dict() for change in result.changes]})


# ---------------------------------------------------------------- 安全与识别


def _tool_ocr(context: ToolContext, args: dict) -> ToolResult:
    path = str(args.get("path") or context.ir.path or "").strip()
    if not path:
        return ToolResult(False, "缺少参数 path")
    from .ocr import available, ocr_image

    if not available():
        return ToolResult(False, "OCR 不可用：请安装 tesseract 或用 MODU_TESSERACT 指定路径")
    text = ocr_image(path)
    if not text:
        return ToolResult(False, "OCR 没有识别出文字")
    context.ir.blocks.append(Block(kind=BLOCK_PARAGRAPH, text=text, meta={"ocr": True}))
    return ToolResult(True, f"OCR 识别 {len(text)} 字（请校对）", {"text": text})


def _tool_mask_document(context: ToolContext, args: dict) -> ToolResult:
    rules = args.get("rules") or list(security.DEFAULT_MASK_RULES)
    if isinstance(rules, str):
        rules = [item.strip() for item in rules.split(",") if item.strip()]
    masked, report = security.mask_document(context.ir, rules)
    context.ir = masked
    context.extras.setdefault("changes", []).extend(report.changes)
    return ToolResult(True, report.summary(), {"counts": report.counts})


def _tool_set_watermark(context: ToolContext, args: dict) -> ToolResult:
    watermark = security.build_watermark(
        str(args.get("text") or ""), footer=str(args.get("footer") or ""),
        tracking_id=str(args.get("tracking_id") or security.new_tracking_id()))
    context.watermark = watermark
    return ToolResult(True, security.describe_watermark(watermark), {"watermark": watermark.to_dict()})


# ---------------------------------------------------------------- 注册


def _register_all() -> None:
    register_tool(ToolSpec(
        "read_document", "读取文档", "读取并解析一个文档文件，替换当前工作文档",
        {"path": "文件路径", "ocr": "是否对扫描件做 OCR（true/false）"}, "read",
        handler=_tool_read_document))
    register_tool(ToolSpec(
        "document_info", "文档体检", "输出结构统计、问题清单与质量评分",
        {}, "read", handler=_tool_document_info))
    register_tool(ToolSpec(
        "export_document", "导出文档", "把当前文档导出为目标格式",
        {"target": "txt/markdown/html/docx/xlsx/csv/tsv/json/pdf", "output_dir": "输出目录",
         "name": "输出文件名（不含扩展名）"}, "write", handler=_tool_export_document))
    register_tool(ToolSpec(
        "write_document", "写入文档", "把当前文档写回指定路径",
        {"path": "目标路径", "target": "目标格式（可省略）", "overwrite": "是否覆盖"},
        "write", handler=_tool_write_document))
    register_tool(ToolSpec(
        "find_replace", "查找替换", "在正文与表格里查找替换（支持正则）",
        {"pattern": "查找内容", "replacement": "替换为", "regex": "是否正则",
         "count": "最多替换次数（0=全部）", "mark_ai": "是否标记为 AI 修改"}, "edit",
        handler=_tool_find_replace))
    register_tool(ToolSpec(
        "unify_terms", "术语统一", "按映射表统一人名/产品名/缩写/单位",
        {"mapping": "映射表，如 {\"旧名\":\"新名\"}"}, "edit", handler=_tool_unify_terms))
    register_tool(ToolSpec(
        "normalize_text", "标点规范", "本地规范中英标点、多余空格与重复标点（不需要大模型）",
        {"mark_ai": "是否标记为 AI 修改"}, "edit", handler=_tool_normalize_text))
    register_tool(ToolSpec(
        "add_comment", "加批注", "给指定块或指定文字所在的段落加批注",
        {"index": "块序号（从 0 开始）", "anchor": "要批注的文字片段（与 index 二选一）",
         "text": "批注内容", "author": "批注人"}, "edit", handler=_tool_add_comment))
    register_tool(ToolSpec(
        "list_comments", "列批注", "列出当前文档的所有批注（含锚点文本）", {}, "edit",
        handler=_tool_list_comments))
    register_tool(ToolSpec(
        "dedupe_paragraphs", "语义去重", "删除重复/高度相似的段落（可调相似度阈值）",
        {"threshold": "相似度阈值 0~1，默认 0.9", "mark_ai": "是否标记为 AI 修改"}, "edit",
        handler=_tool_dedupe_paragraphs))
    register_tool(ToolSpec(
        "beautify", "一键美化", "按模板统一字体/字号/行距/标题层级/表格样式",
        {"template": "general/gov/report/thesis/contract/resume/minutes"}, "edit",
        handler=_tool_beautify))
    register_tool(ToolSpec(
        "number_headings", "自动编号", "按层级重排标题编号（arabic/chinese/legal）",
        {"style": "编号风格"}, "edit", handler=_tool_number_headings))
    register_tool(ToolSpec(
        "set_heading_levels", "识别标题层级", "按编号模式推断标题层级", {}, "edit",
        handler=_tool_set_heading_levels))
    register_tool(ToolSpec(
        "build_toc", "生成目录", "按标题层级生成目录（Word 导出后按 F9 更新页码）",
        {"depth": "目录深度 1-6"}, "edit", handler=_tool_build_toc))
    register_tool(ToolSpec(
        "beautify_table", "表格美化", "统一表格边框、表头底纹、斑马纹与列宽", {}, "edit",
        handler=_tool_beautify_table))
    register_tool(ToolSpec(
        "apply_template", "套用模板", "套用内置模板或本地模板库里的模板",
        {"template": "模板 key、模板名或 user:<路径>"}, "edit", handler=_tool_apply_template))
    register_tool(ToolSpec(
        "register_template", "保存为模板", "把当前（或指定）美化参数存进本地模板库",
        {"name": "模板名", "template": "以哪个内置模板为基准（可选）", "note": "备注"},
        "edit", handler=_tool_register_template))
    register_tool(ToolSpec(
        "compute_formula", "公式计算", "计算一条公式，可把结果写入指定单元格",
        {"formula": "公式，如 =SUM(B2:B9)", "cell": "写回单元格地址（可选）"}, "sheet",
        handler=_tool_compute_formula))
    register_tool(ToolSpec(
        "check_calculation", "计算检查", "检查公式错误、循环引用与引用越界", {}, "sheet",
        handler=_tool_check_calculation))
    register_tool(ToolSpec(
        "autofill", "自动填充", "按规律继续填充某一列（序列/日期/工作日/自定义/公式）",
        {"column": "列名", "count": "填充行数", "custom_list": "自定义列表（可选）"}, "sheet",
        handler=_tool_autofill))
    register_tool(ToolSpec(
        "smart_fill", "智能填充", "按示例反推规则并填充新数据（拆分姓名/提取号码/归类）",
        {"examples": "示例输入", "targets": "每列示例输出", "values": "要处理的新数据"},
        "sheet", handler=_tool_smart_fill))
    register_tool(ToolSpec(
        "statistics", "数据统计", "求和/平均/计数/分组统计（公式列会自动求值）",
        {"table": "第几张表（0 起）", "group_column": "分组列", "value_column": "数值列",
         "how": "sum/count/average/max/min"}, "sheet", handler=_tool_statistics))
    register_tool(ToolSpec(
        "pivot_table", "透视表", "按行列维度做透视表并插入文档",
        {"index_column": "行字段", "column_column": "列字段", "value_column": "值字段",
         "how": "聚合方式"}, "sheet", handler=_tool_pivot_table))
    register_tool(ToolSpec(
        "polish", "AI 润色", "润色全文语言（需大模型）", {"requirement": "额外要求"}, "ai",
        needs_ai=True, handler=_tool_polish))
    register_tool(ToolSpec(
        "summarize", "AI 摘要", "生成要点摘要并插入文档开头（需大模型）",
        {"length": "长度要求"}, "ai", needs_ai=True, handler=_tool_summarize))
    register_tool(ToolSpec(
        "translate", "AI 翻译", "把全文翻译为目标语言（需大模型）",
        {"target_language": "目标语言"}, "ai", needs_ai=True, handler=_tool_translate))
    register_tool(ToolSpec(
        "expand", "AI 扩写", "扩写内容（需大模型）", {"requirement": "扩展方向"}, "ai",
        needs_ai=True, handler=_tool_expand))
    register_tool(ToolSpec(
        "condense", "AI 缩写", "压缩篇幅（需大模型）", {"ratio": "压缩比例"}, "ai",
        needs_ai=True, handler=_tool_condense))
    register_tool(ToolSpec(
        "proofread", "AI 纠错", "纠正错别字与语病（需大模型）", {"requirement": "额外要求"},
        "ai", needs_ai=True, handler=_tool_proofread))
    register_tool(ToolSpec(
        "rewrite", "AI 改写", "按指定风格改写（需大模型）", {"style": "目标风格"}, "ai",
        needs_ai=True, handler=_tool_rewrite))
    register_tool(ToolSpec(
        "quality_check", "质量评估", "按五项指标评估文档质量（可选用大模型）",
        {"goal": "目标描述", "use_ai": "是否用大模型评分"}, "ai", handler=_tool_quality_check))
    register_tool(ToolSpec(
        "merge_documents", "合并文档", "与另一个文档合并（追加/章节/表格/主从/对比）",
        {"path": "副文档路径", "mode": "合并方式", "section_title": "章节标题",
         "key_column": "表格主键列"}, "merge", handler=_tool_merge_documents))
    register_tool(ToolSpec(
        "split_document", "拆分文档", "按章节把文档拆成多个文件",
        {"parts": "拆分份数", "target": "输出格式", "output_dir": "输出目录"}, "merge",
        handler=_tool_split_document))
    register_tool(ToolSpec(
        "diff_documents", "文档对比", "生成逐条差异（不修改文档）", {"path": "对比文档路径"},
        "merge", handler=_tool_diff_documents))
    register_tool(ToolSpec(
        "pdf_info", "PDF 概况", "读取 PDF 的页数、尺寸、元数据与表单域",
        {"path": "PDF 路径（默认当前文档）", "password": "打开密码（加密文件需要）"}, "pdf",
        handler=_tool_pdf_info))
    register_tool(ToolSpec(
        "pdf_split", "PDF 拆分", "按每页 / 每 N 页 / 指定页码段拆成多个 PDF",
        {"path": "PDF 路径", "mode": "each / every / ranges", "every": "每 N 页一组",
         "ranges": "页码段，如 1-3,5", "output_dir": "输出目录"}, "pdf",
        handler=_tool_pdf_split))
    register_tool(ToolSpec(
        "pdf_merge", "PDF 合并", "把多个 PDF 合成一份（可加书签）",
        {"paths": "PDF 路径数组", "name": "输出文件名", "bookmarks": "是否加书签",
         "output_dir": "输出目录"}, "pdf", handler=_tool_pdf_merge))
    register_tool(ToolSpec(
        "pdf_extract_pages", "PDF 抽取页面", "把指定页另存为新的 PDF",
        {"path": "PDF 路径", "pages": "页码段，如 2-4,7"}, "pdf",
        handler=_tool_pdf_extract_pages))
    register_tool(ToolSpec(
        "pdf_compress", "PDF 压缩", "重写内容流并去重相同对象（light/medium/strong）",
        {"path": "PDF 路径", "level": "压缩级别"}, "pdf", handler=_tool_pdf_compress))
    register_tool(ToolSpec(
        "pdf_encrypt", "PDF 加密", "设置打开密码并控制打印/复制/修改权限",
        {"path": "PDF 路径", "user_password": "打开密码", "owner_password": "所有者密码",
         "allow_printing": "允许打印", "allow_copying": "允许复制", "allow_modifying": "允许修改"},
        "pdf", handler=_tool_pdf_encrypt))
    register_tool(ToolSpec(
        "pdf_decrypt", "PDF 解密", "用密码另存为不加密副本",
        {"path": "PDF 路径", "password": "打开密码"}, "pdf", handler=_tool_pdf_decrypt))
    register_tool(ToolSpec(
        "pdf_rotate", "PDF 旋转", "旋转全部或指定页面（90 的倍数）",
        {"path": "PDF 路径", "angle": "角度", "pages": "页码范围"}, "pdf",
        handler=_tool_pdf_rotate))
    register_tool(ToolSpec(
        "pdf_watermark", "PDF 水印", "给每一页加浅灰水印与页脚（含追踪标识）",
        {"path": "PDF 路径", "text": "水印文字", "footer": "页脚", "tracking_id": "追踪标识"},
        "pdf", handler=_tool_pdf_watermark))
    register_tool(ToolSpec(
        "pdf_form", "PDF 表单", "列出表单域；给出 values 时填写并另存",
        {"path": "PDF 路径", "values": "{字段名: 值}（留空只列出字段）", "flatten": "是否拍平"},
        "pdf", handler=_tool_pdf_form))
    register_tool(ToolSpec(
        "pdf_list_annotations", "PDF 读批注", "列出 PDF 里已有的文字类批注", {"path": "PDF 路径"},
        "pdf", handler=_tool_pdf_list_annotations))
    register_tool(ToolSpec(
        "annotate_pdf", "PDF 写批注", "把批注写成真实 PDF 标注（FreeText）",
        {"path": "PDF 路径", "notes": "[{page, text}]（留空则用当前文档的批注）"}, "pdf",
        handler=_tool_annotate_pdf))
    register_tool(ToolSpec(
        "pdf_to_images", "PDF 转图片", "把页面导出为 PNG（OCR 前处理，需要 PyMuPDF）",
        {"path": "PDF 路径", "pages": "页码范围", "dpi": "分辨率", "output_dir": "输出目录"},
        "pdf", handler=_tool_pdf_to_images))
    register_tool(ToolSpec(
        "export_images", "导出为图片", "把文档导出为 PNG（Office 文档先经 LibreOffice 转 PDF）",
        {"path": "源文件路径", "pages": "页码范围", "dpi": "分辨率", "output_dir": "输出目录"},
        "write", handler=_tool_export_images))
    register_tool(ToolSpec(
        "ocr", "OCR 识别", "识别图片/扫描件的文字并追加到文档",
        {"path": "图片路径（默认当前文档）"}, "secure", handler=_tool_ocr))
    register_tool(ToolSpec(
        "mask_document", "文档脱敏", "遮住手机号/身份证/银行卡/邮箱/金额等敏感信息",
        {"rules": "规则列表，默认常用五项"}, "secure", handler=_tool_mask_document))
    register_tool(ToolSpec(
        "set_watermark", "设置水印", "设置导出水印与追踪标识",
        {"text": "水印文字", "footer": "页脚文字", "tracking_id": "追踪标识"}, "secure",
        handler=_tool_set_watermark))


_register_all()


__all__ = [
    "TOOL_CATEGORIES",
    "TOOL_ORDER",
    "TOOL_REGISTRY",
    "ToolContext",
    "ToolResult",
    "ToolSpec",
    "call_tool",
    "catalog_lines",
    "register_tool",
    "tool",
    "tool_names",
    "tools_in_category",
]
