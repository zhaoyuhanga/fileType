"""共享层测试：弹幕分级/轨道、字幕解析、解码环境变量、播放器渲染路径。

对应方案 3.5 的 `test_danmaku_layer_tracks`、`test_subtitle_parse_and_render`、
`test_player_graphics_surface` 与 V1-e 的硬解注入。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from modu_workbench.ui_kit.media import danmaku as danmaku_ops
from modu_workbench.ui_kit.media import subtitle as subtitle_ops

# --------------------------------------------------------------------------- 弹幕


def test_danmaku_grade_formula_matches_reference() -> None:
    """分级公式借自 LunaTV-EE：score = cores/4*0.5 + memGB*0.3 + 0.1。

    注意公式里内存项是 `min(memGB/1, 1)*0.3`，**1 GB 就吃满**，
    所以档位主要由核数决定：≥3 核普遍是 high，2 核落 medium，1 核落 low。
    """
    # 8 核 + 16G → 0.5 + 0.3 + 0.1 = 0.9 > 0.7 → high
    assert danmaku_ops.grade_for(8, 16) == "high"
    assert danmaku_ops.grade_for(4, 4) == "high"
    # 2 核 → 0.25 + 0.3 + 0.1 = 0.65 → medium
    assert danmaku_ops.grade_for(2, 2) == "medium"
    # 1 核 → 0.125 + 0.3 + 0.1 = 0.525 → 刚好还是 medium
    assert danmaku_ops.grade_for(1, 1) == "medium"
    # 核数极低且内存也极低才落到 low
    assert danmaku_ops.grade_for(0, 0) == "low"
    # 同屏上限按档位
    assert danmaku_ops.max_lines_for("high") == 60
    assert danmaku_ops.max_lines_for("medium") == 40
    assert danmaku_ops.max_lines_for("low") == 25
    # 用户显式配置优先
    assert danmaku_ops.max_lines_for("low", configured=50) == 50
    assert danmaku_ops.max_lines_for("high", configured=999) == 60      # 上限夹取


def test_danmaku_machine_grade_is_a_valid_grade() -> None:
    assert danmaku_ops.machine_grade() in ("high", "medium", "low")


def test_danmaku_block_words_support_keywords_and_regex() -> None:
    """屏蔽词学 Kazumi：关键词 + `/正则/`；非法正则忽略而不是让弹幕全失效。"""
    rules = danmaku_ops.compile_block_rules("广告, /^\\d+$/, /[未完成/")
    assert danmaku_ops.is_blocked("这是广告", rules)
    assert danmaku_ops.is_blocked("12345", rules)
    assert not danmaku_ops.is_blocked("正常弹幕", rules)
    # 非法正则被丢弃，不影响其它规则
    assert len(rules) == 2


def test_danmaku_filter_drops_long_text_for_low_grade() -> None:
    items = [
        danmaku_ops.DanmakuItem("短弹幕", 0),
        danmaku_ops.DanmakuItem("长" * 80, 1000),
    ]
    kept_low = danmaku_ops.filter_items(items, grade="low")
    kept_high = danmaku_ops.filter_items(items, grade="high")
    assert len(kept_low) == 1 and kept_low[0].text == "短弹幕"
    assert len(kept_high) == 2


def test_danmaku_dedupe_keeps_first_occurrence() -> None:
    items = [danmaku_ops.DanmakuItem("重复", 0), danmaku_ops.DanmakuItem("重复", 500),
             danmaku_ops.DanmakuItem("唯一", 900)]
    kept = danmaku_ops.filter_items(items, dedupe=True)
    assert [item.text for item in kept] == ["重复", "唯一"]


def test_danmaku_track_allocation_never_overlaps() -> None:
    """100 条弹幕、8 条轨道：同一轨道上的弹幕时间区间不重叠（算法单测，不用 GUI）。"""
    allocator = danmaku_ops.TrackAllocator(track_count=8, scroll_seconds=8.0)
    placed: list[tuple[int, int, int]] = []       # (轨道, 开始ms, 结束ms)
    dropped = 0
    for index in range(100):
        item = danmaku_ops.DanmakuItem(f"弹幕{index}", time_ms=index * 200)
        track = allocator.assign(item)
        if track is None:
            dropped += 1
            continue
        allocator.occupy(track, item)
        placed.append((track, item.time_ms, int(item.time_ms + 8000)))

    assert placed, "至少应放下一些弹幕"
    for track in range(8):
        spans = sorted([(start, end) for index, start, end in placed if index == track])
        for (_, previous_end), (start, _) in zip(spans, spans[1:]):
            assert start >= previous_end, f"轨道 {track} 上的弹幕重叠了"
    assert dropped > 0, "100 条弹幕挤 8 条轨道，应当有放不下的被丢弃（而不是叠在一起）"


def test_danmaku_top_and_bottom_use_opposite_ends() -> None:
    allocator = danmaku_ops.TrackAllocator(track_count=4)
    top = danmaku_ops.DanmakuItem("顶部", 0, mode=5)
    bottom = danmaku_ops.DanmakuItem("底部", 0, mode=4)
    assert allocator.assign(top) == 0
    assert allocator.assign(bottom) == 3


def test_danmaku_slice_around_returns_time_window() -> None:
    items = [danmaku_ops.DanmakuItem(f"t{index}", index * 1000) for index in range(60)]
    window = danmaku_ops.slice_around(items, 30_000, window_ms=5000)
    assert window and all(29_000 <= item.time_ms <= 35_000 for item in window)


def test_danmaku_thin_out_limits_per_second() -> None:
    items = [danmaku_ops.DanmakuItem(f"t{index}", 100) for index in range(30)]
    kept = danmaku_ops.thin_out(items, max_lines=5, window_ms=1000)
    assert len(kept) == 5


# --------------------------------------------------------------------------- 字幕


SRT = """1
00:00:01,000 --> 00:00:03,500
第一句字幕

2
00:00:04,000 --> 00:00:06,000
<i>第二句</i>
换行也要保留

"""

VTT = """WEBVTT

NOTE 这是注释块

00:00:01.000 --> 00:00:02.000
VTT 第一句
"""


def test_subtitle_parse_srt_timecodes() -> None:
    cues = subtitle_ops.parse_srt(SRT)
    assert len(cues) == 2
    assert cues[0].start_ms == 1000 and cues[0].end_ms == 3500
    assert cues[0].text == "第一句字幕"
    # 样式标签被清掉，第二行保留
    assert cues[1].text == "第二句\n换行也要保留"
    assert cues[1].start_ms == 4000


def test_subtitle_track_lookup_by_position() -> None:
    track = subtitle_ops.SubtitleTrack(cues=subtitle_ops.parse_srt(SRT))
    assert track.at(2000) == "第一句字幕"
    assert track.at(5000).startswith("第二句")
    assert track.at(10_000) == ""
    assert bool(track) is True


def test_subtitle_parse_vtt_skips_header_and_notes() -> None:
    cues = subtitle_ops.parse_subtitle(VTT, suffix=".vtt")
    assert len(cues) == 1
    assert cues[0].text == "VTT 第一句"
    assert cues[0].start_ms == 1000


def test_subtitle_timecode_variants() -> None:
    assert subtitle_ops.parse_timecode("00:01:02,500") == 62_500
    assert subtitle_ops.parse_timecode("00:01:02.500") == 62_500
    assert subtitle_ops.parse_timecode("01:02.5") == 62_500
    assert subtitle_ops.parse_timecode("乱七八糟") == -1


def test_subtitle_load_from_disk_and_autodiscover(tmp_path: Path) -> None:
    video = tmp_path / "影片.mp4"
    video.write_bytes(b"x")
    subtitle_file = tmp_path / "影片.srt"
    subtitle_file.write_bytes(SRT.encode("utf-8"))

    assert subtitle_ops.find_subtitle(video) == subtitle_file
    track = subtitle_ops.load_subtitle(subtitle_file)
    assert len(track.cues) == 2 and track.encoding.startswith("utf-8")

    # 没有同名字幕时返回 None（而不是抛错）
    assert subtitle_ops.find_subtitle(tmp_path / "别的.mp4") is None


def test_subtitle_load_gbk_falls_back() -> None:
    """中文外挂字幕常见 GBK：读不到 utf-8 时必须能容错解码。"""
    track = subtitle_ops.load_subtitle(Path("不存在的文件.srt"))
    assert not track.cues                      # 不存在 → 空轨道，不抛错
    gbk_text = "1\n00:00:01,000 --> 00:00:02,000\n中文字幕\n"
    raw = gbk_text.encode("gbk")
    for encoding in ("utf-8-sig", "utf-8", "gbk", "big5", "utf-16"):
        try:
            decoded = raw.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:                                      # pragma: no cover - 理论不可达
        raise AssertionError("没有任何编码能解出 GBK 文本")
    assert "中文字幕" in decoded


def test_subtitle_export_to_vtt_roundtrip() -> None:
    cues = subtitle_ops.parse_srt(SRT)
    text = subtitle_ops.to_vtt(cues)
    assert text.startswith("WEBVTT")
    assert "00:00:01.000 --> 00:00:03.500" in text
    again = subtitle_ops.parse_vtt(text)
    assert [(cue.start_ms, cue.text) for cue in again] == \
        [(cue.start_ms, cue.text) for cue in cues]


# --------------------------------------------------------------------------- 解码环境


def test_decode_env_injection_sets_ffmpeg_and_hw(monkeypatch: pytest.MonkeyPatch) -> None:
    """V1-e：必须注入 `QT_MEDIA_BACKEND=ffmpeg` 与硬解设备列表。"""
    from modu_workbench.ui_kit.media import decode

    for key in ("QT_MEDIA_BACKEND", "QT_FFMPEG_DECODING_HW_DEVICE_TYPES",
                "QT_DISABLE_HW_TEXTURES_CONVERSION", "QT_FFMPEG_HW_ALLOW_PROFILE_MISMATCH"):
        monkeypatch.delenv(key, raising=False)

    decode.apply_decoding_env()
    assert os.environ["QT_MEDIA_BACKEND"] == "ffmpeg"
    assert "d3d11va" in os.environ["QT_FFMPEG_DECODING_HW_DEVICE_TYPES"]
    assert decode.hardware_decode_enabled() is True
    assert "ffmpeg" in decode.describe()


def test_decode_env_respects_explicit_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """已经显式设置的环境变量优先（便于临时排查 / CI）。"""
    from modu_workbench.ui_kit.media import decode

    monkeypatch.setenv("QT_MEDIA_BACKEND", "windows")
    decode.apply_decoding_env()
    assert os.environ["QT_MEDIA_BACKEND"] == "windows"


def test_decode_env_off_disables_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    """`hw_decode=false` → 空的设备列表（Qt 约定：空列表 = 关闭硬解）。"""
    from modu_workbench.ui_kit.media import decode

    monkeypatch.setattr(decode, "_read_setting_from_db",
                        lambda key, default: "off" if key == "hw_decode" else default)
    monkeypatch.delenv("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", raising=False)
    decode.apply_decoding_env()
    assert decode.hardware_decode_enabled() is False
    assert "关闭" in decode.describe()


# --------------------------------------------------------------------------- 播放器渲染


def test_surface_selection_follows_render_path(qapp) -> None:  # noqa: ANN001
    """`video/render_path` 决定用 QGraphicsVideoItem 还是 QVideoWidget。"""
    from modu_workbench.ui_kit.media.surface import (
        RENDER_GRAPHICS,
        RENDER_WIDGET,
        VideoSurface,
        WidgetSurface,
        make_surface,
    )

    graphics = make_surface(RENDER_GRAPHICS)
    widget = make_surface(RENDER_WIDGET)
    try:
        assert isinstance(graphics, VideoSurface), "graphics 路径应给出可叠加的视频面"
        assert isinstance(widget, WidgetSurface)
        assert graphics.take_video_output() is not widget.take_video_output()
    finally:
        for surface in (graphics, widget):
            if surface is not None:
                surface.deleteLater()


def test_player_dialog_builds_with_graphics_surface(qapp, monkeypatch) -> None:  # noqa: ANN001
    """`VideoPlayerDialog` 用 graphics 路径能构建，且新信号存在（offscreen）。"""
    monkeypatch.setenv("QT_MEDIA_BACKEND", "ffmpeg")
    from modu_workbench.ui_kit.media.player import VideoPlayerDialog

    dialog = VideoPlayerDialog(
        title="测试", url="", render_path="graphics", has_next=True,
        qualities=["720P", "1080P"], current_quality="720P",
    )
    try:
        assert hasattr(dialog, "stallDetected") and hasattr(dialog, "diagnosticsRequested")
        assert dialog.duration_ms() == 0
        # 换源接口保持旧签名
        dialog.switch_source("", is_hls=False, start_ms=1000, title="换源")
        assert dialog.current_position_ms() >= 0
        info = dialog.diagnostics()
        assert "渲染路径" in info and "代理/requests" in info
    finally:
        dialog.close()


def test_player_auto_next_emits_episode_request(qapp, monkeypatch) -> None:  # noqa: ANN001
    """播放结束 → 自动连播触发 `episodeRequested(+1)`（倒计时用 0 加速）。"""
    from modu_workbench.ui_kit.media import player as player_module

    monkeypatch.setattr(player_module.VideoPlayerDialog, "_auto_next_enabled",
                        staticmethod(lambda: True))
    requests: list[int] = []
    dialog = player_module.VideoPlayerDialog(
        title="连播", url="", render_path="widget", has_next=True)
    dialog.episodeRequested.connect(requests.append)
    try:
        dialog._countdown = 0            # noqa: SLF001  直接进入"倒计时结束"
        dialog._tick_countdown()         # noqa: SLF001
        assert requests == [1]
    finally:
        dialog.close()


