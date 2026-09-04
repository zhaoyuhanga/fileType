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

AUDIO_TARGETS = {"m4a", "mp3", "wav"}


def find_ffmpeg() -> str | None:
    override = os.environ.get("MODU_FFMPEG")
    if override and Path(override).is_file():
        return override
    on_path = shutil.which("ffmpeg")
    return on_path or None


def convert_media(source_path: str | Path, target_format: str, output_path: str | Path,
                  cancel: threading.Event | None = None) -> None:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise ValueError("未找到 ffmpeg：请安装 ffmpeg 或设置 MODU_FFMPEG 指向二进制")

    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_path)]
    if target_format in AUDIO_TARGETS:
        args.append("-vn")
    if target_format == "m4a":
        args += ["-c:a", "aac"]
    args.append(str(output_path))

    process = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
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
            stderr = process.stderr.read().decode("utf-8", "ignore")
        raise ValueError(f"ffmpeg 转换失败（退出码 {process.returncode}）：{stderr[-300:]}".strip())
