"""转换动作注册表与能力判定（对齐旧版 ConverterRegistry 语义）。

家族（`kind` → 由谁实现）：
- `text`     → `engine._run_text`（txt/md/html/json 互转）
- `word`     → `engine._run_document_family`（doc/docx/odt/rtf）
- `sheet`    → `engine._run_document_family`（xlsx/xls/ods/csv/tsv）
- `image`    → `core/convert/image_io.py`（Pillow，含图片转 PDF）
- `media`    → `core/convert/media_io.py`（ffmpeg）
- `pdf`      → `engine._run_pdf`（pypdf 抽取文本）
- `data`     → `core/convert/data_io.py`（json/xml/ini/yaml 互转）
- `subtitle` → `core/convert/subtitle_io.py`（srt/vtt）
- `ebook`    → `core/convert/ebook_io.py`（epub）
- `archive`  → `core/convert/archive_io.py`（zip/tar/gz/bz2/xz/rar）

动作是**数据**：新增格式只需改 `formats.py` 的元组与这里的 targets，
不要在各处写 if/else。
"""
from __future__ import annotations

from dataclasses import dataclass

from .formats import (
    ARCHIVE_FORMATS,
    AUDIO_FORMATS,
    DATA_FORMATS,
    EBOOK_FORMATS,
    IMAGE_FORMATS,
    IMAGE_READONLY_FORMATS,
    SHEET_FORMATS,
    SUBTITLE_FORMATS,
    TEXT_FORMATS,
    VIDEO_FORMATS,
    WORD_FORMATS,
    format_label,
)

TEXT_TARGETS = ("txt", "markdown", "html", "pdf")
WORD_TARGETS = ("txt", "markdown", "html", "pdf")
SHEET_TARGETS = ("csv", "tsv", "xlsx", "markdown", "html", "txt", "pdf")
# 图片目标：全部可写图片格式 + PDF（反馈里最常见的"图片转 PDF"）
IMAGE_TARGETS = tuple(IMAGE_FORMATS) + ("pdf",)
DATA_TARGETS = ("txt", "markdown", "html", "pdf", "json", "xml", "yaml", "ini", "csv")
SUBTITLE_TARGETS = ("srt", "vtt", "txt")
# 解压动作：格式 → 动作 id。**每个格式必须有独立 id** ——
# 共用 id 时 `union_actions` 会按 id 去重，多选 tar 与 tar.gz 就只剩一个动作，
# 另一个文件会被判成"不适用"而跳过。
ARCHIVE_EXTRACT_IDS = {
    "zip": "zip-extract",
    "tar": "tar-extract",
    "targz": "targz-extract",
    "tarbz2": "tarbz2-extract",
    "tarxz": "tarxz-extract",
    "gz": "gz-extract",
    "bz2": "bz2-extract",
    "xz": "xz-extract",
    "rar": "rar-extract",
}
# tar 家族的解压实现相同（tarfile 按内容自动识别压缩），engine 用这个集合分派
TAR_EXTRACT_IDS = frozenset({"tar-extract", "targz-extract", "tarbz2-extract", "tarxz-extract"})
# 压缩目标：动作 id → （标签，扩展名格式）
ARCHIVE_COMPRESS_TARGETS = (
    ("zip", "压缩为 ZIP"),
    ("tar", "压缩为 TAR"),
    ("targz", "压缩为 TAR.GZ"),
    ("tarbz2", "压缩为 TAR.BZ2"),
    ("tarxz", "压缩为 TAR.XZ"),
    ("gz", "压缩为 GZ（单文件）"),
    ("bz2", "压缩为 BZ2（单文件）"),
    ("xz", "压缩为 XZ（单文件）"),
)
# 可被压缩的源：除了归档本身以外的全部格式
COMPRESSIBLE_FORMATS = (
    TEXT_FORMATS + WORD_FORMATS + SHEET_FORMATS + IMAGE_FORMATS + IMAGE_READONLY_FORMATS
    + VIDEO_FORMATS + AUDIO_FORMATS + DATA_FORMATS + SUBTITLE_FORMATS + EBOOK_FORMATS
    + ("pdf",)
)

FMT_NAMES = {
    "txt": "TXT", "markdown": "Markdown", "html": "HTML", "pdf": "PDF", "csv": "CSV", "tsv": "TSV",
    "docx": "Word", "doc": "Word", "odt": "ODT", "rtf": "RTF",
    "xlsx": "Excel", "xls": "Excel", "ods": "ODS",
    "jpg": "JPG", "png": "PNG", "webp": "WEBP", "bmp": "BMP", "gif": "GIF",
    "tiff": "TIFF", "ico": "ICO", "tga": "TGA", "pcx": "PCX", "ppm": "PPM",
    "psd": "PSD", "dds": "DDS", "jp2": "JP2",
    "mp4": "MP4", "mov": "MOV", "avi": "AVI", "mkv": "MKV", "webm": "WEBM", "flv": "FLV",
    "wmv": "WMV", "m4v": "M4V", "mpg": "MPG", "mpeg": "MPEG", "ts": "TS", "3gp": "3GP", "ogv": "OGV",
    "m4a": "M4A", "mp3": "MP3", "wav": "WAV", "flac": "FLAC", "aac": "AAC", "ogg": "OGG",
    "opus": "OPUS", "wma": "WMA", "m4b": "M4B", "aiff": "AIFF", "amr": "AMR", "ac3": "AC3",
    "json": "JSON", "xml": "XML", "ini": "INI", "yaml": "YAML",
    "srt": "SRT", "vtt": "VTT", "epub": "EPUB",
    "zip": "ZIP", "tar": "TAR", "targz": "TAR.GZ", "tarbz2": "TAR.BZ2", "tarxz": "TAR.XZ",
    "gz": "GZ", "bz2": "BZ2", "xz": "XZ", "rar": "RAR",
}


@dataclass(frozen=True)
class ConverterAction:
    id: str
    label: str
    source_formats: tuple[str, ...]
    target_format: str
    category: str  # document / image / audio / video / archive / data / subtitle / ebook
    kind: str      # text / word / sheet / image / media / pdf / data / subtitle / ebook / archive

    def matches(self, source_format: str) -> bool:
        return source_format in self.source_formats


def _fmt_name(fmt: str) -> str:
    return FMT_NAMES.get(fmt, format_label(fmt))


def _pair_actions(family: tuple[str, ...], targets: tuple[str, ...], kind: str, category: str,
                  label_src: str | None = None) -> list[ConverterAction]:
    actions: list[ConverterAction] = []
    for source in family:
        for target in targets:
            if target == source:
                continue
            src = label_src or _fmt_name(source)
            actions.append(ConverterAction(
                id=f"{source}-to-{target}", label=f"{src} 转 {_fmt_name(target)}",
                source_formats=(source,), target_format=target,
                category=category, kind=kind,
            ))
    return actions


def _build_actions() -> list[ConverterAction]:
    actions: list[ConverterAction] = []
    # 文本族（txt/md/html → txt/md/html/pdf）
    actions.extend(_pair_actions(TEXT_FORMATS, TEXT_TARGETS, "text", "document"))
    # Word 族（doc/docx/odt/rtf → txt/md/html/pdf）
    actions.extend(_pair_actions(WORD_FORMATS, WORD_TARGETS, "word", "document", label_src="Word"))
    # 表格族（xlsx/xls/ods/csv/tsv → csv/tsv/xlsx/md/html/txt/pdf）
    actions.extend(_pair_actions(SHEET_FORMATS, SHEET_TARGETS, "sheet", "document"))
    # 图片族：可写格式两两互转 + 只读格式（psd/dds/jp2）转出 + 全部转 PDF
    for source in tuple(IMAGE_FORMATS) + tuple(IMAGE_READONLY_FORMATS):
        for target in IMAGE_TARGETS:
            if target == source:
                continue
            label = f"{_fmt_name(source)} 转 PDF" if target == "pdf" \
                else f"{_fmt_name(source)} 转 {_fmt_name(target)}"
            actions.append(ConverterAction(
                id=f"{source}-to-{target}", label=label,
                source_formats=(source,), target_format=target, category="image", kind="image",
            ))
    # 视频族内部互转 + 提取音频
    for source in VIDEO_FORMATS:
        for target in VIDEO_FORMATS:
            if target != source:
                actions.append(ConverterAction(
                    id=f"{source}-to-{target}", label=f"{_fmt_name(source)} 转 {_fmt_name(target)}",
                    source_formats=(source,), target_format=target, category="video", kind="media",
                ))
        for target in ("mp3", "wav", "m4a", "aac"):
            actions.append(ConverterAction(
                id=f"{source}-to-{target}", label=f"{_fmt_name(source)} 提取 {_fmt_name(target)}",
                source_formats=(source,), target_format=target, category="audio", kind="media",
            ))
    # 音频族内部互转
    for source in AUDIO_FORMATS:
        for target in AUDIO_FORMATS:
            if target != source:
                actions.append(ConverterAction(
                    id=f"{source}-to-{target}", label=f"{_fmt_name(source)} 转 {_fmt_name(target)}",
                    source_formats=(source,), target_format=target, category="audio", kind="media",
                ))
    # 数据族（json/xml/ini/yaml 互转 + 文本/表格/PDF 输出）
    actions.extend(_pair_actions(DATA_FORMATS, DATA_TARGETS, "data", "data"))
    # 字幕族（srt↔vtt + 转纯文本）
    actions.extend(_pair_actions(SUBTITLE_FORMATS, SUBTITLE_TARGETS, "subtitle", "subtitle"))
    # 电子书族（txt/md/html → epub；epub → txt/md/html/pdf）
    for target in EBOOK_FORMATS:
        for source in TEXT_FORMATS:
            actions.append(ConverterAction(
                id=f"{source}-to-{target}", label=f"{_fmt_name(source)} 转 {_fmt_name(target)}",
                source_formats=(source,), target_format=target, category="ebook", kind="ebook",
            ))
    for source in EBOOK_FORMATS:
        for target in ("txt", "markdown", "html", "pdf"):
            actions.append(ConverterAction(
                id=f"{source}-to-{target}", label=f"EPUB 转 {_fmt_name(target)}",
                source_formats=(source,), target_format=target, category="ebook", kind="ebook",
            ))
    # PDF 输入：抽取文本（pypdf）
    actions.extend([
        ConverterAction(id="pdf-to-txt", label="PDF 提取文本（TXT）",
                        source_formats=("pdf",), target_format="txt",
                        category="document", kind="pdf"),
        ConverterAction(id="pdf-to-markdown", label="PDF 提取文本（Markdown）",
                        source_formats=("pdf",), target_format="markdown",
                        category="document", kind="pdf"),
    ])
    # 归档族：压缩（对除归档外的所有格式）与解压（每个归档格式）
    for target, label in ARCHIVE_COMPRESS_TARGETS:
        # 归档自身也能再打包成别的归档（zip → tar.gz 是常见需求），但不能压成同名格式
        sources = tuple(fmt for fmt in COMPRESSIBLE_FORMATS + ARCHIVE_FORMATS if fmt != target)
        actions.append(ConverterAction(
            id=f"compress-to-{target}", label=label, source_formats=sources,
            target_format=target, category="archive", kind="archive",
        ))
    for fmt, action_id in ARCHIVE_EXTRACT_IDS.items():
        actions.append(ConverterAction(
            id=action_id, label=f"{_fmt_name(fmt)} 解压", source_formats=(fmt,),
            target_format=fmt, category="archive", kind="archive",
        ))
    return actions


ACTIONS: tuple[ConverterAction, ...] = tuple(_build_actions())


def get_action(action_id: str) -> ConverterAction | None:
    return next((a for a in ACTIONS if a.id == action_id), None)


def actions_for_format(source_format: str) -> list[ConverterAction]:
    return [a for a in ACTIONS if a.matches(source_format)]


def union_actions(source_formats: list[str]) -> list[ConverterAction]:
    """各格式各自可用动作的并集（按 id 去重）。

    用途：用户同时勾选了多种格式时，"共同动作"往往为空（例如 md + png 没有交集），
    此时退化为并集，让每个文件用它自己适用的动作，而不是整块面板空白、按钮不可点。
    """
    seen: dict[str, ConverterAction] = {}
    for fmt in dict.fromkeys(source_formats):
        for action in ACTIONS:
            if action.matches(fmt) and action.id not in seen:
                seen[action.id] = action
    return list(seen.values())


def common_actions(source_formats: list[str]) -> list[ConverterAction]:
    """多个源格式共同可用的动作（目标 + 能力族一致）。"""
    if not source_formats:
        return []
    first = actions_for_format(source_formats[0])
    return [
        action
        for action in first
        if all(
            any(cand.id == action.id or (cand.target_format == action.target_format and cand.kind == action.kind)
                for cand in actions_for_format(fmt))
            for fmt in source_formats[1:]
        )
    ]
