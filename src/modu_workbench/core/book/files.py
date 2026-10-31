"""书库文件扫描：支持的电子书扩展名与目录展开。"""
from __future__ import annotations

from pathlib import Path

SUPPORTED_BOOK_EXTENSIONS = (".txt", ".epub")


def walk_book_files(paths: list[str]) -> list[str]:
    """扫描给定文件/文件夹，收集受支持的书本文件（递归、去重）。"""
    result: list[str] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            continue
        if path.is_file():
            if path.suffix.lower() in SUPPORTED_BOOK_EXTENSIONS:
                result.append(str(path))
            continue
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and child.suffix.lower() in SUPPORTED_BOOK_EXTENSIONS:
                    result.append(str(child))
    return list(dict.fromkeys(result))
