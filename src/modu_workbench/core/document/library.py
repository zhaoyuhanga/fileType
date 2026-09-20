"""墨软文档：业务编排层（板块界面只调这一层）。

把"解析 → 美化 / 合并 / 计算 / AI 循环 → 写出"串成完整流程，并统一负责：
- 文档登记与版本快照（每次打开/改写都留一份快照，支持回滚）；
- 审计日志（打开、编辑、导出、合并、AI 调用、脱敏、OCR）；
- 权限与脱敏（AI 调用前统一走 `Permissions`）；
- 工具上下文（界面按钮与 AI 工具调用共用同一份实现）。

界面层不需要知道 parser/beautify/merge/loop 的细节，只需要 `DocumentLibrary`。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from . import merge as merge_engine
from . import quality
from . import security
from . import sheet as sheet_engine
from . import tools as tool_engine
from .ai import DocumentAi
from .beautify import beautify as apply_beautify
from .beautify import template_label
from .formats import EXPORT_TARGET_KEYS, export_label, format_key_for_path, get_spec
from .loop import BeautifyLoop, LoopEvent
from .models import (
    BeautifyOptions,
    BeautifyResult,
    DiffLine,
    DocumentIR,
    LoopOptions,
    LoopResult,
    MergeOptions,
    MergeResult,
    ToolCallRecord,
)
from .parser import ParseError, parse_document
from .storage import DocumentRecord, DocumentStorage
from .writer import WatermarkOptions, WriteError, WriteResult, write_document


class DocumentLibrary:
    """文档板块的业务入口。"""

    def __init__(self, storage: DocumentStorage, *, ai: Optional[DocumentAi] = None,
                 output_dir: str | Path = "", watermark: Optional[WatermarkOptions] = None,
                 options: Optional[BeautifyOptions] = None):
        self.storage = storage
        self.ai = ai
        self.output_dir = str(output_dir or "")
        self.watermark = watermark
        self.options = options or BeautifyOptions()

    # ---------------------------------------------------------------- 文档

    def register(self, ir: DocumentIR) -> int:
        """把解析结果登记进文档库（重复路径则更新统计）。"""
        stats = ir.stats()
        spec = get_spec(ir.format_key)
        try:
            size = Path(ir.path).stat().st_size if ir.path else 0
        except OSError:
            size = 0
        return self.storage.upsert_document(
            ir.path or ir.title, title=ir.title, format_key=ir.format_key,
            category=spec.category if spec else "", size_bytes=size,
            blocks=stats["blocks"], words=stats["words"],
            pages=int(ir.metadata.get("pages") or 0), meta=ir.metadata)

    def open_path(self, path: str | Path, *, ocr: bool = False, snapshot: bool = True) -> DocumentIR:
        """打开（解析）文档：登记 + 留快照 + 记审计。"""
        ir = parse_document(path, ocr=ocr)
        document_id = self.register(ir)
        if snapshot:
            self.storage.save_version(ir, document_id=document_id, label="打开文档", kind="open",
                                      note=f"{ir.format_key} · {ir.stats()['blocks']} 块")
        self.audit("open", str(path), f"{ir.format_key} · {len(ir.blocks)} 块")
        setattr(ir, "_document_id", document_id)
        return ir

    def document_id_of(self, ir: DocumentIR) -> int:
        """取（或补建）文档记录的 id。"""
        document_id = int(getattr(ir, "_document_id", 0) or 0)
        if document_id:
            return document_id
        record = self.storage.get_document_by_path(ir.path) if ir.path else None
        if record is not None:
            setattr(ir, "_document_id", record.id)
            return record.id
        return self.register(ir)

    def documents(self, **kwargs: Any) -> list[DocumentRecord]:
        return self.storage.list_documents(**kwargs)

    def stats(self) -> dict:
        return self.storage.stats()

    def remove_document(self, document_id: int) -> None:
        record = self.storage.get_document(document_id)
        self.storage.delete_document(document_id)
        self.audit("delete", record.path if record else str(document_id), "从文档库移除")

    # ---------------------------------------------------------------- 保存与导出

    def save(self, ir: DocumentIR, path: str | Path = "", *, target: str = "",
             options: Optional[BeautifyOptions] = None, overwrite: bool = True,
             snapshot: bool = True) -> WriteResult:
        """写回/另存：写文件 → 更新文档统计 → 留快照 → 记审计。

        `overwrite` 默认 **True**：保存一份自己打开的文档，意图就是"替换原文件"。
        （防覆盖加序号是"导出/转换"的规则，见 `export`；另存为时可显式传 overwrite=False。）
        """
        destination = Path(path or ir.path)
        if not destination.name:
            raise WriteError("没有指定保存路径")
        previous_path = ir.path
        result = write_document(
            ir, destination, target or format_key_for_path(destination) or ir.format_key,
            options=options or self.options, watermark=self.watermark, overwrite=overwrite)
        # 保存成功即"文档现在住在这个文件里"：另存为之后 Ctrl+S 不该再问一次路径。
        ir.path = str(result.path)
        if previous_path != ir.path and getattr(ir, "_document_id", 0):
            # 落到了另一个文件上：这是"另一份文档"，让它按新路径重新登记（新文件进文档库）
            delattr(ir, "_document_id")
        document_id = self.document_id_of(ir)
        self.storage.touch_document(document_id, edited=True)
        if snapshot:
            self.storage.save_version(ir, document_id=document_id, label="保存文档", kind="save",
                                      note=str(result.path))
        self.audit("edit", str(result.path), f"{result.target} · {len(ir.blocks)} 块")
        return result

    def export(self, ir: DocumentIR, target: str, *, name: str = "",
               output_dir: str | Path = "", options: Optional[BeautifyOptions] = None) -> WriteResult:
        """导出为指定格式（默认输出到板块输出目录）。"""
        target = (target or "docx").lower()
        if target not in EXPORT_TARGET_KEYS:
            raise WriteError(f"不支持的导出目标：{target}")
        folder = Path(output_dir or self.output_dir or ".")
        folder.mkdir(parents=True, exist_ok=True)
        stem = name or ir.title or "导出文档"
        result = write_document(
            ir, folder / f"{stem}.{target}", target,
            options=options or self.options, watermark=self.watermark)
        self.audit("export", str(result.path), export_label(target))
        return result

    # ---------------------------------------------------------------- 美化

    def beautify(self, ir: DocumentIR, options: Optional[BeautifyOptions] = None, *,
                 template: str = "", snapshot: bool = True) -> BeautifyResult:
        """一键/模板美化（结果留快照，可回滚）。"""
        result = apply_beautify(ir, options or self.options, template=template)
        document_id = self.document_id_of(ir)
        if snapshot:
            self.storage.save_version(
                result.ir, document_id=document_id,
                label=f"美化（{template_label(result.template)}）",
                kind="beautify", note=f"{result.change_count} 处修改")
        self.audit("beautify", ir.title or ir.path,
                   f"{template_label(result.template)} · {result.change_count} 处")
        return result

    def preview_beautify(self, ir: DocumentIR, options: Optional[BeautifyOptions] = None, *,
                         template: str = "") -> BeautifyResult:
        """只预览不落库（界面"先看改了什么"）。"""
        return apply_beautify(ir, options or self.options, template=template)

    # ---------------------------------------------------------------- 合并

    def merge(self, main: DocumentIR, other: DocumentIR, options: Optional[MergeOptions] = None, *,
              use_ai: bool = True) -> MergeResult:
        """两个文档合并（报告落库 + 结果留快照 + 记审计）。"""
        chat = None
        if use_ai and self.ai is not None and self.ai.available:
            chat = self.ai.chat_callback()
        result = merge_engine.merge_documents(main, other, options or MergeOptions(), chat=chat)
        document_id = self.document_id_of(main)
        self.storage.save_version(
            result.ir, document_id=document_id,
            label=f"合并（{result.report.mode_label}）", kind="merge",
            note=quality.summarize_diff(result.report.diff))
        report_id = self.storage.save_merge_report(result.report)
        setattr(result.report, "_report_id", report_id)
        self.audit("merge", f"{main.path} + {other.path}",
                   f"{result.report.mode_label} · {len(result.report.changes)} 处变更"
                   + ("（含 AI）" if result.report.ai_used else ""))
        return result

    def merge_paths(self, main_path: str | Path, other_path: str | Path,
                    options: Optional[MergeOptions] = None, *, use_ai: bool = True) -> MergeResult:
        """按路径合并两个文件（界面直接用这个）。"""
        main_ir = parse_document(main_path)
        other_ir = parse_document(other_path)
        return self.merge(main_ir, other_ir, options, use_ai=use_ai)

    def merge_reports(self, limit: int = 50) -> list[dict]:
        return self.storage.list_merge_reports(limit=limit)

    def load_merge_report(self, report_id: int):  # noqa: ANN201
        return self.storage.load_merge_report(report_id)

    # ---------------------------------------------------------------- 计算

    def calculate(self, formula: str, ir: DocumentIR, *, table_index: int = 0) -> Any:
        """在文档里计算一条公式（跨表可用）。"""
        tables = ir.tables()
        if not tables:
            raise sheet_engine.FormulaError("#REF!", "当前文档没有表格")
        table = tables[min(table_index, len(tables) - 1)]
        return sheet_engine.evaluate_formula(
            formula, table, sheet_name=sheet_engine.sheet_name_of(table, table_index),
            tables=tables)

    # ---------------------------------------------------------------- AI 循环美化

    def run_loop(self, ir: DocumentIR, options: LoopOptions, *,
                 on_event: Optional[Callable[[LoopEvent], None]] = None,
                 should_stop: Optional[Callable[[], bool]] = None,
                 confirm: Optional[Callable[[Any], bool]] = None) -> LoopResult:
        """AI 循环美化（每轮快照 + 审计）。"""
        document_id = self.document_id_of(ir)
        engine = BeautifyLoop(self.ai if options.use_ai else None, self.storage,
                              library=self, output_dir=self.output_dir)
        result = engine.run(ir, options, on_event=on_event, should_stop=should_stop,
                            confirm=confirm, document_id=document_id)
        self.storage.save_version(
            result.ir, document_id=document_id, label="循环美化结果", kind="loop",
            round_index=result.round_count,
            note=f"{result.stopped_reason} · {result.change_count} 处")
        self.audit("loop", ir.title or ir.path, result.summary())
        setattr(result, "_rollback", engine.rollback)
        setattr(result, "_snapshot", engine.round_snapshot)
        return result

    # ---------------------------------------------------------------- 批注

    def add_comment(self, ir: DocumentIR, block_index: int, text: str, *,
                    author: str = "local") -> bool:
        """给某个块加批注（记录在 `block.note`，随版本快照一起保存）。"""
        if not (0 <= block_index < len(ir.blocks)):
            return False
        content = (text or "").strip()
        if not content:
            return False
        block = ir.blocks[block_index]
        block.note = content
        block.meta["comment_author"] = author
        self.audit("comment", ir.title or ir.path or f"块 {block_index + 1}",
                   f"{author}：{content[:80]}")
        return True

    def remove_comment(self, ir: DocumentIR, block_index: int) -> bool:
        if not (0 <= block_index < len(ir.blocks)):
            return False
        block = ir.blocks[block_index]
        if not block.note and "comment_author" not in block.meta:
            return False
        block.note = ""
        block.meta.pop("comment_author", None)
        return True

    def clear_comments(self, ir: DocumentIR) -> int:
        removed = 0
        for block in ir.blocks:
            if block.note or "comment_author" in block.meta:
                block.note = ""
                block.meta.pop("comment_author", None)
                removed += 1
        if removed:
            self.audit("comment", ir.title or ir.path, f"清空 {removed} 条批注")
        return removed

    @staticmethod
    def list_comments(ir: DocumentIR) -> list[dict]:
        """列出批注（含锚点文本，便于定位与导出）。"""
        comments: list[dict] = []
        for index, block in enumerate(ir.blocks):
            if not block.note:
                continue
            anchor = (block.text or "").strip().replace("\n", " ")[:60]
            if not anchor and block.is_table and block.table is not None:
                rows = block.table.normalized()
                anchor = "；".join(" | ".join(row) for row in rows[:2])[:60]
            comments.append({
                "index": index,
                "anchor": anchor or f"块 {index + 1}",
                "note": block.note,
                "author": str(block.meta.get("comment_author") or "local"),
                "kind": block.kind,
            })
        return comments

    @staticmethod
    def comments_text(ir: DocumentIR) -> str:
        """批注的纯文本汇总（导出与日志用）。"""
        comments = DocumentLibrary.list_comments(ir)
        if not comments:
            return ""
        lines = ["批注汇总："]
        for number, item in enumerate(comments, start=1):
            lines.append(f"{number}. 「{item['anchor']}」—— {item['note']}（{item['author']}）")
        return "\n".join(lines)

    # ---------------------------------------------------------------- 插件

    def load_plugins(self, *, enabled: Optional[bool] = None) -> list[Any]:
        """加载插件目录里的自定义工具（默认按设置；返回每个插件的加载结果）。"""
        from . import plugins as plugin_module

        records = plugin_module.load_plugins(
            enabled=plugin_module.plugins_enabled(self.storage) if enabled is None else enabled,
            storage=self.storage)
        for record in records:
            self.audit("plugin", record.path, record.summary())
        return records

    def plugin_status(self) -> str:
        from . import plugins as plugin_module

        return plugin_module.status_text(self.storage)

    # ---------------------------------------------------------------- 版本

    def versions(self, ir: DocumentIR, *, limit: int = 60) -> list[dict]:
        document_id = self.document_id_of(ir)
        return self.storage.list_versions(document_id, limit=limit)

    def rollback(self, version_id: int) -> Optional[DocumentIR]:
        """回滚到某个快照（返回那份 IR，是否写文件由调用方决定）。"""
        snapshot = self.storage.load_version_ir(version_id)
        if snapshot is None:
            return None
        record = self.storage.get_version(version_id) or {}
        self.audit("rollback", str(record.get("path") or ""),
                   f"回到版本 #{version_id}（{record.get('label') or ''}）")
        return snapshot

    # ---------------------------------------------------------------- 安全与识别

    def mask(self, ir: DocumentIR, rules: Optional[Sequence[str]] = None) -> tuple[DocumentIR, security.MaskReport]:
        masked, report = security.mask_document(ir, rules)
        self.audit("mask", ir.title or ir.path, report.summary())
        return masked, report

    def ocr(self, path: str | Path) -> str:
        from .ocr import available, ocr_image

        if not available():
            raise ParseError("OCR 不可用：请安装 tesseract 或用 MODU_TESSERACT 指定路径")
        text = ocr_image(path)
        self.audit("ocr", str(path), f"{len(text)} 字")
        return text

    def export_images(self, ir: DocumentIR, *, pages: str = "", dpi: int = 150,
                      output_dir: str | Path = "") -> list[Path]:
        """把文档导出为 PNG（Office 文档先经 LibreOffice 转 PDF）。"""
        from . import pdf_tools

        source = ir.path or str(ir.metadata.get("source") or "")
        if not source or not Path(source).is_file():
            raise WriteError("导出图片需要一份磁盘上的源文件（当前文档还没有保存过）")
        folder = Path(output_dir or self.output_dir or ".")
        folder.mkdir(parents=True, exist_ok=True)
        try:
            produced = pdf_tools.document_to_images(source, folder, pages=pages, dpi=dpi)
        except pdf_tools.PdfError as error:
            raise WriteError(str(error)) from error
        self.audit("export", source, f"导出 {len(produced)} 张图片 → {folder}")
        return produced

    def scan_sensitive(self, ir: DocumentIR, rules: Optional[Sequence[str]] = None) -> dict[str, int]:
        return security.scan_sensitive(ir.text(), rules)

    # ---------------------------------------------------------------- OCR

    def ocr_document(self, path: str | Path, *, lang: str = "") -> DocumentIR:
        """对图片或扫描件 PDF 做 OCR，返回**可编辑的文本文档**（IR）。

        返回的 IR `path` 为空、格式为 `txt`：OCR 结果需要人工校对后再「另存为」，
        不会覆盖原扫描件（原文件是不可编辑的 PDF/图片）。
        """
        from . import ocr as ocr_module
        from .models import BLOCK_PARAGRAPH, Block

        source = Path(path)
        if not source.is_file():
            raise ParseError(f"文件不存在：{source}")
        spec = get_spec(format_key_for_path(source))
        blocks: list[Block] = []
        if spec is not None and spec.render == "image":
            text = ocr_module.ocr_image(source, lang=lang)
            blocks = [Block(kind=BLOCK_PARAGRAPH, text=line.strip())
                      for line in text.splitlines() if line.strip()]
        else:
            for number, text in ocr_module.ocr_pdf_pages(source, lang=lang):
                for chunk in text.split("\n"):
                    if chunk.strip():
                        blocks.append(Block(kind=BLOCK_PARAGRAPH, text=chunk.strip(),
                                            meta={"page": number}))
        if not blocks:
            raise ParseError(ocr_module.describe())
        document = DocumentIR(
            blocks=blocks, title=f"{source.stem}（OCR）", format_key="txt", path="",
            metadata={"ocr": True, "source": str(source), "lang": lang or ocr_module.DEFAULT_LANG},
            warnings=["OCR 结果可能有识别误差，请校对后再另存为文档"],
        )
        self.audit("ocr", str(source), f"{len(blocks)} 段（{spec.label if spec else '文件'}）")
        return document

    # ---------------------------------------------------------------- 对比与快照

    def diff(self, left: DocumentIR, right: DocumentIR) -> list[DiffLine]:
        return quality.diff_irs(left, right)

    def snapshot(self, ir: DocumentIR, *, label: str = "手动快照", kind: str = "manual",
                 note: str = "") -> int:
        return self.storage.save_version(ir, document_id=self.document_id_of(ir),
                                         label=label, kind=kind, note=note)

    # ---------------------------------------------------------------- 工具

    def tool_context(self, ir: DocumentIR, *, options: Optional[BeautifyOptions] = None,
                     watermark: Optional[WatermarkOptions] = None) -> tool_engine.ToolContext:
        return tool_engine.ToolContext(
            ir=ir, library=self, ai=self.ai, output_dir=self.output_dir,
            options=options or self.options, watermark=watermark or self.watermark,
            document_id=self.document_id_of(ir))

    def call_tool(self, name: str, ir: DocumentIR, args: Optional[dict] = None, *,
                  context: Optional[tool_engine.ToolContext] = None) -> tuple[tool_engine.ToolResult, DocumentIR]:
        """执行一个工具（界面按钮与 AI 共用）；返回（结果, 新的 IR）。"""
        ctx = context or self.tool_context(ir)
        result = tool_engine.call_tool(name, ctx, args or {})
        return result, ctx.ir

    def tool_log(self, context: tool_engine.ToolContext) -> list[ToolCallRecord]:
        return list(context.log)

    # ---------------------------------------------------------------- 审计与用量

    def audit(self, action: str, target: str, detail: str = "", actor: str = "local") -> int:
        try:
            return self.storage.add_audit(action, target, detail, actor)
        except Exception:  # noqa: BLE001  审计失败不影响业务
            return 0

    def audit_records(self, *, limit: int = 200, action: str = "") -> list[dict]:
        return self.storage.list_audit(limit=limit, action=action)

    def ai_calls(self, *, limit: int = 100) -> list[dict]:
        return self.storage.list_ai_calls(limit=limit)

    def ai_usage(self) -> dict:
        return self.storage.ai_usage()

    # ---------------------------------------------------------------- 描述

    def describe(self) -> str:
        stats = self.stats()
        ai_state = "已配置" if (self.ai is not None and self.ai.available) else "未配置"
        return (f"文档库 {stats['documents']} 篇 · 版本 {stats['versions']} 个 · 大模型{ai_state}")


__all__ = ["DocumentLibrary"]
