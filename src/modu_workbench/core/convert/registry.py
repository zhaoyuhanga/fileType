"""转换动作注册表与能力判定（对齐旧版 ConverterRegistry 语义）。"""
from __future__ import annotations

from dataclasses import dataclass

from .formats import ARCHIVE_FORMATS, IMAGE_FORMATS, TEXT_FORMATS

TEXT_TARGETS = ("txt", "markdown", "html", "pdf")
ARCHIVE_EXTRACT_IDS = {"zip-extract", "tar-extract", "rar-extract"}


@dataclass(frozen=True)
class ConverterAction:
    id: str
    label: str
    source_formats: tuple[str, ...]
    target_format: str
    category: str  # document / image / archive
    kind: str      # text / image / archive

    def matches(self, source_format: str) -> bool:
        return source_format in self.source_formats


def _build_actions() -> list[ConverterAction]:
    actions: list[ConverterAction] = []

    # 文本族：txt / md / html → txt / md / html / pdf（不含同格式自转）
    for source in TEXT_FORMATS:
        for target in TEXT_TARGETS:
            if target == source:
                continue
            source_label = "Markdown" if source == "markdown" else source.upper()
            target_label = "Markdown" if target == "markdown" else target.upper()
            actions.append(
                ConverterAction(
                    id=f"{source}-to-{target}",
                    label=f"{source_label} 转 {target_label}",
                    source_formats=(source,),
                    target_format=target,
                    category="document",
                    kind="text",
                )
            )

    # 图片族：jpg/png/webp/bmp/gif 两两互转
    for source in IMAGE_FORMATS:
        for target in IMAGE_FORMATS:
            if target == source:
                continue
            actions.append(
                ConverterAction(
                    id=f"{source}-to-{target}",
                    label=f"{source.upper()} 转 {target.upper()}",
                    source_formats=(source,),
                    target_format=target,
                    category="image",
                    kind="image",
                )
            )

    # 归档族：压缩 / 解压
    archive_all = TEXT_FORMATS + IMAGE_FORMATS + ARCHIVE_FORMATS
    actions.extend(
        [
            ConverterAction(
                id="compress-to-zip",
                label="压缩为 ZIP",
                source_formats=archive_all,
                target_format="zip",
                category="archive",
                kind="archive",
            ),
            ConverterAction(
                id="compress-to-tar",
                label="压缩为 TAR",
                source_formats=archive_all,
                target_format="tar",
                category="archive",
                kind="archive",
            ),
            ConverterAction(
                id="zip-extract", label="ZIP 解压",
                source_formats=("zip",), target_format="zip",
                category="archive", kind="archive",
            ),
            ConverterAction(
                id="tar-extract", label="TAR 解压",
                source_formats=("tar",), target_format="tar",
                category="archive", kind="archive",
            ),
            ConverterAction(
                id="rar-extract", label="RAR 解压",
                source_formats=("rar",), target_format="rar",
                category="archive", kind="archive",
            ),
        ]
    )
    return actions


ACTIONS: tuple[ConverterAction, ...] = tuple(_build_actions())


def get_action(action_id: str) -> ConverterAction | None:
    return next((a for a in ACTIONS if a.id == action_id), None)


def actions_for_format(source_format: str) -> list[ConverterAction]:
    return [a for a in ACTIONS if a.matches(source_format)]


def common_actions(source_formats: list[str]) -> list[ConverterAction]:
    """多个源格式共同可用的动作（目标+能力族一致）。"""
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
