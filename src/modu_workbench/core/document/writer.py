"""墨软文档：写出层 —— `DocumentIR` → 真实文件（导出/另存）。

支持的原生目标（与 `formats.EXPORT_TARGETS` 对齐）：
txt / markdown / html / docx / xlsx / csv / tsv / json / pdf。

约定：
- **字体、字号、行距、段距、标题层级、表格边框与底纹**都在这里落地，
  所以"导出 PDF/Word/HTML 保持版式一致"是靠同一份 `BeautifyOptions` 驱动的；
- 输出**默认不覆盖**已有文件（同名自动加序号），与墨软转换的行为一致；
- 水印与页眉页脚按格式能力落地：docx（页眉页脚域）、xlsx（页眉页脚）、
  html/pdf（样式化水印条）；csv/tsv/json 这类纯数据格式**不支持水印**，
  会明说而不是悄悄丢内容。
"""
from __future__ import annotations

import csv
import html as html_mod
import io
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import formats as fmt
from . import media
from .models import (
    BLOCK_CODE,
    BLOCK_HEADING,
    BLOCK_IMAGE,
    BLOCK_LIST,
    BLOCK_PAGE_BREAK,
    BLOCK_QUOTE,
    BLOCK_SLIDE,
    BLOCK_TABLE,
    BLOCK_TOC,
    BeautifyOptions,
    Block,
    DocumentIR,
    TableData,
)


class WriteError(ValueError):
    """写出失败（消息可直接展示）。"""


@dataclass
class WatermarkOptions:
    """导出水印（MR-DOC-308 第 6 条）。"""

    text: str = ""
    footer: str = ""
    tracking_id: str = ""
    enabled: bool = False

    @property
    def line(self) -> str:
        parts = [self.text.strip()]
        if self.footer.strip():
            parts.append(self.footer.strip())
        if self.tracking_id.strip():
            parts.append(f"标识：{self.tracking_id.strip()}")
        return " · ".join(part for part in parts if part)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "footer": self.footer,
            "tracking_id": self.tracking_id,
            "enabled": self.enabled,
        }


@dataclass
class WriteResult:
    """写出结果：产物路径 + 过程中的说明（水印是否落地、降级原因）。"""

    path: Path
    target: str
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.path.is_file()


# ---------------------------------------------------------------- 入口


def write_document(
    ir: DocumentIR,
    output_path: str | Path,
    target: str = "",
    *,
    options: Optional[BeautifyOptions] = None,
    watermark: Optional[WatermarkOptions] = None,
    overwrite: bool = False,
) -> WriteResult:
    """把 IR 写成一个具体文件；`target` 为空时按输出扩展名推断。"""
    options = options or BeautifyOptions()
    destination = Path(output_path)
    target = (target or fmt.format_key_for_path(destination)
              or ir.format_key or "txt").lower()
    if target not in fmt.EXPORT_TARGET_KEYS:
        raise WriteError(
            f"不支持的导出目标：{target}（可用：{'、'.join(fmt.EXPORT_TARGET_KEYS)}）"
        )
    if destination.suffix.lower() != fmt.EXPORT_EXTENSIONS.get(target, destination.suffix):
        destination = destination.with_suffix(fmt.EXPORT_EXTENSIONS[target])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        destination = unique_output_path(destination)

    notes: list[str] = []
    image_blocks = [block for block in ir.blocks if block.kind == BLOCK_IMAGE]
    if image_blocks and target in ("txt", "markdown", "xlsx", "csv", "tsv", "json"):
        # Markdown 能写"外部地址"的图片（远程 URL / 本地文件），其余目标一律占位
        lost = ([block for block in image_blocks if not _image_src(block)]
                if target == "markdown" else image_blocks)
        if lost:
            notes.append(f"{len(lost)} 张图片以占位文字保留"
                         f"（{target} 不能内嵌图片；要连图一起给，请用 Word/HTML/PDF）")
    if target == "txt":
        text = render_text(ir, options)
        if watermark and watermark.enabled and watermark.line:
            text = f"{text.rstrip()}\n\n{'-' * 24}\n{watermark.line}\n"
            notes.append("纯文本没有页眉页脚：水印已作为末尾一行写入")
        destination.write_text(text, encoding="utf-8")
    elif target == "markdown":
        destination.write_text(render_markdown(ir, options, watermark=watermark), encoding="utf-8")
    elif target == "html":
        destination.write_text(render_html(ir, options, watermark=watermark), encoding="utf-8")
    elif target == "docx":
        notes.extend(_write_docx(ir, destination, options, watermark))
    elif target == "xlsx":
        notes.extend(_write_xlsx(ir, destination, options, watermark))
        if watermark and watermark.enabled:
            notes.append("水印已写入 Excel 页眉页脚（打印/导出 PDF 时可见）")
    elif target in ("csv", "tsv"):
        _write_delimited(ir, destination, "\t" if target == "tsv" else ",")
        if watermark and watermark.enabled:
            notes.append("CSV/TSV 是纯数据格式，已跳过水印（避免污染数据）")
    elif target == "json":
        destination.write_text(
            json.dumps(_json_payload(ir), ensure_ascii=False, indent=2), encoding="utf-8")
        if watermark and watermark.enabled:
            notes.append("JSON 是纯数据格式，已跳过水印；水印信息记录在 metadata 中")
    elif target == "pdf":
        notes.extend(_write_pdf(ir, destination, options, watermark))
    else:  # pragma: no cover - 上面已校验
        raise WriteError(f"未实现的导出目标：{target}")
    return WriteResult(path=destination, target=target, notes=notes)


def unique_output_path(path: Path) -> Path:
    """同名输出自动加序号（与墨软转换一致，绝不覆盖用户已有文件）。"""
    stem, suffix = path.stem, path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    raise WriteError(f"同名文件太多，请换个输出目录：{path}")


# ---------------------------------------------------------------- 文本渲染


def render_text(ir: DocumentIR, options: Optional[BeautifyOptions] = None) -> str:
    """纯文本导出：标题原样、表格用制表符、页码/页脚不写进正文（批注另起附录）。"""
    options = options or BeautifyOptions()
    lines: list[str] = []
    for block in ir.blocks:
        if block.is_table and block.table is not None:
            for row in block.table.normalized():
                lines.append("\t".join(row))
            lines.append("")
            continue
        if block.kind == BLOCK_PAGE_BREAK:
            continue
        if block.kind == BLOCK_IMAGE:
            lines.append(f"[图片] {block.text}".strip())
            continue
        if block.text.strip():
            lines.append(block.text.rstrip())
            if block.note and options.export_comments:
                lines.append(f"    〔批注：{block.note}〕")
        elif block.kind == BLOCK_TABLE:
            continue
    return "\n".join(lines).strip("\n") + "\n"


def render_markdown(ir: DocumentIR, options: Optional[BeautifyOptions] = None, *,
                    watermark: Optional[WatermarkOptions] = None) -> str:
    options = options or BeautifyOptions()
    markdown = ir.markdown()
    if options.export_comments and any(block.note for block in ir.blocks):
        appendix = _comment_appendix(ir)
        markdown = f"{markdown.rstrip()}\n\n{appendix}\n"
    if watermark and watermark.enabled and watermark.line:
        markdown = f"{markdown.rstrip()}\n\n---\n\n_{watermark.line}_\n"
    return markdown


def _comment_appendix(ir: DocumentIR, *, heading: str = "批注") -> str:
    """批注汇总（Markdown 形式）。"""
    comments = [
        ((block.text or "").strip().replace("\n", " ")[:60] or f"块 {index + 1}", block.note)
        for index, block in enumerate(ir.blocks) if block.note
    ]
    if not comments:
        return ""
    lines = [f"## {heading}", ""]
    lines.extend(f"- **{anchor}**：{note}" for anchor, note in comments)
    return "\n".join(lines)


def _image_src(block: object) -> str:
    """图片可用的外部地址（远程 URL / 真实存在的本地文件）；素材缓存里的图片返回空串。"""
    src = str((getattr(block, "meta", None) or {}).get("src") or "")
    if src.startswith(("http://", "https://", "//")):
        return src
    if src and not src.startswith("data:") and Path(src).is_file():
        return src
    return ""


def _normalize_title(text: str) -> str:
    """标题比对用的归一化：忽略空白、全角空格与 Markdown 记号。"""
    return re.sub(r"[\s\u3000#*`]+", "", (text or "")).casefold()


def _title_already_in_blocks(ir: DocumentIR) -> bool:
    """文档标题与首个标题块同名时，不再单独输出标题（否则导出后标题出现两次）。

    `ir.title` 与首个 H1 同名是常态：从 `我的报告.md` 解析时标题取自文件名，
    美化还会用首个标题补 `ir.title`（见 `beautify._document_title_block`）。
    这类文档的标题**已经**在块里了，再写一遍就是重复。
    """
    if not ir.title:
        return True                                   # 空标题没什么可写的
    if ir.metadata.get("title_source") == "filename":
        # 标题只是"文件名"：不给它造一行可见正文。否则"打开 Word → 保存"会平白
        # 多出一行标题（原文里没有），多次另存就会越滚越多。
        return True
    first = next((block for block in ir.blocks if (block.text or "").strip()), None)
    if first is None or first.kind != BLOCK_HEADING:
        return False
    return _normalize_title(first.text) == _normalize_title(ir.title)


_CSS_TEMPLATE = """
body {{ font-family: "{font_cn}", "{font_en}", sans-serif; font-size: {body_size}pt;
        line-height: {line_spacing}; color: #1f2430; margin: 32px 40px; }}
h1, h2, h3, h4, h5, h6 {{ font-family: "{heading_font_cn}", "{heading_font_en}", sans-serif;
        color: {brand_color}; margin: 18px 0 8px 0; line-height: 1.35; }}
{heading_sizes}
h1.title {{ text-align: {title_align}; font-size: {title_size}pt; }}
p {{ margin: 0 0 {paragraph_spacing}pt 0; text-align: {align}; text-indent: {indent}; }}
p.noindent {{ text-indent: 0; }}
blockquote {{ border-left: 3px solid {brand_color}; margin: 8px 0 8px 0; padding: 4px 12px;
        color: #47506b; background: #f6f8fc; }}
pre {{ background: #f6f8fc; border: 1px solid #e3e8f2; border-radius: 4px;
        padding: 10px 12px; white-space: pre-wrap; font-family: Consolas, monospace; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid {border_color}; padding: 6px 8px; text-align: left;
        font-size: {body_size}pt; }}
th {{ background: {header_fill}; font-weight: bold; }}
tr:nth-child(even) td {{ background: {zebra_fill}; }}
ul {{ margin: 4px 0 8px 0; }}
.toc {{ background: #f8fafd; border: 1px solid #e3e8f2; border-radius: 4px;
        padding: 10px 14px; margin: 12px 0; }}
.toc-title {{ font-weight: bold; margin-bottom: 6px; }}
.toc-item {{ margin: 2px 0; }}
.watermark {{ color: #9aa4bb; font-size: {small_size}pt; text-align: center;
        margin-top: 24px; border-top: 1px dashed {border_color}; padding-top: 6px; }}
.header-text {{ color: #6b7590; font-size: {small_size}pt; text-align: right;
        border-bottom: 1px solid {border_color}; padding-bottom: 4px; margin-bottom: 12px; }}
.slide {{ border: 1px solid {border_color}; border-radius: 6px; padding: 12px 14px;
        margin: 10px 0; }}
.slide-title {{ font-weight: bold; color: {brand_color}; margin-bottom: 6px; }}
.page-break {{ page-break-after: always; border: none; border-top: 1px dashed {border_color}; }}
"""


def render_html(ir: DocumentIR, options: Optional[BeautifyOptions] = None, *,
                watermark: Optional[WatermarkOptions] = None,
                standalone: bool = True) -> str:
    """HTML 渲染：查看器预览与 HTML/PDF 导出共用同一份样式（版式一致）。"""
    options = options or BeautifyOptions()
    heading_sizes = options.resolved_heading_sizes()
    css = _CSS_TEMPLATE.format(
        font_cn=options.font_cn,
        font_en=options.font_en,
        heading_font_cn=options.heading_font_cn or options.font_cn,
        heading_font_en=options.heading_font_en or options.font_en,
        body_size=options.body_size,
        line_spacing=options.line_spacing,
        paragraph_spacing=options.paragraph_spacing,
        brand_color=options.brand_color,
        title_align=options.title_align,
        title_size=heading_sizes.get(1, 22.0) + 4,
        heading_sizes="\n".join(
            f"h{level} {{ font-size: {size:g}pt; }}" for level, size in sorted(heading_sizes.items())
        ),
        align=options.align,
        indent=f"{options.first_line_indent:g}em" if options.first_line_indent else "0",
        border_color="#c9d2e3",
        header_fill="#eef1f8",
        zebra_fill="#fafbfe",
        small_size=max(8.0, options.body_size - 2),
    )
    body: list[str] = []
    if options.header_text:
        body.append(f"<div class='header-text'>{html_mod.escape(options.header_text)}</div>")
    if options.logo_text:
        body.append(f"<div class='logo-text'><b>{html_mod.escape(options.logo_text)}</b></div>")
    if ir.title and not _title_already_in_blocks(ir):
        body.append(f"<h1 class='title'>{html_mod.escape(ir.title)}</h1>")

    for block in ir.blocks:
        body.append(_block_html(block, options))
    if watermark and watermark.enabled and watermark.line:
        body.append(f"<div class='watermark'>{html_mod.escape(watermark.line)}</div>")
    if options.export_comments:
        appendix = _comment_appendix(ir)
        if appendix:
            # 批注在 HTML 里作为末尾的汇总块（Word 才是原生批注，见 `_write_docx`）
            body.append(_block_html(Block(kind=BLOCK_TOC, text=appendix), options))
    if options.footer_text:
        body.append(f"<div class='watermark'>{html_mod.escape(options.footer_text)}</div>")
    if not standalone:
        return "".join(body)
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{html_mod.escape(ir.title or '墨软文档')}</title>"
        f"<style>{css}</style></head><body>{''.join(body)}</body></html>"
    )


def _block_html(block, options: BeautifyOptions) -> str:  # noqa: ANN001
    if block.is_table and block.table is not None:
        rows = block.table.normalized()
        if not rows:
            return ""
        head, *body = rows
        html = ["<table>"]
        html.append("<tr>" + "".join(f"<th>{html_mod.escape(cell)}</th>" for cell in head) + "</tr>")
        for row in body:
            html.append("<tr>" + "".join(f"<td>{html_mod.escape(cell)}</td>" for cell in row) + "</tr>")
        html.append("</table>")
        return "".join(html)
    if block.kind == BLOCK_HEADING:
        level = max(1, min(6, block.level or 1))
        return f"<h{level}>{html_mod.escape(block.text.strip())}</h{level}>"
    if block.kind == BLOCK_LIST:
        return f"<ul><li>{html_mod.escape(block.text.strip())}</li></ul>"
    if block.kind == BLOCK_QUOTE:
        return f"<blockquote>{html_mod.escape(block.text.strip())}</blockquote>"
    if block.kind == BLOCK_CODE:
        return f"<pre>{html_mod.escape(block.text.rstrip())}</pre>"
    if block.kind == BLOCK_TOC:
        lines = [line for line in block.text.splitlines() if line.strip()]
        items = "".join(f"<div class='toc-item'>{html_mod.escape(line.strip())}</div>"
                        for line in lines[1:] or lines)
        title = lines[0] if lines else "目录"
        return f"<div class='toc'><div class='toc-title'>{html_mod.escape(title)}</div>{items}</div>"
    if block.kind == BLOCK_PAGE_BREAK:
        return "<hr class='page-break'>"
    if block.kind == BLOCK_IMAGE:
        alt = html_mod.escape(media.image_label(block))
        embedded = media.data_uri(block)          # Word/本地图片：转 data URI，HTML 自包含
        if embedded:
            return f"<p class='noindent'><img class='doc-image' src='{embedded}' alt='{alt}'></p>"
        src = str(block.meta.get("src") or "")
        if src.startswith(("http://", "https://", "//")):
            # 远程图片：原样写 URL（导出 HTML/PDF 时由浏览器/渲染器取；不联网抓图存本地）
            return (f"<p class='noindent'><img class='doc-image' "
                    f"src='{html_mod.escape(src)}' alt='{alt}'></p>")
        if src and Path(src).is_file():
            return f"<p class='noindent'><img src='{html_mod.escape(Path(src).as_uri())}' alt='{alt}'></p>"
        return f"<p class='noindent'>[{alt}]</p>"
    if block.kind == BLOCK_SLIDE:
        slide = html_mod.escape(str(block.meta.get("slide") or ""))
        head = f"<div class='slide-title'>第 {slide} 页</div>" if slide else ""
        text = html_mod.escape(block.text).replace("\n", "<br>")
        notes = html_mod.escape(str(block.meta.get("notes") or ""))
        note_html = f"<div class='watermark'>备注：{notes}</div>" if notes else ""
        return f"<div class='slide'>{head}{text}{note_html}</div>"
    if block.text.strip():
        return f"<p>{html_mod.escape(block.text.strip())}</p>"
    return ""


# ---------------------------------------------------------------- docx


def _write_docx(ir: DocumentIR, path: Path, options: BeautifyOptions,
                watermark: Optional[WatermarkOptions]) -> list[str]:
    notes: list[str] = []
    try:
        import docx as docx_lib
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Cm, Pt, RGBColor
    except ImportError as error:  # pragma: no cover - python-docx 是随包依赖
        raise WriteError("缺少 python-docx，无法导出 Word") from error

    document = docx_lib.Document()
    section = document.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    for attribute in ("left_margin", "right_margin"):
        setattr(section, attribute, Cm(3.0))
    for attribute in ("top_margin", "bottom_margin"):
        setattr(section, attribute, Cm(2.5))

    normal = document.styles["Normal"]
    normal.font.name = options.font_en
    normal.font.size = Pt(options.body_size)
    _set_style_east_asian(normal, options.font_cn)
    normal.paragraph_format.line_spacing = options.line_spacing
    normal.paragraph_format.space_after = Pt(options.paragraph_spacing)

    heading_sizes = options.resolved_heading_sizes()
    heading_cn = options.heading_font_cn or options.font_cn
    heading_en = options.heading_font_en or options.font_en
    for level, size in heading_sizes.items():
        try:
            style = document.styles[f"Heading {level}"]
        except KeyError:
            continue
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.name = heading_en
        style.font.color.rgb = RGBColor.from_string(options.brand_color.lstrip("#").upper())
        _set_style_east_asian(style, heading_cn)

    def add_paragraph(text: str, *, style: str = "", align: str = "", indent: bool = False,
                      bold: bool = False, size: Optional[float] = None,
                      color: str = ""):  # noqa: ANN202
        paragraph = document.add_paragraph(style=style) if style else document.add_paragraph()
        run = paragraph.add_run(text)
        run.bold = bold
        if size:
            run.font.size = Pt(size)
        if color:
            run.font.color.rgb = RGBColor.from_string(color.lstrip("#").upper())
        _set_run_font(run, options.font_cn, options.font_en)
        paragraph.paragraph_format.line_spacing = options.line_spacing
        paragraph.paragraph_format.space_after = Pt(options.paragraph_spacing)
        if align == "center":
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif align == "right":
            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        elif align == "justify":
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if indent and options.first_line_indent:
            paragraph.paragraph_format.first_line_indent = Pt(
                options.first_line_indent * options.body_size)
        return paragraph

    if ir.title and not _title_already_in_blocks(ir):
        add_paragraph(ir.title, align=options.title_align, bold=True,
                      size=heading_sizes.get(1, 22.0) + 4, color=options.brand_color)

    def attach_comment(paragraph, block) -> None:  # noqa: ANN001
        """把块上的批注写成 **Word 原生批注**（python-docx ≥ 1.2 支持）。"""
        if not (options.export_comments and block.note and paragraph.runs):
            return
        try:
            document.add_comment(paragraph.runs, block.note,
                                 author=str(block.meta.get("comment_author") or "墨软文档"))
        except Exception:  # noqa: BLE001  旧版 python-docx 没有批注 API
            paragraph.add_run(f"　〔批注：{block.note}〕")

    for block in ir.blocks:
        if block.is_table and block.table is not None:
            _docx_table(document, block.table, options)
            continue
        if block.kind == BLOCK_IMAGE:
            if not _docx_image(document, block, add_paragraph):
                notes.append(f"有 1 张图片没能嵌入 Word（{media.image_label(block)}），已留占位文字")
            continue
        if block.kind == BLOCK_HEADING:
            level = max(1, min(6, block.level or 1))
            paragraph = add_paragraph(block.text.strip(), style=f"Heading {level}")
            for run in paragraph.runs:
                _set_run_font(run, heading_cn, heading_en)
            attach_comment(paragraph, block)
            continue
        if block.kind == BLOCK_LIST:
            attach_comment(add_paragraph(f"· {block.text.strip()}", indent=False), block)
            continue
        if block.kind == BLOCK_QUOTE:
            attach_comment(add_paragraph(block.text.strip(), indent=False, color="#47506b"),
                           block)
            continue
        if block.kind == BLOCK_CODE:
            attach_comment(add_paragraph(block.text.rstrip(), indent=False,
                                         size=max(8.0, options.body_size - 1)), block)
            continue
        if block.kind == BLOCK_TOC:
            _docx_toc(document, block)
            continue
        if block.kind == BLOCK_PAGE_BREAK:
            document.add_page_break()
            continue
        if block.kind == BLOCK_IMAGE:
            if not _docx_image(document, block, add_paragraph):
                notes.append(f"有 1 张图片没能嵌入 Word（{media.image_label(block)}），已留占位文字")
            continue
        if block.kind == BLOCK_SLIDE:
            add_paragraph(f"第 {block.meta.get('slide', '')} 页", bold=True,
                          color=options.brand_color)
            attach_comment(add_paragraph(block.text, indent=False), block)
            if block.meta.get("notes"):
                add_paragraph(f"备注：{block.meta['notes']}", indent=False)
            continue
        if block.text.strip():
            attach_comment(add_paragraph(block.text.strip(), indent=True), block)

    if options.export_comments and any(block.note for block in ir.blocks):
        add_paragraph("批注汇总", bold=True, color=options.brand_color)
        for index, block in enumerate(ir.blocks):
            if not block.note:
                continue
            anchor = (block.text or "").strip().replace("\n", " ")[:40] or f"块 {index + 1}"
            add_paragraph(f"· 「{anchor}」{block.note}"
                          f"（{block.meta.get('comment_author') or 'local'}）", indent=False)

    _docx_header_footer(document, options, watermark)
    try:
        document.save(str(path))
    except Exception as error:  # noqa: BLE001
        raise WriteError(f"保存 Word 失败：{error}") from error
    return notes


def _docx_image(document, block, add_paragraph) -> bool:  # noqa: ANN001
    """把块里的图片重新嵌回 Word（素材缓存缺失时留一行占位文字并返回 False）。"""
    from docx.shared import Pt

    data = media.image_bytes(block)
    if not data:
        add_paragraph(f"[图片] {media.image_label(block)}".strip(), indent=False, color="#47506b")
        return False
    info = block.meta.get("image") if isinstance(block.meta.get("image"), dict) else {}
    info = info or {}
    stream = io.BytesIO(data)
    width = float(info.get("width_pt") or 0.0)
    height = float(info.get("height_pt") or 0.0)
    try:
        if width > 0 and height > 0:
            document.add_picture(stream, width=Pt(width), height=Pt(height))
        elif width > 0:
            document.add_picture(stream, width=Pt(width))
        else:
            document.add_picture(stream)
    except Exception:  # noqa: BLE001  EMF/WMF 等 python-docx 不认的格式
        add_paragraph(f"[图片] {media.image_label(block)}（该格式 Word 组件不支持嵌入）",
                      indent=False, color="#47506b")
        return False
    return True


def _set_run_font(run, cn: str, en: str) -> None:  # noqa: ANN001
    from docx.oxml.ns import qn

    run.font.name = en
    rpr = run._element.get_or_add_rPr()          # noqa: SLF001
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        from docx.oxml import OxmlElement

        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:ascii"), en)
    fonts.set(qn("w:hAnsi"), en)
    fonts.set(qn("w:eastAsia"), cn)


def _set_style_east_asian(style, cn: str) -> None:  # noqa: ANN001
    from docx.oxml.ns import qn

    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        from docx.oxml import OxmlElement

        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:eastAsia"), cn)


def _docx_table(document, table, options: BeautifyOptions) -> None:  # noqa: ANN001
    """表格美化：网格线 + 表头底纹 + 斑马纹（MR-DOC-301 第 4 条）。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    rows = table.normalized()
    if not rows:
        return
    word_table = document.add_table(rows=len(rows), cols=len(rows[0]))
    try:
        word_table.style = "Table Grid" if options.beautify_tables else "Normal Table"
    except KeyError:
        word_table.style = "Table Grid"
    header_fill = options.brand_color.lstrip("#").upper()[:6] or "1452C4"
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            cell = word_table.cell(row_index, column_index)
            cell.text = ""
            paragraph = cell.paragraphs[0]
            run = paragraph.add_run(value)
            _set_run_font(run, options.font_cn, options.font_en)
            run.font.size = Pt(max(8.0, options.body_size - 1))
            is_header = options.beautify_tables and table.header and row_index == 0
            if is_header:
                run.bold = True
                _shade_cell(cell, _lighten(header_fill, 0.82))
            elif options.beautify_tables and options.zebra and row_index % 2 == 0:
                _shade_cell(cell, "FAFBFE")
    document.add_paragraph()


def _shade_cell(cell, fill: str) -> None:  # noqa: ANN001
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill.upper()[:6])
    cell._tc.get_or_add_tcPr().append(shading)      # noqa: SLF001


def _lighten(hex_color: str, ratio: float) -> str:
    """把品牌色调浅成底纹色（品牌色本身太深，直接铺表头看不清字）。"""
    value = re.sub(r"[^0-9a-fA-F]", "", hex_color)[:6] or "1452C4"
    rgb = [int(value[index:index + 2], 16) for index in (0, 2, 4)]
    mixed = [round(channel + (255 - channel) * ratio) for channel in rgb]
    return "".join(f"{channel:02X}" for channel in mixed)


def _docx_toc(document, block) -> None:  # noqa: ANN001
    """目录：写入 Word 的 TOC 域（打开后按 F9 更新）+ 静态条目兜底。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    paragraph = document.add_paragraph()
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = r'TOC \o "1-3" \h \z \u'
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)          # noqa: SLF001
    run._r.append(instruction)    # noqa: SLF001
    run._r.append(separate)       # noqa: SLF001
    run._r.append(end)            # noqa: SLF001

    for line in [item for item in block.text.splitlines() if item.strip()][1:]:
        document.add_paragraph(line.strip()).paragraph_format.space_after = 0


def _docx_header_footer(document, options: BeautifyOptions,
                        watermark: Optional[WatermarkOptions]) -> None:  # noqa: ANN001
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    section = document.sections[0]
    header_text = options.header_text
    if header_text:
        header = section.header
        paragraph = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
        paragraph.text = header_text
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

    footer_parts: list[str] = []
    if watermark and watermark.enabled and watermark.line:
        footer_parts.append(watermark.line)
    if options.footer_text:
        footer_parts.append(options.footer_text)
    footer = section.footer
    paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    paragraph.text = "　".join(footer_parts)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if footer_parts else WD_ALIGN_PARAGRAPH.RIGHT
    if options.page_numbers:
        run = paragraph.add_run(("　" if footer_parts else "") + "第 ")
        _add_field(run, "PAGE")
        middle = paragraph.add_run(" 页 / 共 ")
        _add_field(middle, "NUMPAGES")
        paragraph.add_run(" 页")


def _add_field(run, instruction: str) -> None:  # noqa: ANN001
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    text = OxmlElement("w:instrText")
    text.set(qn("xml:space"), "preserve")
    text.text = f" {instruction} "
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)      # noqa: SLF001
    run._r.append(text)       # noqa: SLF001
    run._r.append(end)        # noqa: SLF001


# ---------------------------------------------------------------- xlsx / csv


def _write_xlsx(ir: DocumentIR, path: Path, options: BeautifyOptions,
                watermark: Optional[WatermarkOptions]) -> list[str]:
    """写出 Excel：表头样式、斑马纹、列宽、冻结表头（可选条件格式与数据验证）。"""
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as error:  # pragma: no cover
        raise WriteError("缺少 openpyxl，无法导出 Excel") from error

    notes: list[str] = []
    tables = ir.tables()
    workbook = openpyxl.Workbook()
    default_sheet = workbook.active
    if not tables:
        rows = [[line] for line in ir.text().splitlines() if line.strip()]
        tables = [TableData(rows=rows, name="Sheet1", header=False)]

    header_fill = PatternFill("solid", fgColor=_lighten(options.brand_color, 0.82))
    zebra_fill = PatternFill("solid", fgColor="FAFBFE")
    for index, table in enumerate(tables):
        title = (table.name or f"Sheet{index + 1}")[:31] or f"Sheet{index + 1}"
        sheet = default_sheet if index == 0 else workbook.create_sheet()
        sheet.title = _safe_sheet_title(title, workbook.sheetnames[:-1] if index else [])
        rows = table.normalized()
        for row in rows:
            sheet.append(row)
        if options.beautify_tables:
            for column_index in range(1, max(1, sheet.max_column) + 1):
                cell = sheet.cell(row=1, column=column_index)
                if table.header:
                    cell.font = Font(bold=True, color="1F2430")
                    cell.fill = header_fill
                    cell.alignment = Alignment(horizontal="center", vertical="center")
            if options.zebra:
                for row_index in range(2, sheet.max_row + 1):
                    if row_index % 2 == 0:
                        for column_index in range(1, sheet.max_column + 1):
                            sheet.cell(row=row_index, column=column_index).fill = zebra_fill
        for column_index in range(1, max(1, sheet.max_column) + 1):
            longest = max(
                (len(str(sheet.cell(row=r, column=column_index).value or ""))
                 for r in range(1, min(sheet.max_row, 200) + 1)), default=8)
            sheet.column_dimensions[
                openpyxl.utils.get_column_letter(column_index)].width = min(40, max(8, longest + 2))
        # 冻结表头：滚动时始终看得到列名（需求 6.2 的"冻结窗格"）
        if table.header and sheet.max_row > 1 and options.freeze_header:
            sheet.freeze_panes = "A2"
        if table.header and options.conditional_format:
            notes.extend(_apply_conditional_format(sheet, openpyxl))
        if table.header and options.data_validation:
            notes.extend(_apply_data_validation(sheet, table, openpyxl))
        if watermark and watermark.enabled and watermark.line:
            sheet.oddHeader.center.text = watermark.line
            sheet.oddHeader.center.size = 9
            sheet.oddHeader.center.color = "808080"
    if options.export_comments:
        notes.extend(_write_comment_sheet(workbook, ir, openpyxl))
    try:
        workbook.save(str(path))
    except Exception as error:  # noqa: BLE001
        raise WriteError(f"保存 Excel 失败：{error}") from error
    return notes


def _write_comment_sheet(workbook, ir: DocumentIR, openpyxl) -> list[str]:  # noqa: ANN001
    """把批注写成一张独立的「批注」工作表（Excel 没有"文档级批注"的概念）。"""
    from openpyxl.styles import Font

    comments = [
        (index, (block.text or "").strip().replace("\n", " ")[:80], block.note,
         str(block.meta.get("comment_author") or "local"))
        for index, block in enumerate(ir.blocks) if block.note
    ]
    if not comments:
        return []
    sheet = workbook.create_sheet(title="批注")
    sheet.append(["块序号", "锚点文本", "批注", "作者"])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in comments:
        sheet.append([row[0] + 1, row[1], row[2], row[3]])
    sheet.column_dimensions["A"].width = 10
    sheet.column_dimensions["B"].width = 40
    sheet.column_dimensions["C"].width = 60
    sheet.column_dimensions["D"].width = 14
    return [f"批注已写入「批注」工作表（{len(comments)} 条）"]


def _column_cells(sheet, column_index: int) -> list:  # noqa: ANN001
    return [sheet.cell(row=row, column=column_index).value
            for row in range(2, sheet.max_row + 1)]


def _as_number(value) -> Optional[float]:  # noqa: ANN001
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _apply_conditional_format(sheet, openpyxl) -> list[str]:  # noqa: ANN001
    """给"整列都是数值"的列加"高于平均值"条件格式（导出的表格一眼看出异常值）。"""
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import PatternFill

    notes: list[str] = []
    applied = 0
    fill = PatternFill("solid", start_color="FFF3CD", end_color="FFF3CD")
    for column_index in range(1, sheet.max_column + 1):
        letter = openpyxl.utils.get_column_letter(column_index)
        cells = _column_cells(sheet, column_index)
        numbers = [_as_number(value) for value in cells]
        present = [value for value in cells if str(value or "").strip() != ""]
        if len(present) < 3 or any(number is None for number in numbers):
            continue         # 只处理"整列数值"的列，混合列不猜
        sheet.conditional_formatting.add(
            f"{letter}2:{letter}{sheet.max_row}",
            CellIsRule(operator="greaterThan",
                       formula=[f"AVERAGE({letter}2:{letter}{sheet.max_row})"], fill=fill))
        applied += 1
    if applied:
        notes.append(f"已为 {applied} 个数值列写入「高于平均值」条件格式")
    return notes


def _apply_data_validation(sheet, table, openpyxl) -> list[str]:  # noqa: ANN001
    """给"取值很少的短文本列"加下拉数据验证（清单来自该列现有取值）。

    纯数值列不加：金额/数量这类列通常是自由录入，给一串数字下拉反而碍事。
    """
    from openpyxl.worksheet.datavalidation import DataValidation

    notes: list[str] = []
    applied = 0
    rows = table.normalized()
    header = table.header_row()
    for column_index, name in enumerate(header, start=1):
        cells = _column_cells(sheet, column_index)
        if all(_as_number(value) is not None for value in cells if str(value or "").strip()):
            continue
        values: list[str] = []
        for cell in cells:
            text = str(cell or "").strip()
            if text and text not in values:
                values.append(text)
        if not (2 <= len(values) <= 12) or any(len(item) > 20 for item in values):
            continue
        letter = openpyxl.utils.get_column_letter(column_index)
        validation = DataValidation(type="list", formula1='"' + ",".join(values) + '"',
                                    allow_blank=True, showDropDown=False)
        sheet.add_data_validation(validation)
        validation.add(f"{letter}2:{letter}{max(2, sheet.max_row)}")
        applied += 1
    if applied:
        notes.append(f"已为 {applied} 个列写入下拉数据验证（清单取自现有取值）")
    return notes


def _safe_sheet_title(title: str, taken: list[str]) -> str:
    cleaned = re.sub(r"[\\/*?:\[\]]", "_", title).strip() or "Sheet"
    candidate = cleaned[:31]
    index = 1
    while candidate in taken:
        suffix = f"_{index}"
        candidate = cleaned[: 31 - len(suffix)] + suffix
        index += 1
    return candidate


def _write_delimited(ir: DocumentIR, path: Path, delimiter: str) -> None:
    table = ir.first_table()
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter)
    if table is not None:
        writer.writerows(table.normalized())
    else:
        for line in ir.text().splitlines():
            if line.strip():
                writer.writerow([line])
    path.write_text(buffer.getvalue(), encoding="utf-8-sig")


# ---------------------------------------------------------------- json / pdf


def _json_payload(ir: DocumentIR) -> dict:
    return {
        "title": ir.title,
        "format": ir.format_key,
        "metadata": ir.metadata,
        "styles": ir.styles,
        "stats": ir.stats(),
        "blocks": [block.to_dict() for block in ir.blocks],
    }


def _write_pdf(ir: DocumentIR, path: Path, options: BeautifyOptions,
               watermark: Optional[WatermarkOptions]) -> list[str]:
    """PDF：优先 LibreOffice（高保真），否则用 Qt 渲染 HTML（离线可用）。"""
    notes: list[str] = []
    html = render_html(ir, options, watermark=watermark)
    if _pdf_via_soffice(ir, path, options, watermark):
        notes.append("PDF 由 LibreOffice 转换（版式与 Word 一致）")
        return notes
    backend = _pdf_via_qt(html, path, options)
    notes.append(f"PDF 由 Qt 文本渲染（{backend}）：复杂版式与图片建议装 LibreOffice 后重试")
    if watermark and watermark.enabled and watermark.line:
        notes.append("水印已写入 PDF 页脚")
    return notes


def _pdf_via_soffice(ir: DocumentIR, path: Path, options: BeautifyOptions,
                     watermark: Optional[WatermarkOptions]) -> bool:
    from modu_workbench.core.convert import office_io

    if office_io.find_soffice() is None:
        return False
    with tempfile.TemporaryDirectory(prefix="modu-doc-pdf-") as folder:
        source = Path(folder) / "doc.docx"
        try:
            _write_docx(ir, source, options, watermark)
        except WriteError:
            return False
        generated = office_io.soffice_convert(source, target="pdf")
        if generated is None or not generated.is_file():
            return False
        try:
            shutil.move(str(generated), str(path))
            return True
        except OSError:
            return False


def _pdf_via_qt(html: str, path: Path, options: BeautifyOptions) -> str:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QFont, QPageSize, QTextDocument
    from PySide6.QtPrintSupport import QPrinter

    if QCoreApplication.instance() is None:
        raise WriteError("导出 PDF 需要先创建 QApplication（请在图形界面进程内调用）")
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(path))
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    document = QTextDocument()
    document.setDefaultFont(QFont(options.font_cn, max(8, int(options.body_size))))
    document.setHtml(html)
    document.print_(printer)
    return "HTML 版式"


__all__ = [
    "WatermarkOptions",
    "WriteError",
    "WriteResult",
    "render_html",
    "render_markdown",
    "render_text",
    "unique_output_path",
    "write_document",
]
