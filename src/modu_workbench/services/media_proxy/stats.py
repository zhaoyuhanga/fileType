"""代理计数器（供 F12 播放诊断面板读取）。

只做**累加**，不做采样落库：诊断面板打开时读一次快照即可。
延迟用固定容量的环形缓冲算 P50，避免统计本身成为开销。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class _LatencyWindow:
    """固定容量的延迟样本环（P50 用）。"""

    capacity: int = 256
    samples: list[float] = field(default_factory=list)
    cursor: int = 0

    def add(self, value: float) -> None:
        if len(self.samples) < self.capacity:
            self.samples.append(value)
            return
        self.samples[self.cursor] = value
        self.cursor = (self.cursor + 1) % self.capacity

    def percentile(self, ratio: float = 0.5) -> float:
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        index = min(len(ordered) - 1, max(0, int(len(ordered) * ratio)))
        return ordered[index]

    def clear(self) -> None:
        self.samples.clear()
        self.cursor = 0


class ProxyStats:
    """线程安全的代理计数器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latency = _LatencyWindow()
        self.reset()

    # ---------- 记录 ----------

    def record_request(self, kind: str = "segment") -> None:
        with self._lock:
            self.requests += 1
            self.by_kind[kind] = self.by_kind.get(kind, 0) + 1

    def record_hit(self, size: int = 0) -> None:
        with self._lock:
            self.hits += 1
            self.hit_bytes += int(size or 0)

    def record_miss(self) -> None:
        with self._lock:
            self.misses += 1

    def record_upstream(self, size: int = 0) -> None:
        with self._lock:
            self.upstream_calls += 1
            self.upstream_bytes += int(size or 0)

    def record_latency(self, seconds: float) -> None:
        with self._lock:
            self._latency.add(max(0.0, float(seconds)))

    def record_error(self, status: int) -> None:
        with self._lock:
            self.errors += 1
            if status == 502:
                self.bad_gateway += 1
            elif status in (403, 429):
                self.throttled += 1

    def record_prefetch(self, count: int = 1) -> None:
        with self._lock:
            self.prefetched += int(count)

    # ---------- 读取 ----------

    def snapshot(self) -> dict:
        with self._lock:
            total = self.requests or 1
            return {
                "requests": self.requests,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3),
                "hit_bytes": self.hit_bytes,
                "upstream_calls": self.upstream_calls,
                "upstream_bytes": self.upstream_bytes,
                "latency_p50_ms": round(self._latency.percentile(0.5) * 1000, 1),
                "latency_p90_ms": round(self._latency.percentile(0.9) * 1000, 1),
                "errors": self.errors,
                "bad_gateway": self.bad_gateway,
                "throttled": self.throttled,
                "prefetched": self.prefetched,
                "by_kind": dict(self.by_kind),
            }

    def reset(self) -> None:
        with self._lock:
            self.requests = 0
            self.hits = 0
            self.misses = 0
            self.hit_bytes = 0
            self.upstream_calls = 0
            self.upstream_bytes = 0
            self.errors = 0
            self.bad_gateway = 0
            self.throttled = 0
            self.prefetched = 0
            self.by_kind: dict[str, int] = {}
            self._latency.clear()

    # 计数器是可累加属性（诊断面板要读原值，不要快照里的派生成分）
    requests: int
    hits: int
    misses: int
    hit_bytes: int
    upstream_calls: int
    upstream_bytes: int
    errors: int
    bad_gateway: int
    throttled: int
    prefetched: int


_stats: ProxyStats | None = None
_stats_lock = threading.Lock()


def get_stats() -> ProxyStats:
    global _stats
    if _stats is None:
        with _stats_lock:
            if _stats is None:
                _stats = ProxyStats()
    return _stats


def reset_stats() -> ProxyStats:
    """仅供测试：清空计数器。"""
    stats = get_stats()
    stats.reset()
    return stats


__all__ = ["ProxyStats", "get_stats", "reset_stats"]
