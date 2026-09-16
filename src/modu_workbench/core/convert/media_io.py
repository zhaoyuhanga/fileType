"""音视频转换（ffmpeg 子进程）。

ffmpeg 解析顺序：环境变量 MODU_FFMPEG → PATH。未找到时抛出中文提示；
打包时（M4）随应用携带 ffmpeg 静态二进制并在启动时注入 MODU_FFMPEG。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

AUDIO_TARGETS = {"m4a", "mp3", "wav", "flac", "aac", "ogg", "opus", "wma"}

# 视频转换目标（墨软影视「视频格式转换」使用）
VIDEO_TARGETS = {"mp4", "mkv", "mov", "avi", "webm", "flv", "ts", "gif"}

# 目标音频格式 → ffmpeg 编码器（未列出者使用 ffmpeg 默认编码器）
AUDIO_CODECS = {
    "mp3": "libmp3lame",
    "m4a": "aac",
    "aac": "aac",
    "flac": "flac",
    "ogg": "libvorbis",
    "opus": "libopus",
    "wma": "wmav2",
}
# 目标视频格式 → ffmpeg 编码器（None 表示交给 ffmpeg 默认，通常更兼容）
VIDEO_CODECS: dict[str, str | None] = {
    "mp4": "copy",      # 优先不重编码（快且无损），失败由调用方重试
    "mkv": "copy",
    "mov": "copy",
    "avi": "mpeg4",
    "webm": "libvpx-vp9",
    "flv": "flv",
    "ts": "copy",
    "gif": None,
}
# 需要显式指定容器（避免 ffmpeg 猜错）
CONTAINER_FORMATS = {"aac": "adts", "webm": "webm", "flv": "flv", "ts": "mpegts", "m4a": "ipod"}

# 目标为纯音频时，从视频里提取音轨
AUDIO_ONLY_TARGETS = AUDIO_TARGETS | {"mp3"}


def bundled_dir() -> Path | None:
    """随包携带的 ffmpeg 目录（打包时放到 _MEIPASS/tools/ffmpeg）。

    这样用户无需自行安装 ffmpeg，HLS 下载开箱即可输出 MP4。
    """
    import sys

    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "tools" / "ffmpeg")
    # 源码运行：仓库根的 tools/ffmpeg
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "tools" / "ffmpeg"
        if candidate.is_dir():
            candidates.append(candidate)
            break
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _tool_name(stem: str) -> str:
    return f"{stem}.exe" if os.name == "nt" else stem


def find_ffmpeg() -> str | None:
    """定位 ffmpeg 可执行文件。

    优先级：`MODU_FFMPEG` 环境变量 → 随包 tools/ffmpeg → PATH。
    """
    override = os.environ.get("MODU_FFMPEG")
    if override and Path(override).is_file():
        return override
    bundled = bundled_dir()
    if bundled is not None:
        candidate = bundled / _tool_name("ffmpeg")
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg") or None


def find_ffprobe() -> str | None:
    """定位 ffprobe（与 ffmpeg 同一套优先级）。"""
    override = os.environ.get("MODU_FFPROBE")
    if override and Path(override).is_file():
        return override
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name(_tool_name("ffprobe"))
        if sibling.is_file():
            return str(sibling)
    bundled = bundled_dir()
    if bundled is not None:
        candidate = bundled / _tool_name("ffprobe")
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffprobe") or None


def convert_media(source_path: str | Path, target_format: str, output_path: str | Path,
                  cancel: threading.Event | None = None) -> None:
    """音视频转换入口。

    音频目标：丢弃视频轨（`-vn`）；
    视频目标：优先流复制（`-c copy`，快且无损），目标容器不兼容时自动回退重编码。
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise ValueError("未找到 ffmpeg：请安装 ffmpeg 或设置 MODU_FFMPEG 指向二进制")

    target_format = (target_format or "").lower()
    if target_format in AUDIO_TARGETS:
        _run_ffmpeg(ffmpeg, _audio_args(source_path, target_format, output_path), cancel)
        return

    if target_format in VIDEO_TARGETS:
        codec = VIDEO_CODECS.get(target_format)
        # 先试流复制；失败（容器/编码不兼容）再回退重编码，避免用户看到晦涩报错
        if codec == "copy":
            try:
                _run_ffmpeg(ffmpeg, _video_args(source_path, target_format, output_path, codec="copy"), cancel)
                return
            except ValueError:
                if cancel is not None and cancel.is_set():
                    raise
        _run_ffmpeg(ffmpeg, _video_args(source_path, target_format, output_path, codec=None), cancel)
        return

    raise ValueError(f"不支持的媒体转换目标：{target_format}")


def _audio_args(source_path: str | Path, target_format: str, output_path: str | Path) -> list[str]:
    args = ["-i", str(source_path), "-vn"]
    codec = AUDIO_CODECS.get(target_format)
    if codec:
        args += ["-c:a", codec]
    if target_format in CONTAINER_FORMATS:
        args += ["-f", CONTAINER_FORMATS[target_format]]
    args.append(str(output_path))
    return args


def _video_args(source_path: str | Path, target_format: str, output_path: str | Path,
                *, codec: str | None) -> list[str]:
    args = ["-i", str(source_path)]
    if codec:
        args += ["-c", codec]
    if target_format == "gif":
        args += ["-vf", "fps=12,scale=640:-1:flags=lanczos", "-loop", "0"]
    if target_format == "mp4":
        args += ["-movflags", "+faststart"]
    if target_format in CONTAINER_FORMATS:
        args += ["-f", CONTAINER_FORMATS[target_format]]
    args.append(str(output_path))
    return args


def _run_ffmpeg(ffmpeg: str, args: list[str], cancel: threading.Event | None) -> None:
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args]
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    while True:
        if cancel is not None and cancel.is_set():
            process.kill()
            process.wait()
            raise RuntimeError("转换已取消")
        if process.poll() is not None:
            break
        time.sleep(0.2)

    if process.returncode != 0:
        stderr = ""
        if process.stderr is not None:
            try:
                stderr = process.stderr.read().decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                stderr = ""
        raise ValueError(f"ffmpeg 转换失败（退出码 {process.returncode}）：{stderr[-300:]}".strip())

