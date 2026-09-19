"""转换能力探测测试：缺依赖的动作必须**置灰并说明原因**，而不是点了才报错。

覆盖原因：ODS / 旧版 DOC 需要 LibreOffice、音视频需要 ffmpeg、AMR 需要 ffmpeg 带
amrnb 编码器、RAR 需要 unrar/7z、YAML 需要 PyYAML。这些依赖不是每台机器都有，
所以探测逻辑本身要有测试（用替身，不依赖本机实际装了什么）。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.convert import capabilities
from modu_workbench.core.convert.registry import get_action


@pytest.fixture()
def no_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    """把外部能力全部设为「不存在」，便于断言置灰原因。"""
    monkeypatch.setattr(capabilities, "has_ffmpeg", lambda: False)
    monkeypatch.setattr(capabilities, "has_soffice", lambda: False)
    monkeypatch.setattr(capabilities, "has_unrar", lambda: False)
    monkeypatch.setattr(capabilities, "has_pyyaml", lambda: False)
    monkeypatch.setattr(capabilities, "ffmpeg_encoders", lambda: frozenset())


@pytest.fixture()
def all_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capabilities, "has_ffmpeg", lambda: True)
    monkeypatch.setattr(capabilities, "has_soffice", lambda: True)
    monkeypatch.setattr(capabilities, "has_unrar", lambda: True)
    monkeypatch.setattr(capabilities, "has_pyyaml", lambda: True)
    monkeypatch.setattr(
        capabilities, "ffmpeg_encoders",
        lambda: frozenset({"libmp3lame", "aac", "libopencore_amrnb", "libvpx-vp9"}),
    )


def test_text_conversion_never_needs_extras(no_extras: None) -> None:
    """纯内置的转换（txt→md 之类）在没有任何外部能力时也必须可用。"""
    for action_id in ("txt-to-markdown", "markdown-to-html", "png-to-jpg", "json-to-csv"):
        action = get_action(action_id)
        assert action is not None
        assert capabilities.blocked_reason(action) is None, action_id


def test_media_actions_blocked_without_ffmpeg(no_extras: None) -> None:
    reason = capabilities.blocked_reason(get_action("mp4-to-mkv"))
    assert reason and "ffmpeg" in reason
    reason = capabilities.blocked_reason(get_action("mp3-to-wav"))
    assert reason and "ffmpeg" in reason


def test_ods_and_doc_need_libreoffice(no_extras: None) -> None:
    reason = capabilities.blocked_reason(get_action("ods-to-csv"))
    assert reason and "LibreOffice" in reason
    reason = capabilities.blocked_reason(get_action("doc-to-txt"))
    assert reason and "LibreOffice" in reason
    # docx / xlsx 走内置解析，不依赖 LibreOffice
    assert capabilities.blocked_reason(get_action("docx-to-txt")) is None
    assert capabilities.blocked_reason(get_action("xlsx-to-csv")) is None


def test_rar_extract_needs_unrar(no_extras: None) -> None:
    reason = capabilities.blocked_reason(get_action("rar-extract"))
    assert reason and ("unrar" in reason or "7z" in reason)
    # 其它归档格式是标准库，任何时候都能用
    assert capabilities.blocked_reason(get_action("zip-extract")) is None
    assert capabilities.blocked_reason(get_action("gz-extract")) is None


def test_yaml_needs_pyyaml(no_extras: None) -> None:
    reason = capabilities.blocked_reason(get_action("json-to-yaml"))
    assert reason and "PyYAML" in reason


def test_amr_needs_the_encoder_not_just_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """ffmpeg 装了但没有 amrnb 编码器时，只有 amr 目标该被拦住。"""
    monkeypatch.setattr(capabilities, "has_ffmpeg", lambda: True)
    monkeypatch.setattr(capabilities, "has_soffice", lambda: True)
    monkeypatch.setattr(capabilities, "ffmpeg_encoders", lambda: frozenset({"libmp3lame", "aac"}))

    reason = capabilities.blocked_reason(get_action("mp3-to-amr"))
    assert reason and "AMR" in reason
    assert capabilities.blocked_reason(get_action("mp3-to-aac")) is None


def test_everything_available_when_capabilities_present(all_extras: None) -> None:
    for action_id in ("mp4-to-mkv", "mp3-to-amr", "ods-to-csv", "rar-extract",
                      "json-to-yaml", "doc-to-txt"):
        action = get_action(action_id)
        assert capabilities.blocked_reason(action) is None, action_id


def test_capability_summary_lists_all_four() -> None:
    keys = {item.key for item in capabilities.capability_summary()}
    assert keys == {"soffice", "ffmpeg", "unrar", "yaml"}
    for item in capabilities.capability_summary():
        assert item.label and item.hint


def test_category_chip_filters_the_action_panel(qapp, tmp_path) -> None:
    """分类芯片把面板收窄到某一族，点「全部」恢复（动作多了以后的主要找法）。"""
    from modu_workbench.boards.convert.board import ConvertBoardPage

    video = tmp_path / "演示.mp4"
    video.write_bytes(b"\x00" * 64)

    page = ConvertBoardPage(output_dir=str(tmp_path / "out"))
    try:
        page._append_paths([str(video)])
        total = len(page._action_buttons)
        assert total >= 10, f"单个 mp4 的动作应该很多，实际 {total}"

        page._set_category_filter("archive")
        assert page._action_buttons, "归档族应当有动作"
        assert {action.category for action in page._current_actions} == {"archive"}
        assert all(("压缩" in button.text() or "解压" in button.text())
                   for button in page._action_buttons)

        # 芯片上的计数与实际动作数一致
        assert "归档" in page._chip_buttons["archive"].text()

        page._set_category_filter("")
        assert len(page._action_buttons) == total
        assert page._chip_buttons[""].isChecked()
    finally:
        page.close()


def test_ui_greys_out_blocked_actions_and_picks_an_available_one(
    qapp, no_extras: None, tmp_path
) -> None:
    """界面契约：缺依赖的动作按钮被禁用且带原因，默认选中「可用」的动作。"""
    from modu_workbench.boards.convert.board import ConvertBoardPage

    video = tmp_path / "演示.mp4"
    video.write_bytes(b"\x00" * 64)
    text = tmp_path / "笔记.txt"
    text.write_text("内容", encoding="utf-8")

    page = ConvertBoardPage(output_dir=str(tmp_path / "out"))
    try:
        page._append_paths([str(text)])
        assert page._selected_action is not None, "至少要有可用的文本文档动作"
        assert capabilities.blocked_reason(page._selected_action) is None

        page._append_paths([str(video)])
        blocked_buttons = [b for b in page._action_buttons if not b.isEnabled()]
        assert blocked_buttons, "没有 ffmpeg 时音视频动作必须置灰"
        assert all("⚠" in button.toolTip() for button in blocked_buttons)
        assert "缺少依赖" in page._action_hint.text() or "置灰" in page._action_hint.text()
    finally:
        page.close()
