"""本地文件工具：受支持文件扫描、文件名净化、目标路径去重。

**为什么这三个函数住在一起**：它们都被多个板块用到，而板块之间不允许互相依赖
（见 `docs/ARCHITECTURE.md` 的「依赖规则」）。此前 `safe_filename` 在乐库/影视/图库
各自的 `models.py` 里各抄一份（三份实现只差默认 fallback 与限长），`unique_path`
在乐库/影视的 `downloader.py` 里各抄一份 —— 改一处忘一处就会出现"乐库能存、影视崩"
这类只在特定板块复现的差异。收敛到本模块后，各板块只保留**签名与默认值**的薄封装，
调用方与 `__all__` 都不用改。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# 支持导入与转换的扩展名（与 core/convert/formats 的能力对齐）
SUPPORTED_IMPORT_EXTENSIONS = (
    ".txt", ".md", ".html", ".json",
    ".docx", ".doc", ".xlsx", ".xls", ".csv",
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif",
    ".mp4", ".mov", ".avi", ".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma",
    ".zip", ".tar", ".rar",
)

#: 文件名里的非法字符（Windows/Unix 取并集，另含换行与制表符）
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')
_REPEATED_UNDERSCORES = re.compile(r"_{2,}")
_REPEATED_SPACES = re.compile(r"\s+")

#: 文件名长度上限的默认值（乐库/影视沿用 120；图库历史上是 110，由调用方显式传入）
DEFAULT_FILENAME_LIMIT = 120

#: `unique_path` 逐个试序号的上限；用尽后改用进程号兜底，保证"绝不覆盖"
_UNIQUE_INDEX_LIMIT = 1000


def scan_paths(paths: list[str]) -> list[str]:
    """把文件/文件夹（递归）展开为受支持的文件绝对路径列表（去重、保持顺序）。"""
    collected: list[str] = []
    for raw in paths:
        target = Path(raw)
        if target.is_file():
            if target.suffix.lower() in SUPPORTED_IMPORT_EXTENSIONS:
                collected.append(str(target))
        elif target.is_dir():
            for child in sorted(target.rglob("*")):
                if child.is_file() and child.suffix.lower() in SUPPORTED_IMPORT_EXTENSIONS:
                    collected.append(str(child))
    return list(dict.fromkeys(collected))


def safe_filename(name: str, fallback: str = "file", limit: int = DEFAULT_FILENAME_LIMIT) -> str:
    """把任意名称（曲名/片名/图片名）转换为合法文件名。

    规则（**顺序敏感**，与收敛前逐字一致）：
    非法字符换成下划线 → 连续下划线合并为 1 个 → 连续空白合并为 1 个 → 去掉首尾的
    空格与 `._` → 空结果用 `fallback` → 截断到 `limit` 个字符。

    `limit` 由调用方决定：乐库/影视 120，图库 110（历史行为，不改）。
    """
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("_", (name or "").strip())
    cleaned = _REPEATED_UNDERSCORES.sub("_", cleaned)
    cleaned = _REPEATED_SPACES.sub(" ", cleaned).strip(" ._")
    return (cleaned or fallback)[:limit]


def unique_path(directory: Path, filename: str) -> Path:
    """目标文件已存在时自动加序号，避免覆盖（`名字 (2).ext`、`名字 (3).ext`…）。

    目录不存在会先创建；序号用尽（1000）后退化为带进程号的名字 —— 宁可名字难看，
    也不能覆盖用户已有的文件。
    """
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(2, _UNIQUE_INDEX_LIMIT):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem} ({os.getpid()}){suffix}"


__all__ = [
    "DEFAULT_FILENAME_LIMIT",
    "SUPPORTED_IMPORT_EXTENSIONS",
    "safe_filename",
    "scan_paths",
    "unique_path",
]
