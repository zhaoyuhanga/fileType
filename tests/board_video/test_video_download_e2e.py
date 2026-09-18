"""ffmpeg 下载链路的真实端到端验证（离线）。

用 ffmpeg 造一段真 HLS（主清单 + 分片）→ 本地 HTTP 提供 →
走 `downloader._ffmpeg_download`（与线上完全同一条代码路径）→ 断言产出可播放的 MP4。

为什么值得单开一个文件：v1.0.2 里这条路径**必然失败**（命令行里 ffmpeg 路径重复，
被 ffmpeg 当成输出文件），而单元测试只替身了 Popen、看不出真 ffmpeg 的反应。
本文件不联网、不依赖外部服务；`ffmpeg` 缺失时自动跳过。
"""
from __future__ import annotations

import functools
import http.server
import socketserver
import subprocess
import threading
from pathlib import Path

import pytest

from modu_workbench.core.platform.media import find_ffmpeg


def _make_hls(ffmpeg: str, work: Path) -> Path | None:
    """用 ffmpeg 生成 3 秒测试流（视频+音频），返回清单路径；编解码器不可用时返回 None。"""
    source = work / "hls"
    source.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod",
         "-hls_segment_filename", str(source / "seg%d.ts"), str(source / "index.m3u8")],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not (source / "index.m3u8").is_file():
        return None
    return source


def test_ffmpeg_download_produces_a_playable_file(tmp_path: Path) -> None:
    """真实 ffmpeg + 本地 HLS：下载必须产出非空、可识别的 MP4。"""
    from modu_workbench.core.video import downloader as downloader_module

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        pytest.skip("本机没有 ffmpeg")

    source = _make_hls(ffmpeg, tmp_path)
    if source is None:
        pytest.skip("当前 ffmpeg 构建缺少 libx264/aac，无法生成测试流")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(source))
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler) as server:
        server.daemon_threads = True
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        target = tmp_path / "下载结果.mp4"
        messages: list[str] = []
        try:
            downloader_module._ffmpeg_download(  # noqa: SLF001
                ffmpeg, f"http://127.0.0.1:{port}/index.m3u8", target, headers={},
                on_progress=lambda done, total, message: messages.append(message),
                cancel=None,
            )
        finally:
            server.shutdown()

    assert target.is_file(), "ffmpeg 下载没有产出文件"
    assert target.stat().st_size > 10_000, "产物太小，多半是失败的空壳"
    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(target)],
                           capture_output=True, text=True)
    assert "Video:" in probe.stderr, "产物不是可识别的视频"
    assert messages, "应当回报过下载进度"
