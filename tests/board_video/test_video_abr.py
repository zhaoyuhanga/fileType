"""ABR-lite 测试（方案 3.5 的 `test_abr_*` 两条 + 档位/升档边界）。

纯算法，不联网、不用 GUI：`HlsVariant` 直接构造。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.video.abr import (
    MAX_DOWNGRADES,
    AbrController,
    BandwidthMeter,
    next_higher,
    next_lower,
    pick_variant,
    variant_label,
    variant_rank,
)
from modu_workbench.core.video.hls import HlsVariant


def _variants() -> list[HlsVariant]:
    """538P / 720P / 1080P 三档（贴近实测到的采集站清单）。"""
    return [
        HlsVariant(url="https://cdn/540/index.m3u8", bandwidth=706_000, height=538),
        HlsVariant(url="https://cdn/720/index.m3u8", bandwidth=2_500_000, height=720),
        HlsVariant(url="https://cdn/1080/index.m3u8", bandwidth=5_000_000, height=1080),
    ]


def test_bandwidth_meter_uses_p50_not_mean() -> None:
    """带宽估算取 P50：偶发慢分片不该把中位数拖垮。"""
    meter = BandwidthMeter(window=6)
    for _ in range(5):
        meter.record(bytes_count=1_000_000, seconds=1.0)      # 8 Mbps
    meter.record(bytes_count=1000, seconds=4.0)               # 极慢的坏样本
    assert meter.sample_count == 6
    assert 7_000_000 < meter.estimate_bps < 9_000_000, meter.estimate_bps


def test_bandwidth_meter_ignores_invalid_samples() -> None:
    meter = BandwidthMeter()
    meter.record(0, 1.0)
    meter.record(1000, 0)
    assert meter.sample_count == 0 and meter.estimate_bps == 0


def test_pick_variant_highest_matches_legacy_behaviour() -> None:
    """`policy=highest` 必须与 v1.0.x 的"锁最高"完全一致（回归安全阀）。"""
    picked = pick_variant(_variants(), policy="highest")
    assert picked is not None and picked.height == 1080


def test_pick_variant_by_policy_targets() -> None:
    variants = _variants()
    assert pick_variant(variants, policy="lowest").height == 538
    assert pick_variant(variants, policy="720p").height == 720
    assert pick_variant(variants, policy="1080p").height == 1080
    # 目标高于全部变体 → 取最低（别把只有 1080P 的源判成"不可播"）
    low_only = [HlsVariant(url="u", height=1080)]
    assert pick_variant(low_only, policy="720p").height == 1080


def test_pick_variant_uses_measured_bandwidth() -> None:
    """估算 6 Mbps（×0.7 余量后 4.2 Mbps）→ 选 720P；`highest` 策略仍选 1080P。"""
    meter = BandwidthMeter()
    meter.record(bytes_count=int(6_000_000 / 8), seconds=1.0)     # 6 Mbps
    picked = pick_variant(_variants(), meter, policy="auto")
    assert picked is not None and picked.height == 720
    assert pick_variant(_variants(), meter, policy="highest").height == 1080


def test_measured_bandwidth_can_reach_1080p() -> None:
    meter = BandwidthMeter()
    meter.record(bytes_count=int(12_000_000 / 8), seconds=1.0)    # 12 Mbps
    assert pick_variant(_variants(), meter, policy="auto").height == 1080


def test_pick_variant_without_history_targets_720p() -> None:
    """没有实测历史时默认目标 720P（方案 3.1.5：默认档），而不是 1080P。"""
    assert pick_variant(_variants(), policy="auto").height == 720


def test_pick_variant_empty_returns_none() -> None:
    assert pick_variant([], policy="auto") is None
    assert pick_variant([HlsVariant(url="", height=720)], policy="auto") is None


def test_next_lower_and_higher_walk_one_step() -> None:
    variants = _variants()
    assert next_lower(variants, variants[2]).height == 720
    assert next_lower(variants, variants[0]) is None
    assert next_higher(variants, variants[0]).height == 720
    assert next_higher(variants, variants[2]) is None


def test_abr_downgrade_on_stall_then_exhausted() -> None:
    """注入 2 次 stall → 档位下降两级；已最低档 → exhausted（交给换源）。"""
    controller = AbrController(_variants(), policy="highest", auto_downgrade=True)
    assert controller.current.height == 1080

    first = controller.on_stall()
    assert first.variant.height == 720 and first.downgrades == 1 and not first.exhausted
    second = controller.on_stall()
    assert second.variant.height == 538 and second.downgrades == 2 and not second.exhausted
    third = controller.on_stall()
    assert third.exhausted is True
    assert third.variant.height == 538, "到底后不应再变（换源由上层做）"
    assert controller.downgrades == MAX_DOWNGRADES


def test_abr_downgrade_disabled_is_noop() -> None:
    controller = AbrController(_variants(), policy="highest", auto_downgrade=False)
    decision = controller.on_stall()
    assert decision.downgrades == 0 and controller.current.height == 1080


def test_abr_single_variant_is_exhausted_immediately() -> None:
    controller = AbrController([HlsVariant(url="u", height=1080)], policy="highest")
    decision = controller.on_stall()
    assert decision.exhausted is True and "一个" in decision.reason


def test_abr_upgrade_requires_quiet_time_and_headroom() -> None:
    """升档默认关闭；打开后既要"稳定够久"也要"带宽有余量"。"""
    meter = BandwidthMeter()
    meter.record(bytes_count=int(20_000_000 / 8), seconds=1.0)     # 20 Mbps
    controller = AbrController(_variants(), policy="720p", meter=meter,
                               auto_upgrade=True, quiet_seconds=180.0)
    assert controller.current.height == 720
    # 稳定时间不够 → 不升
    assert controller.on_stable(seconds=10).variant.height == 720
    # 稳定够久 + 余量充足 → 升一档
    assert controller.on_stable(seconds=200).variant.height == 1080


def test_abr_upgrade_blocked_without_headroom() -> None:
    meter = BandwidthMeter()
    meter.record(bytes_count=int(1_000_000 / 8), seconds=1.0)      # 1 Mbps
    controller = AbrController(_variants(), policy="720p", meter=meter,
                               auto_upgrade=True, quiet_seconds=10.0)
    assert controller.has_headroom_for_upgrade() is False
    assert controller.on_stable(seconds=100).variant.height == 720


def test_abr_state_snapshot_for_diagnostics() -> None:
    meter = BandwidthMeter()
    meter.record(bytes_count=1_000_000, seconds=1.0)
    controller = AbrController(_variants(), policy="auto", meter=meter)
    state = controller.state()
    assert state["policy"] == "auto"
    assert state["estimate_mbps"] == pytest.approx(8.0, rel=0.01)
    assert state["samples"] == 1
    assert state["current"] == controller.current_label


def test_variant_helpers_tolerate_duck_typed_objects() -> None:
    """`variant_rank/label` 要能处理不带 `display` 的对象（测试替身/动漫板块复用）。"""

    class _Duck:
        url = "u"
        height = 0
        bandwidth = 1_500_000

    duck = _Duck()
    assert variant_rank(duck) == 1500
    assert variant_label(duck) == "1500P"
