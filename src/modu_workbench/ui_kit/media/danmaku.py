"""弹幕：数据模型 / 解析 / XML 读写 / 性能分级 / 轨道分配 / 渲染层。

分三层，方便复用与测试：

1. **数据与解析**（不依赖 Qt）：`DanmakuItem` / `parse_danmaku_xml` /
   `to_danmaku_xml` / `filter_items` / 去重归一化；
2. **算法**（不依赖 Qt）：性能分级公式、轨道分配器、同屏上限抽样；
3. **渲染**（Qt）：`DanmakuLayer` 把数据画到 `QGraphicsScene` 上（池化复用）。

性能分级（借 LunaTV-EE 的公式，解决"低端机开弹幕卡死"）：
`score = min(cores/4,1)*0.5 + min(memGB/1,1)*0.3 + 0.5*0.2`
→ `>0.7` 高档（同屏 60）、`>0.4` 中档（40）、否则低档（25）。
"""
from __future__ import annotations

import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

#: 三档同屏上限（见 L16）
MAX_LINES_BY_GRADE: dict[str, int] = {"high": 60, "medium": 40, "low": 25}
#: 三档超出上限时的丢弃率
DROP_RATE_BY_GRADE: dict[str, float] = {"high": 0.1, "medium": 0.3, "low": 0.5}
#: 低档的最长弹幕长度（超长文本渲染很贵）
MAX_LENGTH_BY_GRADE: dict[str, int] = {"high": 100, "medium": 60, "low": 30}


@dataclass
class DanmakuItem:
    """一条弹幕。"""

    text: str
    time_ms: int
    mode: int = 1          # 1=滚动 4=底部 5=顶部
    color: int = 0xFFFFFF
    size: int = 25
    source: str = ""

    @property
    def is_scroll(self) -> bool:
        return self.mode == 1

    @property
    def is_top(self) -> bool:
        return self.mode == 5

    @property
    def is_bottom(self) -> bool:
        return self.mode == 4


def grade_for(cores: float, memory_gb: float, *, is_mobile: bool = False) -> str:
    """按 CPU 核数与内存给出性能档位（high/medium/low）。"""
    score = (min(max(cores, 0.0) / 4.0, 1.0) * 0.5
             + min(max(memory_gb, 0.0) / 1.0, 1.0) * 0.3
             + (0.2 if is_mobile else 0.5) * 0.2)
    if score > 0.7:
        return "high"
    if score > 0.4:
        return "medium"
    return "low"


def machine_grade() -> str:
    """探本机性能档位（探测失败按 medium，宁少勿卡）。"""
    try:
        cores = float(os.cpu_count() or 2)
    except Exception:  # noqa: BLE001
        cores = 2.0
    memory_gb = 4.0
    try:  # psutil 不是依赖项，有则更准
        import psutil  # type: ignore

        memory_gb = float(psutil.virtual_memory().total) / (1024 ** 3)
    except Exception:  # noqa: BLE001
        try:  # Windows: 用 ctypes 拿物理内存，零依赖
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                memory_gb = float(status.ullTotalPhys) / (1024 ** 3)
        except Exception:  # noqa: BLE001
            memory_gb = 4.0
    return grade_for(cores, memory_gb)


def max_lines_for(grade: str, configured: int = 0) -> int:
    """同屏上限：用户显式配置优先，否则按档位。"""
    if configured and configured > 0:
        return max(1, min(60, int(configured)))
    return MAX_LINES_BY_GRADE.get(grade, MAX_LINES_BY_GRADE["medium"])


def drop_rate_for(grade: str) -> float:
    return DROP_RATE_BY_GRADE.get(grade, DROP_RATE_BY_GRADE["medium"])


def max_length_for(grade: str) -> int:
    return MAX_LENGTH_BY_GRADE.get(grade, MAX_LENGTH_BY_GRADE["medium"])


def compile_block_rules(words: str) -> list:
    """把屏蔽词编译成规则列表（支持 `关键词` 与 `/正则/`，学 Kazumi）。

    非法正则**不抛错**：忽略该条并继续（用户手写规则出错不该让弹幕全失效）。
    """
    rules: list = []
    for raw in (words or "").split(","):
        token = raw.strip()
        if not token:
            continue
        if len(token) >= 2 and token.startswith("/") and token.endswith("/"):
            try:
                rules.append(re.compile(token[1:-1], re.IGNORECASE))
            except re.error:
                continue
        else:
            rules.append(token.lower())
    return rules


def is_blocked(text: str, rules: list) -> bool:
    lowered = (text or "").lower()
    for rule in rules or []:
        if isinstance(rule, str):
            if rule in lowered:
                return True
        else:
            try:
                if rule.search(text or ""):
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


def filter_items(items: list[DanmakuItem], *, block_words: str = "", grade: str = "medium",
                 dedupe: bool = False) -> list[DanmakuItem]:
    """按屏蔽词、长度与性能档位过滤弹幕（保持时间序）。

    这里**不做随机丢弃** —— 随机丢弹幕会让"同一份弹幕每次看都不一样"，
    观感很差。真正的降载手段是"同屏上限抽样"（`thin_out`）与"低档截断超长文本"。
    """
    rules = compile_block_rules(block_words)
    limit = max_length_for(grade)
    seen: set[str] = set()
    kept: list[DanmakuItem] = []
    for item in items:
        text = (item.text or "").strip()
        if not text or len(text) > limit:
            continue
        if rules and is_blocked(text, rules):
            continue
        if dedupe:
            if text in seen:
                continue
            seen.add(text)
        kept.append(item)
    return kept


def thin_out(items: list[DanmakuItem], max_lines: int, *, window_ms: int = 1000) -> list[DanmakuItem]:
    """同屏上限抽样：按每秒窗口保留 `max_lines` 条（低端机自动变稀）。"""
    if max_lines <= 0:
        return list(items)
    kept: list[DanmakuItem] = []
    bucket: list[DanmakuItem] = []
    bucket_at = None
    for item in sorted(items, key=lambda entry: entry.time_ms):
        if bucket_at is None or item.time_ms - bucket_at < window_ms:
            bucket.append(item)
        else:
            kept.extend(bucket[:max_lines])
            bucket = [item]
            bucket_at = item.time_ms
        if bucket_at is None:
            bucket_at = item.time_ms
    kept.extend(bucket[:max_lines])
    return kept


@dataclass
class _TrackState:
    """一条轨道的占用状态：`free_at_ms` 之后才能放下一条。"""

    free_at_ms: float = 0.0


class TrackAllocator:
    """把弹幕分配到不重叠的轨道上（算法单测用，不依赖 GUI）。

    - 滚动弹幕：占用时长 = 屏幕宽度 / 速度（秒/屏）；
    - 顶部/底部弹幕：占用 = `static_seconds`；
    - 找不到空闲轨道时返回 None（调用方丢弃该条，而不是叠在一起）。
    """

    def __init__(self, *, track_count: int = 12, scroll_seconds: float = 8.0,
                 static_seconds: float = 4.0) -> None:
        self.track_count = max(1, int(track_count))
        self.scroll_seconds = max(1.0, float(scroll_seconds))
        self.static_seconds = max(1.0, float(static_seconds))
        self._tracks = [_TrackState() for _ in range(self.track_count)]

    def reset(self) -> None:
        self._tracks = [_TrackState() for _ in range(self.track_count)]

    def assign(self, item: DanmakuItem) -> int | None:
        """返回轨道序号（0 起）或 None（放不下）。"""
        at = float(item.time_ms)
        # 顶部/底部弹幕从两端向中间占轨道，减少与滚动弹幕打架
        order = (list(range(self.track_count)) if item.is_scroll
                 else (list(range(self.track_count)) if item.is_top
                       else list(reversed(range(self.track_count)))))
        for index in order:
            track = self._tracks[index]
            if track.free_at_ms <= at:
                return index
        return None

    def occupy(self, index: int, item: DanmakuItem) -> None:
        """记录占用：下一条同轨道弹幕必须等到 `free_at_ms`。"""
        if not (0 <= index < self.track_count):
            return
        duration = self.scroll_seconds if item.is_scroll else self.static_seconds
        self._tracks[index].free_at_ms = float(item.time_ms) + duration * 1000.0

    def occupy_until(self, index: int, until_ms: float) -> None:
        if 0 <= index < self.track_count:
            self._tracks[index].free_at_ms = float(until_ms)

    def occupancy(self) -> list[float]:
        return [track.free_at_ms for track in self._tracks]


def visible_at(items: list[DanmakuItem], position_ms: int, *,
               lead_ms: int = 0) -> list[DanmakuItem]:
    """某一时刻应当出现在屏幕上（含未来 `lead_ms` 预排）的弹幕。"""
    at = int(position_ms)
    return [item for item in items if 0 <= at - item.time_ms <= max(0, lead_ms) + 12_000
            or item.time_ms >= at]


def slice_around(items: list[DanmakuItem], position_ms: int, *,
                 window_ms: int = 15_000) -> list[DanmakuItem]:
    """按时间窗口切一段弹幕（预排队列用，避免每帧扫全量）。"""
    low = int(position_ms) - 1_000
    high = int(position_ms) + max(1_000, window_ms)
    return [item for item in items if low <= item.time_ms <= high]


# --------------------------------------------------------------------------- 归一化与去重


_DANMAKU_PUNCT = re.compile(r"[\s!-/:-@\[-`{-~！-／：-＠［-｀｛-～、-〜]+")
_DANMAKU_REPEAT = re.compile(r"(.)\1{2,}")


def normalize_text(text: str) -> str:
    """弹幕归一化（Kazumi 的"归一化去重"思路）：

    全角转半角 → 去空白与标点 → 连续 3 个以上相同字符合并为 3 个 → 小写。
    """
    value = unicodedata.normalize("NFKC", text or "")
    value = _DANMAKU_PUNCT.sub("", value)
    value = _DANMAKU_REPEAT.sub(lambda match: match.group(1) * 3, value)
    return value.casefold()


def merge_duplicates(items: list[DanmakuItem]) -> list[DanmakuItem]:
    """同屏重复弹幕合并显示为 `原文 xN`（**默认关闭**：合并会改变观感）。

    合并只在**同一秒窗口**内做 —— 跨时间的"重复"是正常刷屏，不该被合并。
    """
    buckets: dict[tuple[int, str], list[DanmakuItem]] = {}
    order: list[tuple[int, str]] = []
    for item in items:
        key = (int(item.time_ms // 1000), normalize_text(item.text))
        if not key[1]:
            continue
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(item)
    merged: list[DanmakuItem] = []
    for key in order:
        group = buckets[key]
        first = group[0]
        if len(group) > 1:
            merged.append(DanmakuItem(
                text=f"{first.text} x{len(group)}", time_ms=first.time_ms,
                mode=first.mode, color=first.color, size=first.size,
                source=first.source))
        else:
            merged.append(first)
    merged.sort(key=lambda entry: entry.time_ms)
    return merged


# --------------------------------------------------------------------------- XML 读写


def parse_danmaku_xml(text: str, *, source: str = "local") -> list[DanmakuItem]:
    """解析弹幕 XML（弹弹play / BiliBili / AcFun 都用同一套 `<d p="...">text</d>`）。

    `p` 属性格式：`时间秒,模式,字号,颜色,时间戳,池,用户ID,行ID`（前四项必有）。
    容错：XML 头缺失、单引号、`&` 未转义、属性项不足都能解析。
    """
    items: list[DanmakuItem] = []
    if not (text or "").strip():
        return items
    cleaned = text.lstrip("\ufeff").strip()
    # 先按行解析 `<d p="...">text</d>`：比整树解析更能容忍坏结构
    for match in re.finditer(r"<d\s+p=([\"'])(.*?)\1[^>]*>(.*?)</d>", cleaned, re.DOTALL):
        raw_p = match.group(2)
        body = _unescape(match.group(3))
        parts = raw_p.split(",")
        if len(parts) < 4:
            continue
        try:
            seconds = float(parts[0])
            mode = int(float(parts[1]))
            size = int(float(parts[2]))
            color = int(float(parts[3]))
        except (TypeError, ValueError):
            continue
        if not body.strip():
            continue
        items.append(DanmakuItem(
            text=body, time_ms=int(max(0.0, seconds) * 1000), mode=mode,
            color=color & 0xFFFFFF, size=max(8, min(64, size)), source=source))
    if items:
        items.sort(key=lambda entry: entry.time_ms)
        return items
    # 回退：整树解析（有些导出文件用 CDATA 或自闭合写法）
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        return items
    for node in root.iter("d"):
        raw_p = node.get("p") or ""
        body = (node.text or "").strip()
        parts = raw_p.split(",")
        if len(parts) < 4 or not body:
            continue
        try:
            items.append(DanmakuItem(
                text=body, time_ms=int(float(parts[0]) * 1000),
                mode=int(float(parts[1])), size=int(float(parts[2])),
                color=int(float(parts[3])) & 0xFFFFFF, source=source))
        except (TypeError, ValueError):
            continue
    items.sort(key=lambda entry: entry.time_ms)
    return items


def _unescape(text: str) -> str:
    value = (text or "").replace("<br>", "\n").replace("<br/>", "\n")
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                         ("&apos;", "'"), ("&#39;", "'"), ("&amp;", "&")):
        value = value.replace(entity, char)
    return value.strip()


def _escape(text: str) -> str:
    return ((text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def to_danmaku_xml(items: list[DanmakuItem], *, chat_id: int = 0) -> str:
    """导出为标准弹幕 XML（可与主流播放器互导）。"""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<i>",
             "  <chatserver>modu-workbench</chatserver>",
             f"  <chatid>{int(chat_id)}</chatid>",
             "  <mission>0</mission>", "  <maxlimit>99999</maxlimit>"]
    for item in sorted(items, key=lambda entry: entry.time_ms):
        stamp = f"{item.time_ms / 1000:.2f}"
        payload = (f"{stamp},{int(item.mode)},{int(item.size)},{int(item.color)},"
                   "0,0,0,0")
        lines.append(f'  <d p="{payload}">{_escape(item.text)}</d>')
    lines.append("</i>")
    return "\n".join(lines) + "\n"


def load_danmaku_file(path: str | Path) -> list[DanmakuItem]:
    """从磁盘读取弹幕（容错编码；失败返回空列表）。"""
    target = Path(path)
    if not target.is_file():
        return []
    raw = target.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk", "big5"):
        try:
            return parse_danmaku_xml(raw.decode(encoding))
        except (UnicodeDecodeError, LookupError):
            continue
    return parse_danmaku_xml(raw.decode("utf-8", "replace"))


def save_danmaku_file(path: str | Path, items: list[DanmakuItem],
                      *, chat_id: int = 0) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_danmaku_xml(items, chat_id=chat_id), encoding="utf-8")
    return target


# --------------------------------------------------------------------------- 渲染层（Qt）


class DanmakuLayer:
    """把弹幕画到 `QGraphicsScene` 上（池化复用 item，避免每帧 new/GC 掉帧）。

    与 `player.py` 的轻量叠加层相比，这一层多了：轨道分配、按模式分轨、
    透明度/速度设置、同屏上限。**只在 `QGraphicsVideoItem` 路径下可用**
    （`QVideoWidget` 是原生窗口，叠不上东西）。
    """

    def __init__(self, surface, *, opacity: int = 80, scroll_seconds: float = 8.0,  # noqa: ANN001
                 grade: str = "", max_lines: int = 0, static_seconds: float = 4.0) -> None:
        self.surface = surface
        self.opacity = max(20, min(100, int(opacity))) / 100.0
        self.grade = grade or machine_grade()
        self.max_lines = max_lines or max_lines_for(self.grade)
        self.allocator = TrackAllocator(scroll_seconds=scroll_seconds,
                                        static_seconds=static_seconds)
        self._pool: list = []
        self._in_use: list = []
        self._items: list[DanmakuItem] = []
        self.enabled = False
        self.dropped = 0

    # ------------------------------------------------------------------ 数据

    def set_items(self, items: list[DanmakuItem], *, block_words: str = "",
                  dedupe: bool = False) -> None:
        filtered = filter_items(items, block_words=block_words, grade=self.grade,
                                dedupe=False)
        if dedupe:
            filtered = merge_duplicates(filtered)
        self._items = filtered
        self.allocator.reset()

    @property
    def count(self) -> int:
        return len(self._items)

    # ------------------------------------------------------------------ 渲染

    def clear(self) -> None:
        for item in self._in_use:
            item.setVisible(False)
            self._pool.append(item)
        self._in_use.clear()

    def render(self, position_ms: int, *, window_ms: int = 1500) -> int:
        """按当前进度渲染一帧；返回本帧实际画出的条数。

        每帧先清空再重画（池化 item 复用，无 new），因此**同一帧内**不会重复；
        轨道分配器按"上一条的离开时间"决定是否放得下，放不下就计入 `dropped`。
        """
        if not self.enabled or not self._items:
            return 0
        self.clear()
        width, height = self._viewport()
        track_count = max(4, min(24, int(height // 30) or 12))
        self.allocator.track_count = track_count
        drawn = 0
        for entry in slice_around(self._items, position_ms, window_ms=window_ms):
            if drawn >= self.max_lines:
                self.dropped += 1
                continue
            track = self.allocator.assign(entry)
            if track is None:
                self.dropped += 1
                continue
            self.allocator.occupy(track, entry)
            item = self._acquire()
            self._paint(item, entry, track, width, height)
            drawn += 1
        return drawn

    def _viewport(self) -> tuple[float, float]:
        viewport = getattr(self.surface, "viewport_size", None)
        if callable(viewport):
            return viewport()
        return (1280.0, 720.0)

    def _acquire(self):  # noqa: ANN202
        from PySide6.QtGui import QBrush, QColor, QPen
        from PySide6.QtWidgets import QGraphicsSimpleTextItem

        if self._pool:
            item = self._pool.pop()
        else:
            item = QGraphicsSimpleTextItem()
            item.setPen(QPen(QColor(0, 0, 0, 200), 1.5))
            # 描边后再填白色：黑边 + 白字，任何画面上都看得清
            item.setBrush(QBrush(QColor("#ffffff")))
            self.surface.scene.addItem(item)
            item.setVisible(False)
        item.setOpacity(self.opacity)
        self._in_use.append(item)
        return item

    def _paint(self, item, entry: DanmakuItem, track: int,  # noqa: ANN001
               width: float, height: float) -> None:
        from PySide6.QtGui import QBrush, QColor, QFont

        item.setText(entry.text)
        font = QFont()
        font.setPointSize(max(8, min(40, int((entry.size or 25) * height / 720))))
        item.setFont(font)
        item.setBrush(QBrush(QColor(f"#{entry.color & 0xFFFFFF:06x}")))
        rect = item.boundingRect()
        top = 8 + track * 30
        if entry.is_bottom:
            item.setPos((width - rect.width()) / 2, height - rect.height() - 12 - track * 30)
        elif entry.is_top:
            item.setPos((width - rect.width()) / 2, top)
        else:
            item.setPos(width, top)
        item.setVisible(True)


__all__ = [
    "DROP_RATE_BY_GRADE", "DanmakuItem", "DanmakuLayer", "MAX_LENGTH_BY_GRADE",
    "MAX_LINES_BY_GRADE", "TrackAllocator", "compile_block_rules", "drop_rate_for",
    "filter_items", "grade_for", "is_blocked", "load_danmaku_file", "machine_grade",
    "max_length_for", "max_lines_for", "merge_duplicates", "normalize_text",
    "parse_danmaku_xml", "save_danmaku_file", "slice_around", "thin_out",
    "to_danmaku_xml", "visible_at",
]
