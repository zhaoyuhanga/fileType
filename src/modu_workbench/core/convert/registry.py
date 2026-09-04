"""转换动作注册表与能力判定（对齐旧版 ConverterRegistry 语义）。

家族：text / word / sheet / image / media / archive。
"""
from __future__ import annotations

from dataclasses import dataclass

from .formats import (
    ARCHIVE_FORMATS,
    AUDIO_FORMATS,
    IMAGE_FORMATS,
    SHEET_FORMATS,
    TEXT_FORMATS,
    VIDEO_FORMATS,
    WORD_FORMATS,
)

TEXT_TARGETS = ("txt", "markdown", "html", "pdf")
WORD_TARGETS = ("txt", "markdown", "html", "pdf")
SHEET_TARGETS = ("csv", "html", "txt", "pdf")
ARCHIVE_EXTRACT_IDS = {"zip-extract", "tar-extract", "rar-extract"}

FMT_NAMES = {
    "txt": "TXT", "markdown": "Markdown", "html": "HTML", "pdf": "PDF", "csv": "CSV",
    "docx": "Word", "doc": "Word", "xlsx": "Excel", "xls": "Excel",
    "jpg": "JPG", "png": "PNG", "webp": "WEBP", "bmp": "BMP", "gif": "GIF",
    "mp4": "MP4", "mov": "MOV", "avi": "AVI", "m4a": "M4A", "mp3": "MP3", "wav": "WAV",
}


@dataclass(frozen=True)
class ConverterAction:
    id: str
    label: str
    source_formats: tuple[str, ...]
    target_format: str
    category: str  # document / image / audio / video / archive
    kind: str      # text / word / sheet / image / media / archive

    def matches(self, source_format: str) -> bool:
        return source_format in self.source_formats


def _fmt_name(fmt: str) -> str:
    return FMT_NAMES.get(fmt, fmt.upper())


def _pair_actions(family: tuple[str, ...], targets: tuple[str, ...], kind: str, category: str,
                  label_src: str | None = None) -> list[ConverterAction]:
    actions: list[ConverterAction] = []
    for source in family:
        for target in targets:
            if target == source:
                continue
            if source in ("csv",) and target == "csv":
                continue
            src = label_src or _fmt_name(source)
            tgt = _fmt_name(target)
            actions.append(ConverterAction(
                id=f"{source}-to-{target}", label=f"{src} 转 {tgt}",
                source_formats=(source,), target_format=target,
                category=category, kind=kind,
            ))
    return actions


def _build_actions() -> list[ConverterAction]:
    actions: list[ConverterAction] = []
    # 文本族（txt/md/html ↔ txt/md/html/pdf）
    actions.extend(_pair_actions(TEXT_FORMATS, TEXT_TARGETS, "text", "document"))
    # Word 族（doc/docx → txt/md/html/pdf）
    actions.extend(_pair_actions(WORD_FORMATS, WORD_TARGETS, "word", "document", label_src="Word"))
    # 表格族（xlsx/xls/csv → csv/html/txt/pdf）
    actions.extend(_pair_actions(SHEET_FORMATS, SHEET_TARGETS, "sheet", "document"))
    # 图片族：两两互转
    for source in IMAGE_FORMATS:
        for target in IMAGE_FORMATS:
            if target == source:
                continue
            actions.append(ConverterAction(
                id=f"{source}-to-{target}", label=f"{_fmt_name(source)} 转 {_fmt_name(target)}",
                source_formats=(source,), target_format=target, category="image", kind="image",
            ))
    # 视频族内部互转
    for source in VIDEO_FORMATS:
        for target in VIDEO_FORMATS:
            if target == source:
                continue
            actions.append(ConverterAction(
                id=f"{source}-to-{target}", label=f"{_fmt_name(source)} 转 {_fmt_name(target)}",
                source_formats=(source,), target_format=target, category="video", kind="media",
            ))
    # 音频族内部互转
    for source in AUDIO_FORMATS:
        for target in AUDIO_FORMATS:
            if target == source:
                continue
            actions.append(ConverterAction(
                id=f"{source}-to-{target}", label=f"{_fmt_name(source)} 转 {_fmt_name(target)}",
                source_formats=(source,), target_format=target, category="audio", kind="media",
            ))
    # 视频提取音频（mp3 / wav）
    for source in VIDEO_FORMATS:
        for target in ("mp3", "wav"):
            actions.append(ConverterAction(
                id=f"{source}-to-{target}", label=f"{_fmt_name(source)} 提取 {_fmt_name(target)}",
                source_formats=(source,), target_format=target, category="audio", kind="media",
            ))
    # 归档族
    archive_all = TEXT_FORMATS + WORD_FORMATS + SHEET_FORMATS + IMAGE_FORMATS + VIDEO_FORMATS + AUDIO_FORMATS + ARCHIVE_FORMATS
    actions.extend([
        ConverterAction(id="compress-to-zip", label="压缩为 ZIP", source_formats=archive_all,
                        target_format="zip", category="archive", kind="archive"),
        ConverterAction(id="compress-to-tar", label="压缩为 TAR", source_formats=archive_all,
                        target_format="tar", category="archive", kind="archive"),
        ConverterAction(id="zip-extract", label="ZIP 解压", source_formats=("zip",),
                        target_format="zip", category="archive", kind="archive"),
        ConverterAction(id="tar-extract", label="TAR 解压", source_formats=("tar",),
                        target_format="tar", category="archive", kind="archive"),
        ConverterAction(id="rar-extract", label="RAR 解压", source_formats=("rar",),
                        target_format="rar", category="archive", kind="archive"),
    ])
    return actions


ACTIONS: tuple[ConverterAction, ...] = tuple(_build_actions())


def get_action(action_id: str) -> ConverterAction | None:
    return next((a for a in ACTIONS if a.id == action_id), None)


def actions_for_format(source_format: str) -> list[ConverterAction]:
    return [a for a in ACTIONS if a.matches(source_format)]


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
