"""离线画质增强测试（D-b）。

分两层：
1. **纯逻辑**（不依赖 ffmpeg）：档位校验、滤镜串、命令行拼装、进度解析；
2. **真 ffmpeg**（可用时）：把 320x180 升到 1920x1080 并验证产物可解析 ——
   这是"离线增强真的能用"的唯一硬证据，跳过了就只剩"命令拼对了"。
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from modu_workbench.core.video.enhance import (
    ENHANCE_MODES,
    EnhanceResult,
    build_args,
    build_filter,
    enhance_video,
    parse_progress,
    probe_duration_ms,
)


def test_modes_are_documented_and_distinct() -> None:
    assert set(ENHANCE_MODES) == {"upscale", "reencode"}
    assert all(label for label in ENHANCE_MODES.values())


def test_upscale_filter_uses_lanczos_and_light_sharpen() -> None:
    filters = build_filter("upscale")
    assert "scale=1920:1080" in filters and "lanczos" in filters
    # 轻微锐化：参数太大反而出白边
    assert "unsharp=5:5:0.8" in filters
    assert filters.endswith("format=yuv420p")


def test_reencode_has_no_filter() -> None:
    assert build_filter("reencode") == ""


def test_build_args_puts_progress_before_input(tmp_path: Path) -> None:
    args = build_args("ffmpeg", tmp_path / "a.mp4", tmp_path / "b.mp4", mode="upscale")
    assert args[0] == "ffmpeg"
    assert args.count("ffmpeg") == 1
    assert args.index("-progress") < args.index("-i")
    assert args[-1].endswith("b.mp4")
    assert "-crf" in args and "-preset" in args
    # 音频固定 AAC 192k：重编码后源音频可能被丢弃或不可播
    assert args[args.index("-c:a") + 1] == "aac"


def test_parse_progress_reads_key_value() -> None:
    assert parse_progress("out_time_us=1500000") == ("out_time_us", "1500000")
    assert parse_progress("  speed=2.5x  ") == ("speed", "2.5x")
    assert parse_progress("") == ("", "")


@pytest.mark.parametrize("mode", ["nope", "", "UPSCALE2"])
def test_invalid_mode_is_rejected(tmp_path: Path, mode: str) -> None:
    result = enhance_video(tmp_path / "a.mp4", tmp_path / "b.mp4", mode=mode)
    assert not result.ok and "不支持的增强档位" in result.message


def test_missing_source_reports_clearly(tmp_path: Path) -> None:
    result = enhance_video(tmp_path / "none.mp4", tmp_path / "out.mp4")
    assert not result.ok and "源文件不存在" in result.message


def test_missing_ffmpeg_reports_clearly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 ffmpeg 时要说清"装 ffmpeg"，而不是抛异常。"""
    import modu_workbench.core.video.downloader as downloader_module

    source = tmp_path / "a.mp4"
    source.write_bytes(b"x" * 32)
    monkeypatch.setattr(downloader_module, "find_ffmpeg", lambda: None)
    result = enhance_video(source, tmp_path / "out.mp4")
    assert not result.ok and "找不ffmpeg" in result.message or "ffmpeg" in result.message


def test_cancel_removes_partial_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """取消必须清理半成品，而不是留一个坏文件骗用户。"""
    import modu_workbench.core.video.enhance as enhance_module
    import modu_workbench.core.video.downloader as downloader_module

    source = tmp_path / "a.mp4"
    source.write_bytes(b"x" * 32)
    monkeypatch.setattr(downloader_module, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(enhance_module, "probe_duration_ms", lambda binary, path: 1000)

    class _SlowProcess:
        def __init__(self, *_args, **_kwargs) -> None:
            self.returncode = None
            self.stdout = None
            self.stderr = None
            self.killed = False

        def poll(self):  # noqa: ANN201
            return None

        def kill(self) -> None:
            self.killed = True

        def wait(self) -> int:
            self.returncode = -9
            return -9

    monkeypatch.setattr(enhance_module.subprocess, "Popen", _SlowProcess)
    cancel = threading.Event()
    cancel.set()
    target = tmp_path / "out.mp4"
    result = enhance_video(source, target, cancel=cancel)
    assert not result.ok and "取消" in result.message
    assert not target.exists()


def test_probe_duration_returns_zero_without_ffprobe(tmp_path: Path) -> None:
    assert probe_duration_ms("/nonexistent/ffmpeg", tmp_path / "a.mp4") == 0


def test_enhance_result_size_handles_missing_file(tmp_path: Path) -> None:
    assert EnhanceResult().size_bytes == 0
    assert EnhanceResult(path=tmp_path / "none.mp4").size_bytes == 0


# --------------------------------------------------------------------------- 真 ffmpeg


def _make_clip(ffmpeg: str, work: Path) -> Path | None:
    source = work / "src.mp4"
    result = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    return source if result.returncode == 0 and source.is_file() else None


def test_upscale_really_produces_1080p(tmp_path: Path) -> None:
    """真 ffmpeg：320x180 → 1920x1080，产物可被 ffmpeg 解析。"""
    from modu_workbench.core.platform.media import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        pytest.skip("本机没有 ffmpeg")
    source = _make_clip(ffmpeg, tmp_path)
    if source is None:
        pytest.skip("当前 ffmpeg 构建缺少 libx264")

    target = tmp_path / "enhanced.mp4"
    messages: list[str] = []
    result = enhance_video(source, target, mode="upscale", preset="ultrafast",
                           on_progress=lambda done, total, message: messages.append(message))
    assert result.ok, result.message
    assert target.is_file() and target.stat().st_size > 0

    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(target)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
    assert "1920x1080" in probe.stderr, "放大档必须真的产出 1080p"
    assert "Video:" in probe.stderr
    assert messages, "增强应当回报进度"


def test_reencode_keeps_resolution(tmp_path: Path) -> None:
    """重编码档不动分辨率（只改码率/封装）。"""
    from modu_workbench.core.platform.media import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        pytest.skip("本机没有 ffmpeg")
    source = _make_clip(ffmpeg, tmp_path)
    if source is None:
        pytest.skip("当前 ffmpeg 构建缺少 libx264")

    target = tmp_path / "reencoded.mp4"
    result = enhance_video(source, target, mode="reencode", preset="ultrafast")
    assert result.ok, result.message
    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(target)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
    assert "320x180" in probe.stderr
