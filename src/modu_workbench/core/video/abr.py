"""ABR-lite：带宽估算 + 变体选择 + 降档/升档（对应方案 3.1.5）。

对标结论里最值得抄的一条是"别再锁最高码率"：慢网下锁最高 = 持续 stall。
但我们也不引入 hls.js 那种复杂 ABR —— 桌面端只有**一个播放器、一条连接**，
三段式足够了：

1. **开播选路**：按"上次实测带宽 × 0.7"挑变体；没有历史时用目标档（默认 720P）；
2. **播放中降档**：看门狗触发 → 降到下一档，最多降 2 次，到底则交给换源；
3. **稳定升档**（默认关）：连续 N 分钟无卡顿且余量 > 1.5× → 升一档。

模块内不做任何网络请求，因此可以纯单测（见 tests/board_video/test_video_abr.py）。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

#: "安全余量"：实测带宽要高于变体码率的这个倍数才认为能稳住
SAFETY_FACTOR = 0.7
#: 升档所需的余量倍数
UPGRADE_MARGIN = 1.5
#: 最多连续降档次数（到底后交给换源）
MAX_DOWNGRADES = 2
#: 升档前需要连续无卡顿的秒数
UPGRADE_QUIET_SECONDS = 180.0

#: 画质策略 → 目标高度（0 = 不设目标，交给带宽/最高）
POLICY_TARGET_HEIGHT: dict[str, int] = {
    "auto": 0,
    "highest": 0,
    "1080p": 1080,
    "720p": 720,
    "lowest": -1,       # 特殊值：直接取最低
}


@dataclass
class BandwidthSample:
    """一次分片下载的观测值。"""

    bytes_count: int
    seconds: float
    at: float = field(default_factory=time.time)

    @property
    def bits_per_second(self) -> float:
        if self.seconds <= 0:
            return 0.0
        return self.bytes_count * 8.0 / self.seconds


class BandwidthMeter:
    """滑动窗口带宽估算（默认最近 20 次，取 P50）。

    为什么用 P50 而不是平均：单个分片偶发慢（源站抖动）会把平均值拖垮，
    中位数更能代表"稳定的可用带宽"，也就更不容易误降档。
    """

    def __init__(self, *, window: int = 20) -> None:
        self.window = max(1, int(window))
        self._samples: list[BandwidthSample] = []
        self._lock = threading.Lock()

    def record(self, bytes_count: int, seconds: float) -> None:
        if bytes_count <= 0 or seconds <= 0:
            return
        with self._lock:
            self._samples.append(BandwidthSample(bytes_count, seconds))
            if len(self._samples) > self.window:
                del self._samples[0: len(self._samples) - self.window]

    def record_response(self, bytes_count: int, started_at: float,
                        finished_at: float | None = None) -> None:
        end = time.time() if finished_at is None else finished_at
        self.record(bytes_count, end - started_at)

    @property
    def sample_count(self) -> int:
        with self._lock:
            return len(self._samples)

    def percentile(self, ratio: float = 0.5) -> float:
        """P50（默认）带宽，单位 bit/s；没有样本返回 0。"""
        with self._lock:
            values = sorted(sample.bits_per_second for sample in self._samples)
        if not values:
            return 0.0
        index = min(len(values) - 1, max(0, int(len(values) * ratio)))
        return values[index]

    @property
    def estimate_bps(self) -> float:
        return self.percentile(0.5)

    def estimated_height(self) -> int:
        """按估算带宽给出"能稳住的高度"。

        换算表（H.264 常见码率，含 `SAFETY_FACTOR` 折算前的原始值）：
        2160p ≈ 25 Mbps、1440p ≈ 14 Mbps、1080p ≈ 6 Mbps、720p ≈ 3 Mbps、
        480p ≈ 1.2 Mbps。乘 0.7 的余量后：4 Mbps→720P、2 Mbps→480P。
        """
        bps = self.estimate_bps * SAFETY_FACTOR
        if bps <= 0:
            return 0
        for height, rate in ((2160, 25_000_000), (1440, 14_000_000), (1080, 6_000_000),
                             (720, 3_000_000), (480, 1_200_000)):
            if bps >= rate:
                return height
        return 360

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()


def variant_rank(variant) -> int:  # noqa: ANN001
    """变体的"档位"数值：优先分辨率高度，其次带宽换算。

    与 `HlsVariant.rank` 同语义；这里放宽到任意带 `height`/`bandwidth`/`rank`
    的对象，便于测试替身与动漫板块复用。
    """
    height = int(getattr(variant, "height", 0) or 0)
    if height:
        return height
    bandwidth = int(getattr(variant, "bandwidth", 0) or 0)
    if bandwidth:
        return bandwidth // 1000
    return int(getattr(variant, "rank", 0) or 0)


def variant_bandwidth(variant) -> float:  # noqa: ANN001
    return float(getattr(variant, "bandwidth", 0) or 0)


def variant_label(variant) -> str:  # noqa: ANN001
    display = getattr(variant, "display", "")
    if display:
        return str(display)
    label = getattr(variant, "label", "")
    if label:
        return str(label)
    height = variant_rank(variant)
    return f"{height}P" if height else ""


def pick_variant(variants: list, meter: BandwidthMeter | None = None, *,
                 policy: str = "auto"):  # noqa: ANN001, ANN201
    """按策略与实测带宽挑一个变体；`variants` 为空返回 None。

    策略语义：
    - `highest`：旧行为（锁最高，也是升级前的默认值）；
    - `lowest`：极弱网；
    - `1080p` / `720p`：目标高度，取"不超过目标里最高的那个"；
    - `auto`：有实测带宽就按带宽选，否则回落到目标档（默认 720P）。
    """
    items = [item for item in (variants or []) if getattr(item, "url", "")]
    if not items:
        return None
    ordered = sorted(items, key=variant_rank)
    key = (policy or "auto").strip().lower()
    if key not in POLICY_TARGET_HEIGHT:
        key = "auto"

    if key == "highest":
        return ordered[-1]
    if key == "lowest":
        return ordered[0]

    target = POLICY_TARGET_HEIGHT[key]
    if target > 0:
        # 显式目标档：取不超过目标的最高一档；都比目标高就取最低（别把 1080P 源锁死）
        below = [item for item in ordered if variant_rank(item) <= target]
        return below[-1] if below else ordered[0]

    estimate = meter.estimated_height() if meter is not None else 0
    wanted = estimate if estimate > 0 else DEFAULT_TARGET_HEIGHT
    below = [item for item in ordered if variant_rank(item) <= wanted]
    return below[-1] if below else ordered[0]


#: `auto` 没有实测带宽时的目标档（见方案 3.1.5：默认 720P，可配）
DEFAULT_TARGET_HEIGHT = 720


def next_lower(variants: list, current) -> object | None:  # noqa: ANN001
    """比当前低一档的变体；已是最低返回 None（调用方据此触发换源）。"""
    items = [item for item in (variants or []) if getattr(item, "url", "")]
    if not items:
        return None
    ordered = sorted(items, key=variant_rank)
    if current is None:
        return ordered[0]
    current_rank = variant_rank(current)
    lower = [item for item in ordered if variant_rank(item) < current_rank]
    return lower[-1] if lower else None


def next_higher(variants: list, current) -> object | None:  # noqa: ANN001
    """比当前高一档的变体；已是最高返回 None。"""
    items = [item for item in (variants or []) if getattr(item, "url", "")]
    if not items:
        return None
    ordered = sorted(items, key=variant_rank)
    if current is None:
        return ordered[-1]
    current_rank = variant_rank(current)
    higher = [item for item in ordered if variant_rank(item) > current_rank]
    return higher[0] if higher else None


@dataclass
class AbrDecision:
    """一次调档决策的结果（供日志/诊断面板展示）。"""

    variant: object | None
    reason: str = ""
    downgrades: int = 0
    exhausted: bool = False


class AbrController:
    """把一个播放会话的"档位状态机"收在一起。

    - `on_stall()`：卡顿 → 降一档；到底返回 `exhausted=True`（交给换源）；
    - `on_stable(seconds)`：稳定够久 → 升一档（仅 `auto_upgrade=True` 时）。
    """

    def __init__(self, variants: list, *, policy: str = "auto",  # noqa: ANN001
                 meter: BandwidthMeter | None = None,
                 auto_downgrade: bool = True, auto_upgrade: bool = False,
                 max_downgrades: int = MAX_DOWNGRADES,
                 quiet_seconds: float = UPGRADE_QUIET_SECONDS) -> None:
        self.variants = list(variants or [])
        self.policy = policy or "auto"
        self.meter = meter or BandwidthMeter()
        self.auto_downgrade = bool(auto_downgrade)
        self.auto_upgrade = bool(auto_upgrade)
        self.max_downgrades = int(max_downgrades)
        self.quiet_seconds = float(quiet_seconds)
        self.current = pick_variant(self.variants, self.meter, policy=self.policy)
        self.downgrades = 0
        self.upgrades = 0
        self._stable_since = time.time()

    # ------------------------------------------------------------------ 事件

    def on_stall(self, *, max_downgrades: int | None = None) -> AbrDecision:
        limit = self.max_downgrades if max_downgrades is None else int(max_downgrades)
        self._stable_since = time.time()
        if not self.auto_downgrade:
            return AbrDecision(self.current, "自动降档已关闭")
        if self.downgrades >= limit:
            return AbrDecision(self.current, "已到最低档", self.downgrades, exhausted=True)
        lower = next_lower(self.variants, self.current)
        if lower is None:
            if len(self.variants) <= 1:
                return AbrDecision(self.current, "只有一个清晰度", self.downgrades,
                                   exhausted=True)
            return AbrDecision(self.current, "已到最低档", self.downgrades, exhausted=True)
        self.current = lower
        self.downgrades += 1
        return AbrDecision(lower, f"卡顿降档（第 {self.downgrades} 次）", self.downgrades)

    def on_stable(self, seconds: float | None = None) -> AbrDecision:
        """稳定够久则升一档（默认关闭）。"""
        elapsed = (time.time() - self._stable_since) if seconds is None else float(seconds)
        if not self.auto_upgrade or elapsed < self.quiet_seconds:
            return AbrDecision(self.current, "未满足升档条件", self.downgrades)
        if not self.has_headroom_for_upgrade():
            return AbrDecision(self.current, "带宽余量不足，不升档", self.downgrades)
        higher = next_higher(self.variants, self.current)
        if higher is None:
            return AbrDecision(self.current, "已是最高档", self.downgrades)
        self.current = higher
        self.upgrades += 1
        self._stable_since = time.time()
        return AbrDecision(higher, f"稳定 {int(elapsed)}s 后升档", self.downgrades)

    def on_recover(self) -> None:
        """一次成功起播/无卡顿播放 → 重置稳定计时。"""
        self._stable_since = time.time()

    # ------------------------------------------------------------------ 状态

    @property
    def current_label(self) -> str:
        return variant_label(self.current) if self.current is not None else ""

    def has_headroom_for_upgrade(self) -> bool:
        """实测带宽是否显著高于当前档（升档的前置条件之一）。"""
        if self.current is None:
            return False
        need = variant_bandwidth(self.current)
        if need <= 0:
            return True
        return self.meter.estimate_bps >= need * UPGRADE_MARGIN

    def state(self) -> dict:
        return {
            "policy": self.policy,
            "current": self.current_label,
            "downgrades": self.downgrades,
            "upgrades": self.upgrades,
            "estimate_mbps": round(self.meter.estimate_bps / 1_000_000, 2),
            "samples": self.meter.sample_count,
        }


__all__ = [
    "AbrController", "AbrDecision", "BandwidthMeter", "BandwidthSample",
    "DEFAULT_TARGET_HEIGHT", "MAX_DOWNGRADES", "POLICY_TARGET_HEIGHT",
    "SAFETY_FACTOR", "UPGRADE_MARGIN", "UPGRADE_QUIET_SECONDS", "next_higher",
    "next_lower", "pick_variant", "variant_bandwidth", "variant_label",
    "variant_rank",
]
