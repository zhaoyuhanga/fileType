"""墨软文档：统一中间结构（IR）与能力选项/结果模型。

设计要点（对应 PRD 6.4 的"文档解析层 / AI 语义层 / 工具执行层"三层）：

- **解析层**把 DOCX / PDF / XLSX / MD / TXT 等解析成同一种 `DocumentIR`
  （`Block` 列表 + 表格 + 元数据 + 样式），上层所有能力（美化、合并、计算、
  AI 循环）都只认 IR，因此新增一种格式只需扩 `parser.py`；
- **AI 语义层**拿到的也是 IR（文本化后的块 + 表格），做出的修改写回 IR；
- **工具执行层**负责把 IR 写成真实文件（`writer.py`）或做局部改写（`tools.py`）。

时间字段一律 epoch 秒（与单库约定一致）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------- 块类型

BLOCK_HEADING = "heading"
BLOCK_PARAGRAPH = "paragraph"
BLOCK_LIST = "list_item"
BLOCK_QUOTE = "quote"
BLOCK_CODE = "code"
BLOCK_TABLE = "table"
BLOCK_IMAGE = "image"
BLOCK_TOC = "toc"
BLOCK_PAGE_BREAK = "page_break"
BLOCK_SLIDE = "slide"

BLOCK_KINDS = (
    BLOCK_HEADING, BLOCK_PARAGRAPH, BLOCK_LIST, BLOCK_QUOTE, BLOCK_CODE,
    BLOCK_TABLE, BLOCK_IMAGE, BLOCK_TOC, BLOCK_PAGE_BREAK, BLOCK_SLIDE,
)

BLOCK_LABELS = {
    BLOCK_HEADING: "标题",
    BLOCK_PARAGRAPH: "正文",
    BLOCK_LIST: "列表",
    BLOCK_QUOTE: "引用",
    BLOCK_CODE: "代码",
    BLOCK_TABLE: "表格",
    BLOCK_IMAGE: "图片",
    BLOCK_TOC: "目录",
    BLOCK_PAGE_BREAK: "分页",
    BLOCK_SLIDE: "幻灯片",
}

#: 修订标记（合并/循环美化时用来标注"哪些是 AI 改的"）
REVISION_NONE = ""
REVISION_INSERTED = "inserted"
REVISION_DELETED = "deleted"
REVISION_MODIFIED = "modified"


def block_label(kind: str) -> str:
    return BLOCK_LABELS.get(kind, kind)


# ---------------------------------------------------------------- 表格


@dataclass
class TableData:
    """一张表（Word 表格 / 一个 Sheet / CSV 全文）。"""

    rows: list[list[str]] = field(default_factory=list)
    name: str = ""
    header: bool = True
    styles: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    # ---------- 基础 ----------

    @property
    def width(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    @property
    def height(self) -> int:
        return len(self.rows)

    def normalized(self) -> list[list[str]]:
        """补齐成矩形（短行补空单元格），避免写文件时列数不齐。"""
        width = self.width
        return [list(row) + [""] * (width - len(row)) for row in self.rows]

    def header_row(self) -> list[str]:
        return list(self.rows[0]) if self.rows else []

    def body_rows(self) -> list[list[str]]:
        return self.rows[1:] if (self.header and len(self.rows) > 1) else list(self.rows)

    def column(self, index: int) -> list[str]:
        return [row[index] if index < len(row) else "" for row in self.rows]

    def column_index(self, name: str) -> int:
        """按表头名找列号（找不到返回 -1）；表头缺失时按 `列1/列2` 兜底。"""
        needle = (name or "").strip().lower()
        if not needle:
            return -1
        for index, cell in enumerate(self.header_row()):
            if cell.strip().lower() == needle:
                return index
        return -1

    def clone(self) -> "TableData":
        return TableData(
            rows=[list(row) for row in self.rows],
            name=self.name,
            header=self.header,
            styles=dict(self.styles),
            meta=dict(self.meta),
        )

    def to_dict(self) -> dict:
        return {
            "rows": [list(row) for row in self.rows],
            "name": self.name,
            "header": self.header,
            "styles": dict(self.styles),
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TableData":
        return cls(
            rows=[list(row) for row in (data.get("rows") or [])],
            name=str(data.get("name") or ""),
            header=bool(data.get("header", True)),
            styles=dict(data.get("styles") or {}),
            meta=dict(data.get("meta") or {}),
        )


# ---------------------------------------------------------------- 块


@dataclass
class Block:
    """IR 的基本单位：一段标题 / 正文 / 表格 / 图片 …。"""

    kind: str = BLOCK_PARAGRAPH
    text: str = ""
    level: int = 0                     # 标题层级 1..6（0 = 非标题）
    style: dict[str, Any] = field(default_factory=dict)
    table: Optional[TableData] = None
    meta: dict[str, Any] = field(default_factory=dict)
    revision: str = REVISION_NONE      # inserted / deleted / modified
    ai_generated: bool = False         # 是否由 AI 生成或改写（验收标准要求可标注）
    note: str = ""                     # 批注

    # ---------- 便捷 ----------

    @property
    def is_heading(self) -> bool:
        return self.kind == BLOCK_HEADING

    @property
    def is_table(self) -> bool:
        return self.kind == BLOCK_TABLE and self.table is not None

    @property
    def is_textual(self) -> bool:
        return self.kind in (
            BLOCK_HEADING, BLOCK_PARAGRAPH, BLOCK_LIST, BLOCK_QUOTE, BLOCK_CODE, BLOCK_SLIDE,
        )

    def table_markdown(self) -> str:
        if self.table is None:
            return ""
        rows = self.table.normalized()
        if not rows:
            return ""
        width = len(rows[0])

        def line(cells: list[str]) -> str:
            return "| " + " | ".join((cell or "").replace("|", "\\|") for cell in cells) + " |"

        head = line(rows[0])
        sep = "| " + " | ".join("---" for _ in range(width)) + " |"
        return "\n".join([head, sep, *[line(row) for row in rows[1:]]])

    def clone(self) -> "Block":
        return Block(
            kind=self.kind,
            text=self.text,
            level=self.level,
            style=dict(self.style),
            table=self.table.clone() if self.table is not None else None,
            meta=dict(self.meta),
            revision=self.revision,
            ai_generated=self.ai_generated,
            note=self.note,
        )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "text": self.text,
            "level": self.level,
            "style": dict(self.style),
            "table": self.table.to_dict() if self.table is not None else None,
            "meta": dict(self.meta),
            "revision": self.revision,
            "ai_generated": self.ai_generated,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Block":
        table = data.get("table")
        return cls(
            kind=str(data.get("kind") or BLOCK_PARAGRAPH),
            text=str(data.get("text") or ""),
            level=int(data.get("level") or 0),
            style=dict(data.get("style") or {}),
            table=TableData.from_dict(table) if table else None,
            meta=dict(data.get("meta") or {}),
            revision=str(data.get("revision") or ""),
            ai_generated=bool(data.get("ai_generated")),
            note=str(data.get("note") or ""),
        )


# ---------------------------------------------------------------- 文档 IR

DEFAULT_STYLES: dict[str, Any] = {
    "font_cn": "宋体",
    "font_en": "Times New Roman",
    "body_size": 12.0,
    "heading_sizes": {1: 22.0, 2: 18.0, 3: 16.0, 4: 14.0, 5: 13.0, 6: 12.0},
    "line_spacing": 1.5,
    "paragraph_spacing": 6.0,
    "first_line_indent": 2.0,
    "brand_color": "#1452C4",
    "align": "left",
    "table_style": "grid",
    "zebra": True,
}


@dataclass
class DocumentIR:
    """统一中间结构：一份文档（无论来源格式）都表示成它。"""

    blocks: list[Block] = field(default_factory=list)
    title: str = ""
    path: str = ""
    format_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    styles: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # ---------- 结构访问 ----------

    def clone(self) -> "DocumentIR":
        return DocumentIR(
            blocks=[block.clone() for block in self.blocks],
            title=self.title,
            path=self.path,
            format_key=self.format_key,
            metadata=dict(self.metadata),
            styles={**self.styles},
            warnings=list(self.warnings),
        )

    def headings(self) -> list[Block]:
        return [block for block in self.blocks if block.is_heading]

    def outline(self) -> list[tuple[int, str, int]]:
        """(层级, 标题, 块序号) 列表 —— 界面的「大纲 / 目录」用它。"""
        return [
            (block.level or 1, block.text.strip(), index)
            for index, block in enumerate(self.blocks)
            if block.is_heading and block.text.strip()
        ]

    def tables(self) -> list[TableData]:
        return [block.table for block in self.blocks if block.is_table and block.table is not None]

    def first_table(self) -> Optional[TableData]:
        tables = self.tables()
        return tables[0] if tables else None

    def text(self, *, include_tables: bool = True) -> str:
        """纯文本（AI 上下文与导出 TXT 用）。"""
        lines: list[str] = []
        for block in self.blocks:
            if block.is_table:
                if include_tables and block.table is not None:
                    lines.append("\n".join("\t".join(row) for row in block.table.normalized()))
                continue
            if block.kind == BLOCK_PAGE_BREAK:
                continue
            if block.kind == BLOCK_IMAGE:
                lines.append(f"[图片] {(block.text or '未命名').strip()}")
                continue
            if block.text.strip():
                lines.append(block.text.rstrip())
            elif block.kind == BLOCK_PARAGRAPH:
                lines.append("")
        return "\n".join(lines).strip("\n")

    def markdown(self) -> str:
        """Markdown（写出 .md / 给 AI 当上下文都用它）。"""
        parts: list[str] = []
        for block in self.blocks:
            if block.is_table:
                parts.append(block.table_markdown())
            elif block.kind == BLOCK_HEADING:
                level = max(1, min(6, block.level or 1))
                parts.append(f"{'#' * level} {block.text.strip()}")
            elif block.kind == BLOCK_LIST:
                parts.append(f"- {block.text.strip()}")
            elif block.kind == BLOCK_QUOTE:
                parts.append(f"> {block.text.strip()}")
            elif block.kind == BLOCK_CODE:
                parts.append(f"```\n{block.text.rstrip()}\n```")
            elif block.kind == BLOCK_TOC:
                parts.append(block.text)
            elif block.kind == BLOCK_PAGE_BREAK:
                parts.append("---")
            elif block.kind == BLOCK_IMAGE:
                # 图片在素材缓存里（见 media.py）时 Markdown 只留一行说明；
                # 有外部地址（远程 URL / 本地文件）的就写成真正的图片语法
                src = str(block.meta.get("src") or "")
                name = (block.text or "图片").strip() or "图片"
                if src and not src.startswith("data:"):
                    parts.append(f"![{name}]({src})")
                else:
                    parts.append(f"_[图片：{name}]_")
            elif block.kind == BLOCK_SLIDE:
                parts.append(f"## {block.text.strip()}")
            elif block.text.strip():
                parts.append(block.text.rstrip())
        return "\n\n".join(part for part in parts if part is not None).strip() + "\n"

    def stats(self) -> dict[str, int]:
        text = self.text(include_tables=True)
        chars = len([char for char in text if not char.isspace()])
        paragraphs = len([b for b in self.blocks if b.kind == BLOCK_PARAGRAPH and b.text.strip()])
        words = len(text.split())
        return {
            "blocks": len(self.blocks),
            "paragraphs": paragraphs,
            "headings": len(self.headings()),
            "tables": len(self.tables()),
            "chars": chars,
            "words": words,
        }

    def table_count(self) -> int:
        return len(self.tables())

    # ---------- 变更 ----------

    def replace_text(self, pattern: str, replacement: str, *, regex: bool = False,
                     count: int = 0, mark_ai: bool = False) -> int:
        """查找替换（命中数）；`regex=True` 时按正则。"""
        import re

        hits = 0
        matcher = re.compile(pattern) if regex else None
        for block in self.blocks:
            if block.is_table and block.table is not None:
                for row_index, row in enumerate(block.table.rows):
                    for cell_index, cell in enumerate(row):
                        new_value, changed = _replace_in_text(
                            cell, pattern, replacement, matcher, count - hits if count else 0)
                        if changed:
                            hits += changed
                            block.table.rows[row_index][cell_index] = new_value
                if hits and mark_ai:
                    block.ai_generated = True
                continue
            if not block.text:
                continue
            new_value, changed = _replace_in_text(
                block.text, pattern, replacement, matcher, count - hits if count else 0)
            if changed:
                hits += changed
                block.text = new_value
                if mark_ai:
                    block.ai_generated = True
                if block.revision == REVISION_NONE:
                    block.revision = REVISION_MODIFIED
        return hits

    # ---------- 序列化（版本快照） ----------

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "path": self.path,
            "format_key": self.format_key,
            "metadata": dict(self.metadata),
            "styles": {**self.styles},
            "warnings": list(self.warnings),
            "blocks": [block.to_dict() for block in self.blocks],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentIR":
        return cls(
            blocks=[Block.from_dict(item) for item in (data.get("blocks") or [])],
            title=str(data.get("title") or ""),
            path=str(data.get("path") or ""),
            format_key=str(data.get("format_key") or ""),
            metadata=dict(data.get("metadata") or {}),
            styles={**DEFAULT_STYLES, **dict(data.get("styles") or {})},
            warnings=list(data.get("warnings") or []),
        )

    @classmethod
    def from_text(cls, text: str, *, title: str = "", format_key: str = "txt") -> "DocumentIR":
        """把纯文本按空行切段（最小可用的 IR 构造，测试与导入用）。"""
        blocks: list[Block] = []
        for chunk in text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n"):
            stripped = chunk.strip("\n")
            if stripped.strip():
                blocks.append(Block(kind=BLOCK_PARAGRAPH, text=stripped.strip()))
        return cls(blocks=blocks, title=title, format_key=format_key)


def _replace_in_text(text: str, pattern: str, replacement: str, matcher, limit: int) -> tuple[str, int]:
    if matcher is not None:
        new_value, hits = matcher.subn(replacement, text, count=limit)
        return new_value, hits
    if not pattern:
        return text, 0
    new_value = text.replace(pattern, replacement, limit if limit else -1)
    if new_value == text:
        return text, 0
    hits = text.count(pattern) if not limit else min(limit, text.count(pattern))
    return new_value, hits


# ---------------------------------------------------------------- 美化

#: 模板 key → 模板说明（具体参数在 beautify.TEMPLATES）
TEMPLATE_KEYS = ("general", "gov", "report", "thesis", "contract", "resume", "minutes")

TEMPLATE_LABELS = {
    "general": "通用（一键美化）",
    "gov": "公文",
    "report": "报告",
    "thesis": "论文",
    "contract": "合同",
    "resume": "简历",
    "minutes": "会议纪要",
}


@dataclass
class BeautifyOptions:
    """格式美化参数（MR-DOC-301）。"""

    template: str = "general"
    font_cn: str = "宋体"
    font_en: str = "Times New Roman"
    heading_font_cn: str = ""
    heading_font_en: str = ""
    body_size: float = 12.0
    heading_sizes: dict[int, float] = field(default_factory=dict)
    line_spacing: float = 1.5
    paragraph_spacing: float = 6.0
    first_line_indent: float = 2.0
    align: str = "left"
    number_style: str = "arabic"
    unify_styles: bool = True
    beautify_tables: bool = True
    zebra: bool = True
    freeze_header: bool = True
    conditional_format: bool = False
    data_validation: bool = False
    auto_number: bool = False
    build_toc: bool = False
    toc_depth: int = 3
    export_comments: bool = True        # 导出时带上批注（Word 原生批注 / 其它格式附录汇总）
    brand_color: str = "#1452C4"
    header_text: str = ""
    footer_text: str = ""
    logo_text: str = ""
    page_numbers: bool = True
    infer_heading_levels: bool = True
    title_align: str = "center"

    def resolved_heading_sizes(self) -> dict[int, float]:
        base = dict(DEFAULT_STYLES["heading_sizes"])
        base.update({int(k): float(v) for k, v in (self.heading_sizes or {}).items()})
        return base

    def to_dict(self) -> dict:
        return {
            "template": self.template,
            "font_cn": self.font_cn,
            "font_en": self.font_en,
            "heading_font_cn": self.heading_font_cn,
            "heading_font_en": self.heading_font_en,
            "body_size": self.body_size,
            "heading_sizes": {int(k): float(v) for k, v in self.heading_sizes.items()},
            "line_spacing": self.line_spacing,
            "paragraph_spacing": self.paragraph_spacing,
            "first_line_indent": self.first_line_indent,
            "align": self.align,
            "number_style": self.number_style,
            "unify_styles": self.unify_styles,
            "beautify_tables": self.beautify_tables,
            "zebra": self.zebra,
            "freeze_header": self.freeze_header,
            "conditional_format": self.conditional_format,
            "data_validation": self.data_validation,
            "auto_number": self.auto_number,
            "build_toc": self.build_toc,
            "toc_depth": self.toc_depth,
            "export_comments": self.export_comments,
            "brand_color": self.brand_color,
            "header_text": self.header_text,
            "footer_text": self.footer_text,
            "logo_text": self.logo_text,
            "page_numbers": self.page_numbers,
            "infer_heading_levels": self.infer_heading_levels,
            "title_align": self.title_align,
        }

    def describe(self) -> str:
        return (
            f"{TEMPLATE_LABELS.get(self.template, self.template)} · "
            f"{self.font_cn} {self.body_size:g}pt · 行距 {self.line_spacing:g} · "
            f"段距 {self.paragraph_spacing:g}pt"
            + ("· 自动编号" if self.auto_number else "")
            + ("· 生成目录" if self.build_toc else "")
        )


@dataclass
class Change:
    """一处修改（格式美化 / 合并 / 循环美化的审计单位）。"""

    kind: str
    location: str
    detail: str
    ai_generated: bool = False
    before: str = ""
    after: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "location": self.location,
            "detail": self.detail,
            "ai_generated": self.ai_generated,
            "before": self.before,
            "after": self.after,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Change":
        return cls(
            kind=str(data.get("kind") or ""),
            location=str(data.get("location") or ""),
            detail=str(data.get("detail") or ""),
            ai_generated=bool(data.get("ai_generated")),
            before=str(data.get("before") or ""),
            after=str(data.get("after") or ""),
        )

    def line(self) -> str:
        flag = "🤖 " if self.ai_generated else ""
        return f"{flag}{self.location}｜{self.kind}：{self.detail}"


@dataclass
class BeautifyResult:
    ir: DocumentIR
    changes: list[Change] = field(default_factory=list)
    template: str = "general"
    toc_entries: int = 0

    @property
    def change_count(self) -> int:
        return len(self.changes)

    def report_lines(self) -> list[str]:
        return [change.line() for change in self.changes]


# ---------------------------------------------------------------- 质量评分


@dataclass
class QualityScore:
    """质量评估（MR-DOC-305 第 5 步）：五项 0~1 分 + 加权总分。"""

    format_consistency: float = 0.0
    language_quality: float = 0.0
    readability: float = 0.0
    factual_consistency: float = 1.0
    goal_match: float = 0.0
    notes: list[str] = field(default_factory=list)
    source: str = "heuristic"        # heuristic / ai

    WEIGHTS = {
        "format_consistency": 0.20,
        "language_quality": 0.25,
        "readability": 0.20,
        "factual_consistency": 0.15,
        "goal_match": 0.20,
    }
    LABELS = {
        "format_consistency": "格式一致性",
        "language_quality": "语言质量",
        "readability": "可读性",
        "factual_consistency": "事实一致性",
        "goal_match": "目标匹配度",
    }

    @property
    def overall(self) -> float:
        total = sum(getattr(self, name) * weight for name, weight in self.WEIGHTS.items())
        return round(max(0.0, min(1.0, total)), 4)

    def items(self) -> list[tuple[str, float]]:
        return [(self.LABELS[name], float(getattr(self, name))) for name in self.WEIGHTS]

    def weakest(self) -> tuple[str, float]:
        name = min(self.WEIGHTS, key=lambda item: float(getattr(self, item)))
        return self.LABELS[name], float(getattr(self, name))

    def summary(self) -> str:
        parts = [f"{label} {value * 100:.0f}" for label, value in self.items()]
        return f"总分 {self.overall * 100:.0f}（" + " · ".join(parts) + "）"

    def to_dict(self) -> dict:
        return {
            "format_consistency": self.format_consistency,
            "language_quality": self.language_quality,
            "readability": self.readability,
            "factual_consistency": self.factual_consistency,
            "goal_match": self.goal_match,
            "overall": self.overall,
            "notes": list(self.notes),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QualityScore":
        return cls(
            format_consistency=float(data.get("format_consistency") or 0.0),
            language_quality=float(data.get("language_quality") or 0.0),
            readability=float(data.get("readability") or 0.0),
            factual_consistency=float(data.get("factual_consistency") or 0.0),
            goal_match=float(data.get("goal_match") or 0.0),
            notes=list(data.get("notes") or []),
            source=str(data.get("source") or "heuristic"),
        )


# ---------------------------------------------------------------- 合并

MERGE_APPEND = "append"
MERGE_SECTION = "section"
MERGE_TABLE_ROWS = "table_rows"
MERGE_TABLE_COLUMNS = "table_columns"
MERGE_TABLE_KEY = "table_key"
MERGE_SLAVE = "slave"
MERGE_DIFF = "diff"

MERGE_MODES: tuple[tuple[str, str, str], ...] = (
    (MERGE_APPEND, "追加合并", "把副文档整体接到主文档末尾"),
    (MERGE_SECTION, "章节合并", "按标题层级插入到主文档的指定章节"),
    (MERGE_TABLE_ROWS, "表格按行合并", "副文档表格的行追加到主文档表格"),
    (MERGE_TABLE_COLUMNS, "表格按列合并", "副文档表格的列接到主文档表格右侧"),
    (MERGE_TABLE_KEY, "表格按主键合并", "按主键列对齐，左右取值合并成一张表"),
    (MERGE_SLAVE, "主从合并", "副文档作为附件/补充材料并入"),
    (MERGE_DIFF, "对比合并", "两稿差异逐条保留，生成修订记录"),
)

MERGE_MODE_LABELS = {key: label for key, label, _note in MERGE_MODES}
MERGE_MODE_NOTES = {key: note for key, _label, note in MERGE_MODES}

#: 表格类合并模式（走 TableData 合并而不是块拼接）
TABLE_MERGE_MODES = (MERGE_TABLE_ROWS, MERGE_TABLE_COLUMNS, MERGE_TABLE_KEY)


@dataclass
class MergeOptions:
    """两个文档合并的选项（MR-DOC-304）。"""

    mode: str = MERGE_APPEND
    target_format: str = ""             # 空 = 跟随主文档格式
    section_title: str = ""             # 章节合并：插入到哪个标题之后
    key_column: str = ""                # 表格按主键合并：主键列名
    dedupe: bool = True                 # AI 语义去重（重复段落/条款/数据）
    dedupe_threshold: float = 0.9       # 相似度阈值
    unify_terms: bool = False           # 术语统一
    term_map: dict[str, str] = field(default_factory=dict)
    unify_style: bool = True            # 风格统一（合并后按模板美化）
    template: str = "general"
    add_toc: bool = True                # 合并后自动生成目录
    toc_depth: int = 3                  # 目录深度
    add_summary: bool = True            # 生成合并摘要
    keep_revisions: bool = True         # 保留修订记录（标记哪些内容来自副文档/AI）
    add_transition: bool = True         # 自动生成章节过渡段
    mark_ai: bool = True                # 标注 AI 生成/修改的内容
    author: str = "墨软文档"

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "target_format": self.target_format,
            "section_title": self.section_title,
            "key_column": self.key_column,
            "dedupe": self.dedupe,
            "dedupe_threshold": self.dedupe_threshold,
            "unify_terms": self.unify_terms,
            "term_map": dict(self.term_map),
            "unify_style": self.unify_style,
            "template": self.template,
            "add_toc": self.add_toc,
            "toc_depth": self.toc_depth,
            "add_summary": self.add_summary,
            "keep_revisions": self.keep_revisions,
            "add_transition": self.add_transition,
            "mark_ai": self.mark_ai,
            "author": self.author,
        }


@dataclass
class DiffLine:
    """两稿对比的一行（对比合并与循环美化的每轮 diff 都用它）。"""

    kind: str          # same / added / removed / changed
    left: str = ""
    right: str = ""
    index: int = -1

    LABELS = {"same": "相同", "added": "新增", "removed": "删除", "changed": "修改"}

    def label(self) -> str:
        return self.LABELS.get(self.kind, self.kind)

    def render(self) -> str:
        if self.kind == "same":
            return f"  {self.left}"
        if self.kind == "added":
            return f"+ {self.right}"
        if self.kind == "removed":
            return f"- {self.left}"
        return f"~ {self.left} → {self.right}"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "left": self.left, "right": self.right, "index": self.index}

    @classmethod
    def from_dict(cls, data: dict) -> "DiffLine":
        return cls(
            kind=str(data.get("kind") or "same"),
            left=str(data.get("left") or ""),
            right=str(data.get("right") or ""),
            index=int(data.get("index", -1)),
        )


@dataclass
class MergeReport:
    """合并差异报告 + 摘要（验收标准要求可预览、可导出、可回滚）。"""

    mode: str = MERGE_APPEND
    mode_label: str = ""
    main_title: str = ""
    other_title: str = ""
    main_path: str = ""
    other_path: str = ""
    output_path: str = ""
    target_format: str = ""
    changes: list[Change] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    diff: list[DiffLine] = field(default_factory=list)
    summary: str = ""
    deduped: int = 0
    ai_used: bool = False
    created_at: int = field(default_factory=lambda: int(time.time()))

    @property
    def ai_change_count(self) -> int:
        return len([change for change in self.changes if change.ai_generated])

    def report_lines(self) -> list[str]:
        lines = [
            f"合并方式：{self.mode_label or MERGE_MODE_LABELS.get(self.mode, self.mode)}",
            f"主文档：{self.main_title or self.main_path}",
            f"副文档：{self.other_title or self.other_path}",
            f"修改条目：{len(self.changes)}（其中 AI 参与 {self.ai_change_count}）",
        ]
        if self.deduped:
            lines.append(f"语义去重：删除重复内容 {self.deduped} 处")
        if self.conflicts:
            lines.append("冲突处理：")
            lines.extend(f"  · {item}" for item in self.conflicts)
        if self.changes:
            lines.append("变更明细：")
            lines.extend(f"  · {change.line()}" for change in self.changes)
        return lines

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "mode_label": self.mode_label,
            "main_title": self.main_title,
            "other_title": self.other_title,
            "main_path": self.main_path,
            "other_path": self.other_path,
            "output_path": self.output_path,
            "target_format": self.target_format,
            "changes": [change.to_dict() for change in self.changes],
            "conflicts": list(self.conflicts),
            "diff": [line.to_dict() for line in self.diff],
            "summary": self.summary,
            "deduped": self.deduped,
            "ai_used": self.ai_used,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MergeReport":
        return cls(
            mode=str(data.get("mode") or MERGE_APPEND),
            mode_label=str(data.get("mode_label") or ""),
            main_title=str(data.get("main_title") or ""),
            other_title=str(data.get("other_title") or ""),
            main_path=str(data.get("main_path") or ""),
            other_path=str(data.get("other_path") or ""),
            output_path=str(data.get("output_path") or ""),
            target_format=str(data.get("target_format") or ""),
            changes=[Change.from_dict(item) for item in (data.get("changes") or [])],
            conflicts=list(data.get("conflicts") or []),
            diff=[DiffLine.from_dict(item) for item in (data.get("diff") or [])],
            summary=str(data.get("summary") or ""),
            deduped=int(data.get("deduped") or 0),
            ai_used=bool(data.get("ai_used")),
            created_at=int(data.get("created_at") or 0),
        )


@dataclass
class MergeResult:
    ir: DocumentIR
    report: MergeReport


# ---------------------------------------------------------------- 循环美化

STOP_THRESHOLD = "threshold"
STOP_MAX_ROUNDS = "max_rounds"
STOP_NO_CHANGE = "no_change"
STOP_USER = "user"
STOP_COST = "cost"
STOP_TIME = "time"
STOP_ERROR = "error"

STOP_LABELS = {
    STOP_THRESHOLD: "达到目标质量",
    STOP_MAX_ROUNDS: "达到最大轮次",
    STOP_NO_CHANGE: "本轮无可改进项",
    STOP_USER: "用户停止",
    STOP_COST: "达到成本上限",
    STOP_TIME: "达到时间上限",
    STOP_ERROR: "执行出错",
}


@dataclass
class LoopOptions:
    """AI 循环美化参数（MR-DOC-305）。"""

    goal: str = ""
    scene: str = "polish"          # 见 ai.SCENES
    template: str = "general"
    max_rounds: int = 3
    quality_threshold: float = 0.85
    max_cost: float = 0.0          # 0 = 不限制（单位：与模型价格同币种）
    max_seconds: float = 0.0       # 0 = 不限制
    stop_on_no_change: bool = True
    require_confirm: bool = False  # 每轮结束后人工确认
    use_ai: bool = True            # 关掉则只用本地规则（离线可用）
    mark_ai: bool = True
    snapshot: bool = True          # 每轮保存版本快照
    allow_tools: tuple[str, ...] = ()   # 允许调用的工具（空 = 全部）

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "scene": self.scene,
            "template": self.template,
            "max_rounds": self.max_rounds,
            "quality_threshold": self.quality_threshold,
            "max_cost": self.max_cost,
            "max_seconds": self.max_seconds,
            "stop_on_no_change": self.stop_on_no_change,
            "require_confirm": self.require_confirm,
            "use_ai": self.use_ai,
            "mark_ai": self.mark_ai,
            "snapshot": self.snapshot,
            "allow_tools": list(self.allow_tools),
        }


@dataclass
class ToolCallRecord:
    """一次工具调用（调用日志：工具、参数、耗时、结果）。"""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    detail: str = ""
    duration_ms: int = 0
    round_index: int = 0

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "args": dict(self.args),
            "ok": self.ok,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "round_index": self.round_index,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ToolCallRecord":
        return cls(
            tool=str(data.get("tool") or ""),
            args=dict(data.get("args") or {}),
            ok=bool(data.get("ok", True)),
            detail=str(data.get("detail") or ""),
            duration_ms=int(data.get("duration_ms") or 0),
            round_index=int(data.get("round_index") or 0),
        )

    def line(self) -> str:
        status = "✔" if self.ok else "✘"
        return f"{status} {self.tool}({self.args}) {self.detail}".strip()


@dataclass
class RoundReport:
    """一轮循环的结果：计划 → 工具调用 → 评分 → 每轮 diff。"""

    index: int = 1
    plan: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    score: QualityScore = field(default_factory=QualityScore)
    previous_score: Optional[QualityScore] = None
    diff: list[DiffLine] = field(default_factory=list)
    summary: str = ""
    duration_ms: int = 0
    cost: float = 0.0
    version_id: int = 0
    ai_used: bool = False          # 本轮是否用到大模型（计划/评分/改写）

    @property
    def change_count(self) -> int:
        return len(self.changes)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "plan": list(self.plan),
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "changes": [change.to_dict() for change in self.changes],
            "score": self.score.to_dict(),
            "previous_score": self.previous_score.to_dict() if self.previous_score else None,
            "diff": [line.to_dict() for line in self.diff],
            "summary": self.summary,
            "duration_ms": self.duration_ms,
            "cost": self.cost,
            "version_id": self.version_id,
            "ai_used": self.ai_used,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RoundReport":
        previous = data.get("previous_score")
        return cls(
            index=int(data.get("index") or 1),
            plan=list(data.get("plan") or []),
            tool_calls=[ToolCallRecord.from_dict(item) for item in (data.get("tool_calls") or [])],
            changes=[Change.from_dict(item) for item in (data.get("changes") or [])],
            score=QualityScore.from_dict(data.get("score") or {}),
            previous_score=QualityScore.from_dict(previous) if previous else None,
            diff=[DiffLine.from_dict(item) for item in (data.get("diff") or [])],
            summary=str(data.get("summary") or ""),
            duration_ms=int(data.get("duration_ms") or 0),
            cost=float(data.get("cost") or 0.0),
            version_id=int(data.get("version_id") or 0),
            ai_used=bool(data.get("ai_used")),
        )


@dataclass
class LoopResult:
    """循环美化的最终结果：文档 + 每轮报告 + 停止原因。"""

    ir: DocumentIR
    rounds: list[RoundReport] = field(default_factory=list)
    stopped_reason: str = STOP_MAX_ROUNDS
    before_score: QualityScore = field(default_factory=QualityScore)
    after_score: QualityScore = field(default_factory=QualityScore)
    log: list[str] = field(default_factory=list)

    @property
    def stop_label(self) -> str:
        return STOP_LABELS.get(self.stopped_reason, self.stopped_reason)

    @property
    def round_count(self) -> int:
        return len(self.rounds)

    @property
    def change_count(self) -> int:
        return sum(round_report.change_count for round_report in self.rounds)

    @property
    def total_cost(self) -> float:
        return round(sum(round_report.cost for round_report in self.rounds), 6)

    @property
    def total_ms(self) -> int:
        return sum(round_report.duration_ms for round_report in self.rounds)

    @property
    def ai_change_count(self) -> int:
        return len([
            change for round_report in self.rounds
            for change in round_report.changes if change.ai_generated
        ])

    def summary(self) -> str:
        parts = [
            f"{self.round_count} 轮 · {self.change_count} 处修改（AI {self.ai_change_count} 处）",
            f"停止原因：{self.stop_label}",
            f"质量 {self.before_score.overall * 100:.0f} → {self.after_score.overall * 100:.0f}",
        ]
        if self.total_cost or self.total_ms:
            parts.append(f"耗时 {self.total_ms / 1000:.1f}s / 成本 {self.total_cost:.4f}")
        return " · ".join(parts)

    def to_dict(self) -> dict:
        return {
            "stopped_reason": self.stopped_reason,
            "before_score": self.before_score.to_dict(),
            "after_score": self.after_score.to_dict(),
            "rounds": [round_report.to_dict() for round_report in self.rounds],
            "log": list(self.log),
        }


# ---------------------------------------------------------------- AI 记录


@dataclass
class AiCallRecord:
    """一次大模型调用（模型 / 提示词 / 工具 / 输入输出 / 耗时 / 成本）。"""

    kind: str = "chat"          # chat / agent / evaluate / plan
    role: str = ""
    model: str = ""
    prompt_preview: str = ""
    tools: list[str] = field(default_factory=list)
    output_preview: str = ""
    ok: bool = True
    detail: str = ""
    duration_ms: int = 0
    est_cost: float = 0.0
    masked: int = 0
    created_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "role": self.role,
            "model": self.model,
            "prompt_preview": self.prompt_preview,
            "tools": list(self.tools),
            "output_preview": self.output_preview,
            "ok": self.ok,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "est_cost": self.est_cost,
            "masked": self.masked,
            "created_at": self.created_at,
        }


__all__ = [
    "AiCallRecord",
    "BLOCK_CODE",
    "BLOCK_HEADING",
    "BLOCK_IMAGE",
    "BLOCK_KINDS",
    "BLOCK_LABELS",
    "BLOCK_LIST",
    "BLOCK_PAGE_BREAK",
    "BLOCK_PARAGRAPH",
    "BLOCK_QUOTE",
    "BLOCK_SLIDE",
    "BLOCK_TABLE",
    "BLOCK_TOC",
    "BeautifyOptions",
    "BeautifyResult",
    "Block",
    "Change",
    "DEFAULT_STYLES",
    "DiffLine",
    "DocumentIR",
    "LoopOptions",
    "LoopResult",
    "MERGE_APPEND",
    "MERGE_DIFF",
    "MERGE_MODES",
    "MERGE_MODE_LABELS",
    "MERGE_MODE_NOTES",
    "MERGE_SECTION",
    "MERGE_SLAVE",
    "MERGE_TABLE_COLUMNS",
    "MERGE_TABLE_KEY",
    "MERGE_TABLE_ROWS",
    "MergeOptions",
    "MergeReport",
    "MergeResult",
    "QualityScore",
    "REVISION_DELETED",
    "REVISION_INSERTED",
    "REVISION_MODIFIED",
    "REVISION_NONE",
    "RoundReport",
    "STOP_COST",
    "STOP_ERROR",
    "STOP_LABELS",
    "STOP_MAX_ROUNDS",
    "STOP_NO_CHANGE",
    "STOP_THRESHOLD",
    "STOP_TIME",
    "STOP_USER",
    "TABLE_MERGE_MODES",
    "TEMPLATE_KEYS",
    "TEMPLATE_LABELS",
    "TableData",
    "ToolCallRecord",
    "block_label",
]
