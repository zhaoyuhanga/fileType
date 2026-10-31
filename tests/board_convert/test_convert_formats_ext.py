"""转换格式扩展的端到端测试（图片/表格/文档/PDF 方向）。

覆盖「把已知能转的格式都加进来」里我负责的部分：
图片新增格式与**图片转 PDF**、表格 CSV/TSV→XLSX 与 →Markdown、ODT/RTF 文本抽取。
（归档族见 `test_convert_archive_ext.py`；数据/字幕/电子书见对应测试文件。）
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from PIL import Image

from modu_workbench.core.convert.engine import run_conversion
from modu_workbench.core.convert.registry import common_actions, get_action


@pytest.fixture(scope="module")
def qapp():
    """PDF 渲染走 Qt（QTextDocument/QPrinter），必须有 QApplication。

    没有 QApplication 时 QPrinter 会让进程**直接崩**（实测 0xC0000409，不是抛异常），
    所以这个夹具是必需的 —— pdf_out 里现在也会显式检查并给出可读错误。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _action(source_format: str, target: str):  # noqa: ANN202
    action = get_action(f"{source_format}-to-{target}")
    assert action is not None, f"缺少动作 {source_format}-to-{target}"
    return action


def _make_png(path: Path, size: tuple[int, int] = (32, 24), mode: str = "RGB") -> Path:
    color = (200, 30, 30) if mode == "RGB" else (200, 30, 30, 128)
    Image.new(mode, size, color).save(path, format="PNG")
    return path


# ---------------------------------------------------------------- 图片

@pytest.mark.parametrize("target", ["tiff", "ico", "tga", "pcx", "ppm", "bmp", "webp", "gif"])
def test_png_converts_to_every_new_image_format(qapp, tmp_path: Path, target: str) -> None:
    """PNG → 每个新登记的图片格式，都要能产出非空文件并再读回。"""
    source = _make_png(tmp_path / "原图.png")
    out_dir = tmp_path / f"out-{target}"
    out_dir.mkdir()
    result = run_conversion(_action("png", target), source, out_dir)
    assert result.status == "succeeded", result.message
    produced = Path(result.output_path)
    assert produced.is_file() and produced.stat().st_size > 0
    with Image.open(produced) as reopened:
        if target == "ico":
            # ICO 只能是方形，而且 Pillow 默认会写出一整套尺寸（16/24/32…），
            # image_io 里已显式指定单一尺寸，这里只要求方形且不超上限
            width, height = reopened.size
            assert width == height <= 256, f"ICO 尺寸异常：{reopened.size}"
        else:
            assert reopened.size == (32, 24), f"{target} 尺寸被改变：{reopened.size}"


def test_rgba_png_to_jpg_flattens_transparency(qapp, tmp_path: Path) -> None:
    """带透明通道的 PNG 转 JPG 不能报错（自动合成白底）。"""
    source = _make_png(tmp_path / "透明.png", mode="RGBA")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = run_conversion(_action("png", "jpg"), source, out_dir)
    assert result.status == "succeeded", result.message
    with Image.open(result.output_path) as reopened:
        assert reopened.mode == "RGB"


def test_image_to_pdf_produces_a_real_pdf(qapp, tmp_path: Path) -> None:
    """图片 → PDF（反馈里最常见的转换之一）。"""
    from pypdf import PdfReader

    source = _make_png(tmp_path / "照片.png", size=(64, 48))
    out_dir = tmp_path / "out-pdf"
    out_dir.mkdir()
    result = run_conversion(_action("png", "pdf"), source, out_dir)
    assert result.status == "succeeded", result.message
    produced = Path(result.output_path)
    assert produced.suffix == ".pdf" and produced.stat().st_size > 0
    assert len(PdfReader(str(produced)).pages) == 1


def test_animated_gif_to_pdf_keeps_all_frames(qapp, tmp_path: Path) -> None:
    """动图转 PDF：每一帧一页。"""
    from pypdf import PdfReader

    frames = [Image.new("RGB", (16, 16), (index * 60, 0, 0)) for index in range(3)]
    source = tmp_path / "动图.gif"
    frames[0].save(source, format="GIF", save_all=True, append_images=frames[1:], duration=100)

    out_dir = tmp_path / "out-anim"
    out_dir.mkdir()
    result = run_conversion(_action("gif", "pdf"), source, out_dir)
    assert result.status == "succeeded", result.message
    assert len(PdfReader(str(result.output_path)).pages) == 3


def test_readonly_image_format_can_be_converted_out(qapp) -> None:
    """PSD/DDS/JP2 这类「只能读」的格式也要能给用户转出来。"""
    actions = common_actions(["psd"])
    assert any(action.target_format == "png" for action in actions), "PSD 必须能转 PNG"


# ---------------------------------------------------------------- 表格

def test_csv_to_xlsx_and_back(qapp, tmp_path: Path) -> None:
    """CSV → XLSX → CSV：单元格内容要保持。"""
    source = tmp_path / "数据.csv"
    source.write_text("姓名,年龄\n张三,30\n李四,28\n", encoding="utf-8")

    first = tmp_path / "step1"
    first.mkdir()
    to_xlsx = run_conversion(_action("csv", "xlsx"), source, first)
    assert to_xlsx.status == "succeeded", to_xlsx.message
    assert Path(to_xlsx.output_path).suffix == ".xlsx"

    second = tmp_path / "step2"
    second.mkdir()
    back = run_conversion(_action("xlsx", "csv"), Path(to_xlsx.output_path), second)
    assert back.status == "succeeded", back.message
    text = Path(back.output_path).read_text(encoding="utf-8")
    assert "张三" in text and "30" in text


def test_tsv_to_csv_uses_commas(qapp, tmp_path: Path) -> None:
    source = tmp_path / "数据.tsv"
    source.write_text("a\tb\n1\t2\n", encoding="utf-8")
    out_dir = tmp_path / "out-tsv"
    out_dir.mkdir()
    result = run_conversion(_action("tsv", "csv"), source, out_dir)
    assert result.status == "succeeded", result.message
    text = Path(result.output_path).read_text(encoding="utf-8")
    assert "a,b" in text


def test_xlsx_to_markdown_table(qapp, tmp_path: Path) -> None:
    import openpyxl

    source = tmp_path / "表.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["列一", "列二"])
    sheet.append(["甲", "乙"])
    book.save(source)

    out_dir = tmp_path / "out-md"
    out_dir.mkdir()
    result = run_conversion(_action("xlsx", "markdown"), source, out_dir)
    assert result.status == "succeeded", result.message
    text = Path(result.output_path).read_text(encoding="utf-8")
    assert text.startswith("| 列一 | 列二 |")
    assert "| --- | --- |" in text
    assert "| 甲 | 乙 |" in text


def test_csv_can_choose_every_sheet_target(qapp, tmp_path: Path) -> None:
    """CSV 作为源，登记过的每个表格目标都要真的能转（防止「登记了却没实现」）。"""
    source = tmp_path / "源.csv"
    source.write_text("h1,h2\nv1,v2\n", encoding="utf-8")
    for target in ("tsv", "xlsx", "markdown", "html", "txt", "pdf"):
        out_dir = tmp_path / f"out-{target}"
        out_dir.mkdir()
        result = run_conversion(_action("csv", target), source, out_dir)
        assert result.status == "succeeded", f"{target}: {result.message}"
        assert Path(result.output_path).stat().st_size > 0


# ---------------------------------------------------------------- 文档（ODT / RTF）

def _make_odt(path: Path, paragraphs: list[str]) -> Path:
    """手工拼一个最小 ODT（关键是 content.xml，其余给空壳即可）。"""
    body = "".join(f"<text:p>{text}</text:p>" for text in paragraphs)
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<office:document-content "
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        f"<office:body><office:text>{body}</office:text></office:body>"
        "</office:document-content>"
    )
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        bundle.writestr("content.xml", content)
    return path


def test_odt_text_extraction_without_libreoffice(qapp, tmp_path: Path) -> None:
    """ODT → TXT 必须不依赖 LibreOffice（直接读 content.xml）。"""
    source = _make_odt(tmp_path / "文档.odt", ["第一段", "第二段"])
    out_dir = tmp_path / "out-odt"
    out_dir.mkdir()
    result = run_conversion(_action("odt", "txt"), source, out_dir)
    assert result.status == "succeeded", result.message
    text = Path(result.output_path).read_text(encoding="utf-8")
    assert "第一段" in text and "第二段" in text


def test_rtf_text_extraction(qapp, tmp_path: Path) -> None:
    source = tmp_path / "文档.rtf"
    source.write_bytes(rb"{\rtf1\ansi\deff0 {\fonttbl {\f0 Arial;}}\fs20 hello world\par }")
    out_dir = tmp_path / "out-rtf"
    out_dir.mkdir()
    result = run_conversion(_action("rtf", "txt"), source, out_dir)
    assert result.status == "succeeded", result.message
    text = Path(result.output_path).read_text(encoding="utf-8")
    assert "hello world" in text


def test_rtf_par_breaks_become_newlines(qapp, tmp_path: Path) -> None:
    """\\par 要变成换行，否则整篇挤成一行。"""
    source = tmp_path / "两段.rtf"
    source.write_bytes(rb"{\rtf1\ansi first\par second\par}")
    out_dir = tmp_path / "out-rtf2"
    out_dir.mkdir()
    result = run_conversion(_action("rtf", "txt"), source, out_dir)
    assert result.status == "succeeded", result.message
    text = Path(result.output_path).read_text(encoding="utf-8")
    assert "first" in text and "second" in text
    assert text.count("\n") >= 1
