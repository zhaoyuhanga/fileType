"""曲目可用性判定：把「试听片段 / 伪条目」从完整曲目里分出来。

为什么要单独一层（用户反馈）：酷我音源的搜索结果里混着大量只有 10~40 秒的条目
（同一首歌的"片段/串烧/铃声"版本），点下载还会提示"当前歌曲只能在酷我手机端播放"。
这类条目既不能完整试听也不能完整下载，应该默认隐藏、至少排在完整曲目之后。

判定依据（**纯本地规则，不额外发请求**，实测酷我搜索接口的 PAY / isdownload /
svip_preview / payInfo 字段在片段与完整曲目上完全一致，只有时长能区分）：

1. 音源自己标注的 `extra["preview"]`；
2. 音源本身是"试听型"（`SourceInfo.kind == KIND_PREVIEW`，例如 iTunes 30 秒片段）；
3. 时长明显偏短（低于 `min_full_seconds()`，默认 45 秒）；
4. 标题里带"片段 / 试听 / 铃声 / 伴奏串烧"等字样。

时长阈值可通过 `set_min_full_seconds()` 调整（设置页会写入），并支持环境变量
`MODU_MIN_FULL_SECONDS` 覆盖，便于测试与用户自行放宽。
"""
from __future__ import annotations

import os
import re
from typing import Iterable, List

from .base import KIND_PREVIEW

#: 低于这个秒数就认为不是完整曲目（可被设置/环境变量覆盖）
DEFAULT_MIN_FULL_SECONDS = 45

#: 标题里的片段/铃声等关键词（"demo/伴奏/现场"这类是**正常版本**，不算片段）
_TITLE_HINTS = (
    "片段", "试听", "铃声", "彩铃", "抢先听", "预告", "ringtone", "preview",
)
_TITLE_PATTERN = re.compile("|".join(re.escape(word) for word in _TITLE_HINTS), re.IGNORECASE)

#: 运行时阈值（由设置页或环境变量写入）
_min_full_seconds = DEFAULT_MIN_FULL_SECONDS


def min_full_seconds() -> int:
    """当前生效的「完整曲目最短时长」（秒）。"""
    override = os.environ.get("MODU_MIN_FULL_SECONDS")
    if override:
        try:
            value = int(float(override))
            if value > 0:
                return value
        except ValueError:
            pass
    return _min_full_seconds


def set_min_full_seconds(seconds: int) -> int:
    """设置阈值（秒）；返回实际生效值（夹在 15~600 之间）。"""
    global _min_full_seconds
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        value = DEFAULT_MIN_FULL_SECONDS
    _min_full_seconds = max(15, min(600, value))
    return _min_full_seconds


def preview_reason(track) -> str:  # noqa: ANN001  RemoteTrack
    """判断曲目是否为试听/片段；是则返回原因（可直接展示给用户），否则返回空串。"""
    flagged = bool(getattr(track, "preview", False))
    stored = str(getattr(track, "preview_reason", "") or "")
    if flagged:
        return stored or "音源标注为试听片段"

    extra = getattr(track, "extra", None) or {}
    if isinstance(extra, dict) and (extra.get("preview") or extra.get("is_preview")):
        return "音源标注为试听片段"

    duration_ms = int(getattr(track, "duration_ms", 0) or 0)
    if duration_ms and duration_ms < min_full_seconds() * 1000:
        if is_source_preview(track):
            return "该音源只提供试听片段"
        return f"时长仅 {duration_ms // 1000} 秒（试听/片段条目）"

    title = str(getattr(track, "title", "") or "")
    match = _TITLE_PATTERN.search(title)
    if match:
        return f"标题含「{match.group(0)}」（可能是片段/铃声）"
    return ""


def is_preview(track) -> bool:  # noqa: ANN001  RemoteTrack
    return bool(preview_reason(track))


_SOURCE_KINDS: dict[str, str] = {}


def source_kind_of(track) -> str:  # noqa: ANN001  RemoteTrack
    """查这条结果来自"什么定位"的音源（完整曲目 / 试听片段 / 自由授权…）。

    优先用标注时写入的 `extra["source_kind"]`；没有就去内置音源里按 key 查，
    这样即使调用方直接构造 RemoteTrack 也能得到一致的判定。
    """
    extra = getattr(track, "extra", None) or {}
    if isinstance(extra, dict) and extra.get("source_kind"):
        return str(extra["source_kind"])
    key = str(getattr(track, "source", "") or "")
    if not key:
        return ""
    if key not in _SOURCE_KINDS:
        try:
            from .providers import provider_class

            cls = provider_class(key)
            _SOURCE_KINDS[key] = cls.info.kind if cls is not None else ""
        except Exception:  # noqa: BLE001  查不到就当普通音源
            _SOURCE_KINDS[key] = ""
    return _SOURCE_KINDS[key]


def is_source_preview(track) -> bool:  # noqa: ANN001  RemoteTrack
    """结果来自"试听型音源"（如 iTunes 30 秒片段）——这是音源本身的定位，不是脏数据。"""
    return source_kind_of(track) == KIND_PREVIEW


def should_hide(track) -> bool:  # noqa: ANN001  RemoteTrack
    """默认隐藏策略：只隐藏**混在完整音源里的**片段/伪条目。

    为什么不连试听型音源一起隐藏：iTunes 这类音源本来就是"只给 30 秒片段"，
    名字里也写着"试听"，用户点它时心里有数；真正让人难受的是酷我这种
    "本来是完整曲目音源，却混进来一堆 10~40 秒的同名片段"。
    """
    return bool(getattr(track, "preview", False)) and not is_source_preview(track)


def annotate(track, *, source_kind: str = "") -> bool:  # noqa: ANN001  RemoteTrack
    """就地把判定结果写到 `track.preview` / `track.preview_reason`；返回是否试听。"""
    if source_kind and isinstance(getattr(track, "extra", None), dict):
        track.extra.setdefault("source_kind", source_kind)
    reason = preview_reason(track)
    track.preview = bool(reason)
    track.preview_reason = reason
    return track.preview


def annotate_many(tracks: Iterable, *, source_kind: str = "") -> List:  # noqa: ANN001
    items = list(tracks)
    for track in items:
        annotate(track, source_kind=source_kind)
    return items


def split_by_quality(tracks: Iterable) -> tuple[List, List]:  # noqa: ANN001
    """拆成 (完整曲目, 试听/片段)，各自保持原有顺序。"""
    full: list = []
    previews: list = []
    for track in tracks:
        annotate(track)
        (previews if track.preview else full).append(track)
    return full, previews


def sort_full_first(tracks: Iterable) -> List:  # noqa: ANN001
    """稳定排序：完整曲目在前、试听/片段在后（组内保持原顺序＝音源相关度）。"""
    full, previews = split_by_quality(tracks)
    return full + previews


def describe_hidden(previews: Iterable, limit: int = 3) -> str:  # noqa: ANN001
    """把隐藏的试听条目缩成一句话，便于放在状态栏。"""
    items = list(previews)
    if not items:
        return ""
    names = "、".join(str(getattr(item, "title", "") or "")[:16] for item in items[:limit])
    more = f" 等 {len(items)} 条" if len(items) > limit else ""
    return f"已隐藏 {len(items)} 条试听/片段（{names}{more}）"


__all__ = [
    "DEFAULT_MIN_FULL_SECONDS",
    "annotate",
    "annotate_many",
    "describe_hidden",
    "is_preview",
    "is_source_preview",
    "min_full_seconds",
    "preview_reason",
    "set_min_full_seconds",
    "should_hide",
    "sort_full_first",
    "source_kind_of",
    "split_by_quality",
]
