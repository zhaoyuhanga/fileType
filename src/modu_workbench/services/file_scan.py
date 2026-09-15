"""文件扫描：收集可转换/可预览的本地文件（唯一实现，供桥接与导入复用）。"""
from __future__ import annotations

from pathlib import Path

# 支持导入与转换的扩展名（与 core/convert/formats 的能力对齐）
SUPPORTED_IMPORT_EXTENSIONS = (
    ".txt", ".md", ".html", ".json",
    ".docx", ".doc", ".xlsx", ".xls", ".csv",
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif",
    ".mp4", ".mov", ".avi", ".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma",
    ".zip", ".tar", ".rar",
)


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
