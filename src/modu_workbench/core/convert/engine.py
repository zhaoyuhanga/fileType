"""转换分发引擎：文本 / 图片 / 归档 + 输出防覆盖。"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from . import archive_io, image_io
from .formats import TARGET_EXTENSION, format_from_extension
from .registry import ARCHIVE_EXTRACT_IDS, TAR_EXTRACT_IDS, ConverterAction


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
        elif action.kind == "pdf":
            output_path = _run_pdf(action, source, output_dir_path)
        elif action.kind == "data":
            output_path = _run_data(action, source, output_dir_path)
        elif action.kind == "subtitle":
            output_path = _run_subtitle(action, source, output_dir_path)
        elif action.kind == "ebook":
            output_path = _run_ebook(action, source, output_dir_path)
        elif action.kind == "archive":
            result = _run_archive(action, source, output_dir_path, cancel)
            if result.ok and result.output_path:
                _ensure_output_exists(Path(result.output_path))
            return result
        else:
            return _fail(result_base, f"未知转换族：{action.kind}")

        _ensure_output_exists(output_path)
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


def _ensure_output_exists(path: Path) -> None:
    """确认转换**真的**产出了文件。

    反馈里有「转换显示成功、但输出目录里没有数据」：以前只要转换函数不抛异常就报成功，
    产物缺失/空文件也会被当成成功，用户只能自己去猜文件去哪了。
    这里统一把关：目录要求非空，文件要求存在且非空，否则报出明确原因。
    """
    target = Path(path)
    if target.is_dir():
        if not any(target.iterdir()):
            raise ValueError(f"转换没有产出任何文件（目录为空：{target}）")
        return
    if not target.exists():
        raise ValueError(f"转换没有产出文件（{target} 不存在）")
    try:
        size = target.stat().st_size
    except OSError as error:
        raise ValueError(f"无法读取转换结果（{target}）：{error}") from error
    if size <= 0:
        raise ValueError(f"转换产出的是空文件（{target}）")


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
    if source_format == "json":
        _run_json(source, target, output, title)
        return
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


def _run_json(source: Path, target: str, output: Path, title: str) -> None:
    """JSON → txt / csv / pdf（v1.0.0 新增）。"""
    import csv
    import json
    import io

    from . import text_io

    raw = text_io.read_text_smart(source)
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise ValueError(f"不是有效 JSON：{error}") from error

    if target in ("txt", "markdown"):
        pretty = json.dumps(data, ensure_ascii=False, indent=2)
        text_io.write_text(output, pretty if target == "txt" else f"```json\n{pretty}\n```\n")
        return
    if target == "pdf":
        pretty = json.dumps(data, ensure_ascii=False, indent=2)
        from .pdf_out import render_text_pdf

        render_text_pdf(pretty, output, title=title or source.stem)
        return
    if target == "csv":
        rows = data
        if isinstance(data, dict):
            # 常见包装：{"items": [...]} / {"data": [...]} / {"list": [...]}
            for key in ("items", "data", "list", "records", "rows"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
            else:
                rows = [data]
        if not isinstance(rows, list) or not rows:
            raise ValueError("JSON 里没有可导出的数组（需要对象数组）")
        if not all(isinstance(item, dict) for item in rows):
            raise ValueError("JSON 转 CSV 需要「对象数组」，当前元素不是对象")
        columns: list[str] = []
        for item in rows:
            for key in item:
                if key not in columns:
                    columns.append(str(key))
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for item in rows:
            writer.writerow({key: item.get(key, "") for key in columns})
        text_io.write_text(output, buffer.getvalue())
        return
    raise ValueError(f"不支持的 JSON 目标：{target}")


def _paragraph_html(text: str, title: str) -> str:
    """纯文本 → 简单 HTML 文档（与 txt→html 转换保持同一套包装）。"""
    import html as html_mod

    escaped_title = html_mod.escape(title)
    paragraphs = "".join(
        f"<p>{html_mod.escape(line) or '&nbsp;'}</p>" for line in (text or "").splitlines()
    )
    return (
        f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{escaped_title}</title></head><body>{paragraphs}</body></html>"
    )


def _run_data(action, source: Path, output_dir: Path) -> Path:  # noqa: ANN001
    """数据族：json / xml / ini / yaml 互转（另可输出 txt / markdown / html / pdf / csv）。"""
    from . import data_io

    extension = TARGET_EXTENSION.get(action.target_format, ".txt")
    output = _unique_output(output_dir, source.stem, extension)
    data_io.convert_data(source, format_from_extension(source.suffix), action.target_format,
                         output, title=source.stem)
    return output


def _run_subtitle(action, source: Path, output_dir: Path) -> Path:  # noqa: ANN001
    """字幕族：srt / vtt 互转，或转成不带时间轴的纯文本。"""
    from . import subtitle_io

    extension = TARGET_EXTENSION.get(action.target_format, ".txt")
    output = _unique_output(output_dir, source.stem, extension)
    subtitle_io.convert_subtitle(source, format_from_extension(source.suffix),
                                 action.target_format, output)
    return output


def _run_ebook(action, source: Path, output_dir: Path) -> Path:  # noqa: ANN001
    """电子书族：epub → txt/md/html/pdf，或 txt/md/html → epub。"""
    from . import ebook_io
    from . import text_io

    source_format = format_from_extension(source.suffix)
    if source_format != "epub":
        output = _unique_output(output_dir, source.stem, TARGET_EXTENSION.get(action.target_format, ".epub"))
        ebook_io.write_epub(text_io.read_text_smart(source), output, title=source.stem)
        return output

    text = ebook_io.epub_to_text(source)
    output = _unique_output(output_dir, source.stem, TARGET_EXTENSION.get(action.target_format, ".txt"))
    if action.target_format == "pdf":
        from .pdf_out import render_text_pdf

        render_text_pdf(text, output, title=source.stem)
    elif action.target_format == "html":
        text_io.write_text(output, _paragraph_html(text, source.stem))
    else:
        text_io.write_text(output, text)
    return output


def _run_pdf(action, source: Path, output_dir: Path) -> Path:  # noqa: ANN001
    """PDF → TXT / Markdown（pypdf 抽取文本）。"""
    try:
        from pypdf import PdfReader
    except ImportError as error:  # pragma: no cover - 依赖缺失时给出可操作提示
        raise ValueError("PDF 文本抽取需要 pypdf：请执行 pip install pypdf") from error

    from . import text_io

    try:
        reader = PdfReader(str(source))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"PDF 读取失败：{error}") from error
    text = "\n\n".join(page for page in pages if page)
    if not text.strip():
        raise ValueError("这个 PDF 里没有可抽取的文本（可能是扫描件/图片型 PDF）")

    extension = TARGET_EXTENSION.get(action.target_format, ".txt")
    output = _unique_output(output_dir, source.stem, extension)
    if action.target_format == "markdown":
        text_io.write_text(output, text)
    else:
        text_io.write_text(output, text)
    return output


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
    if action.id.startswith("compress-to-"):
        target = action.id.removeprefix("compress-to-")
        extension = TARGET_EXTENSION.get(target, f".{target}")
        # 单文件压缩保留源文件全名（报告.txt → 报告.txt.gz）：
        # bz2/xz 格式本身没有文件名字段，只有这样才能把原名带回去；
        # tar 系列本来就记条目名，仍用 stem（报告.tar.gz）。
        stem = source.name if target in ("gz", "bz2", "xz") else source.stem
        output = _unique_output(output_dir, stem, extension)
        if target == "zip":
            archive_io.compress_zip(source, output)
        elif target in ("tar", "targz", "tarbz2", "tarxz"):
            archive_io.compress_tar(source, output, compression=target[3:])
        elif target in ("gz", "bz2", "xz"):
            archive_io.compress_single(source, output, algorithm=target)
        else:
            return _fail(ConversionResult(action.id, str(source), "failed"), f"未知压缩目标：{target}")
        return ConversionResult(action.id, str(source), "succeeded",
                                output_path=str(output), target_format=target)

    if action.id in set(ARCHIVE_EXTRACT_IDS.values()):
        if cancel is not None and cancel.is_set():
            raise ConversionCancelled()
        target_dir = _unique_dir(output_dir, source.stem)
        if action.id == "zip-extract":
            archive_io.extract_zip(source, target_dir)
        elif action.id == "rar-extract":
            archive_io.extract_rar(source, target_dir)
        elif action.id in TAR_EXTRACT_IDS:
            # tar / tar.gz / tar.bz2 / tar.xz 共用（tarfile 按内容自动识别压缩）
            archive_io.extract_tar(source, target_dir)
        else:
            # gz / bz2 / xz 单文件：解出原始文件（放在同名目录里）
            archive_io.extract_single(source, target_dir)
        return ConversionResult(
            action.id, str(source), "succeeded", output_path=str(target_dir), target_format=action.target_format
        )

    return _fail(ConversionResult(action.id, str(source), "failed"), f"未知归档动作：{action.id}")
