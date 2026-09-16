"""影视条目匹配：判断两个源的搜索结果是否是「同一部片」。

换源重试的关键一环 —— 主源解析不出地址时，去其他源搜索同一部片，
必须能可靠地认出「这就是同一部」，否则会把用户带到不相干的视频上。

打分维度：标题相似度（主）+ 年份（加分）+ 类型（加分）+ 集数（弱加分）。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable, Sequence

from ..models import RemoteVideo

# 标题里常见的装饰性后缀（画质/语言/版本说明），比较前去掉
_NOISE = re.compile(
    r"(国语|粤语|英语|双语|中字|中文字幕|高清|超清|蓝光|bd|hd|4k|1080p?|720p?|"
    r"抢先版|枪版|tc|ts|hdts|完整版|未删减|导演剪辑版|重制版|修复版)",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[\s\.\-_·:：,，、!！?？'\"“”‘’()（）\[\]【】]+")


def normalize_title(text: str) -> str:
    """归一化标题：去噪词、去标点、统一小写。"""
    value = _NOISE.sub("", text or "")
    value = _PUNCT.sub("", value)
    return value.strip().lower()


def title_similarity(left: str, right: str) -> float:
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # 包含关系（如「流浪地球」vs「流浪地球2」）不应给满分，但要有较高基线
    if a in b or b in a:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        return 0.82 + 0.18 * (len(shorter) / len(longer))
    return SequenceMatcher(None, a, b).ratio()


def match_score(base: RemoteVideo, candidate: RemoteVideo) -> float:
    """0~1 的匹配分。"""
    score = title_similarity(base.title, candidate.title)
    if not score:
        return 0.0

    # 年份：一致加分，冲突减分（同名不同年基本是不同作品）
    if base.year and candidate.year:
        if base.year[:4] == candidate.year[:4]:
            score = min(1.0, score + 0.06)
        else:
            score = max(0.0, score - 0.22)

    # 类型一致加分
    if base.kind and candidate.kind and base.kind == candidate.kind:
        score = min(1.0, score + 0.04)

    # 主演有一处交集说明大概率同片
    if base.actors and candidate.actors:
        base_actors = {a.strip() for a in re.split(r"[,，/、]", base.actors) if a.strip()}
        cand_actors = {a.strip() for a in re.split(r"[,，/、]", candidate.actors) if a.strip()}
        if base_actors & cand_actors:
            score = min(1.0, score + 0.05)

    return round(min(1.0, score), 4)


def best_match(base: RemoteVideo, candidates: Sequence[RemoteVideo],
               threshold: float = 0.62) -> RemoteVideo | None:
    """在候选里挑出最像 base 的一个；都不够像则返回 None。"""
    best: RemoteVideo | None = None
    best_score = threshold
    for candidate in candidates or ():
        if candidate is base:
            continue
        score = match_score(base, candidate)
        if score >= best_score:
            best, best_score = candidate, score
    return best


def best_matches(base: RemoteVideo, candidates: Iterable[RemoteVideo],
                 threshold: float = 0.62) -> list[tuple[RemoteVideo, float]]:
    """按分数从高到低返回全部达标候选（用于「换源选择」界面）。"""
    scored = [(item, match_score(base, item)) for item in candidates or ()]
    scored = [(item, score) for item, score in scored if score >= threshold]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored
