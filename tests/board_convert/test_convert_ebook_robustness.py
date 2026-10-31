"""加密/DRM 标记 EPUB 的健壮性（离线可验证的那部分）。

此前报告里标注「真实第三方 EPUB（含 DRM）未实测」。真 DRM 书籍需要购买/授权文件，
不适合放进仓库；但**加密标记本身**（`META-INF/encryption.xml`）可以离线构造，
它正是多数阅读器/库判断"这本书被加密"的依据。

这里锁定的契约是：**不许崩、不许抛英文堆栈** ——
要么给出可读的中文 ValueError（推荐路径），要么照常读出文本（ebooklib 忽略加密标记的情况）。
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from modu_workbench.core.convert.ebook_io import epub_to_text, write_epub

ENCRYPTION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>
    <enc:CipherData><enc:CipherReference URI="EPUB/chapter.xhtml"/></enc:CipherData>
  </enc:EncryptedData>
</encryption>
"""


def _make_epub(path: Path) -> Path:
    write_epub("第一段。\n\n第二段。", path, title="测试书")
    return path


def _add_encryption_marker(epub: Path, marked: Path) -> Path:
    """复制一份 EPUB 并在 META-INF 里塞入加密标记（模拟 DRM 书籍的结构特征）。"""
    shutil.copy(epub, marked)
    with zipfile.ZipFile(marked, "a", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("META-INF/encryption.xml", ENCRYPTION_XML)
    return marked


def test_encryption_marker_epub_never_crashes_or_leaks_english_traceback(tmp_path: Path) -> None:
    plain = _make_epub(tmp_path / "书.epub")
    marked = _add_encryption_marker(plain, tmp_path / "加密书.epub")

    try:
        text = epub_to_text(marked)
    except ValueError as error:
        message = str(error)
        assert message.strip(), "错误信息不能是空的"
        assert any("\u4e00" <= char <= "\u9fff" for char in message), \
            f"错误信息应为中文可读提示，实际：{message}"
    except Exception as error:  # noqa: BLE001
        raise AssertionError(
            f"加密标记的 EPUB 抛出了非 ValueError（可能是英文堆栈）：{type(error).__name__}: {error}"
        ) from error
    else:
        # 允许"ebooklib 忽略加密标记、照常读出正文"这条现实路径
        assert isinstance(text, str)


def test_truncated_epub_gives_a_chinese_error(tmp_path: Path) -> None:
    """被截断的 EPUB（真实世界里下载中断的常见形态）必须是中文可读错误。"""
    plain = _make_epub(tmp_path / "书.epub")
    truncated = tmp_path / "断书.epub"
    truncated.write_bytes(plain.read_bytes()[:200])

    with pytest.raises(ValueError) as excinfo:
        epub_to_text(truncated)
    message = str(excinfo.value)
    assert any("\u4e00" <= char <= "\u9fff" for char in message), f"应为中文提示，实际：{message}"
