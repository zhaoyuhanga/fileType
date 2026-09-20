"""墨软文档：PDF 工具箱测试（拆分/合并/压缩/加密/水印/表单/工具注册）。

需要 Qt 的只有水印（用 Qt 生成同尺寸覆盖层），其余纯 pypdf。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from modu_workbench.core.document import (
    TOOL_REGISTRY,
    DocumentLibrary,
    DocumentStorage,
    PdfError,
    call_tool,
    compress_pdf,
    decrypt_pdf,
    encrypt_pdf,
    extract_pages,
    fill_form,
    list_form_fields,
    merge_pdfs,
    parse_pages,
    parse_document,
    pdf_info,
    rotate_pdf,
    split_pdf,
    tool_names,
    watermark_pdf,
)
from modu_workbench.core.document import pdf_tools


def make_pdf(path: Path, pages: int = 3) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def make_form_pdf(path: Path) -> Path:
    """手工拼一个带 AcroForm 文本域的 PDF（pypdf 没有高层"加表单域"API）。"""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    field = DictionaryObject({
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject("姓名"),
        NameObject("/V"): TextStringObject(""),
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/Rect"): ArrayObject([NumberObject(100), NumberObject(700),
                                          NumberObject(300), NumberObject(730)]),
    })
    reference = writer._add_object(field)                     # noqa: SLF001
    page[NameObject("/Annots")] = ArrayObject([reference])
    writer._root_object[NameObject("/AcroForm")] = writer._add_object(  # noqa: SLF001
        DictionaryObject({
            NameObject("/Fields"): ArrayObject([reference]),
            NameObject("/NeedAppearances"): BooleanObject(True),
        }))
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


@pytest.fixture()
def sample(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "报告.pdf", 4)


# ---------------------------------------------------------------- 概况与页码


def test_pdf_info(sample: Path) -> None:
    info = pdf_info(sample)
    assert info.pages == 4
    assert info.encrypted is False
    assert info.page_size == (595.0, 842.0)
    assert info.form_fields == []
    assert "4 页" in info.summary()


def test_parse_pages_specs() -> None:
    assert parse_pages("", 3) == [0, 1, 2]
    assert parse_pages("1-2,4", 5) == [0, 1, 3]
    assert parse_pages("2-", 4) == [1, 2, 3]
    assert parse_pages("3,1,3", 4) == [0, 2]
    for bad in ("9", "0", "3-1", "abc"):
        with pytest.raises(PdfError):
            parse_pages(bad, 3)


def test_pdf_info_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PdfError) as info:
        pdf_info(tmp_path / "无.pdf")
    assert "不存在" in str(info.value)


# ---------------------------------------------------------------- 合并 / 拆分


def test_merge_pdfs_with_bookmarks(tmp_path: Path) -> None:
    first = make_pdf(tmp_path / "a.pdf", 2)
    second = make_pdf(tmp_path / "b.pdf", 3)
    merged = merge_pdfs([first, second], tmp_path / "merged.pdf")
    info = pdf_info(merged)
    assert info.pages == 5
    from pypdf import PdfReader

    outlines = PdfReader(str(merged)).outline
    assert outlines, "开启书签后应有目录项"


def test_merge_requires_two_files(tmp_path: Path) -> None:
    single = make_pdf(tmp_path / "a.pdf", 1)
    with pytest.raises(PdfError):
        merge_pdfs([single], tmp_path / "out.pdf")


def test_split_modes(tmp_path: Path) -> None:
    sample = make_pdf(tmp_path / "s.pdf", 5)
    each = split_pdf(sample, tmp_path / "each", mode="each")
    assert len(each) == 5 and all(pdf_info(item).pages == 1 for item in each)

    every = split_pdf(sample, tmp_path / "every", mode="every", every=2)
    assert [pdf_info(item).pages for item in every] == [2, 2, 1]

    ranges = split_pdf(sample, tmp_path / "ranges", mode="ranges", ranges="1-2,4")
    assert [pdf_info(item).pages for item in ranges] == [2, 1]
    assert "第1至2页" in ranges[0].name


def test_extract_pages(sample: Path, tmp_path: Path) -> None:
    produced = extract_pages(sample, tmp_path / "x.pdf", pages="2-3")
    assert pdf_info(produced).pages == 2


# ---------------------------------------------------------------- 压缩 / 旋转


def test_compress_pdf(sample: Path, tmp_path: Path) -> None:
    produced = compress_pdf(sample, tmp_path / "c.pdf", level="strong")
    assert produced.is_file()
    assert pdf_info(produced).pages == 4
    assert produced.stat().st_size <= sample.stat().st_size + 200


def test_rotate_pdf(sample: Path, tmp_path: Path) -> None:
    produced = rotate_pdf(sample, tmp_path / "r.pdf", angle=90, pages="1")
    from pypdf import PdfReader

    reader = PdfReader(str(produced))
    assert int(reader.pages[0].get("/Rotate", 0)) == 90
    assert int(reader.pages[1].get("/Rotate", 0) or 0) == 0
    with pytest.raises(PdfError):
        rotate_pdf(sample, tmp_path / "bad.pdf", angle=45)


# ---------------------------------------------------------------- 加密 / 解密


def test_encrypt_and_decrypt(sample: Path, tmp_path: Path) -> None:
    encrypted = encrypt_pdf(sample, tmp_path / "pwd.pdf", user_password="s3cret",
                           allow_printing=True, allow_copying=False)
    info = pdf_info(encrypted)
    assert info.encrypted is True, "加密后必须标记为已加密"
    assert info.pages == 4 or info.pages == 0        # 未解密时页数可能读不出

    with pytest.raises(PdfError) as wrong:
        pdf_info(encrypted, password="nope")
    assert "密码不正确" in str(wrong.value)

    # 只看概况时不带密码也不报错（能报出"已加密"），
    # 但任何需要解密内容的操作都必须先给密码
    assert pdf_info(encrypted).encrypted is True
    with pytest.raises(PdfError) as need_password:
        extract_pages(encrypted, tmp_path / "x.pdf", pages="1")
    assert "已加密" in str(need_password.value)

    assert pdf_info(encrypted, password="s3cret").pages == 4
    opened = decrypt_pdf(encrypted, tmp_path / "dec.pdf", password="s3cret")
    assert pdf_info(opened).encrypted is False
    assert pdf_info(opened).pages == 4


def test_encrypt_requires_password(sample: Path, tmp_path: Path) -> None:
    with pytest.raises(PdfError):
        encrypt_pdf(sample, tmp_path / "x.pdf", user_password="  ")


# ---------------------------------------------------------------- 水印


def test_watermark_pdf(qapp, sample: Path, tmp_path: Path) -> None:  # noqa: ANN001
    produced = watermark_pdf(sample, tmp_path / "wm.pdf", text="内部资料",
                            footer="请勿外传", tracking_id="PDF-TEST-1")
    assert produced.is_file() and produced.stat().st_size > 1000
    assert pdf_info(produced).pages == 4
    with pytest.raises(PdfError):
        watermark_pdf(sample, tmp_path / "none.pdf", text="")


def test_render_overlay_pdf(qapp, tmp_path: Path) -> None:  # noqa: ANN001
    overlay = pdf_tools.render_overlay_pdf("测试水印", tmp_path / "overlay.pdf",
                                           width=595.0, height=842.0)
    assert overlay.is_file()
    assert pdf_info(overlay).pages == 1


# ---------------------------------------------------------------- 表单


def test_list_and_fill_form(tmp_path: Path) -> None:
    form = make_form_pdf(tmp_path / "form.pdf")
    fields = list_form_fields(form)
    assert [item["name"] for item in fields] == ["姓名"]

    filled = fill_form(form, tmp_path / "filled.pdf", {"姓名": "张三"})
    assert pdf_info(filled).pages == 1
    values = {item["name"]: item.get("value") for item in list_form_fields(filled)}
    assert values.get("姓名") == "张三"


def test_fill_form_errors(sample: Path, tmp_path: Path) -> None:
    with pytest.raises(PdfError) as info:
        fill_form(sample, tmp_path / "x.pdf", {"姓名": "张三"})
    assert "没有可填写的表单域" in str(info.value)
    with pytest.raises(PdfError):
        fill_form(sample, tmp_path / "y.pdf", {})
    assert list_form_fields(sample) == []


# ---------------------------------------------------------------- 工具与界面


def test_pdf_tools_registered() -> None:
    names = tool_names()
    expected = {"pdf_info", "pdf_split", "pdf_merge", "pdf_extract_pages", "pdf_compress",
                "pdf_encrypt", "pdf_decrypt", "pdf_rotate", "pdf_watermark", "pdf_form",
                "pdf_to_images"}
    assert expected.issubset(set(names))
    for name in expected:
        spec = TOOL_REGISTRY[name]
        assert spec.category == "pdf" and spec.handler is not None and spec.description


def test_pdf_tool_end_to_end(sample: Path, tmp_path: Path) -> None:
    store = DocumentStorage(tmp_path / "modu.db")
    library = DocumentLibrary(store, output_dir=tmp_path / "out")
    ir = parse_document(sample)
    context = library.tool_context(ir)

    result = call_tool("pdf_info", context, {})
    assert result.ok and "4 页" in result.summary

    result = call_tool("pdf_split", context, {"mode": "every", "every": 2})
    assert result.ok and len(result.data["paths"]) == 2

    result = call_tool("pdf_compress", context, {"level": "medium"})
    assert result.ok and Path(result.data["path"]).is_file()

    result = call_tool("pdf_rotate", context, {"angle": 180, "pages": "1"})
    assert result.ok

    result = call_tool("pdf_extract_pages", context, {"pages": "1-2"})
    assert result.ok

    merged_target = tmp_path / "out" / "合并.pdf"
    result = call_tool("pdf_merge", context, {
        "paths": [str(sample), result.data["path"]], "name": merged_target.stem})
    assert result.ok and pdf_info(result.data["path"]).pages == 6

    result = call_tool("pdf_encrypt", context, {"user_password": "pw", "allow_copying": False})
    assert result.ok and pdf_info(result.data["path"]).encrypted

    result = call_tool("pdf_form", context, {})
    assert result.ok is False and "表单域" in result.summary

    result = call_tool("pdf_to_images", context, {})
    assert result.ok is False, "未安装 PyMuPDF 时应给出可操作提示"
    assert "PyMuPDF" in result.summary or "图片" in result.summary

    assert any(call.tool.startswith("pdf_") for call in context.log)
    actions = {record["action"] for record in library.audit_records()}
    assert "tool" in actions, "工具调用必须写审计（需求 6.8）"
    assert any(record["action"] == "tool" and "PDF" in record["detail"]
               for record in library.audit_records())


def test_pdf_tool_watermark_uses_context_watermark(qapp, sample: Path,  # noqa: ANN001
                                                   tmp_path: Path) -> None:
    from modu_workbench.core.document import build_watermark

    store = DocumentStorage(tmp_path / "modu.db")
    library = DocumentLibrary(store, output_dir=tmp_path / "out")
    library.watermark = build_watermark("机密", tracking_id="T-1")
    context = library.tool_context(parse_document(sample))
    result = call_tool("pdf_watermark", context, {})
    assert result.ok and Path(result.data["path"]).is_file()


def test_pdf_tool_without_text_reports_reason(tmp_path: Path) -> None:
    sample = make_pdf(tmp_path / "a.pdf", 1)
    store = DocumentStorage(tmp_path / "modu.db")
    library = DocumentLibrary(store, output_dir=tmp_path / "out")
    context = library.tool_context(parse_document(sample))
    result = call_tool("pdf_watermark", context, {})
    assert result.ok is False and "水印" in result.summary


def test_pdf_dialog_builds_and_runs(qapp, tmp_path: Path) -> None:  # noqa: ANN001
    from modu_workbench.boards.document.pdf_dialog import PdfToolsDialog

    sample = make_form_pdf(tmp_path / "form.pdf")
    store = DocumentStorage(tmp_path / "modu.db")
    library = DocumentLibrary(store, output_dir=tmp_path / "out")
    dialog = PdfToolsDialog(library, source=str(sample))
    dialog.show()
    qapp.processEvents()
    try:
        assert dialog.source_path() == str(sample)
        assert "页" in dialog._info_label.text()                               # noqa: SLF001

        # 读取表单域 → 填值 → 填写
        dialog._load_form_fields()                                             # noqa: SLF001
        assert dialog._field_table.rowCount() == 1                              # noqa: SLF001
        dialog._field_table.item(0, 3).setText("李四")                           # noqa: SLF001
        dialog._do_fill_form()                                                 # noqa: SLF001
        qapp.processEvents()
        assert "已填写" in dialog._log.toPlainText()                            # noqa: SLF001

        # 拆分 + 压缩 + 旋转 + 加密
        dialog._do_split()                                                     # noqa: SLF001
        dialog._do_compress()                                                  # noqa: SLF001
        dialog._do_rotate()                                                    # noqa: SLF001
        dialog._user_password.setText("pw")                                    # noqa: SLF001
        dialog._do_encrypt()                                                   # noqa: SLF001
        qapp.processEvents()
        log_text = dialog._log.toPlainText()                                    # noqa: SLF001
        assert "拆分" in log_text and "压缩" in log_text and "加密" in log_text
        assert len(list((tmp_path / "out").glob("*.pdf"))) >= 3
    finally:
        dialog.close()


def test_pdf_dialog_merge_flow(qapp, tmp_path: Path) -> None:  # noqa: ANN001
    from modu_workbench.boards.document.pdf_dialog import PdfToolsDialog

    first = make_pdf(tmp_path / "a.pdf", 1)
    second = make_pdf(tmp_path / "b.pdf", 1)
    store = DocumentStorage(tmp_path / "modu.db")
    library = DocumentLibrary(store, output_dir=tmp_path / "out")
    dialog = PdfToolsDialog(library, source=str(first))
    try:
        dialog._merge_list.addItem(str(first))                                 # noqa: SLF001
        dialog._merge_list.addItem(str(second))                                # noqa: SLF001
        dialog._do_merge()                                                     # noqa: SLF001
        qapp.processEvents()
        assert "已合并 2 份" in dialog._log.toPlainText()                        # noqa: SLF001
    finally:
        dialog.close()
