"""格式集合与扩展名映射（转换引擎使用的唯一事实来源）。

维护约定：
- 这里的每个格式都必须**真的能转换**（有一个实现它的转换族），否则不要登记 ——
  登记了却没实现，用户会看到"点了没反应"。
- 「输入格式」与「输出目标」是两件事：少数格式只能读不能写（例如 PSD 只能读），
  因此它们只出现在 `*_FORMATS`（输入）里，不进 `TARGET_EXTENSION`（输出）。
- 扩展名别名统一在这里收口（jpeg→jpg、tif→tiff、pgm→ppm、tgz→targz…），
  别在业务代码里各自 `endswith`。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 转换族
TEXT_FORMATS = ("txt", "markdown", "html")
# PDF 作为「输入」格式（v1.0.0：此前只能作为输出目标，导致 pdf 文件没有任何动作）
PDF_FORMATS = ("pdf",)
WORD_FORMATS = ("docx", "doc", "odt", "rtf")
SHEET_FORMATS = ("xlsx", "xls", "ods", "csv", "tsv")
# 可读可写（Pillow 支持写出）；TIFF/ICO/TGA/PCX/PPM 为 v1.0.4 新增
IMAGE_FORMATS = ("jpg", "png", "webp", "bmp", "gif", "tiff", "ico", "tga", "pcx", "ppm")
# 只能读、不能写出（Pillow 无写出支持）：作为输入可转成常见图片/PDF，但不作为目标
IMAGE_READONLY_FORMATS = ("psd", "dds", "jp2")
VIDEO_FORMATS = (
    "mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "m4v", "mpg", "mpeg", "ts", "3gp", "ogv",
)
AUDIO_FORMATS = (
    "m4a", "mp3", "wav", "flac", "aac", "ogg", "opus", "wma", "m4b", "aiff", "amr", "ac3",
)
MEDIA_FORMATS = VIDEO_FORMATS + AUDIO_FORMATS
# zip/tar 之外补上标准库就能做的 gz/bz2/xz 单文件压缩与 tar.* 变体
ARCHIVE_FORMATS = ("zip", "tar", "targz", "tarbz2", "tarxz", "gz", "bz2", "xz", "rar")
ARCHIVE_READONLY_FORMATS = ("7z",)          # 需要 py7zr，未随包，仅识别不提供动作
DATA_FORMATS = ("json", "xml", "ini", "yaml")
SUBTITLE_FORMATS = ("srt", "vtt")
EBOOK_FORMATS = ("epub",)

SUPPORTED_FORMATS = (
    TEXT_FORMATS + WORD_FORMATS + SHEET_FORMATS + IMAGE_FORMATS + IMAGE_READONLY_FORMATS
    + MEDIA_FORMATS + ARCHIVE_FORMATS + ARCHIVE_READONLY_FORMATS + DATA_FORMATS
    + SUBTITLE_FORMATS + EBOOK_FORMATS + PDF_FORMATS
)

# 转换输出的目标格式 → 扩展名（归档解压输出为目录，不入此表）
TARGET_EXTENSION = {
    "txt": ".txt",
    "markdown": ".md",
    "html": ".html",
    "pdf": ".pdf",
    "csv": ".csv",
    "tsv": ".tsv",
    "xlsx": ".xlsx",
    "docx": ".docx",
    "epub": ".epub",
    "json": ".json",
    "xml": ".xml",
    "ini": ".ini",
    "yaml": ".yaml",
    "srt": ".srt",
    "vtt": ".vtt",
    "jpg": ".jpg",
    "png": ".png",
    "webp": ".webp",
    "bmp": ".bmp",
    "gif": ".gif",
    "tiff": ".tiff",
    "ico": ".ico",
    "tga": ".tga",
    "pcx": ".pcx",
    "ppm": ".ppm",
    "mp4": ".mp4",
    "mov": ".mov",
    "avi": ".avi",
    "mkv": ".mkv",
    "webm": ".webm",
    "flv": ".flv",
    "wmv": ".wmv",
    "m4v": ".m4v",
    "mpg": ".mpg",
    "mpeg": ".mpeg",
    "ts": ".ts",
    "3gp": ".3gp",
    "ogv": ".ogv",
    "m4a": ".m4a",
    "mp3": ".mp3",
    "wav": ".wav",
    "flac": ".flac",
    "aac": ".aac",
    "ogg": ".ogg",
    "opus": ".opus",
    "wma": ".wma",
    "m4b": ".m4b",
    "aiff": ".aiff",
    "amr": ".amr",
    "ac3": ".ac3",
    "zip": ".zip",
    "tar": ".tar",
    "targz": ".tar.gz",
    "tarbz2": ".tar.bz2",
    "tarxz": ".tar.xz",
    "gz": ".gz",
    "bz2": ".bz2",
    "xz": ".xz",
}

EXTENSION_ALIASES = {
    "md": "markdown",
    "markdown": "markdown",
    "htm": "html",
    "jpeg": "jpg",
    "jfif": "jpg",
    "tif": "tiff",
    "pnm": "ppm",
    "pgm": "ppm",
    "pbm": "ppm",
    "tgz": "targz",
    "tbz": "tarbz2",
    "tbz2": "tarbz2",
    "txz": "tarxz",
    "yml": "yaml",
    "sub": "srt",
}


def format_from_extension(extension: str) -> str:
    ext = (extension or "").lstrip(".").lower()
    if ext in EXTENSION_ALIASES:
        return EXTENSION_ALIASES[ext]
    return ext if ext in SUPPORTED_FORMATS else "unknown"


def format_label(format: str) -> str:
    """界面显示名：多数格式直接大写，少数用惯用写法。"""
    pretty = {
        "markdown": "Markdown",
        "targz": "TAR.GZ",
        "tarbz2": "TAR.BZ2",
        "tarxz": "TAR.XZ",
        "docx": "Word DOCX",
        "doc": "Word DOC",
        "xlsx": "Excel XLSX",
        "xls": "Excel XLS",
        "ods": "ODS",
        "aiff": "AIFF",
        "m4v": "M4V",
        "3gp": "3GP",
        "ogv": "OGV",
        "vtt": "VTT",
        "srt": "SRT",
        "yaml": "YAML",
        "ini": "INI",
        "tsv": "TSV",
        "epub": "EPUB",
    }
    if format == "unknown":
        return format
    return pretty.get(format, format.upper())
