"""ffmpeg / ffprobe 定位与媒体时长探测（板块无关）。

定位优先级：`MODU_FFMPEG` / `MODU_FFPROBE` 环境变量 → 随包 `tools/ffmpeg` → PATH。
随包目录由 PyInstaller 放在 `_MEIPASS/tools/ffmpeg`（见 workbench.spec 的 datas）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def bundled_dir() -> Path | None:
    """随包携带的 ffmpeg 目录（打包时放到 _MEIPASS/tools/ffmpeg）。"""
    candidates: list[Path] = []
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
    """定位 ffmpeg 可执行文件（环境变量 → 随包 → PATH）。"""
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


def probe_duration_ms(path: str | Path) -> int:
    """用 ffprobe 读取时长（毫秒）；不可用时返回 0（不做硬依赖）。"""
    ffprobe = find_ffprobe()
    if not ffprobe:
        return 0
    try:
        output = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        value = (output.stdout or "").strip().splitlines()
        seconds = float(value[0]) if value and value[0] else 0.0
        return int(seconds * 1000)
    except Exception:  # noqa: BLE001  探测失败不影响主流程
        return 0
