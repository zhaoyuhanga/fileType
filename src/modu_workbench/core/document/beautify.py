"""墨软文档：格式美化（MR-DOC-301）。

一条流水线，六件事（顺序有讲究）：
1. `infer_heading_levels` —— 没有标题层级的文档按编号/首位块推断层级（否则后面无从"统一"）；
2. `number_headings` —— 自动编号（阿拉伯 / 中文 / 条文），并且**先剥掉旧编号**，可重复执行；
3. `unify_styles` —— 字体、字号、行距、段距、对齐、首行缩进统一写进块的 style；
4. `beautify_tables` —— 边框、底纹、表头、斑马纹、列宽；
5. `build_toc` —— 生成目录（Word 导出时还会写 TOC 域，打开后按 F9 更新页码）；
6. `apply_brand` —— 品牌色、页眉页脚、Logo 文案、页码。

所有修改都记成 `Change`（含位置），因此界面可以"预览、确认、撤销"，
循环美化引擎也能在每轮里说清楚"改了什么"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import formats as fmt
from .models import (
    BLOCK_HEADING,
    BLOCK_PAGE_BREAK,
    BLOCK_PARAGRAPH,
    BLOCK_TABLE,
    BLOCK_TOC,
    TEMPLATE_KEYS,
    TEMPLATE_LABELS,
    BeautifyOptions,
    BeautifyResult,
    Block,
    Change,
    DocumentIR,
    TableData,
)
from .parser import guess_heading_level

#: 旧编号前缀（重新编号前先剥掉，保证可重复执行不叠加）
_NUMBER_PREFIX = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千零〇\d]+[章篇部条节]"
    r"|[一二三四五六七八九十]+、"
    r"|[（(][一二三四五六七八九十\d]+[)）]"
    r"|\d+(?:\.\d+)*[\.、]?)\s*"
)

_CN_DIGITS = "零一二三四五六七八九"


def cn_number(value: int) -> str:
    """阿拉伯数字 → 中文数字（1→一，11→十一，20→二十，105→一百零五）。"""
    number = int(value)
    if number <= 0:
        return _CN_DIGITS[0]
    if number < 10:
        return _CN_DIGITS[number]
    if number < 20:
        return "十" + (_CN_DIGITS[number - 10] if number > 10 else "")
    if number < 100:
        tens, ones = divmod(number, 10)
        return _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")
    hundreds, rest = divmod(number, 100)
    text = _CN_DIGITS[hundreds] + "百"
    if rest == 0:
        return text
    if rest < 10:
        return text + "零" + _CN_DIGITS[rest]
    return text + cn_number(rest)


# ---------------------------------------------------------------- 模板


@dataclass(frozen=True)
class TemplateSpec:
    key: str
    label: str
    note: str
    options: dict = field(default_factory=dict)


TEMPLATES: dict[str, TemplateSpec] = {
    "general": TemplateSpec(
        "general", "通用（一键美化）", "统一字体字号行距、标题层级与表格样式",
        {},
    ),
    "gov": TemplateSpec(
        "gov", "公文", "标题小标宋 + 正文仿宋三号、固定行距、无目录",
        {
            "font_cn": "仿宋_GB2312", "font_en": "Times New Roman",
            "heading_font_cn": "方正小标宋简体",
            "body_size": 16.0, "line_spacing": 1.5, "paragraph_spacing": 0.0,
            "first_line_indent": 2.0, "align": "justify", "brand_color": "#B01F24",
            "heading_sizes": {1: 22.0, 2: 16.0, 3: 16.0, 4: 16.0, 5: 16.0, 6: 16.0},
            "auto_number": False, "build_toc": False, "number_style": "chinese",
            "title_align": "center", "zebra": False,
        },
    ),
    "report": TemplateSpec(
        "report", "报告", "微软雅黑标题 + 宋体正文、自动编号、生成目录",
        {
            "font_cn": "宋体", "font_en": "Times New Roman",
            "heading_font_cn": "微软雅黑",
            "body_size": 12.0, "line_spacing": 1.5, "paragraph_spacing": 6.0,
            "auto_number": True, "build_toc": True, "number_style": "arabic",
            "brand_color": "#1452C4",
        },
    ),
    "thesis": TemplateSpec(
        "thesis", "论文", "黑体标题 + 宋体小四、1.5 倍行距、三级自动编号与目录",
        {
            "font_cn": "宋体", "font_en": "Times New Roman",
            "heading_font_cn": "黑体",
            "body_size": 12.0, "line_spacing": 1.5, "paragraph_spacing": 6.0,
            "first_line_indent": 2.0, "align": "justify",
            "auto_number": True, "build_toc": True, "toc_depth": 3,
            "brand_color": "#1F4E79",
        },
    ),
    "contract": TemplateSpec(
        "contract", "合同", "条文式编号（第X条）、两端对齐、条款表格美化",
        {
            "font_cn": "宋体", "font_en": "Times New Roman",
            "body_size": 12.0, "line_spacing": 1.5, "paragraph_spacing": 8.0,
            "first_line_indent": 0.0, "align": "justify",
            "auto_number": True, "number_style": "legal", "build_toc": False,
            "brand_color": "#1F2430",
        },
    ),
    "resume": TemplateSpec(
        "resume", "简历", "无缩进、蓝色小标题、紧凑行距、不出页码",
        {
            "font_cn": "微软雅黑", "heading_font_cn": "微软雅黑",
            "body_size": 10.5, "line_spacing": 1.3, "paragraph_spacing": 4.0,
            "first_line_indent": 0.0, "align": "left", "brand_color": "#0F62FE",
            "auto_number": False, "build_toc": False, "page_numbers": False,
        },
    ),
    "minutes": TemplateSpec(
        "minutes", "会议纪要", "黑体标题 + 仿宋正文、要点列表化、不出目录",
        {
            "font_cn": "仿宋_GB2312", "heading_font_cn": "黑体",
            "body_size": 14.0, "line_spacing": 1.5, "paragraph_spacing": 6.0,
            "brand_color": "#1452C4", "auto_number": False, "build_toc": False,
        },
    ),
}


def template_options(template: str, base: BeautifyOptions | None = None) -> BeautifyOptions:
    """取模板参数（未收录的模板回退到"通用"）。"""
    options = base or BeautifyOptions()
    spec = TEMPLATES.get(template or "general", TEMPLATES["general"])
    options.template = spec.key
    for key, value in spec.options.items():
        setattr(options, key, value)
    return options


def template_label(template: str) -> str:
    return TEMPLATE_LABELS.get(template, TEMPLATES.get(template, TEMPLATES["general"]).label)


def template_choices() -> list[tuple[str, str]]:
    return [(key, TEMPLATES[key].label) for key in TEMPLATE_KEYS if key in TEMPLATES]


# ---------------------------------------------------------------- 主流程


def beautify(ir: DocumentIR, options: BeautifyOptions | None = None, *,
             template: str = "") -> BeautifyResult:
    """按 `options` 美化一份文档（在副本上执行，原文档不动）。

    `template` 非空时先取模板默认值，再用 `options` 里**用户显式改过**的项覆盖
    （界面上"选了公文模板，又把正文改成 14pt"这种组合必须生效）。
    """
    resolved = _merge_options(template, options) if template else (options or BeautifyOptions())

    target = ir.clone()
    changes: list[Change] = []
    if resolved.infer_heading_levels:
        changes.extend(infer_heading_levels(target, resolved))
    if resolved.auto_number:
        changes.extend(number_headings(target, resolved))
    if resolved.unify_styles:
        changes.extend(unify_styles(target, resolved))
    if resolved.beautify_tables:
        changes.extend(beautify_tables(target, resolved))
    toc_entries = 0
    if resolved.build_toc:
        toc_entries, toc_changes = build_toc(target, resolved)
        changes.extend(toc_changes)
    changes.extend(apply_brand(target, resolved))

    target.styles.update(resolved.to_dict())
    return BeautifyResult(ir=target, changes=changes, template=resolved.template,
                          toc_entries=toc_entries)


def _merge_options(template: str, options: BeautifyOptions | None) -> BeautifyOptions:
    merged = template_options(template)
    if options is None:
        return merged
    defaults = BeautifyOptions()
    for key, value in options.to_dict().items():
        if value != getattr(defaults, key):
            setattr(merged, key, value)
    merged.template = template
    return merged


def _location(index: int, block: Block) -> str:
    preview = (block.text or "").strip().replace("\n", " ")[:18]
    kind = "标题" if block.kind == BLOCK_HEADING else (
        "表格" if block.kind == BLOCK_TABLE else "段落")
    return f"块 {index + 1}（{kind}「{preview}」）"


# ---------------------------------------------------------------- 1. 标题层级


def infer_heading_levels(ir: DocumentIR, options: BeautifyOptions) -> list[Change]:
    """按编号模式推断标题层级；整篇没有标题时把首个正文块当文档标题。"""
    changes: list[Change] = []
    has_heading = any(block.is_heading for block in ir.blocks)
    for index, block in enumerate(ir.blocks):
        if block.is_heading or block.kind != BLOCK_PARAGRAPH or not block.text.strip():
            continue
        level = guess_heading_level(block.text)
        if level:
            block.kind = BLOCK_HEADING
            block.level = level
            changes.append(Change(
                "标题层级", _location(index, block), f"按编号识别为 {level} 级标题",
                before=block.text[:40]))
    if not has_heading and ir.blocks:
        first = ir.blocks[0]
        if first.is_textual and first.text.strip():
            first.kind = BLOCK_HEADING
            first.level = 1
            if not ir.title:
                ir.title = first.text.strip()
            changes.append(Change("标题层级", _location(0, first), "作为文档标题（1 级）"))
    return changes


# ---------------------------------------------------------------- 2. 自动编号


def _strip_number(text: str) -> str:
    return _NUMBER_PREFIX.sub("", text or "").strip()


def _numbered_text(counters: list[int], level: int, text: str, style: str) -> str:
    if style == "chinese":
        if level == 1:
            return f"第{cn_number(counters[0])}章 {text}"
        if level == 2:
            return f"{cn_number(counters[1])}、{text}"
        if level == 3:
            return f"（{cn_number(counters[2])}）{text}"
        return f"{'.'.join(str(item) for item in counters[:level])} {text}"
    if style == "legal":
        if level == 1:
            return f"第{cn_number(counters[0])}条 {text}"
        return f"{'.'.join(str(item) for item in counters[:level])} {text}"
    return f"{'.'.join(str(item) for item in counters[:level])} {text}"


def number_headings(ir: DocumentIR, options: BeautifyOptions) -> list[Change]:
    """多级自动编号（幂等：重复执行结果一致，不会叠加编号）。

    文档标题（第一个标题且与 `ir.title` 同名）**不参与编号** ——
    否则"年度报告"会变成"1 年度报告"，目录里也会多出一条章。
    """
    changes: list[Change] = []
    counters = [0] * 6
    style = options.number_style or "arabic"
    title_block = _document_title_block(ir)
    if title_block is not None:
        # 标题本身不编号，但它就是"第一章"：这样下级标题会得到 1.1 / 1.2 而不是 0.1
        counters[0] = 1
    for index, block in enumerate(ir.blocks):
        if not block.is_heading:
            continue
        if block is title_block:
            stripped = _strip_number(block.text)
            if stripped != block.text:
                block.text = stripped
            continue
        level = max(1, min(6, block.level or 1))
        counters[level - 1] += 1
        for deeper in range(level, 6):
            counters[deeper] = 0
        body = _strip_number(block.text)
        new_text = _numbered_text(counters, level, body, style)
        if new_text != block.text:
            changes.append(Change(
                "自动编号", _location(index, block),
                f"{block.text.strip()[:20]} → {new_text[:24]}",
                before=block.text, after=new_text))
            block.text = new_text
    return changes


def _document_title_block(ir: DocumentIR) -> Block | None:
    """判断哪个标题是"文档标题"（不参与编号）。

    判据（都满足才算标题）：是文档的第一块、本身没有编号、层级是本篇最高级、
    且同层级只有它一个 —— 这样"# 年度报告 + ## 1.1 概述"里的年度报告不会被编成
    「1 年度报告」，而"# 概述 + # 方法"这种多章文档仍会正常编号。
    """
    headings = [block for block in ir.blocks if block.is_heading]
    if not headings:
        return None
    first = headings[0]
    if not ir.blocks or ir.blocks[0] is not first:
        return None
    if _NUMBER_PREFIX.match(first.text or ""):
        return None
    level = first.level or 1
    if level > min((item.level or 1) for item in headings):
        return None
    same_level = [item for item in headings if (item.level or 1) <= level]
    if len(same_level) > 1:
        return None
    return first


# ---------------------------------------------------------------- 3. 样式统一


def unify_styles(ir: DocumentIR, options: BeautifyOptions) -> list[Change]:
    """字体/字号/行距/段距/对齐/首行缩进统一（写进块的 style，导出时落地）。"""
    heading_sizes = options.resolved_heading_sizes()
    changes: list[Change] = []
    seen: set[str] = set()
    for index, block in enumerate(ir.blocks):
        style: dict = {
            "font_cn": options.font_cn,
            "font_en": options.font_en,
            "line_spacing": options.line_spacing,
            "paragraph_spacing": options.paragraph_spacing,
        }
        if block.kind == BLOCK_HEADING:
            level = max(1, min(6, block.level or 1))
            style.update({
                "font_cn": options.heading_font_cn or options.font_cn,
                "font_en": options.heading_font_en or options.font_en,
                "size": heading_sizes.get(level, options.body_size),
                "bold": True,
                "level": level,
                "color": options.brand_color,
                "align": options.title_align if level == 1 else options.align,
            })
        elif block.kind == BLOCK_TABLE:
            continue
        elif block.kind in (BLOCK_TOC, BLOCK_PAGE_BREAK):
            # 目录与分页由生成器自己负责排版，不参与"正文样式统一"
            continue
        else:
            style.update({
                "size": options.body_size,
                "bold": False,
                "align": options.align,
                "first_line_indent": options.first_line_indent,
            })
        if block.style != style:
            block.style = style
            key = f"{block.kind}-{block.level}" if block.kind == BLOCK_HEADING else block.kind
            if key not in seen:
                seen.add(key)
                changes.append(Change(
                    "样式统一", _location(index, block),
                    f"{_style_text(style)}"))
    return changes


def _style_text(style: dict) -> str:
    parts = [f"{style.get('font_cn', '')}"]
    if style.get("size"):
        parts.append(f"{float(style['size']):g}pt")
    if style.get("bold"):
        parts.append("加粗")
    if style.get("line_spacing"):
        parts.append(f"行距 {float(style['line_spacing']):g}")
    if style.get("first_line_indent"):
        parts.append(f"首行缩进 {float(style['first_line_indent']):g} 字")
    return " · ".join(part for part in parts if part)


# ---------------------------------------------------------------- 4. 表格美化


def beautify_tables(ir: DocumentIR, options: BeautifyOptions) -> list[Change]:
    """表格边框/底纹/表头/对齐/列宽（导出 docx/xlsx 时落地）。"""
    changes: list[Change] = []
    for index, block in enumerate(ir.blocks):
        if block.kind != BLOCK_TABLE or block.table is None:
            continue
        table = block.table
        before = dict(table.styles)
        rows = table.rows
        header_missing = not any(cell.strip() for cell in table.header_row())
        if header_missing and len(rows) > 1:
            table.header = False
        widths = _column_widths(table)
        table.styles.update({
            "border": "grid",
            "header": bool(table.header),
            "header_fill": _lighten(options.brand_color, 0.82),
            "zebra": options.zebra,
            "align": "left",
            "header_align": "center",
            "valign": "center",
            "column_widths": widths,
            "font_cn": options.font_cn,
            "font_en": options.font_en,
            "size": max(8.0, options.body_size - 1),
        })
        if table.styles != before:
            changes.append(Change(
                "表格美化",
                f"块 {index + 1}（表格「{table.name or '未命名'}」）",
                f"网格边框 · 表头底纹 · {'斑马纹 · ' if options.zebra else ''}"
                f"{len(widths)} 列宽自适应",
            ))
    return changes


def _column_widths(table: TableData) -> list[int]:
    """按内容长度估算列宽（导出 Excel 时按字符宽度落地）。"""
    rows = table.normalized()
    if not rows:
        return []
    widths: list[int] = []
    for column in range(len(rows[0])):
        longest = max((len(str(row[column])) for row in rows), default=6)
        widths.append(min(40, max(8, longest + 2)))
    return widths


def _lighten(hex_color: str, ratio: float) -> str:
    value = re.sub(r"[^0-9a-fA-F]", "", hex_color or "")[:6] or "1452C4"
    rgb = [int(value[index:index + 2], 16) for index in (0, 2, 4)]
    return "".join(f"{round(channel + (255 - channel) * ratio):02X}" for channel in rgb)


# ---------------------------------------------------------------- 5. 目录


def build_toc(ir: DocumentIR, options: BeautifyOptions) -> tuple[int, list[Change]]:
    """生成目录块（放在文档标题之后）；已有且内容一致时不重复报告改动。"""
    existing = next((block for block in ir.blocks if block.kind == BLOCK_TOC), None)
    previous_text = existing.text if existing is not None else None
    ir.blocks = [block for block in ir.blocks if block.kind != BLOCK_TOC]
    entries = [item for item in ir.outline() if item[0] <= max(1, options.toc_depth)]
    if not entries:
        return 0, []
    lines = ["目录"]
    for level, text, _index in entries:
        lines.append(f"{'    ' * (level - 1)}{text}")
    text = "\n".join(lines)
    block = Block(kind=BLOCK_TOC, text=text, meta={"entries": len(entries)})
    position = 1 if ir.blocks and ir.blocks[0].is_heading else 0
    ir.blocks.insert(min(position, len(ir.blocks)), block)
    if previous_text == text:
        return len(entries), []
    change = Change(
        "生成目录", "位于文档开头",
        f"共 {len(entries)} 条（深度 {options.toc_depth} 级）；Word 导出后按 F9 可更新页码",
    )
    return len(entries), [change]


# ---------------------------------------------------------------- 6. 品牌与页眉页脚


def apply_brand(ir: DocumentIR, options: BeautifyOptions) -> list[Change]:
    """品牌色 / Logo 文案 / 页眉页脚 / 页码写进文档样式（导出时落地）。"""
    changes: list[Change] = []
    brand = {
        "brand_color": options.brand_color,
        "header_text": options.header_text,
        "footer_text": options.footer_text,
        "logo_text": options.logo_text,
        "page_numbers": options.page_numbers,
        "title_align": options.title_align,
    }
    for key, value in brand.items():
        if ir.styles.get(key) != value:
            ir.styles[key] = value
    if options.header_text or options.footer_text or options.logo_text:
        parts = []
        if options.header_text:
            parts.append(f"页眉「{options.header_text}」")
        if options.footer_text:
            parts.append(f"页脚「{options.footer_text}」")
        if options.logo_text:
            parts.append(f"Logo「{options.logo_text}」")
        parts.append(f"品牌色 {options.brand_color}")
        changes.append(Change("品牌规范", "整篇", " · ".join(parts)))
    if options.page_numbers and not ir.styles.get("page_numbers"):
        ir.styles["page_numbers"] = True
        changes.append(Change("页码", "页脚", "已启用页码（Word/PDF 导出可见）"))
    return changes


# ---------------------------------------------------------------- 体检辅助


def capability_text() -> str:
    """美化能力的说明（设置页/帮助）。"""
    return (
        f"一键美化支持 {len(TEMPLATES)} 套模板（"
        + "、".join(spec.label for spec in TEMPLATES.values())
        + "），覆盖 "
        + "、".join([
            "字体字号统一", "标题层级识别", "多级自动编号", "目录生成",
            "表格美化", "品牌配色与页眉页脚", "页码与导出保版",
        ])
    )


def template_matrix() -> list[dict[str, str]]:
    """模板一览（界面下拉与文档表格用）。"""
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "note": spec.note,
            "font": str(spec.options.get("font_cn", "宋体")),
            "size": f"{float(spec.options.get('body_size', 12.0)):g}pt",
            "toc": "生成目录" if spec.options.get("build_toc") else "不生成目录",
        }
        for spec in TEMPLATES.values()
    ]


def export_targets_hint() -> str:
    return "、".join(fmt.export_label(key) for key in fmt.EXPORT_TARGET_KEYS)


__all__ = [
    "TEMPLATES",
    "TemplateSpec",
    "apply_brand",
    "beautify",
    "beautify_tables",
    "build_toc",
    "capability_text",
    "cn_number",
    "export_targets_hint",
    "infer_heading_levels",
    "number_headings",
    "template_choices",
    "template_label",
    "template_matrix",
    "template_options",
    "unify_styles",
]
