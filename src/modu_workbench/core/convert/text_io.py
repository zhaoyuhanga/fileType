"""文本读写：编码自适应解码 / JSON 美化（含行·列定位报错）。"""
from __future__ import annotations

import json
from pathlib import Path


class JsonFormatError(ValueError):
    pass


def read_text_smart(path: str | Path) -> str:
    """读取文本文件：优先 UTF-8(-BOM)，非法时回退 GB18030。"""
    data = Path(path).read_bytes()
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        encoding = "utf-16" if data[:2] == b"\xff\xfe" else "utf-16-be"
        return data.decode(encoding)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gb18030")


def write_text(path: str | Path, content: str) -> None:
    Path(path).write_text(content, encoding="utf-8")


def json_pretty(source: str, indent: int = 2) -> str:
    """JSON 美化：解析并缩进格式化；失败抛 JsonFormatError（含行/列）。"""
    trimmed = source.lstrip("\ufeff").strip()
    if not trimmed:
        return ""
    try:
        parsed = json.loads(trimmed)
    except json.JSONDecodeError as error:
        line, col = _locate(trimmed, error.pos)
        raise JsonFormatError(
            f"JSON 语法错误（第 {line} 行，第 {col} 列）：{error.msg}。请检查引号、逗号与括号。"
        ) from error
    return json.dumps(parsed, ensure_ascii=False, indent=indent)


def _locate(text: str, index: int) -> tuple[int, int]:
    before = text[: index + 1]
    line = before.count("\n") + 1
    last_nl = before.rfind("\n")
    col = index - last_nl
    return line, col
