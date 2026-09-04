"""转换分发引擎：文本 / 图片 / 归档 + 输出防覆盖。"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from . import archive_io, image_io
from .formats import TARGET_EXTENSION, format_from_extension
from .registry import ARCHIVE_EXTRACT_IDS, ConverterAction


class ConversionCancelled(Exception):
    """用户取消。"""


@dataclass
class ConversionResult:
    action_id: str
    source: str
    status: str  # succeeded / failed / cancelled
    output_path: str | None = None
    target_format: str | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


def run_conversion(
    action: ConverterAction,
    source_path: str | Path,
    output_dir: str | Path,
    cancel: threading.Event | None = None,
) -> ConversionResult:
    source = Path(source_path)
    output_dir_path = Path(output_dir)
    source_format = format_from_extension(source.suffix)
    result_base = ConversionResult(action.id, str(source), "failed", target_format=action.target_format)

    if cancel is not None and cancel.is_set():
        return ConversionResult(action.id, str(source), "cancelled", message="已取消")

    try:
        if action.kind == "text":
            target = action.target_format
            output_path = _unique_output(output_dir_path, source.stem, TARGET_EXTENSION[target])
            _run_text(source, source_format, target, output_path, source.stem)
        elif action.kind in ("word", "sheet"):
            output_path = _run_document_family(action, source, output_dir_path)
        elif action.kind == "image":
            target = action.target_format
            output_path = _unique_output(output_dir_path, source.stem, TARGET_EXTENSION[target])
            image_io.convert_image(source, target, output_path)
        elif action.kind == "media":
            target = action.target_format
            output_path = _unique_output(output_dir_path, source.stem, TARGET_EXTENSION[target])
            from . import media_io

            media_io.convert_media(source, target, output_path, cancel)
        elif action.kind == "archive":
            return _run_archive(action, source, output_dir_path, cancel)
        else:
            return _fail(result_base, f"未知转换族：{action.kind}")

        return ConversionResult(
            action.id, str(source), "succeeded",
            output_path=str(output_path), target_format=action.target_format,
        )
    except ConversionCancelled:
        return ConversionResult(action.id, str(source), "cancelled", message="已取消")
    except Exception as error:  # noqa: BLE001
        return _fail(result_base, str(error))


def _fail(base: ConversionResult, message: str) -> ConversionResult:
    return ConversionResult(base.action_id, base.source, "failed", message=message)


# ---------- 输出防覆盖 ----------

def _unique_output(output_dir: Path, stem: str, extension: str) -> Path:
    candidate = output_dir / f"{stem}{extension}"
    index = 2
    while candidate.exists():
        candidate = output_dir / f"{stem} ({index}){extension}"
        index += 1
    return candidate


def _unique_dir(output_dir: Path, stem: str) -> Path:
    candidate = output_dir / stem
    index = 2
    while candidate.exists():
        candidate = output_dir / f"{stem} ({index})"
        index += 1
    return candidate


# ---------- 文本族 ----------

def _run_text(source: Path, source_format: str, target: str, output: Path, title: str) -> None:
    if source_format not in ("txt", "markdown", "html"):
        raise ValueError("该文本转换需要 txt / md / html 源文件")

    from . import text_io
    from bs4 import BeautifulSoup
    import html as html_mod
    import markdown as md_lib
    import html2text

    raw = text_io.read_text_smart(source)

    def strip_html(content: str) -> str:
        return BeautifulSoup(content, "html.parser").get_text("\n")

    if target == "txt":
        text = raw if source_format != "html" else strip_html(raw)
        text_io.write_text(output, text)
        return

    if target == "markdown":
        if source_format == "html":
            text = html2text.html2text(raw).strip()
        else:
            text = raw.strip()
        text_io.write_text(output, text)
        return

    if target == "html":
        if source_format == "markdown":
            body = md_lib.markdown(raw, extensions=["extra"])
            html = f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>{html_mod.escape(title)}</title></head><body>{body}</body></html>"
        elif source_format == "html":
            html = raw
        else:
            paragraphs = "".join(f"<p>{html_mod.escape(line) or '&nbsp;'}</p>" for line in raw.splitlines())
            html = f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>{html_mod.escape(title)}</title></head><body>{paragraphs}</body></html>"
        text_io.write_text(output, html)
        return

    if target == "pdf":
        if source_format == "markdown":
            body = md_lib.markdown(raw, extensions=["extra"])
            text = strip_html(body)
        elif source_format == "html":
            text = strip_html(raw)
        else:
            text = raw
        from .pdf_out import render_text_pdf
        render_text_pdf(text, output, title=title)
        return

    raise ValueError(f"不支持的文本目标：{target}")


# ---------- 文档族（word / sheet） ----------

def _run_document_family(action: ConverterAction, source: Path, output_dir: Path) -> Path:
    """Word 与表格族：优先 LibreOffice 高保真，缺失时走内置文本/表格兜底。"""
    target = action.target_format
    out = _unique_output(output_dir, source.stem, TARGET_EXTENSION[target])

    if action.kind == "word":
        from . import office_io

        if target == "pdf" and office_io.soffice_convert_to_pdf(source, out):
            return out
        text = office_io.extract_word_text(source)
        office_io.write_text_content(text, target, out, source.stem)
        return out

    from . import office_io, sheet_io

    if target == "pdf" and office_io.soffice_convert_to_pdf(source, out):
        return out
    sheet_io.convert_sheet(source, target, out, source.stem)
    return out


# ---------- 归档族 ----------

def _run_archive(
    action: ConverterAction, source: Path, output_dir: Path, cancel: threading.Event | None
) -> ConversionResult:
    if action.id == "compress-to-zip":
        output = _unique_output(output_dir, source.stem, ".zip")
        archive_io.compress_zip(source, output)
        return ConversionResult(action.id, str(source), "succeeded", output_path=str(output), target_format="zip")

    if action.id == "compress-to-tar":
        output = _unique_output(output_dir, source.stem, ".tar")
        archive_io.compress_tar(source, output)
        return ConversionResult(action.id, str(source), "succeeded", output_path=str(output), target_format="tar")

    if action.id in ARCHIVE_EXTRACT_IDS:
        if cancel is not None and cancel.is_set():
            raise ConversionCancelled()
        target_dir = _unique_dir(output_dir, source.stem)
        if action.id == "zip-extract":
            archive_io.extract_zip(source, target_dir)
        elif action.id == "tar-extract":
            archive_io.extract_tar(source, target_dir)
        else:
            archive_io.extract_rar(source, target_dir)
        return ConversionResult(
            action.id, str(source), "succeeded", output_path=str(target_dir), target_format=action.target_format
        )

    return _fail(ConversionResult(action.id, str(source), "failed"), f"未知归档动作：{action.id}")
