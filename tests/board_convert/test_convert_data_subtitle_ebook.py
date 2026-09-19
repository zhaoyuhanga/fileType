"""数据族 / 字幕族 / 电子书族转换测试（模块级 + 通过 engine 的端到端）。

三族的新模块是 engine 新动作的实际实现，所以每个族除了直接调模块，
还要用 `common_actions([...])` 取一个真实动作走一遍 `run_conversion`，
确认「界面上点得动的按钮」真能产出文件（历史上这里出过
「显示成功但输出目录没有数据」的问题）。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from modu_workbench.core.convert import data_io, ebook_io, subtitle_io
from modu_workbench.core.convert.engine import run_conversion
from modu_workbench.core.convert.registry import common_actions, get_action
from modu_workbench.core.convert.text_io import read_text_smart


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """PDF 输出要走 Qt 的 QTextDocument，必须先有 QApplication。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def out_dir(tmp_path: Path) -> Path:
    path = tmp_path / "out"
    path.mkdir()
    return path


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _read(path: Path) -> str:
    """读取文本并把换行统一成 LF。

    ``text_io.write_text`` 用的是平台换行（Windows 上是 CRLF），
    SRT / VTT / TXT 都允许 CRLF，所以断言只关心内容、不纠结换行符。
    """
    return read_text_smart(path).replace("\r\n", "\n")


# ================================================================ 数据族

NESTED_DOCUMENT = {
    "书名": "墨软手册",
    "版本": 2,
    "启用": True,
    "标签": ["转换", "离线"],
    "作者": {"姓名": "墨软", "联系方式": {"邮箱": "hi@modu.example"}},
}
# XML 里的文本没有类型（都是字符串），所以 XML 往返用例只用字符串标量
XML_FRIENDLY_DOCUMENT = {"book": {"@id": "7", "#text": "前言", "item": ["a", "b"], "title": "书"}}
# INI 只能表达两层：段 → 键 = 值
TWO_LEVEL_DOCUMENT = {
    "server": {"host": "localhost", "port": "8080"},
    "user": {"name": "墨软", "vip": "true"},
}


def test_json_yaml_json_roundtrip(tmp_path: Path, out_dir: Path) -> None:
    """JSON ↔ YAML 往返：类型（数字/布尔/数组/嵌套）与中文都必须原样保留。"""
    source = _write(tmp_path / "doc.json", json.dumps(NESTED_DOCUMENT, ensure_ascii=False))
    yaml_out = out_dir / "doc.yaml"
    data_io.convert_data(source, "json", "yaml", yaml_out)

    yaml_text = read_text_smart(yaml_out)
    assert "墨软手册" in yaml_text, "YAML 输出必须保留中文，不能转成 \\uXXXX 转义"
    assert "\\u" not in yaml_text

    assert data_io.parse_data(yaml_out, "yaml") == NESTED_DOCUMENT

    back = out_dir / "back.json"
    data_io.convert_data(yaml_out, "yaml", "json", back)
    assert json.loads(read_text_smart(back)) == NESTED_DOCUMENT


def test_json_ini_json_roundtrip(tmp_path: Path, out_dir: Path) -> None:
    """JSON ↔ INI 往返（两层结构）；INI 的键名大小写不能被 configparser 静默改写。"""
    source = _write(tmp_path / "conf.json", json.dumps(TWO_LEVEL_DOCUMENT, ensure_ascii=False))
    ini_out = out_dir / "conf.ini"
    data_io.convert_data(source, "json", "ini", ini_out)

    ini_text = read_text_smart(ini_out)
    assert "[server]" in ini_text and "host = localhost" in ini_text

    assert data_io.parse_data(ini_out, "ini") == TWO_LEVEL_DOCUMENT

    camel = {"User": {"UserName": "modu"}}
    assert data_io.parse_ini(data_io.serialize_data(camel, "ini")) == camel


def test_xml_object_model_attributes_text_and_repeated_tags() -> None:
    """锁定 XML↔dict 对象模型：@属性、#text、同名子标签成列表。"""
    xml = '<book id="7" lang="zh">前言<item>a</item><item>b</item><title>书</title></book>'
    parsed = data_io.parse_xml_text(xml)
    assert parsed == {
        "book": {"@id": "7", "@lang": "zh", "#text": "前言", "item": ["a", "b"], "title": "书"}
    }
    # 该模型必须能原样写回（往返稳定），否则 JSON→XML→JSON 会变形
    assert data_io.parse_xml_text(data_io.data_to_xml_text(parsed)) == parsed

    # 命名空间写法要能原样往返（ElementTree 用 {uri}local 表示）
    namespaced = {"root": {"{http://example.com/ns}item": "v"}}
    assert data_io.parse_xml_text(data_io.data_to_xml_text(namespaced)) == namespaced

    # 非法标签名（含数字开头的 YAML 整数键）必须给中文错误，不能写出打不开的 XML
    for bad_key in ("a b", "1", ""):
        with pytest.raises(ValueError) as error:
            data_io.data_to_xml_text({bad_key: "x"})
        assert "XML" in str(error.value)


def test_json_xml_json_roundtrip(tmp_path: Path, out_dir: Path) -> None:
    """JSON ↔ XML 往返：单键 dict 直接当根标签名，列表写成重复的 <item>。"""
    source = _write(
        tmp_path / "doc.json", json.dumps(XML_FRIENDLY_DOCUMENT, ensure_ascii=False)
    )
    xml_out = out_dir / "doc.xml"
    data_io.convert_data(source, "json", "xml", xml_out)

    xml_text = read_text_smart(xml_out)
    assert xml_text.startswith("<?xml")
    assert "<book" in xml_text and "<item>a</item>" in xml_text and "<item>b</item>" in xml_text

    assert data_io.parse_data(xml_out, "xml") == XML_FRIENDLY_DOCUMENT

    # 顶层列表：数组 → <root><item>…</item></root>，回来是 root/item 列表
    assert data_io.parse_xml_text(data_io.data_to_xml_text(["x", "y"])) == {
        "root": {"item": ["x", "y"]}
    }


def test_yaml_special_scalars_do_not_break_other_targets(tmp_path: Path, out_dir: Path) -> None:
    """YAML 的日期/布尔会被 PyYAML 解析成 date/bool：转 JSON / TXT 不能抛英文 TypeError。"""
    source = _write(tmp_path / "meta.yaml", "发布: 2024-05-06\n草稿: false\n")
    json_out = out_dir / "meta.json"
    data_io.convert_data(source, "yaml", "json", json_out)

    parsed = json.loads(read_text_smart(json_out))
    assert parsed["发布"] == "2024-05-06" and parsed["草稿"] is False

    txt_out = out_dir / "meta.txt"
    data_io.convert_data(source, "yaml", "txt", txt_out)
    assert "2024-05-06" in read_text_smart(txt_out)


def test_data_to_csv_from_list_and_single_object() -> None:
    """CSV 目标：对象数组 → 多行；{"items": [...]} 包装先拆开；单对象 → 一行。"""
    text = data_io.serialize_data({"items": [{"名称": "苹果", "数量": 3}, {"名称": "香蕉", "数量": 5}]}, "csv")
    lines = text.splitlines()
    assert lines[0] == "名称,数量"
    assert lines[1] == "苹果,3"
    assert lines[2] == "香蕉,5"

    single = data_io.serialize_data({"名称": "梨", "数量": 1}, "csv")
    assert single.splitlines() == ["名称,数量", "梨,1"]

    # 单元格里的数字/布尔/字符串都按原意写出（不是 Python 的 True/None 写法）
    typed = data_io.serialize_data([{"a": 1, "b": True, "c": None}], "csv")
    assert typed.splitlines()[1] == "1,true,"


def test_data_text_targets_include_pdf(tmp_path: Path, out_dir: Path, qapp: QApplication) -> None:
    """txt / markdown / html / pdf 目标：都要真的产出非空文件，txt 仍是可解析的 JSON。"""
    source = _write(tmp_path / "doc.json", json.dumps(NESTED_DOCUMENT, ensure_ascii=False))

    txt_out = out_dir / "doc.txt"
    data_io.convert_data(source, "json", "txt", txt_out, title="手册")
    assert json.loads(read_text_smart(txt_out)) == NESTED_DOCUMENT

    md_out = out_dir / "doc.md"
    data_io.convert_data(source, "json", "markdown", md_out)
    markdown_text = read_text_smart(md_out)
    assert markdown_text.startswith("```json") and "墨软手册" in markdown_text

    html_out = out_dir / "doc.html"
    data_io.convert_data(source, "json", "html", html_out, title="手册")
    html_text = read_text_smart(html_out)
    assert "<title>手册</title>" in html_text and "<p>" in html_text

    pdf_out = out_dir / "doc.pdf"
    data_io.convert_data(source, "json", "pdf", pdf_out, title="手册")
    assert pdf_out.stat().st_size > 0
    assert pdf_out.read_bytes()[:4] == b"%PDF"


@pytest.mark.parametrize(
    ("source_format", "suffix", "broken", "keyword"),
    [
        ("json", ".json", '{"a": 1,}', "JSON"),
        ("json", ".json", '{\n"a": ,\n}', "第 2 行"),
        ("xml", ".xml", "<a><b></a>", "XML"),
        ("ini", ".ini", "b = 1", "INI"),
        ("yaml", ".yaml", "a: [1,", "YAML"),
    ],
)
def test_malformed_data_source_raises_value_error(
    source_format: str, suffix: str, broken: str, keyword: str, tmp_path: Path, out_dir: Path
) -> None:
    """坏输入必须是带中文说明的 ValueError（含格式名/行列），不能漏英文堆栈给用户。"""
    source = _write(tmp_path / f"broken{suffix}", broken)
    with pytest.raises(ValueError) as error:
        data_io.convert_data(source, source_format, "json", out_dir / "out.json")
    message = str(error.value)
    assert keyword in message
    assert "Traceback" not in message
    assert not (out_dir / "out.json").exists()


def test_data_to_ini_rejects_nested_structure() -> None:
    """INI 表达不了三层/列表：要抛中文错误并提示改用 JSON / YAML / XML。"""
    with pytest.raises(ValueError) as error:
        data_io.serialize_data(NESTED_DOCUMENT, "ini")
    message = str(error.value)
    assert "INI" in message and "JSON" in message


def test_data_family_through_engine(tmp_path: Path, out_dir: Path) -> None:
    """端到端：用 common_actions 取真实动作跑 engine（JSON → CSV / YAML）。"""
    source = _write(
        tmp_path / "清单.json",
        json.dumps({"items": [{"名称": "苹果", "数量": 3}, {"名称": "香蕉", "数量": 5}]}, ensure_ascii=False),
    )

    actions = common_actions(["json", "yaml", "ini", "xml"])
    csv_action = next(action for action in actions if action.target_format == "csv")
    result = run_conversion(csv_action, source, out_dir)
    assert result.status == "succeeded", result.message
    csv_path = Path(result.output_path or "")
    assert csv_path.is_file() and csv_path.stat().st_size > 0
    assert "苹果,3" in read_text_smart(csv_path)

    xml_source = _write(tmp_path / "配置.xml", '<config mode="fast"><retry>3</retry></config>')
    yaml_action = next(action for action in common_actions(["xml", "json"]) if action.target_format == "yaml")
    yaml_result = run_conversion(yaml_action, xml_source, out_dir)
    assert yaml_result.status == "succeeded", yaml_result.message
    yaml_path = Path(yaml_result.output_path or "")
    assert yaml_path.is_file()
    assert data_io.parse_data(yaml_path, "yaml") == {"config": {"@mode": "fast", "retry": "3"}}


# ================================================================ 字幕族

SRT_SAMPLE = (
    "1\n"
    "00:00:01,000 --> 00:00:02,500\n"
    "第一条字幕\n"
    "\n"
    "2\n"
    "00:00:03,000 --> 00:00:04,000\n"
    "第二条字幕\n"
    "第二行\n"
)


def test_srt_to_vtt_to_srt_keeps_timings_and_text(tmp_path: Path, out_dir: Path) -> None:
    """srt → vtt → srt 必须无损：时间码（逗号↔点）与文本都要一致。"""
    source = _write(tmp_path / "字幕.srt", SRT_SAMPLE)

    vtt_path = out_dir / "字幕.vtt"
    subtitle_io.convert_subtitle(source, "srt", "vtt", vtt_path)
    vtt_text = _read(vtt_path)
    assert vtt_text.startswith("WEBVTT")
    assert "00:00:01.000 --> 00:00:02.500" in vtt_text, "VTT 用点毫秒"

    back_path = out_dir / "回到srt.srt"
    subtitle_io.convert_subtitle(vtt_path, "vtt", "srt", back_path)
    srt_text = _read(back_path)
    assert "00:00:01,000 --> 00:00:02,500" in srt_text, "SRT 用逗号毫秒"

    original = subtitle_io.parse_subtitle(source, "srt")
    roundtrip = subtitle_io.parse_subtitle(back_path, "srt")
    assert [(c.start_ms, c.end_ms, c.text) for c in roundtrip] == [
        (c.start_ms, c.end_ms, c.text) for c in original
    ]
    # 索引重新编号，从 1 连续（源文件里的索引不可信）
    assert srt_text.splitlines()[0] == "1" and "\n2\n" in srt_text


def test_srt_without_blank_lines_between_cues_still_parses() -> None:
    """现实里的 SRT 经常漏写 cue 之间的空行：按行推进也要能拆对，不能把序号吃进台词。"""
    srt = (
        "1\n"
        "00:00:01,000 --> 00:00:02,000\n"
        "第一条\n"
        "2\n"
        "00:00:03,000 --> 00:00:04,000\n"
        "第二条\n"
        "3\n"
        "00:00:05,000 --> 00:00:06,000\n"
        "第三条\n"
    )
    cues = subtitle_io.parse_subtitle_text(srt, "srt")
    assert [(c.start_ms, c.text) for c in cues] == [(1000, "第一条"), (3000, "第二条"), (5000, "第三条")]


def test_vtt_header_note_and_cue_settings_are_not_subtitle_text() -> None:
    """VTT 的 WEBVTT 头 / NOTE 块 / cue 设置都不能混进台词，也支持省略小时的时间码。"""
    vtt = (
        "WEBVTT\n"
        "Kind: captions\n"
        "\n"
        "NOTE 这是译者注，不是台词\n"
        "第二行注释\n"
        "\n"
        "intro\n"
        "00:01.000 --> 00:02.000 align:middle line:90%\n"
        "台词一\n"
        "\n"
        "00:00:03.000 --> 00:00:04.000\n"
        "台词二\n"
    )
    cues = subtitle_io.parse_subtitle_text(vtt, "vtt")
    assert [(c.start_ms, c.end_ms, c.text) for c in cues] == [
        (1000, 2000, "台词一"),
        (3000, 4000, "台词二"),
    ]
    assert cues[0].settings == "align:middle line:90%"
    assert "NOTE" not in subtitle_io.cues_to_text(cues)
    assert "WEBVTT" not in subtitle_io.cues_to_text(cues)
    # 设置项要在 VTT 输出里保留，SRT 输出里没有这个概念
    assert "align:middle" in subtitle_io.cues_to_vtt(cues)
    assert "align:middle" not in subtitle_io.cues_to_srt(cues)


def test_srt_to_txt_drops_indices_and_timestamps(tmp_path: Path, out_dir: Path) -> None:
    source = _write(tmp_path / "字幕.srt", SRT_SAMPLE)
    txt_path = out_dir / "字幕.txt"
    subtitle_io.convert_subtitle(source, "srt", "txt", txt_path)

    text = _read(txt_path)
    assert "-->" not in text and "00:00" not in text
    assert text == "第一条字幕\n第二条字幕\n第二行"


@pytest.mark.parametrize(
    ("broken", "keyword"),
    [
        ("", "字幕"),
        ("这里只有一句普通文本，没有时间码", "时间码"),
        ("1\n00:00:0X,000 --> 00:00:02,000\n文本", "时间码"),
        ("1\n00:00:05,000 --> 00:00:02,000\n文本", "结束时间"),
    ],
)
def test_malformed_subtitle_raises_value_error(broken: str, keyword: str) -> None:
    with pytest.raises(ValueError) as error:
        subtitle_io.parse_subtitle_text(broken, "srt")
    message = str(error.value)
    assert keyword in message
    assert "Traceback" not in message


def test_subtitle_family_through_engine(tmp_path: Path, out_dir: Path) -> None:
    """端到端：common_actions(["srt","vtt"]) 里的动作（转 txt）真能跑出文件。"""
    source = _write(tmp_path / "字幕.srt", SRT_SAMPLE)

    txt_action = next(action for action in common_actions(["srt", "vtt"]) if action.target_format == "txt")
    result = run_conversion(txt_action, source, out_dir)
    assert result.status == "succeeded", result.message
    txt_path = Path(result.output_path or "")
    assert txt_path.is_file() and txt_path.stat().st_size > 0
    assert _read(txt_path) == "第一条字幕\n第二条字幕\n第二行"

    vtt_result = run_conversion(get_action("srt-to-vtt"), source, out_dir)
    assert vtt_result.status == "succeeded", vtt_result.message
    vtt_path = Path(vtt_result.output_path or "")
    assert vtt_path.is_file() and _read(vtt_path).startswith("WEBVTT")

    # 再用 engine 把 vtt 转回 srt，确认引擎两侧闭环
    back = run_conversion(get_action("vtt-to-srt"), vtt_path, out_dir)
    assert back.status == "succeeded", back.message
    assert "00:00:01,000 --> 00:00:02,500" in _read(Path(back.output_path or ""))


# ================================================================ 电子书族

EBOOK_TEXT = "第一段正文。\n\n第二段第一行\n第二段第二行\n\n第三段正文。"


def test_txt_to_epub_to_txt_roundtrip(tmp_path: Path) -> None:
    """纯文本 → EPUB → 纯文本：段落与段内换行都要保持一致。"""
    epub_path = tmp_path / "书.epub"
    ebook_io.write_epub(EBOOK_TEXT, epub_path, title="测试书")

    assert epub_path.stat().st_size > 0
    with zipfile.ZipFile(epub_path) as archive:
        assert archive.read("mimetype") == b"application/epub+zip"
        assert any(name.endswith(".opf") for name in archive.namelist())

    assert ebook_io.epub_to_text(epub_path) == EBOOK_TEXT


def test_long_text_becomes_multiple_chapters_and_still_roundtrips(tmp_path: Path) -> None:
    """超长文本按段落切成多章（避免单个巨型 xhtml），往返仍然一致。"""
    text = "\n\n".join(f"第 {index} 段" + "正文" * 30 for index in range(600))
    epub_path = tmp_path / "长书.epub"
    ebook_io.write_epub(text, epub_path, title="长书")

    with zipfile.ZipFile(epub_path) as archive:
        chapters = [name for name in archive.namelist() if "text_" in name]
    assert len(chapters) > 1
    assert ebook_io.epub_to_text(epub_path) == text


def test_epub_escapes_markup_like_text(tmp_path: Path) -> None:
    """正文里的 < > & 必须转义后再写进 <p>，不能变成标签、也不能在往返中丢失。"""
    text = "带 <尖括号> 与 & 符号\n\n第二段 <i>这不该变成斜体标签</i>"
    epub_path = tmp_path / "符号.epub"
    ebook_io.write_epub(text, epub_path, title="符号")

    with zipfile.ZipFile(epub_path) as archive:
        chapter_name = next(name for name in archive.namelist() if "text_" in name)
        chapter = archive.read(chapter_name).decode("utf-8")
    assert "&lt;尖括号&gt;" in chapter and "&amp;" in chapter
    assert ebook_io.epub_to_text(epub_path) == text


def test_broken_epub_raises_chinese_error(tmp_path: Path) -> None:
    """损坏/非 EPUB 文件、空文件、空文本都要给中文 ValueError。"""
    broken = tmp_path / "坏书.epub"
    broken.write_bytes(b"this is not an epub")
    with pytest.raises(ValueError) as error:
        ebook_io.epub_to_text(broken)
    message = str(error.value)
    assert "EPUB" in message and "Traceback" not in message

    empty = tmp_path / "空书.epub"
    empty.write_bytes(b"")
    with pytest.raises(ValueError) as empty_error:
        ebook_io.epub_to_text(empty)
    assert "EPUB" in str(empty_error.value)

    with pytest.raises(ValueError) as text_error:
        ebook_io.write_epub("   \n\n  ", tmp_path / "无内容.epub")
    assert "EPUB" in str(text_error.value)


def test_ebook_family_through_engine(tmp_path: Path, out_dir: Path) -> None:
    """端到端：文本 → EPUB（common_actions）以及 EPUB → TXT 都走 engine。"""
    source = _write(tmp_path / "正文.md", "第一段内容。\n\n第二段内容。")

    to_epub = next(action for action in common_actions(["txt", "markdown", "html"]) if action.target_format == "epub")
    epub_result = run_conversion(to_epub, source, out_dir)
    assert epub_result.status == "succeeded", epub_result.message
    epub_path = Path(epub_result.output_path or "")
    assert epub_path.is_file() and epub_path.suffix == ".epub" and epub_path.stat().st_size > 0
    assert ebook_io.epub_to_text(epub_path) == "第一段内容。\n\n第二段内容。"

    to_txt = next(action for action in common_actions(["epub"]) if action.target_format == "txt")
    txt_result = run_conversion(to_txt, epub_path, out_dir)
    assert txt_result.status == "succeeded", txt_result.message
    txt_path = Path(txt_result.output_path or "")
    assert txt_path.is_file() and txt_path.stat().st_size > 0
    assert "第二段内容" in read_text_smart(txt_path)
