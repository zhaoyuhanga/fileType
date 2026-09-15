"""曲目匹配：跨源兜底时判断“是不是同一首歌”，避免下错。"""
from __future__ import annotations

import html
import re
import unicodedata

from ..models import RemoteTrack

# 常见的“版本/后缀”噪声，匹配时忽略
_NOISE = re.compile(
    r"(official|audio|video|mv|hd|hq|live|remix|cover|instrumental|karaoke|"
    r"现场|伴奏|翻唱|纯音乐|无损|高音质|完整版|抖音|铃声|片段|试听)",
    re.IGNORECASE,
)
_BRACKETS = re.compile(r"[（(\[【].*?[)）\]】]")
_NON_WORD = re.compile(r"[\s\-_·、,，.。'\"!！?？:：/\\|+&]+")
_ARTIST_SPLIT = re.compile(r"[、,，/&;；]|feat\.?|ft\.?", re.IGNORECASE)


def normalize(text: str) -> str:
    """规范化：去 HTML 实体、括号内容、噪声词、标点与大小写。"""
    cleaned = html.unescape(text or "")
    cleaned = _BRACKETS.sub(" ", cleaned)
    cleaned = unicodedata.normalize("NFKC", cleaned)
    cleaned = _NOISE.sub(" ", cleaned)
    cleaned = _NON_WORD.sub("", cleaned)
    return cleaned.lower()


def artists_of(text: str) -> set[str]:
    """把 “周杰伦、温岚” 这类多歌手串拆成集合。"""
    parts = [normalize(part) for part in _ARTIST_SPLIT.split(text or "")]
    return {part for part in parts if part}


def title_score(left: str, right: str) -> float:
    """曲名相似度 0~1（互为包含 0.9，字符集重合度兜底）。"""
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    common = sum(1 for char in set(a) if char in b)
    return 0.6 * common / max(len(set(a)), len(set(b)))


def artist_score(left: str, right: str) -> float:
    a, b = artists_of(left), artists_of(right)
    if not a or not b:
        return 0.5          # 一方缺失时不因此否决
    if a & b:
        return 1.0
    for one in a:
        for other in b:
            if one in other or other in one:
                return 0.8
    return 0.0


def duration_score(left_ms: int, right_ms: int) -> float:
    """时长接近程度：完全一致 1.0，差 5 秒内 0.8，差 15 秒内 0.5，否则 0。"""
    if not left_ms or not right_ms:
        return 0.5
    delta = abs(left_ms - right_ms) / 1000
    if delta <= 1:
        return 1.0
    if delta <= 5:
        return 0.8
    if delta <= 15:
        return 0.5
    return 0.0


def match_score(original: RemoteTrack, candidate: RemoteTrack) -> float:
    """综合评分：曲名 60% + 歌手 30% + 时长 10%。"""
    return (
        0.6 * title_score(original.title, candidate.title)
        + 0.3 * artist_score(original.artist, candidate.artist)
        + 0.1 * duration_score(original.duration_ms, candidate.duration_ms)
    )


def best_match(original: RemoteTrack, candidates: list[RemoteTrack],
               threshold: float = 0.62) -> RemoteTrack | None:
    """在候选里挑最像 original 的一首；低于阈值视为没找到。"""
    best: RemoteTrack | None = None
    best_score = 0.0
    for candidate in candidates:
        score = match_score(original, candidate)
        if score > best_score:
            best, best_score = candidate, score
    return best if best is not None and best_score >= threshold else None
