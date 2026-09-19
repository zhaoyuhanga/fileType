"""转换格式覆盖与注册表不变量。

为什么单独一个文件：动作是**数据**（`registry.py` 里批量生成），
最容易出的错不是"转换写错了"，而是"格式登记了却没有动作 / 动作 id 撞车 /
目标没有扩展名映射" —— 这些在界面上表现为"点了没反应"或"文件被跳过"。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.convert.formats import (
    ARCHIVE_FORMATS,
    ARCHIVE_READONLY_FORMATS,
    SUPPORTED_FORMATS,
    TARGET_EXTENSION,
    format_from_extension,
    format_label,
)
from modu_workbench.core.convert.registry import (
    ACTIONS,
    actions_for_format,
    common_actions,
    get_action,
    union_actions,
)

# 用户最常打交道的格式：每一个都必须有可用动作，否则"加进来却转不了"
HEADLINE_FORMATS = (
    "txt", "markdown", "html", "pdf", "json", "xml", "ini", "yaml",
    "csv", "tsv", "xlsx", "xls", "ods", "docx", "doc", "odt", "rtf",
    "jpg", "png", "webp", "bmp", "gif", "tiff", "ico", "tga", "pcx", "ppm", "psd",
    "mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "m4v", "mpg", "mpeg", "ts", "3gp", "ogv",
    "mp3", "m4a", "wav", "flac", "aac", "ogg", "opus", "wma", "m4b", "aiff", "amr", "ac3",
    "srt", "vtt", "epub",
    "zip", "tar", "targz", "tarbz2", "tarxz", "gz", "bz2", "xz", "rar",
)


def test_action_ids_are_unique() -> None:
    """id 撞车会让并集去重把文件判成"不适用"（tar 与 tar.gz 就踩过这个坑）。"""
    ids = [action.id for action in ACTIONS]
    duplicates = {action_id for action_id in ids if ids.count(action_id) > 1}
    assert not duplicates, f"动作 id 重复：{sorted(duplicates)}"


def test_every_supported_format_has_at_least_one_action() -> None:
    missing = [fmt for fmt in HEADLINE_FORMATS if not actions_for_format(fmt)]
    assert not missing, f"这些格式登记了却没有任何动作：{missing}"


def test_every_action_target_has_an_extension() -> None:
    """目标格式必须有扩展名映射，否则产物没有后缀。

    例外：解压类动作的 target_format 只是"这个动作属于哪种归档"的标记，
    产物是**目录**（rar 甚至不支持创建，所以 .rar 不在 TARGET_EXTENSION 里）。
    """
    allowed_without_extension = set(ARCHIVE_READONLY_FORMATS) | set(ARCHIVE_FORMATS)
    missing = sorted({
        action.target_format for action in ACTIONS
        if action.target_format not in TARGET_EXTENSION
        and action.target_format not in allowed_without_extension
    })
    assert not missing, f"这些目标格式缺少 TARGET_EXTENSION：{missing}"


def test_every_action_has_a_source_in_supported_formats() -> None:
    unknown = sorted({
        fmt for action in ACTIONS for fmt in action.source_formats
        if fmt not in SUPPORTED_FORMATS
    })
    assert not unknown, f"动作引用了未登记的源格式：{unknown}"


def test_all_supported_formats_are_recognised_from_extension() -> None:
    """反向检查：每个登记的格式都得能从自己的扩展名解析出来。"""
    broken = [fmt for fmt in SUPPORTED_FORMATS if format_from_extension(fmt) != fmt
              and fmt not in ("markdown",)]
    assert not broken, f"这些格式无法从扩展名解析回自身：{broken}"


@pytest.mark.parametrize("extension,expected", [
    ("jpeg", "jpg"), (".JPG", "jpg"), ("tif", "tiff"), ("tgz", "targz"), ("tbz2", "tarbz2"),
    ("txz", "tarxz"), ("yml", "yaml"), ("htm", "html"), ("md", "markdown"), ("sub", "srt"),
    ("pgm", "ppm"), ("jfif", "jpg"),
])
def test_extension_aliases(extension: str, expected: str) -> None:
    assert format_from_extension(extension) == expected
    assert format_label(expected)


def test_union_actions_keeps_each_archive_extract_action() -> None:
    """同时勾选 tar 与 tar.gz 时，两个解压动作都要在（否则其中一个文件被跳过）。"""
    actions = union_actions(["tar", "targz"])
    ids = {action.id for action in actions}
    assert "tar-extract" in ids
    assert "targz-extract" in ids
    assert {action.id for action in actions_for_format("targz")} >= {"targz-extract"}


def test_new_families_are_registered() -> None:
    """各族的关键动作确实存在，且 kind 指向正确的实现。"""
    expected = {
        "png-to-pdf": "image",
        "jpg-to-tiff": "image",
        "csv-to-xlsx": "sheet",
        "xlsx-to-markdown": "sheet",
        "json-to-yaml": "data",
        "yaml-to-xml": "data",
        "xml-to-csv": "data",
        "srt-to-vtt": "subtitle",
        "vtt-to-txt": "subtitle",
        "epub-to-txt": "ebook",
        "txt-to-epub": "ebook",
        "compress-to-targz": "archive",
        "gz-extract": "archive",
        "xz-extract": "archive",
        "mp4-to-mkv": "media",
        "mp4-to-aac": "media",
        "mp4-to-mp3": "media",
    }
    for action_id, kind in expected.items():
        action = get_action(action_id)
        assert action is not None, f"缺少动作 {action_id}"
        assert action.kind == kind, f"{action_id} 的 kind 应为 {kind}，实际 {action.kind}"


def test_common_actions_still_work_for_identical_formats() -> None:
    """同格式多选：共同动作不为空（面板不会一片空白）。"""
    assert common_actions(["png", "png"])
    assert common_actions(["mp4", "mp4", "mp4"])
