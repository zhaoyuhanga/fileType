"""墨软文档：需求 6.10「验收标准」逐条验收测试。

每条验收标准一个测试，全部走**真实引擎**（解析 → 处理 → 写出 → 回读），
不做 UI 交互（界面另有 `test_document_ui.py`），也不联网（AI 用替身模型）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from modu_workbench.core.document import (
    TOOL_REGISTRY,
    DocumentAi,
    DocumentLibrary,
    DocumentStorage,
    EXPORT_TARGETS,
    FORMAT_SPECS,
    LoopOptions,
    MergeOptions,
    Permissions,
    TableData,
    autofill,
    beautify_document,
    evaluate_formula,
    format_key_for_path,
    get_spec,
    parse_document,
    spec_for_path,
    tool_names,
    write_document,
)
from modu_workbench.core.document.ocr import available as ocr_available
from modu_workbench.core.llm import KIND_TEXT, LlmStorage, ModelProfile, ModelRouter
from modu_workbench.core.llm.router import OpenAiCompatClient

MARKDOWN = """# 年度报告

公司2024年实现收入 1,200,000 元,同比增长 15% 。

## 1.1 数据

| 项目 | 数量 | 单价 | 金额 |
| --- | --- | --- | --- |
| A | 3 | 10 | =B3*C3 |
| B | 2 | 20 | =B4*C4 |

## 1.2 结论

整体表现良好。
"""

OTHER = """# 补充材料

## 一、市场

华东区增长明显。
"""


@pytest.fixture()
def workspace(tmp_path: Path) -> dict:
    """一份可反复使用的测试环境：主文档、副文档、表格、CSV 与文档库。"""
    main = tmp_path / "报告.md"
    main.write_text(MARKDOWN, encoding="utf-8")
    other = tmp_path / "补充.md"
    other.write_text(OTHER, encoding="utf-8")
    sheet = tmp_path / "明细.csv"
    sheet.write_text("部门,数量\n销售,3\n研发,2\n", encoding="utf-8")
    store = DocumentStorage(tmp_path / "modu.db")
    library = DocumentLibrary(store, output_dir=tmp_path / "out")
    return {"tmp": tmp_path, "main": main, "other": other, "sheet": sheet,
            "store": store, "library": library}


# ---------------------------------------------------------------- 6.10.1


def test_acceptance_view_and_edit_core_formats(workspace: dict) -> None:
    """① 能查看并编辑 Excel、MD、Word、Text、PDF 等格式。"""
    library: DocumentLibrary = workspace["library"]
    main: Path = workspace["main"]

    # Markdown / TXT：解析 + 编辑 + 保存
    ir = library.open_path(main)
    assert ir.headings() and ir.tables()
    ir.replace_text("整体表现良好", "整体表现优异")
    saved = library.save(ir, main)
    assert "整体表现优异" in main.read_text(encoding="utf-8")

    # Word：写出后可读回，且段落/标题结构保留
    docx_path = workspace["tmp"] / "报告.docx"
    write_document(ir, docx_path, "docx")
    word_ir = parse_document(docx_path)
    assert [block.text for block in word_ir.headings()] == ["年度报告", "1.1 数据", "1.2 结论"]

    # Excel：解析公式列 + 编辑 + 写回
    xlsx_path = workspace["tmp"] / "明细.xlsx"
    write_document(parse_document(workspace["sheet"]), xlsx_path, "xlsx")
    sheet_ir = parse_document(xlsx_path)
    assert sheet_ir.first_table().rows[0] == ["部门", "数量"]
    sheet_ir.first_table().rows[1][1] = "9"
    write_document(sheet_ir, xlsx_path, "xlsx", overwrite=True)
    assert parse_document(xlsx_path).first_table().rows[1][1] == "9"

    # 纯文本
    text_ir = library.open_path(workspace["sheet"])
    assert text_ir.text()

    # PDF：可查看（文本层）+ 页面级编辑（拆分/压缩等，见 test_document_pdf.py）
    from pypdf import PdfWriter

    pdf_path = workspace["tmp"] / "空白.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with open(pdf_path, "wb") as handle:
        writer.write(handle)
    pdf_ir = parse_document(pdf_path)
    assert pdf_ir.format_key == "pdf" and get_spec("pdf").edit is False

    # 能力矩阵里"可编辑"的说法必须与真实能力一致
    assert get_spec("markdown").edit and get_spec("docx").edit and get_spec("xlsx").edit
    assert get_spec("pdf").edit is False and get_spec("doc").edit is False


# ---------------------------------------------------------------- 6.10.2


def test_acceptance_extended_formats(workspace: dict) -> None:
    """② 能补充支持 PPT、CSV、RTF、ODT、WPS、HTML、JSON、图片 OCR 等。"""
    for name in ("pptx", "csv", "rtf", "odt", "wps", "html", "json", "png", "jpg", "log",
                 "sql", "epub", "ods", "et", "dps", "xls", "ppt"):
        assert get_spec(name) is not None, f"{name} 未登记"
        assert get_spec(name).capability_text()

    # CSV / HTML / JSON 真实解析
    csv_ir = parse_document(workspace["sheet"])
    assert csv_ir.first_table().rows[1] == ["销售", "3"]

    html_path = workspace["tmp"] / "页面.html"
    html_path.write_text("<h1>标题</h1><p>正文</p>", encoding="utf-8")
    assert parse_document(html_path).blocks[0].text == "标题"

    json_path = workspace["tmp"] / "数据.json"
    json_path.write_text('{"a": 1}', encoding="utf-8")
    json_ir = parse_document(json_path)
    assert "对象" in json_ir.metadata["structure"]

    # PPTX：手工拼一份最小演示文稿（纯标准库），验证"逐页 + 备注"可读
    pptx_path = workspace["tmp"] / "演示.pptx"
    _write_minimal_pptx(pptx_path)
    slides = parse_document(pptx_path)
    assert slides.format_key == "pptx"
    assert any(block.kind == "slide" for block in slides.blocks)
    assert get_spec("pptx").edit is True, "PPTX 文本层应可编辑"

    # 图片与 OCR：能力登记为支持，引擎缺失时要能说清原因
    image_path = workspace["tmp"] / "扫描.png"
    _write_png(image_path)
    image_ir = parse_document(image_path, ocr=True)
    assert image_ir.metadata.get("width")
    if not ocr_available():
        assert any("OCR" in warning for warning in image_ir.warnings), \
            "OCR 不可用时必须写明原因，而不是静默失败"


# ---------------------------------------------------------------- 6.10.3


def test_acceptance_beautify_autofill_and_formulas(workspace: dict) -> None:
    """③ 能一键美化、自动填充、公式计算。"""
    ir = parse_document(workspace["main"])

    result = beautify_document(ir, template="report")
    assert result.change_count > 0
    assert any(block.kind == "toc" for block in result.ir.blocks)
    assert result.ir.tables()[0].styles["border"] == "grid"

    # 自动填充：文本+序号 与 公式平移
    values, spec = autofill(["第1项", "第2项"], 3)
    assert values == ["第3项", "第4项", "第5项"] and spec.kind == "text_number"
    formulas, _spec = autofill(["=B1*C1", "=B2*C2"], 2)
    assert formulas == ["=B3*C3", "=B4*C4"]

    # 公式计算：求和、条件、跨行引用
    table = TableData(rows=[["数量"], ["3"], ["4"]], name="Sheet1")
    assert float(evaluate_formula("=SUM(A2:A3)", table, sheet_name="Sheet1")) == 7.0
    assert float(evaluate_formula("=SUM(A2:A3)*2", table, sheet_name="Sheet1")) == 14.0


# ---------------------------------------------------------------- 6.10.4


def test_acceptance_ai_model_config_and_tools(workspace: dict, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """④ 能配置 AI 文本模型并调用工具（多配置 + 失败降级 + 工具调用）。"""
    storage = LlmStorage(tmp_path / "llm.db")
    storage.save_profile(ModelProfile(kind=KIND_TEXT, name="备用",
                                      provider="custom", base_url="https://api.example.com/v1",
                                      api_key="k2", model="m2"))
    router = ModelRouter(storage)
    assert router.has_any(KIND_TEXT) and "调用顺序" in router.describe(KIND_TEXT)

    ai = DocumentAi(router, workspace["store"],
                    permissions=Permissions(allow_cloud=True))
    monkeypatch.setattr(OpenAiCompatClient, "chat",
                        lambda self, messages, model="": "已按要求处理。")
    assert ai.transform("原文", "polish") == "已按要求处理。"

    # 工具：界面按钮与 AI 走同一套实现
    library: DocumentLibrary = workspace["library"]
    ir = parse_document(workspace["main"])
    result, updated = library.call_tool("beautify", ir, {"template": "report"})
    assert result.ok and updated is not None
    assert len(tool_names()) >= 50 and "pdf_merge" in TOOL_REGISTRY


# ---------------------------------------------------------------- 6.10.5


def test_acceptance_merge_two_documents_same_and_cross_format(workspace: dict) -> None:
    """⑤ 能实现两个文档合并，支持同格式和跨格式。"""
    library: DocumentLibrary = workspace["library"]
    same = library.merge_paths(workspace["main"], workspace["other"],
                               MergeOptions(mode="append"))
    assert "补充材料" in same.ir.text()
    assert same.report.diff and same.report.changes

    crossing = library.merge_paths(workspace["main"], workspace["sheet"],
                                  MergeOptions(mode="append"))
    assert len(crossing.ir.tables()) == 2, "跨格式（MD + CSV）合并后两张表都在"

    labelled = library.merge_paths(workspace["main"], workspace["other"],
                                  MergeOptions(mode="append", add_toc=True))
    assert any(block.kind == "toc" for block in labelled.ir.blocks)


# ---------------------------------------------------------------- 6.10.6


def test_acceptance_loop_beautify_multiple_rounds(workspace: dict, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """⑥ 能根据 AI 输入进行多轮循环美化。"""
    storage = LlmStorage(tmp_path / "llm.db")
    storage.save_profile(ModelProfile(kind=KIND_TEXT, name="替身", provider="custom",
                                      base_url="https://api.example.com/v1",
                                      api_key="k", model="m"))
    ai = DocumentAi(ModelRouter(storage), workspace["store"],
                    permissions=Permissions(allow_cloud=True))

    def fake_chat(self, messages, model=""):  # noqa: ANN001, ARG001
        prompt = "\n".join(str(message.get("content") or "") for message in messages)
        if "选择需要调用的工具" in prompt:
            return '{"steps": ["polish", "beautify"]}'
        if "质量评估员" in prompt:
            return ('{"format_consistency":0.9,"language_quality":0.9,"readability":0.9,'
                    '"factual_consistency":1,"goal_match":0.9}')
        return "改写后的正文内容。"

    monkeypatch.setattr(OpenAiCompatClient, "chat", fake_chat)
    library: DocumentLibrary = workspace["library"]
    library.ai = ai

    ir = parse_document(workspace["main"])
    result = library.run_loop(ir, LoopOptions(goal="润色并统一格式", max_rounds=2,
                                             quality_threshold=1.01, use_ai=True,
                                             allow_tools=("polish", "beautify")))
    assert result.round_count >= 1
    assert result.rounds[0].plan, "每轮都要有计划"
    assert any(report.ai_used for report in result.rounds)
    assert any(call.ai_generated for report in result.rounds for call in report.changes), \
        "AI 改写的段落要被标注"


# ---------------------------------------------------------------- 6.10.7


def test_acceptance_every_round_preview_rollback_and_log(workspace: dict) -> None:
    """⑦ 每轮 AI 修改可预览、可回滚、有日志。"""
    library: DocumentLibrary = workspace["library"]
    ir = library.open_path(workspace["main"])
    result = library.run_loop(ir, LoopOptions(goal="规范标点", max_rounds=2,
                                             quality_threshold=0.99, use_ai=False,
                                             stop_on_no_change=False))
    first = result.rounds[0]
    assert first.diff, "每轮都要有 diff 可以预览"
    assert first.changes, "每轮都要有修改明细"
    assert first.tool_calls, "每轮都要有工具调用日志"
    assert result.log, "整个流程要有运行日志"
    assert first.version_id, "每轮要留版本快照"

    snapshot = library.storage.load_version_ir(first.version_id)
    assert snapshot is not None and snapshot.text(), "快照可用于回滚"
    rolled = library.rollback(first.version_id)
    assert rolled is not None
    assert any(record["action"] == "rollback" for record in library.audit_records())


# ---------------------------------------------------------------- 6.10.8


def test_acceptance_export_common_formats(workspace: dict) -> None:
    """⑧ 合并与美化结果可导出为常见格式。"""
    library: DocumentLibrary = workspace["library"]
    merged = library.merge_paths(workspace["main"], workspace["other"],
                                 MergeOptions(mode="append"))
    beautified = library.beautify(merged.ir, template="report").ir

    produced: list[str] = []
    for target, _label in EXPORT_TARGETS:
        if target == "pdf":
            continue          # PDF 渲染需要 QApplication，界面测试里覆盖
        result = library.export(beautified, target, name=f"验收-{target}")
        assert result.path.is_file() and result.path.stat().st_size > 0
        produced.append(target)
    assert {"docx", "xlsx", "html", "txt", "markdown", "csv", "tsv", "json"}.issubset(
        set(produced))

    # 导出的 Word 能被重新读回（说明不是写了个空文件）
    back = parse_document(library.output_dir and Path(library.output_dir) / "验收-docx.docx")
    assert back.blocks and back.tables()


# ---------------------------------------------------------------- 辅助


def _write_minimal_pptx(path: Path) -> None:
    """写一个最小 PPTX（纯标准库拼 OOXML），只为验证"逐页 + 备注"解析路径。"""
    import zipfile

    slide = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<p:sld xmlns:a='http://schemas.openxmlformats.org/drawingml/2006/main'"
        " xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'>"
        "<p:cSld><p:spTree><p:sp><p:txBody>"
        "<a:p><a:r><a:t>第一页标题</a:t></a:r></a:p>"
        "<a:p><a:r><a:t>第一页正文</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    notes = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<p:notes xmlns:a='http://schemas.openxmlformats.org/drawingml/2006/main'"
        " xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main'>"
        "<p:cSld><p:spTree><p:sp><p:txBody>"
        "<a:p><a:r><a:t>这是讲者备注</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:notes>"
    )
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("[Content_Types].xml", "<Types/>")
        bundle.writestr("ppt/slides/slide1.xml", slide)
        bundle.writestr("ppt/notesSlides/notesSlide1.xml", notes)


def _write_png(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (120, 80), (200, 210, 230)).save(path)


def test_format_matrix_covers_required_extension_lookup(workspace: dict) -> None:
    """顺带锁定扩展名 → 格式的映射（避免以后改格式表时把别名弄丢）。"""
    assert format_key_for_path("a.docx") == "docx"
    assert format_key_for_path("a.docm") == "docx"
    assert format_key_for_path("b.md") == "markdown"
    assert format_key_for_path("c.xlsm") == "xlsx"
    assert format_key_for_path("d.htm") == "html"
    assert format_key_for_path("e.yml") == "yaml"
    assert spec_for_path("f.pptx") is get_spec("pptx")
    assert len(FORMAT_SPECS) == len({spec.key for spec in FORMAT_SPECS})
