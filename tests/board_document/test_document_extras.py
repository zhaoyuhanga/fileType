"""墨软文档：本地模板库 / OCR / 导出图片（需求 6.2 与 6.11）测试。

- 模板库：保存、列出、套用、导出、导入、删除（JSON 可分享）；
- OCR：图片与扫描 PDF 走真实引擎，测试用替身引擎（本机不一定装了 tesseract），
  同时验证"引擎缺失时明确报错"；
- 导出图片：PDF 直接渲染，Office 文档走 LibreOffice（缺失时给可操作提示）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from modu_workbench.core.document import (
    TOOL_REGISTRY,
    BeautifyOptions,
    DocumentLibrary,
    DocumentStorage,
    ParseError,
    call_tool,
    parse_markdown,
    tool_names,
)
from modu_workbench.core.document import ocr as ocr_module
from modu_workbench.core.document import pdf_tools, templates
from modu_workbench.core.document.beautify import beautify as apply_beautify

SAMPLE = """# 季度报告

第一条结论。

| 项目 | 金额 |
| --- | --- |
| A | 100 |
"""


@pytest.fixture()
def library(tmp_path: Path) -> DocumentLibrary:
    return DocumentLibrary(DocumentStorage(tmp_path / "modu.db"), output_dir=tmp_path / "out")


# ---------------------------------------------------------------- 模板库


def test_template_save_load_list_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODU_DATA_DIR", str(tmp_path / "data"))
    options = BeautifyOptions(font_cn="仿宋_GB2312", body_size=16.0, auto_number=True,
                              build_toc=True, number_style="chinese", template="gov")
    path = templates.save_template("公司公文", options, note="内部规范")
    assert path.is_file() and path.suffix == ".json"

    loaded = templates.load_template("公司公文")
    assert loaded.name == "公司公文"
    assert loaded.options is not None
    assert loaded.options.font_cn == "仿宋_GB2312"
    assert loaded.options.body_size == 16.0
    assert loaded.options.auto_number is True
    assert loaded.options.number_style == "chinese"
    assert loaded.label.startswith(templates.USER_PREFIX)
    assert "仿宋_GB2312" in loaded.summary()

    listed = templates.list_user_templates()
    assert [item.name for item in listed] == ["公司公文"]
    assert any(label.startswith(templates.USER_PREFIX) for _value, label in templates.choices())

    assert templates.delete_template("公司公文") is True
    assert templates.list_user_templates() == []


def test_template_options_roundtrip_keeps_heading_sizes(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODU_DATA_DIR", str(tmp_path / "data"))
    options = BeautifyOptions(heading_sizes={1: 24.0, 2: 18.0})
    templates.save_template("字号模板", options)
    loaded = templates.load_template("字号模板")
    assert loaded.options is not None
    assert loaded.options.resolved_heading_sizes()[1] == 24.0
    assert loaded.options.resolved_heading_sizes()[2] == 18.0


def test_template_export_and_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODU_DATA_DIR", str(tmp_path / "data"))
    templates.save_template("分享模板", BeautifyOptions(font_cn="黑体", auto_number=True))

    exported = templates.export_template("分享模板", tmp_path / "share" / "别人.json")
    assert exported.is_file()

    # 换一台"机器"导入（清空模板目录）
    for item in templates.list_user_templates():
        templates.delete_template(item.name)
    assert templates.list_user_templates() == []

    imported = templates.import_template(exported, name="导入的模板")
    assert imported.name == "导入的模板"
    assert (imported.options or BeautifyOptions()).font_cn == "黑体"
    assert [item.name for item in templates.list_user_templates()] == ["导入的模板"]


def test_template_errors_are_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODU_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(templates.TemplateError):
        templates.load_template("不存在的模板")

    broken = templates.template_dir() / "坏模板.json"
    broken.write_text("{不是 JSON", encoding="utf-8")
    with pytest.raises(templates.TemplateError) as info:
        templates.load_template("坏模板")
    assert "JSON" in str(info.value)
    # 损坏的模板仍会被列出来（带原因），不抛异常
    assert any(item.name == "坏模板" for item in templates.list_user_templates())

    wrong = templates.template_dir() / "缺字段.json"
    wrong.write_text('{"name": "x"}', encoding="utf-8")
    with pytest.raises(templates.TemplateError):
        templates.load_template("缺字段")


def test_user_template_can_be_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODU_DATA_DIR", str(tmp_path / "data"))
    options = BeautifyOptions(font_cn="黑体", body_size=15.0, auto_number=True,
                              template="report")
    path = templates.save_template("自定义公文", options)
    resolved, label = templates.resolve_choice(templates.token_for_user_template(path))
    assert label.startswith(templates.USER_PREFIX)
    assert resolved.font_cn == "黑体"

    result = apply_beautify(parse_markdown(SAMPLE, title="季度报告"), resolved)
    assert result.change_count > 0
    paragraph = next(block for block in result.ir.blocks if block.kind == "paragraph")
    assert paragraph.style["font_cn"] == "黑体"


def test_template_tools(library: DocumentLibrary) -> None:
    names = tool_names()
    assert {"apply_template", "register_template"}.issubset(set(names))
    assert TOOL_REGISTRY["apply_template"].handler is not None

    document = parse_markdown(SAMPLE, title="季度报告")
    context = library.tool_context(document)
    result = call_tool("apply_template", context, {"template": "gov"})
    assert result.ok and "公文" in result.summary

    result = call_tool("register_template", context, {"name": "工具存的模板"})
    assert result.ok and Path(result.data["path"]).is_file()


# ---------------------------------------------------------------- OCR


def test_ocr_document_image(library: DocumentLibrary, tmp_path: Path,
                            monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    image = tmp_path / "扫描.png"
    Image.new("RGB", (100, 60), (250, 250, 250)).save(image)
    monkeypatch.setattr(ocr_module, "ocr_image", lambda path, **kwargs: "识别出的第一行\n识别出的第二行")

    document = library.ocr_document(image)
    assert document.format_key == "txt", "OCR 结果应当是可直接编辑的文本"
    assert document.path == "", "OCR 结果还没有落盘，需要另存为"
    assert [block.text for block in document.blocks] == ["识别出的第一行", "识别出的第二行"]
    assert document.metadata["ocr"] is True
    assert document.warnings and "校对" in document.warnings[0]
    assert any(record["action"] == "ocr" for record in library.audit_records())


def test_ocr_document_pdf(library: DocumentLibrary, tmp_path: Path,
                          monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PdfWriter

    pdf_path = tmp_path / "扫描件.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with open(pdf_path, "wb") as handle:
        writer.write(handle)

    monkeypatch.setattr(ocr_module, "ocr_pdf_pages",
                        lambda path, **kwargs: [(1, "第一页文字"), (2, "第二页文字")])
    document = library.ocr_document(pdf_path)
    assert len(document.blocks) == 2
    assert document.blocks[0].meta["page"] == 1
    assert "第一页文字" in document.text()


def test_ocr_unavailable_reports_reason(library: DocumentLibrary, tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    image = tmp_path / "扫描.png"
    Image.new("RGB", (80, 40)).save(image)
    monkeypatch.setattr(ocr_module, "describe",
                        lambda: "未检测到 tesseract：扫描件/图片 OCR 不可用")
    monkeypatch.setattr(ocr_module, "ocr_image", lambda path, **kwargs: "")
    with pytest.raises(ParseError) as info:
        library.ocr_document(image)
    assert "tesseract" in str(info.value)


def test_ocr_tool_registered() -> None:
    assert TOOL_REGISTRY["ocr"].handler is not None
    assert "OCR" in TOOL_REGISTRY["ocr"].label


# ---------------------------------------------------------------- 导出图片


def test_export_images_from_pdf(library: DocumentLibrary, tmp_path: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PdfWriter

    pdf_path = tmp_path / "两页.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=595, height=842)
    with open(pdf_path, "wb") as handle:
        writer.write(handle)

    # 用替身渲染（真渲染需要 PyMuPDF）
    def fake_to_images(path, output_dir, *, pages="", dpi=150):  # noqa: ANN001
        folder = Path(output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        return [folder / f"p{index}.png" for index in (1, 2)]

    monkeypatch.setattr(pdf_tools, "to_images", fake_to_images)
    document = library.open_path(pdf_path)
    produced = library.export_images(document, dpi=100)
    assert len(produced) == 2
    assert any("导出" in record["detail"] for record in library.audit_records())


def test_export_images_needs_saved_source(library: DocumentLibrary) -> None:
    from modu_workbench.core.document import WriteError

    document = parse_markdown(SAMPLE, title="未保存")
    with pytest.raises(WriteError) as info:
        library.export_images(document)
    assert "源文件" in str(info.value)


def test_export_images_rejects_image_source(tmp_path: Path) -> None:
    from PIL import Image

    image = tmp_path / "图.png"
    Image.new("RGB", (60, 40)).save(image)
    with pytest.raises(pdf_tools.PdfError) as info:
        pdf_tools.document_to_images(image, tmp_path / "out")
    assert "本身就是图片" in str(info.value)


def test_export_images_tool(library: DocumentLibrary, tmp_path: Path,
                            monkeypatch: pytest.MonkeyPatch) -> None:
    from pypdf import PdfWriter

    pdf_path = tmp_path / "x.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with open(pdf_path, "wb") as handle:
        writer.write(handle)

    monkeypatch.setattr(pdf_tools, "document_to_images",
                        lambda path, output_dir, **kwargs: [Path(output_dir) / "p1.png"])
    context = library.tool_context(library.open_path(pdf_path))
    result = call_tool("export_images", context, {})
    assert result.ok and result.data["paths"]
