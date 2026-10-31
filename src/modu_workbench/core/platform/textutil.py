"""平台层文本工具：标题归一化与集数解析。

**为什么在这里**：这两个函数原本住在 `core/anime/models.py`，被
`ui_kit/media/danmaku_bridge.py`（弹幕匹配要用）调用。v2.0.0 移除第七板块后，
它们仍然是弹幕匹配的必需品 —— 而 `core.platform` 是所有板块都能用的共享层，
放在这里既不违反架构闸门（`ui_kit` 可引用 `core.platform`），也不会
让弹幕功能绑在一个已经不存在的板块上。

语义**刻意保持与迁移前完全一致**（含正则与归一化顺序），因为弹幕缓存文件名
用归一化后的哈希做键：改了归一化规则会让老缓存全部失配（用户看到"弹幕没了"）。
"""
from __future__ import annotations

import re
import unicodedata

#: 线路/剧集名里提取集数的多套正则（对齐 Kazumi 的容错做法，顺序敏感）
EPISODE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"第\s*(\d{1,4})\s*[集话話期]"),
    re.compile(r"\bEP?\s*(\d{1,4})\b", re.IGNORECASE),
    re.compile(r"\bE\s*(\d{1,4})\b", re.IGNORECASE),
    re.compile(r"【\s*(\d{1,4})\s*】"),
    re.compile(r"^\s*(\d{1,4})\s*[\.、,，]"),
    re.compile(r"\[\s*(\d{1,4})\s*\]"),
)

_HTML_TAG = re.compile(r"<[^>]+>")
_PUNCT = re.compile(r"[\s\-_·・:：,，.。!！?？~～/\\|()（）\[\]【】\"'`]+")
_REPEAT = re.compile(r"(.)\1{2,}")


def strip_html(text: str) -> str:
    """站点简介/标题里常带 HTML 标签，展示与匹配前先清洗。"""
    return _HTML_TAG.sub("", (text or "").replace("&nbsp;", " ")).strip()


def normalize_title(title: str) -> str:
    """标题归一化（用于弹幕匹配与同类去重）。

    规则：全角转半角 → 去掉标点与空白 → 连续 3 个以上相同字符合并为 3 个 → 小写。
    保守（不做翻译/别名映射），改动它会让既有弹幕缓存失配。
    """
    text = unicodedata.normalize("NFKC", strip_html(title or ""))
    text = _PUNCT.sub("", text)
    text = _REPEAT.sub(lambda match: match.group(1) * 3, text)
    return text.casefold()


def parse_episode_number(name: str, *extra_patterns: str) -> int:
    """从剧集名里提取集数；提不到返回 0（调用方用序号兜底）。

    `extra_patterns` 允许调用方补自定义格式（优先于内置正则）。
    """
    text = strip_html(name or "")
    if not text:
        return 0
    for pattern in extra_patterns:
        if not pattern:
            continue
        try:
            match = re.search(pattern, text)
        except re.error:
            continue
        if match and match.groups():
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue
    for pattern in EPISODE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue
    return 0


__all__ = ["EPISODE_PATTERNS", "normalize_title", "parse_episode_number", "strip_html"]
