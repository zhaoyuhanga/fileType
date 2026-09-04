"""转换核心测试：registry / 文本 / 图片 / 归档 / JSON / 防覆盖 / 取消。"""
from __future__ import annotations

import threading
import zipfile
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from modu_workbench.core.convert.engine import ConversionCancelled, run_conversion
from modu_workbench.core.convert.registry import actions_for_format, common_actions, get_action
from modu_workbench.core.convert.text_io import JsonFormatError, json_pretty, read_text_smart


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def sample_txt(tmp_path: Path) -> Path:
    path = tmp_path / "示例.txt"
    path.write_text("第一行文字。\n\n第二行文字。", encoding="utf-8")
    return path


@pytest.fixture()
def sample_md(tmp_path: Path) -> Path:
    path = tmp_path / "readme.md"
    path.write_text("# 墨读\n\n一段正文。", encoding="utf-8")
    return path


def test_registry_matrix() -> None:
    assert get_action("txt-to-html") is not None
    assert get_action("png-to-jpg") is not None
    assert get_action("compress-to-zip") is not None
    assert any(a.id == "rar-extract" for a in actions_for_format("rar"))
    common = common_actions(["txt", "markdown"])
    targets = {a.target_format for a in common}
    assert "html" in targets and "pdf" in targets


def test_txt_to_html_and_pdf(qapp: QApplication, sample_txt: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    html_result = run_conversion(get_action("txt-to-html"), sample_txt, out)
    assert html_result.status == "succeeded"
    html = (out / "示例.html").read_text(encoding="utf-8")
    assert "<p>第一行文字。</p>" in html

    pdf_result = run_conversion(get_action("txt-to-pdf"), sample_txt, out)
    assert pdf_result.status == "succeeded"
    data = (out / "示例.pdf").read_bytes()
    assert data[:4] == b"%PDF"

    # 同名输出自动加序号
    again = run_conversion(get_action("txt-to-html"), sample_txt, out)
    assert again.status == "succeeded"
    assert again.output_path and again.output_path.endswith("示例 (2).html")


def test_md_to_html_and_html_to_markdown(sample_md: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    result = run_conversion(get_action("markdown-to-html"), sample_md, out)
    assert result.status == "succeeded"
    assert "<h1" in (out / "readme.html").read_text(encoding="utf-8")

    html = tmp_path / "page.html"
    html.write_text("<h1>标题</h1><p>正文</p>", encoding="utf-8")
    md_result = run_conversion(get_action("html-to-markdown"), html, out)
    assert md_result.status == "succeeded"
    assert "标题" in (out / "page.md").read_text(encoding="utf-8")


def test_json_pretty() -> None:
    assert json_pretty('{"a":1}') == '{\n  "a": 1\n}'
    with pytest.raises(JsonFormatError) as exc:
        json_pretty('{\n"a": ,\n}')
    assert "第 2 行" in str(exc.value)
    assert read_text_smart  # noqa: B018  # 仅确保导入


def test_image_roundtrip(qapp: QApplication, tmp_path: Path) -> None:
    src = tmp_path / "red.png"
    Image.new("RGB", (8, 8), (255, 0, 0)).save(src)
    out = tmp_path / "out"
    out.mkdir()

    for action_id, suffix, expect_red in (
        ("png-to-jpg", ".jpg", True),
        ("png-to-bmp", ".bmp", True),
        ("png-to-webp", ".webp", True),
    ):
        result = run_conversion(get_action(action_id), src, out)
        assert result.status == "succeeded", result.message
        with Image.open(out / f"red{suffix}") as img:
            r, g, b = img.convert("RGB").getpixel((4, 4))
        if expect_red:
            assert r > 200 and g < 80 and b < 80


def test_archives_roundtrip_and_traversal_guard(tmp_path: Path) -> None:
    src = tmp_path / "data.txt"
    src.write_text("归档内容", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()

    for compress_id, ext, extract_id in (
        ("compress-to-zip", ".zip", "zip-extract"),
        ("compress-to-tar", ".tar", "tar-extract"),
    ):
        c = run_conversion(get_action(compress_id), src, out)
        assert c.status == "succeeded"
        archive = Path(c.output_path or "")
        assert archive.exists()
        e = run_conversion(get_action(extract_id), archive, out)
        assert e.status == "succeeded"
        extracted_dir = Path(e.output_path or "")
        assert (extracted_dir / "data.txt").read_text(encoding="utf-8") == "归档内容"

    # 防穿越：手工构造含 ../ 的 tar
    import tarfile

    evil = tmp_path / "evil.tar"
    with tarfile.open(evil, "w") as tf:
        info = tarfile.TarInfo("../escape.txt")
        data = b"bad"
        info.size = len(data)
        tf.addfile(info, __import__("io").BytesIO(data))
    result = run_conversion(get_action("tar-extract"), evil, out)
    assert result.status == "failed"
    assert not (tmp_path / "escape.txt").exists()


def test_cancelled_conversion_short_circuits(sample_txt: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    cancel = threading.Event()
    cancel.set()
    result = run_conversion(get_action("txt-to-html"), sample_txt, out, cancel=cancel)
    assert result.status == "cancelled"


def test_zip_compression_roundtrip_content(tmp_path: Path) -> None:
    src = tmp_path / "note.txt"
    src.write_text("hello zip", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    result = run_conversion(get_action("compress-to-zip"), src, out)
    assert result.status == "succeeded"
    with zipfile.ZipFile(result.output_path) as zf:
        assert zf.read("note.txt").decode("utf-8") == "hello zip"
