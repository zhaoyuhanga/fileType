"""离线画质增强：**放大**与**重编码**（第七大板块 D-b / 影视板块 P2）。

先说清边界（这一点必须诚实）：**这不是 Anime4K 实时超分**。
实时超分需要 libmpv 播放内核 + 着色器管线，与"Qt 单栈、不引入新播放内核"的决策冲突，
而且低端机会直接卡死（Kazumi 自己的 Q&A 与 issue #739/#591 都印证了这点）。
我们做的是**下载完成后的一次性 ffmpeg 处理**：

| 档位 | 滤镜与编码 | 适用 |
|---|---|---|
| `upscale`（放大） | `scale=1920:1080:flags=lanczos`（可选 `unsharp` 轻锐化）+ `libx264 -crf 20 -preset medium` | 480P/720P 想在大屏上看 |
| `reencode`（重编码） | 保持分辨率，`libx264 -crf 20 -preset medium` | 码率过高想压小、或想统一封装 |

`-c copy` 的 remux 走 `core/video/segment_downloader.remux_to_mp4()`（不重编码，秒级完成），
本模块只负责"真的要动像素"的那两档 —— 因此耗时以分钟计，必须给进度与取消。
"""
from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

#: 档位 → 展示文案
ENHANCE_MODES: dict[str, str] = {
    "upscale": "放大到 1080P（lanczos + 轻锐化）",
    "reencode": "重编码（保持分辨率，CRF 20）",
}

ProgressFn = Callable[[int, int, str], None]


@dataclass
class EnhanceResult:
    """一次增强的结果。"""

    path: Path | None = None
    ok: bool = False
    message: str = ""
    elapsed: float = 0.0

    @property
    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size if self.path and self.path.is_file() else 0
        except OSError:
            return 0


def build_filter(mode: str) -> str:
    """档位 → ffmpeg 滤镜串（`reencode` 不加滤镜）。"""
    if mode == "upscale":
        # lanczos 是"放大不糊"的常规选择；unsharp 轻微锐化（0.8 是保守值，
        # 太大反而出现白边），最后 `format=yuv420p` 保证浏览器/播放器都能解
        return "scale=1920:1080:flags=lanczos,unsharp=5:5:0.8:3:3:0.4,format=yuv420p"
    return ""


def build_args(ffmpeg: str, source: Path, target: Path, *, mode: str,
               crf: int = 20, preset: str = "medium") -> list[str]:
    """构造 ffmpeg 命令行（`-progress pipe:1` 供进度解析）。"""
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-progress", "pipe:1", "-nostats", "-i", str(source)]
    filters = build_filter(mode)
    if filters:
        args += ["-vf", filters]
    args += ["-c:v", "libx264", "-crf", str(int(crf)), "-preset", preset,
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target)]
    return args


def parse_progress(line: str) -> tuple[str, str]:
    """解析 `-progress` 的一行 `key=value`（只关心时长与速度）。"""
    key, _, value = (line or "").strip().partition("=")
    return key.strip(), value.strip()


def probe_duration_ms(ffmpeg: str, source: Path) -> int:
    """用 ffprobe（同目录）/ ffmpeg 读时长，用于百分比进度；拿不到返回 0。"""
    probe = str(Path(ffmpeg).with_name("ffprobe.exe" if ffmpeg.lower().endswith(".exe")
                                       else "ffprobe"))
    if not Path(probe).is_file():
        probe = str(Path(ffmpeg).with_name("ffprobe"))
    if not Path(probe).is_file():
        return 0
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=creationflags, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 0
    try:
        return int(float((result.stdout or "0").strip()) * 1000)
    except (TypeError, ValueError):
        return 0


def enhance_video(
    source_path: str | Path,
    output_path: str | Path,
    *,
    mode: str = "upscale",
    ffmpeg: str | None = None,
    crf: int = 20,
    preset: str = "medium",
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> EnhanceResult:
    """离线增强一个视频文件；失败时清理半成品并返回可读原因。

    注意：目标文件**已存在时会被覆盖**（调用方负责用 `unique_path` 挑一个不冲突的名字）。
    """
    import time

    from modu_workbench.core.video.downloader import find_ffmpeg

    if mode not in ENHANCE_MODES:
        return EnhanceResult(message=f"不支持的增强档位：{mode}")
    source = Path(source_path)
    if not source.is_file():
        return EnhanceResult(message=f"源文件不存在：{source}")
    binary = ffmpeg or find_ffmpeg()
    if not binary:
        return EnhanceResult(
            message="找不到 ffmpeg（随包 tools/ffmpeg 或系统 PATH），无法做离线增强")

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    total_ms = probe_duration_ms(binary, source)
    args = build_args(binary, source, target, mode=mode, crf=crf, preset=preset)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", bufsize=1, creationflags=creationflags)
    except OSError as error:
        return EnhanceResult(message=f"无法启动 ffmpeg：{error}")

    state = {"out_ms": 0, "speed": ""}
    stop = threading.Event()

    def pump() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            if stop.is_set():
                break
            key, value = parse_progress(line)
            if key == "out_time_us":
                try:
                    state["out_ms"] = int(value) // 1000
                except ValueError:
                    pass
            elif key == "out_time_ms":
                try:
                    state["out_ms"] = int(value)
                except ValueError:
                    pass
            elif key == "speed":
                state["speed"] = value

    pump_thread = threading.Thread(target=pump, daemon=True)
    pump_thread.start()

    last_report = 0.0
    while True:
        if cancel is not None and cancel.is_set():
            process.kill()
            process.wait()
            stop.set()
            target.unlink(missing_ok=True)
            return EnhanceResult(message="已取消增强")
        if process.poll() is not None:
            break
        now = time.monotonic()
        if on_progress and now - last_report > 1.0:
            out_ms = state["out_ms"]
            if total_ms > 0 and out_ms > 0:
                percent = min(100, int(out_ms * 100 / total_ms))
                on_progress(percent, 100,
                            f"增强中 {percent}%" + (f"（{state['speed']}）"
                                                   if state["speed"] else ""))
            else:
                on_progress(0, 0, "增强中…（正在重编码，耗时以分钟计）")
            last_report = now
        time.sleep(0.3)
    stop.set()
    elapsed = time.monotonic() - started

    if process.returncode != 0:
        stderr = ""
        if process.stderr is not None:
            try:
                stderr = process.stderr.read()
            except Exception:  # noqa: BLE001
                stderr = ""
        target.unlink(missing_ok=True)
        reason = re.sub(r"\s+", " ", (stderr or "")).strip()[-300:]
        return EnhanceResult(message=f"ffmpeg 增强失败（退出码 {process.returncode}）：{reason}",
                             elapsed=elapsed)
    if not target.is_file() or target.stat().st_size <= 0:
        target.unlink(missing_ok=True)
        return EnhanceResult(message="ffmpeg 没有产出文件（源可能已损坏）", elapsed=elapsed)
    if on_progress:
        on_progress(100, 100, f"完成：{target.name}")
    return EnhanceResult(path=target, ok=True, elapsed=elapsed,
                         message=f"完成：{target.name}（{_size_text(target)}）")


def _size_text(path: Path) -> str:
    try:
        return f"{path.stat().st_size / 1048576:.1f} MB"
    except OSError:
        return "大小未知"


__all__ = [
    "ENHANCE_MODES", "EnhanceResult", "build_args", "build_filter", "enhance_video",
    "parse_progress", "probe_duration_ms",
]
