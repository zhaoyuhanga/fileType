"""格式集合与扩展名映射（转换引擎使用的唯一事实来源）。"""
from __future__ import annotations

TEXT_FORMATS = ("txt", "markdown", "html")
IMAGE_FORMATS = ("jpg", "png", "webp", "bmp", "gif")
ARCHIVE_FORMATS = ("zip", "tar", "rar")

# 转换动作可用的源/目标集合（json 仅编辑查看，不入转换矩阵）
SUPPORTED_FORMATS = TEXT_FORMATS + IMAGE_FORMATS + ARCHIVE_FORMATS + ("json",)

EXTENSION_ALIASES = {"md": "markdown", "htm": "html", "jpeg": "jpg"}

TARGET_EXTENSION = {
    "markdown": ".md",
    "txt": ".txt",
    "html": ".html",
    "pdf": ".pdf",
    "jpg": ".jpg",
    "png": ".png",
    "webp": ".webp",
    "bmp": ".bmp",
    "gif": ".gif",
    "zip": ".zip",
    "tar": ".tar",
}


def format_from_extension(extension: str) -> str:
    ext = (extension or "").lstrip(".").lower()
    if ext in EXTENSION_ALIASES:
        return EXTENSION_ALIASES[ext]
    return ext if ext in SUPPORTED_FORMATS else "unknown"


def format_label(format: str) -> str:
    return format.upper() if format != "unknown" else format
