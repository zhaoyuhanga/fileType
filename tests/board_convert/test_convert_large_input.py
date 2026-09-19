"""大输入与编码回退的实测（把「未实测」变成「已锁定」）。

覆盖两个此前只在报告里标注"逻辑上覆盖、未实测"的场景：
1. **超大字幕文件**（几千条 cue）—— 解析必须是线性量级，不能卡住界面；
2. **GB18030 编码的字幕**—— `text_io.read_text_smart` 会在 UTF-8 解码失败时回退 GB18030，
   这里用真实 GB18030 字节验证这条回退路径。
"""
from __future__ import annotations

import time
from pathlib import Path

from modu_workbench.core.convert.subtitle_io import convert_subtitle

CUE_COUNT = 5000
# 上限给得很宽（正常应远低于 1 秒）：目的是拦住"不小心写成 O(n²) 导致几分钟"
TIME_LIMIT_SECONDS = 5.0


def _make_srt(path: Path, count: int, *, encoding: str = "utf-8") -> Path:
    lines: list[str] = []
    for index in range(1, count + 1):
        start = index  # 每条 1 秒，时间戳单调递增
        lines.append(str(index))
        lines.append(f"00:{start // 60:02d}:{start % 60:02d},000 --> 00:{start // 60:02d}:{start % 60:02d},500")
        lines.append(f"第 {index} 句台词")
        lines.append("")
    path.write_text("\n".join(lines), encoding=encoding)
    return path


def test_large_subtitle_parses_in_linear_time(tmp_path: Path) -> None:
    """5000 条 cue 的 SRT 转 VTT：结果完整，且耗时是线性量级。"""
    source = _make_srt(tmp_path / "大字幕.srt", CUE_COUNT)
    output = tmp_path / "大字幕.vtt"

    started = time.monotonic()
    convert_subtitle(source, "srt", "vtt", output)
    elapsed = time.monotonic() - started

    text = output.read_text(encoding="utf-8")
    assert text.startswith("WEBVTT")
    # 每条的文本都要在，且序号重编到 CUE_COUNT
    assert f"第 {CUE_COUNT} 句台词" in text
    assert text.count("-->") == CUE_COUNT
    assert elapsed < TIME_LIMIT_SECONDS, f"{CUE_COUNT} 条字幕耗时 {elapsed:.2f}s，疑似退化"


def test_large_subtitle_to_plain_text(tmp_path: Path) -> None:
    """转纯文本同样要快要完整（去掉了序号与时间轴）。"""
    source = _make_srt(tmp_path / "大字幕.srt", CUE_COUNT)
    output = tmp_path / "大字幕.txt"

    convert_subtitle(source, "srt", "txt", output)

    lines = [line for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == CUE_COUNT
    assert "-->" not in output.read_text(encoding="utf-8")


def test_gb18030_subtitle_falls_back_correctly(tmp_path: Path) -> None:
    """GB18030 编码的字幕：非 UTF-8 字节必须走回退解码，中文不能变乱码。"""
    source = tmp_path / "国标字幕.srt"
    body = "1\n00:00:01,000 --> 00:00:02,000\n你好，世界\n"
    source.write_bytes(body.encode("gb18030"))
    # 确认这确实是"UTF-8 解不开"的字节，否则测试等于没测回退路径
    try:
        source.read_bytes().decode("utf-8")
        raise AssertionError("构造的字节居然能被 UTF-8 解码，用例失去意义")
    except UnicodeDecodeError:
        pass

    output = tmp_path / "国标字幕.txt"
    convert_subtitle(source, "srt", "txt", output)
    assert "你好，世界" in output.read_text(encoding="utf-8")
