"""Word 文档读写与 LibreOffice 高保真转换（可选）。

- docx：python-docx 提取段落文本；
- doc：若为 zip 容器按 docx 处理；否则提示先用 LibreOffice/另存为 DOCX；
- 系统装有 LibreOffice(soffice) 时：doc/docx/xlsx/xls → pdf/html 等高保真由 soffice 完成。
"""
from __future__ import annotations

import html as html_mod
import os
import re
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
    """返回 doc/docx/odt/rtf 的纯文本；无法解析时抛中文错误。

    - `odt`：本身就是 zip，直接读 `content.xml`（纯标准库，不依赖 LibreOffice）；
    - `rtf`：走轻量 RTF 解析（够用于纯文本提取），失败再交给 soffice；
    - `docx`：python-docx；`doc`（旧版二进制）：必须 LibreOffice。
    """
    suffix = path.suffix.lower()
    if suffix == ".odt":
        text = _extract_odt(path)
        if text.strip():
            return text
    if suffix == ".rtf":
        text = _extract_rtf(path)
        if text.strip():
            return text
        soffice = find_soffice()
        if soffice:
            return _soffice_to_text(path, soffice)
        raise ValueError("这个 RTF 解析不出文本（可能是复杂的富文本），建议先用 LibreOffice 另存为 DOCX")

    head = path.read_bytes()[:2]
    if head == b"PK":
        return _extract_docx(path)
    if suffix == ".doc":
        soffice = find_soffice()
        if soffice:
            return _soffice_to_text(path, soffice)
        raise ValueError("旧版二进制 .doc 需要先另存为 DOCX，或安装 LibreOffice 后重试")
    raise ValueError("仅支持 DOCX / DOC / ODT / RTF 文件")


def _extract_odt(path: Path) -> str:
    """从 ODT 的 content.xml 里取正文段落（跳过样式与脚注）。"""
    import xml.etree.ElementTree as ET
    import zipfile

    try:
        with zipfile.ZipFile(path) as bundle:
            payload = bundle.read("content.xml")
    except (KeyError, zipfile.BadZipFile) as error:
        raise ValueError(f"无法读取 ODT 文档：{error}") from error

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise ValueError(f"无法解析 ODT 内容：{error}") from error

    lines: list[str] = []
    for paragraph in root.iter():
        tag = paragraph.tag.rsplit("}", 1)[-1]
        if tag not in ("p", "h"):
            continue
        text = "".join(node.text or "" for node in paragraph.iter() if node.tag.rsplit("}", 1)[-1] == "span")
        text = text or (paragraph.text or "")
        text = text.strip()
        if text:
            lines.append(text)
    return "\n".join(lines).strip()


def _extract_rtf(path: Path) -> str:
    """轻量 RTF 文本提取：去掉控制字/分组/图片，保留段落换行。

    不追求排版还原 —— 目标是"能拿到可读文本"，复杂文档请用 LibreOffice 兜底。
    """
    raw = path.read_bytes().decode("latin-1", "ignore")
    output: list[str] = []
    index = 0
    depth_skip = 0          # >0 表示正处在 {\*\...} 之类的可忽略分组里
    while index < len(raw):
        char = raw[index]
        if char == "\\":
            match = re.match(r"\\([a-zA-Z]+)(-?\d+)?[ ]?", raw[index:])
            if match:
                word = match.group(1)
                if word in ("par", "line", "sect", "pard"):
                    output.append("\n")
                elif word == "tab":
                    output.append("\t")
                elif word in ("fonttbl", "colortbl", "stylesheet", "info", "pict", "object",
                              "themedata", "datastore", "generator"):
                    depth_skip += 1
                elif word == "u" and match.group(2):
                    try:
                        output.append(chr(int(match.group(2)) % 65536))
                    except ValueError:
                        pass
                index += match.end()
                continue
            hex_match = re.match(r"\\'([0-9a-fA-F]{2})", raw[index:])
            if hex_match:
                code = int(hex_match.group(1), 16)
                output.append(bytes([code]).decode("cp1252", "ignore"))
                index += hex_match.end()
                continue
            index += 1
            continue
        if char == "{":
            index += 1
            continue
        if char == "}":
            depth_skip = max(0, depth_skip - 1)
            index += 1
            continue
        if depth_skip == 0:
            output.append(char)
        index += 1
    text = "".join(output)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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


def soffice_convert(source: Path, *, target: str) -> Path | None:
    """用 LibreOffice 把文档转成 `target`（csv / txt / html / docx / ods …）。

    返回产物路径（在新建的临时目录里），调用方读完后 `unlink()` 即可；
    没有 soffice、或转换失败/产物缺失时返回 None（由调用方给出中文提示）。
    """
    soffice = find_soffice()
    if not soffice:
        return None
    out_dir = Path(tempfile.mkdtemp(prefix="modu-soffice-"))
    try:
        proc = subprocess.run(
            [soffice, "--headless", "--convert-to", target, "--outdir", str(out_dir), str(source)],
            capture_output=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    produced = sorted(out_dir.glob(f"{source.stem}.*"))
    return produced[0] if produced else None


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
