"""字幕：SRT/VTT 解析 + 渲染用的文本切片（对应方案 3.3.2）。

范围（诚实边界）：
- **外挂字幕做**：`.srt` / `.vtt` 解析 + 按时间轴取值 + 与视频同目录的自动匹配；
- **内嵌字幕轨不做**：`QMediaPlayer` 没有字幕轨 API。需要内嵌字幕请下载后用
  《墨软转换》抽出来（ffmpeg `-map 0:s:0` 已具备）。

纯解析部分不依赖 Qt，可单测；渲染由 `player.py` 的叠加层完成。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TAG = re.compile(r"<[^>]+>")
#: 兜底用：从任意文本里嗅出时间码（部分字幕的时间行前后带杂字符）
_TIMECODE = re.compile(r"\d{1,2}:\d{1,2}(?::\d{1,2})?(?:[.,]\d{1,3})?")
#: 常见字幕扩展名（按优先级）
SUBTITLE_SUFFIXES = (".srt", ".vtt", ".ass", ".ssa", ".sub")


@dataclass
class SubtitleCue:
    """一条字幕。"""

    start_ms: int
    end_ms: int
    text: str

    def contains(self, position_ms: int) -> bool:
        return self.start_ms <= position_ms < self.end_ms


@dataclass
class SubtitleTrack:
    """一份字幕（有序 cue 列表 + 来源路径）。"""

    cues: list[SubtitleCue]
    path: Path | None = None
    encoding: str = "utf-8"

    def __bool__(self) -> bool:
        return bool(self.cues)

    def at(self, position_ms: int) -> str:
        """取该时刻应显示的字幕文本（多条时用换行拼接）。"""
        found = [cue.text for cue in self.cues if cue.contains(position_ms)]
        return "\n".join(item for item in found if item)

    def index_at(self, position_ms: int) -> int:
        """二分不了（cue 可能重叠），返回第一条命中的下标（找不到为 -1）。"""
        for index, cue in enumerate(self.cues):
            if cue.contains(position_ms):
                return index
        return -1


def parse_timecode(text: str) -> int:
    """`00:01:02,500` / `00:01:02.500` / `01:02.5` → 毫秒；解析失败返回 -1。

    支持三种常见写法（SRT 用逗号、VTT 用点、部分工具省略小时位）：
    两个冒号 = 时:分:秒，一个冒号 = 分:秒。没有冒号的裸数字不算时间码。
    """
    raw = (text or "").strip()
    parts = [item for item in re.split(r"[:]", raw) if item != ""]
    if len(parts) < 2:
        return -1
    tail = parts[-1]
    seconds_text, _, millis_text = tail.partition("," if "," in tail else ".")
    try:
        numbers = [int(part) for part in parts[:-1]] + [int(seconds_text)]
    except ValueError:
        return -1
    if len(numbers) == 2:
        hours, minutes, seconds = 0, numbers[0], numbers[1]
    else:
        hours, minutes, seconds = numbers[0], numbers[1], numbers[2]
    millis = int((millis_text or "0").ljust(3, "0")[:3]) if millis_text else 0
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _clean(text: str) -> str:
    """去掉字幕里的样式标签（`<i>`/`{\\an8}`）与多余空行。"""
    lines = []
    for raw in (text or "").splitlines():
        line = _TAG.sub("", raw)
        line = re.sub(r"\{[^}]*\}", "", line)          # ASS 的 {\an8} / {\pos(..)}
        line = line.strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def parse_srt(text: str) -> list[SubtitleCue]:
    """解析 SRT：块 = 序号 / 时间行 / 文本行（空行分隔）。"""
    cues: list[SubtitleCue] = []
    blocks = re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").replace("\r", "\n"))
    for block in blocks:
        lines = [line for line in block.split("\n")]
        if not lines:
            continue
        time_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if time_index is None:
            continue
        head, _, tail = lines[time_index].partition("-->")
        start = parse_timecode(head)
        end = parse_timecode(tail)
        if start < 0 or end < 0:
            continue
        body = _clean("\n".join(lines[time_index + 1:]))
        if body:
            cues.append(SubtitleCue(start_ms=start, end_ms=max(end, start + 1), text=body))
    cues.sort(key=lambda cue: cue.start_ms)
    return cues


def parse_vtt(text: str) -> list[SubtitleCue]:
    """解析 WebVTT：跳过 `WEBVTT` 头与 `NOTE` 块，时间行同样用 `-->`。"""
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    body = re.sub(r"^WEBVTT.*?(\n\s*\n|$)", "", body, flags=re.DOTALL)
    body = re.sub(r"(?m)^NOTE\b.*?(?=\n\s*\n)", "", body)
    return parse_srt(body)


def parse_subtitle(text: str, *, suffix: str = ".srt") -> list[SubtitleCue]:
    """按扩展名选解析器（`.vtt` 走 VTT，其余按 SRT 语义 —— 两者时间轴格式一致）。"""
    if (suffix or "").lower() == ".vtt":
        return parse_vtt(text)
    return parse_srt(text)


def read_text(path: Path) -> tuple[str, str]:
    """读字幕文件并对编码做容错（中文外挂字幕常见 GBK/Big5）。"""
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk", "big5", "utf-16"):
        try:
            return raw.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace"), "utf-8"


def load_subtitle(path: str | Path) -> SubtitleTrack:
    """从文件加载字幕（解析失败返回空轨道，绝不抛错打断播放）。"""
    target = Path(path)
    if not target.is_file():
        return SubtitleTrack(cues=[], path=target)
    try:
        text, encoding = read_text(target)
        cues = parse_subtitle(text, suffix=target.suffix)
    except Exception:  # noqa: BLE001
        return SubtitleTrack(cues=[], path=target)
    return SubtitleTrack(cues=cues, path=target, encoding=encoding)


def find_subtitle(video_path: str | Path, *, suffixes=SUBTITLE_SUFFIXES) -> Path | None:
    """在视频同目录找同名外挂字幕（`影片.mp4` → `影片.srt`）。"""
    target = Path(video_path)
    for suffix in suffixes:
        candidate = target.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def to_vtt(cues: list[SubtitleCue]) -> str:
    """导出为 WebVTT（外部播放器/调试用）。"""
    def stamp(ms: int) -> str:
        ms = max(0, int(ms))
        return (f"{ms // 3600000:02d}:{(ms % 3600000) // 60000:02d}:"
                f"{(ms % 60000) // 1000:02d}.{ms % 1000:03d}")

    lines = ["WEBVTT", ""]
    for index, cue in enumerate(cues, start=1):
        lines.append(str(index))
        lines.append(f"{stamp(cue.start_ms)} --> {stamp(cue.end_ms)}")
        lines.append(cue.text)
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "SUBTITLE_SUFFIXES", "SubtitleCue", "SubtitleTrack", "find_subtitle",
    "load_subtitle", "parse_subtitle", "parse_srt", "parse_timecode", "parse_vtt",
    "read_text", "to_vtt",
]
