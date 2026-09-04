"""Word 文档读写与 LibreOffice 高保真转换（可选）。

- docx：python-docx 提取段落文本；
- doc：若为 zip 容器按 docx 处理；否则提示先用 LibreOffice/另存为 DOCX；
- 系统装有 LibreOffice(soffice) 时：doc/docx/xlsx/xls → pdf/html 等高保真由 soffice 完成。
"""
from __future__ import annotations

import html as html_mod
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import text_io
from .pdf_out import render_text_pdf


def find_soffice() -> str | None:
    override = os.environ.get("MODU_SOFFICE")
    if override:
        return override if Path(override).is_file() else None
    on_path = shutil.which("soffice") or shutil.which("libreoffice")
    if on_path:
        return on_path
    if os.name == "nt":
        for base in (
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LibreOffice/program/soffice.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")),
        ):
            candidate = base if base.name == "soffice.exe" else base / "LibreOffice/program/soffice.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def extract_word_text(path: Path) -> str:
    """返回 doc/docx 的纯文本；无法解析时抛中文错误。"""
    head = path.read_bytes()[:2]
    if head == b"PK":
        return _extract_docx(path)
    if path.suffix.lower() == ".doc":
        soffice = find_soffice()
        if soffice:
            return _soffice_to_text(path, soffice)
        raise ValueError("旧版二进制 .doc 需要先另存为 DOCX，或安装 LibreOffice 后重试")
    raise ValueError("仅支持 DOCX / DOC 文件")


def _extract_docx(path: Path) -> str:
    import docx as docx_lib

    try:
        document = docx_lib.Document(str(path))
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"无法读取 Word 文档：{error}") from error
    paragraphs: list[str] = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                paragraphs.append(" | ".join(cells))
    return "\n".join(paragraphs).strip()


def _soffice_to_text(path: Path, soffice: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [soffice, "--headless", "--convert-to", "txt:Text (encoded):UTF8", "--outdir", tmp, str(path)],
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0:
            raise ValueError("LibreOffice 转换失败：" + (proc.stderr or b"").decode("utf-8", "ignore")[:200])
        out = Path(tmp) / f"{path.stem}.txt"
        return text_io.read_text_smart(out) if out.exists() else ""


def soffice_convert_to_pdf(source: Path, desired: Path) -> bool:
    """用 LibreOffice 把 Word/Excel 转 PDF；成功时移动产物到 desired。"""
    soffice = find_soffice()
    if not soffice:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(source)],
            capture_output=True, timeout=180,
        )
        if proc.returncode != 0:
            return False
        produced = Path(tmp) / f"{source.stem}.pdf"
        if not produced.is_file():
            return False
        desired.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(produced), str(desired))
            return True
        except OSError:
            return False


def write_text_content(text: str, target: str, output: Path, title: str) -> None:
    """把纯文本按目标（txt/markdown/html/pdf）写出。"""
    if target in ("txt", "markdown"):
        text_io.write_text(output, text.strip() if text.strip() else title)
        return
    if target == "html":
        escaped_title = html_mod.escape(title)
        paragraphs = "".join(f"<p>{html_mod.escape(line) or '&nbsp;'}</p>" for line in text.splitlines())
        text_io.write_text(output, f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>{escaped_title}</title></head><body>{paragraphs}</body></html>")
        return
    if target == "pdf":
        render_text_pdf(text, output, title=title)
        return
    raise ValueError(f"不支持的输出目标：{target}")
