"""格式集合与扩展名映射（转换引擎使用的唯一事实来源）。"""
from __future__ import annotations

# 转换族
TEXT_FORMATS = ("txt", "markdown", "html")
WORD_FORMATS = ("docx", "doc")
SHEET_FORMATS = ("xlsx", "xls", "csv")
IMAGE_FORMATS = ("jpg", "png", "webp", "bmp", "gif")
VIDEO_FORMATS = ("mp4", "mov", "avi")
AUDIO_FORMATS = ("m4a", "mp3", "wav")
MEDIA_FORMATS = VIDEO_FORMATS + AUDIO_FORMATS
ARCHIVE_FORMATS = ("zip", "tar", "rar")

SUPPORTED_FORMATS = (
    TEXT_FORMATS + WORD_FORMATS + SHEET_FORMATS + IMAGE_FORMATS + MEDIA_FORMATS + ARCHIVE_FORMATS + ("json",)
)

# 文本族动作可选择的源集合（引擎层用于文件家族判断）
TEXT_SOURCE_FORMATS = TEXT_FORMATS

# 转换输出的目标格式 → 扩展名（归档解压输出为目录，不入此表）
TARGET_EXTENSION = {
    "txt": ".txt",
    "markdown": ".md",
    "html": ".html",
    "pdf": ".pdf",
    "csv": ".csv",
    "jpg": ".jpg",
    "png": ".png",
    "webp": ".webp",
    "bmp": ".bmp",
    "gif": ".gif",
    "mp4": ".mp4",
    "mov": ".mov",
    "avi": ".avi",
    "m4a": ".m4a",
    "mp3": ".mp3",
    "wav": ".wav",
    "zip": ".zip",
    "tar": ".tar",
}

EXTENSION_ALIASES = {"md": "markdown", "htm": "html", "jpeg": "jpg", "markdown": "markdown"}


def format_from_extension(extension: str) -> str:
    ext = (extension or "").lstrip(".").lower()
    if ext in EXTENSION_ALIASES:
        return EXTENSION_ALIASES[ext]
    return ext if ext in SUPPORTED_FORMATS else "unknown"


def format_label(format: str) -> str:
    return format.upper() if format != "unknown" else format
