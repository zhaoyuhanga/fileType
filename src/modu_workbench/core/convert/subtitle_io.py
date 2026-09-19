"""字幕族转换：SRT ↔ WebVTT，以及导出不带时间轴的纯文本。

关键取舍（都是踩过的坑）：

1. **时间码先转成「毫秒整数」再格式化**。不要用 float 秒做中间量：
   ``1.001`` 这类值在格式化时会因为二进制浮点误差变成 ``1,000``，
   字幕整体后移 1ms 很难查。整数毫秒让 ``srt → vtt → srt`` 完全无损。

2. **SRT 与 VTT 只差「逗号还是点」**：``00:00:01,000`` ↔ ``00:00:01.000``。
   VTT 允许省略小时（``00:01.000``），也允许逗号（个别工具这么写），
   解析时一并接受，输出时按各自规范写。

3. **VTT 的头部与注释块不是台词**：``WEBVTT`` 头、``NOTE`` / ``STYLE`` / ``REGION``
   块，以及时间码行后面的「cue 设置」（``align:middle line:90%``）都要单独处理，
   否则转 txt 时会把 ``WEBVTT``、``NOTE ...`` 当成字幕正文导出去。

4. **解析按行推进，而不是「严格按空行分块」**：规范要求 cue 之间有空行，
   但现实里漏写空行的 SRT 一大把（下一段的索引紧跟着上一段文本）。
   按行推进时，看到「单独一行的整数 + 下一行是时间码」就当作新 cue 的开始，
   这类文件也能读对；同时在 ``NOTE`` / ``STYLE`` / ``WEBVTT`` 块内整块跳过，
   不会把注释里的时间码当成台词。

5. **失败要能定位**：时间码写坏了、整份文件找不到任何时间码，都会抛带行号的
   中文 ``ValueError``；engine 会原样展示给用户，不会漏英文堆栈。

6. 索引号在写出时**重新编号**（从 1 连续），因为播放器只按顺序认，
   而源文件里的索引经常跳号/重复；解析时也只是忽略它。

7. 写文件统一走 ``text_io.write_text``（编码只在一个地方管）。注意它在 Windows 上
   会按平台习惯写成 CRLF：SRT / VTT 规范都允许 CRLF，解析器也会先归一化成 LF，
   所以往返不受影响；不要为了「一定是 LF」去绕开 text_io 自己写文件。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from . import text_io

SUBTITLE_FORMATS = ("srt", "vtt")
SUBTITLE_TARGETS = ("srt", "vtt", "txt")

# 时间码：可选小时 + 分:秒 + 毫秒（毫秒前是逗号或点，1~3 位都接受）
_TIMESTAMP_PATTERN = re.compile(
    r"^(?:(?P<hours>\d+):)?(?P<minutes>\d{1,2}):(?P<seconds>\d{1,2})[,.](?P<millis>\d{1,3})$"
)
# 箭头：标准是 " --> "，这里容忍多余的空格与多写一个 '-'（--->）
_ARROW_PATTERN = re.compile(r"\s*-{2,}>\s*")
# WEBVTT 头与 NOTE / STYLE / REGION 块：以这些整词开头的行表示「接下来是注释，不是台词」。
# 用整词匹配（后接空白或行尾），避免把 cue 标识符 "NOTE1" 误判成注释。
_COMMENT_START_PATTERN = re.compile(r"^(?:WEBVTT|NOTE|STYLE|REGION)(?:\s|$)", re.IGNORECASE)
# 字幕序号行（漏写空行的 SRT 里，它出现在下一段时间码的上一行）
_INDEX_ONLY_PATTERN = re.compile(r"^\d+$")


@dataclass
class Cue:
    """一条字幕：起止时间用毫秒整数存，文本保留内部换行。"""

    start_ms: int
    end_ms: int
    text: str
    settings: str = ""  # 仅 VTT 有：align:middle line:90% 之类
    lines: list[int] = field(default_factory=list)  # 源文件行号（报错/调试用）

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


# ---------------------------------------------------------------- 解析

def parse_subtitle(source: str | Path, source_format: str = "") -> list[Cue]:
    """读取并解析字幕文件（SRT / VTT 都走同一套解析器）。"""
    path = Path(source)
    if not path.exists():
        raise ValueError(f"字幕文件不存在：{path}")
    return parse_subtitle_text(text_io.read_text_smart(path), source_format=source_format or path.suffix)


def parse_subtitle_text(content: str, source_format: str = "") -> list[Cue]:
    """解析字幕文本 → ``Cue`` 列表（SRT / VTT 共用一套解析器）。

    SRT 与 VTT 的区别只有头部、毫秒分隔符和 cue 设置，按格式严格分流反而更容易坏
    （用户把 .vtt 内容存成 .srt 是常有的事）；所以这里只按行推进：
    时间码行开一条 cue，空行结束它，其它行归入当前 cue 的文本。
    """
    label = _format_label(source_format)
    text = (content or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ValueError(f"字幕文件是空的，没有可转换的内容（{label}）")

    lines = text.split("\n")
    total = len(lines)
    cues: list[Cue] = []
    cue_texts: list[list[str]] = []
    current: list[str] | None = None  # 当前 cue 的文本行；None 表示「还没进入任何 cue」
    in_comment = False

    index = 0
    while index < total:
        raw = lines[index]
        stripped = raw.strip()
        line_number = index + 1
        index += 1  # 指针先前进，下面判断「下一行」时刚好是 lines[index]

        if in_comment:
            if not stripped:
                in_comment = False  # 空行结束 NOTE / STYLE / WEBVTT 头
            continue

        if _ARROW_PATTERN.search(stripped):
            start, end, settings = _parse_timecode_line(stripped, line_number, label)
            cues.append(Cue(start_ms=start, end_ms=end, text="", settings=settings, lines=[line_number]))
            current = []
            cue_texts.append(current)
            continue

        if current is None:
            # 还没进入字幕：WEBVTT 头、注释、空行、序号行、cue 标识符一律忽略
            if _COMMENT_START_PATTERN.match(stripped):
                in_comment = True
            continue

        if not stripped:
            current = None  # 空行结束当前 cue
            continue

        if _INDEX_ONLY_PATTERN.match(stripped) and index < total and _ARROW_PATTERN.search(lines[index]):
            # 下一段的序号（文件漏写了之间的空行）：当前 cue 到此为止，这一行本身丢弃
            current = None
            continue

        current.append(raw)

    for cue, text_lines in zip(cues, cue_texts):
        cue.text = "\n".join(text_lines).strip("\n")

    if not cues:
        raise ValueError(
            f"没有在{label}里找到任何字幕时间码（形如 00:00:01,000 --> 00:00:02,000）。"
            "请确认文件内容确实是 SRT / VTT 字幕。"
        )
    return cues


def _parse_timecode_line(line: str, line_number: int, label: str) -> tuple[int, int, str]:
    """``"00:00:01,000 --> 00:00:02,000 align:middle"`` → (start_ms, end_ms, settings)。"""
    parts = _ARROW_PATTERN.split(line.strip(), maxsplit=1)
    if len(parts) != 2:
        raise ValueError(
            f"第 {line_number} 行的字幕时间码不完整（{label}）：「{line.strip()}」。"
            "正确写法：00:00:01,000 --> 00:00:02,000"
        )
    start = parse_timestamp(parts[0], line_number, label)
    tokens = parts[1].split()
    if not tokens:
        raise ValueError(
            f"第 {line_number} 行的字幕时间码缺少结束时间（{label}）：「{line.strip()}」"
        )
    end = parse_timestamp(tokens[0], line_number, label)
    settings = " ".join(tokens[1:]).strip()
    if end < start:
        # 时间倒挂的文件播放器会整段跳过，早点提示比默默转出一份坏字幕好
        raise ValueError(
            f"第 {line_number} 行的字幕结束时间早于开始时间（{label}）："
            f"{format_timestamp(start, ',')} --> {format_timestamp(end, ',')}"
        )
    return start, end, settings


def parse_timestamp(value: str, line_number: int = 0, label: str = "") -> int:
    """时间码字符串 → 毫秒整数；格式不对抛中文 ``ValueError``。"""
    match = _TIMESTAMP_PATTERN.match((value or "").strip())
    if match is None:
        where = f"（第 {line_number} 行）" if line_number else ""
        raise ValueError(
            f"字幕时间码格式不正确{where}：「{(value or '').strip()}」。"
            "应写成 00:00:01,000（SRT）或 00:00:01.000（VTT），毫秒固定 3 位。"
        )
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    millis = int(match.group("millis").ljust(3, "0"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def format_timestamp(milliseconds: int, separator: str = ",") -> str:
    """毫秒整数 → ``HH:MM:SS,mmm``（separator="," 是 SRT，"." 是 VTT）。"""
    total = max(0, int(milliseconds))
    hours, remainder = divmod(total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


# ---------------------------------------------------------------- 写出

def cues_to_srt(cues: list[Cue]) -> str:
    """Cue 列表 → SRT 文本（逗号毫秒，索引从 1 重新编号）。"""
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{format_timestamp(cue.start_ms, ',')} --> {format_timestamp(cue.end_ms, ',')}\n{cue.text}\n"
        )
    return "\n".join(blocks)


def cues_to_vtt(cues: list[Cue]) -> str:
    """Cue 列表 → WebVTT 文本（WEBVTT 头 + 点毫秒 + 保留 cue 设置）。

    文本按原样写出（含 ``<i>`` 这类常见样式标签），不做 HTML 转义：
    转义会把样式标记变成可见字符，反而破坏字幕。
    """
    blocks = ["WEBVTT"]
    for cue in cues:
        timecode = f"{format_timestamp(cue.start_ms, '.')} --> {format_timestamp(cue.end_ms, '.')}"
        settings = cue.settings.strip()
        if settings:
            timecode = f"{timecode} {settings}"
        blocks.append(f"{timecode}\n{cue.text}" if cue.text else timecode)
    return "\n\n".join(blocks) + "\n"


def cues_to_text(cues: list[Cue]) -> str:
    """Cue 列表 → 不带索引与时间轴的纯文本（每条的文本用换行连接）。"""
    return "\n".join(cue.text for cue in cues)


def convert_subtitle(source: str | Path, source_format: str, target: str, output: str | Path) -> None:
    """字幕族统一入口（engine._run_subtitle 调用）。"""
    target_format = (target or "").lstrip(".").lower()
    if target_format not in SUBTITLE_TARGETS:
        raise ValueError(f"不支持的字幕目标：{target}（可用：srt / vtt / txt）")
    cues = parse_subtitle(source, source_format)

    if target_format == "srt":
        text = cues_to_srt(cues)
    elif target_format == "vtt":
        text = cues_to_vtt(cues)
    else:
        text = cues_to_text(cues)

    text_io.write_text(output, text)


def _format_label(source_format: str) -> str:
    value = (source_format or "").lstrip(".").lower()
    return {"srt": "SRT", "vtt": "VTT"}.get(value, "字幕文件")
