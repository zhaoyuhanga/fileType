"""视频源注册表：统一搜索、健康度（熔断）、跨源兜底解析与配置持久化。

设计要点（对齐 listen1 / LibreTV / MusicFree 的多源思路，全部本地 Python 实现）：
- **多源**：任意源实现 `VideoSource` 即可注册，默认顺序见 `DEFAULT_PROVIDER_ORDER`；
- **重试**：HTTP 层带退避重试，注册表层做「换源重试」；
- **熔断**：某源连续失败会被临时降级，聚合搜索自动跳过，避免每次都在坏源上耗时；
- **跨源兜底**：某源解析不出播放地址（接口变更 / 线路失效）时，
  自动到其他源搜索同一部片继续尝试，用户无感；
- **可扩展**：整个影视板块只依赖本类的公开方法，新增源不需要改任何业务代码。
"""
from __future__ import annotations

import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Callable, Iterable, List

from ..models import Episode, Quality, RemoteVideo
from .base import ResolvedPlay, SourceHealth, SourceInfo, VideoSource
from .http import SourceError, guess_referer
from .matcher import best_match
from .probe import (
    DEFAULT_PROBE_KEYWORD,
    PROBE_TIMEOUT,
    ProbeResult,
    probe_source,
)
from .providers import DEFAULT_PROVIDER_ORDER, build_default_providers

SETTING_ENABLED = "sources/enabled"
SETTING_ORDER = "sources/priority"
SETTING_SOURCE_URLS = "sources/urls"      # 用户覆盖的采集接口地址 {key: url}
SETTING_VERSION = "sources/version"       # 源清单版本（升级时迁移用户的启用/排序设置）

# 测速参数（计划 3.3.5：并发 3、最多测 20 个源 = 权重最高的前 5 + 剩余随机 15）
SPEED_TEST_CONCURRENCY = 3
SPEED_TEST_MAX_SOURCES = 20
SPEED_TEST_TOP_SOURCES = 5

# 聚合搜索的单源时间预算与并发度。
#
# 真实故障（本机实测）：`archive.org` 完全不可达，它自己的客户端要"15s × 3 次 + 退避"
# 才会放弃；而原来的 `search_all` 是**顺序遍历且没有单源超时**，整条搜索被它拖住约 50 秒，
# 界面上表现为一直"搜索中"、一条结果都不出。用户看到的就是"卡死了"。
#
# 9 秒的依据：13 个内置源里 11 个实测 ≤ 5 秒返回（含最慢的采集站 4.9 秒），
# 所以 9 秒足够覆盖正常抖动，又不会让死源拖住整体。
DEFAULT_SEARCH_TIMEOUT = 9.0
DEFAULT_SEARCH_WORKERS = 8

# 源清单版本：改动内置源（下线失效源、新增可用源）时递增。
# 递增后，旧的「启用集合/排序」不再直接套用 —— 否则新加的源会默认处于停用状态，
# 用户会以为「源没了」。用户自己填的接口地址（sources/urls）始终保留。
SOURCES_VERSION = 3

# 熔断参数：连续失败 3 次 → 降级 120 秒（视频源抖动比音乐源更常见）
DEGRADE_AFTER = 3
DEGRADE_SECONDS = 120.0

# 权重刻度：顺序第 1 位 = 100 分，之后每往后一位减 `WEIGHT_MAX / (n-1)`，
# 最后一位正好 0 分。借打分公式里 `weight` 的 0–100 语义
# （`1 + (weight - 50) * 0.005`），50 分对应"排在正中间"的源。
WEIGHT_MAX = 100.0


@dataclass
class SpeedResult:
    """批量测速里单个源的结果（`measure_sources` 的返回值）。

    字段与 `probe.ProbeResult` 对齐，另加权重与最终评分，方便界面一次拿全。
    `error` 为空表示延迟和速度都测到了；非空也可能是"部分成功"（比如只测到延迟）。
    """

    key: str
    label: str = ""
    ok: bool = False
    latency_ms: float = 0.0
    mbit_per_s: float = 0.0
    height: int = 0
    resolution: str = ""
    weight: float = 50.0
    score: float = 0.0
    error: str = ""

    @property
    def speed_mbytes(self) -> float:
        """吞吐（MB/s），界面展示用。"""
        return self.mbit_per_s / 8.0

    def as_text(self) -> str:
        """一行摘要：延迟 / 速度 / 评分（源设置的「测速」列）。"""
        latency = f"{self.latency_ms:.0f} ms" if self.latency_ms > 0 else "延迟 --"
        speed = f"{self.speed_mbytes:.1f} MB/s" if self.mbit_per_s > 0 else "速度 --"
        return f"{latency} / {speed} / 评分 {self.score:.2f}"


def _safe_partial(callback, done: int, total: int, videos: list, errors: list) -> None:  # noqa: ANN001
    """回调 `on_partial`；回调自己炸了不能影响搜索（进度提示是锦上添花）。"""
    try:
        callback(done, total, list(videos), list(errors))
    except Exception:  # noqa: BLE001
        pass


class VideoRegistry:
    """多视频源注册表（应用级单例，见 services.app_context.video_registry）。"""

    def __init__(self, providers: Iterable[VideoSource] | None = None,
                 *, rng: random.Random | None = None):
        self._providers: dict[str, VideoSource] = {}
        self._order: list[str] = []
        self._health: dict[str, SourceHealth] = {}
        self._health_lock = threading.Lock()
        # 「按测速结果排序」开关的运行时状态：由设置页/源设置对话框同步进来
        # （核心层不读 QSettings，保持可离线测试）。默认关，行为与旧版完全一致。
        self._sort_by_speed = False
        # 测速要"从剩余源里随机取 15 个"：用实例级 Random，测试可注入固定种子，
        # 生产上也不去动全局 random（别影响应用里其他地方的可复现性）。
        self._rng = rng or random.Random()
        if providers is None:
            # 内置源：默认按 DEFAULT_PROVIDER_ORDER 启用（磁盘配置会覆盖）
            self._enabled: set[str] = set(DEFAULT_PROVIDER_ORDER)
            items = build_default_providers()
        else:
            # 外部注入（测试替身 / 未来多源包）：默认全部启用，
            # 否则自定义源的 key 不在内置清单里，会被静默过滤掉
            items = list(providers)
            self._enabled = {provider.key for provider in items}
        for provider in items:
            self.register(provider)

    # ---------- 注册与配置 ----------

    def register(self, provider: VideoSource, *, position: int | None = None) -> None:
        key = provider.key
        self._providers[key] = provider
        self._health.setdefault(key, SourceHealth())
        if key not in self._order:
            if position is None:
                self._order.append(key)
            else:
                self._order.insert(max(0, min(position, len(self._order))), key)

    @property
    def order(self) -> list[str]:
        return [key for key in self._order if key in self._providers]

    def ordered_keys(self, *, by_speed: bool = False,
                     enabled_only: bool = True) -> list[str]:
        """源的最终顺序：默认**原样返回用户顺序**，`by_speed=True` 时按评分重排。

        - 用户顺序 = 「源设置」里上下移动出来的顺序（`SETTING_ORDER`）；
        - 评分 = `SourceHealth.health_score()`（分辨率 40% + 速度 40% + 延迟 20%，
          再乘权重加成），**只在测过速的源之间有意义**；
        - 排序用稳定排序：分数相同的源保持用户顺序，结果可预期（不会每次刷新都跳）。

        为什么默认不按测速排序（`video/sort_by_speed` 默认 false，见计划 3.3.5 末条）：
        测速结果会随网络波动，按它排序会让"搜索先后 / 去重保留谁"悄悄改变；
        用户明确打开这个开关才承担这个不确定性。搜索与解析在没有显式要求时
        必须继续用用户顺序。
        """
        keys = self.order
        if enabled_only:
            keys = [key for key in keys if key in self._enabled]
        if not by_speed:
            return list(keys)
        weights = self._weights(keys)
        scores = {key: self.health(key).health_score(weight=weights[key]) for key in keys}
        return sorted(keys, key=lambda key: scores[key], reverse=True)

    def _weights(self, keys: Iterable[str] | None = None) -> dict[str, float]:
        """把「用户顺序」折成 0–100 权重：第 1 位 100 分，最后一位 0 分，等步长递减。

        注册表本身没有"权重"概念（计划里的 `weight` 来自 LunaTV-EE 的源权重配置），
        这里直接用顺序推导：让排在前面的源在打分里拿到最多 1.25 倍、最后一名拿 0.75 倍。
        只有一个源时给它中性值 50（不因"只有它"而被加成或惩罚）。
        """
        keys = list(keys if keys is not None else self.order)
        if not keys:
            return {}
        if len(keys) == 1:
            return {keys[0]: WEIGHT_MAX / 2}
        step = WEIGHT_MAX / (len(keys) - 1)
        return {key: WEIGHT_MAX - index * step for index, key in enumerate(keys)}

    def weight_of(self, key: str) -> float:
        """某个源的权重（0–100），界面与测试用。"""
        return self._weights().get(key, WEIGHT_MAX / 2)

    def score_of(self, key: str) -> float:
        """某个源当前的评分（未测速时是"只有延迟项为 0 的保守分"）。"""
        return self.health(key).health_score(weight=self.weight_of(key))

    def providers(self, *, enabled_only: bool = True) -> list[VideoSource]:
        items = [self._providers[key] for key in self.order if key in self._providers]
        if enabled_only:
            items = [item for item in items if item.key in self._enabled]
        return items

    def get(self, key: str) -> VideoSource | None:
        return self._providers.get(key)

    def require(self, key: str) -> VideoSource:
        provider = self._providers.get(key)
        if provider is None:
            raise SourceError(f"未知视频源：{key}")
        return provider

    def is_enabled(self, key: str) -> bool:
        return key in self._enabled

    def set_enabled(self, key: str, enabled: bool) -> None:
        if enabled:
            self._enabled.add(key)
        else:
            self._enabled.discard(key)

    def set_enabled_keys(self, keys: Iterable[str]) -> None:
        """整体替换启用集合（「恢复默认」用）。"""
        self._enabled = {key for key in keys if key in self._providers}

    def set_order(self, keys: Iterable[str]) -> None:
        keys = [key for key in keys if key in self._providers]
        self._order = keys + [key for key in self._order if key not in keys]

    def infos(self, *, enabled_only: bool = False) -> list[SourceInfo]:
        return [provider.info for provider in self.providers(enabled_only=enabled_only)]

    def health(self, key: str) -> SourceHealth:
        return self._health.setdefault(key, SourceHealth())

    def status_text(self, key: str) -> str:
        provider = self._providers.get(key)
        if provider is None:
            return "未知"
        if not self.is_enabled(key):
            return "已停用"
        if not provider.available():
            return "需配置"
        return self.health(key).as_text(time.time())

    # ---------- 健康度（熔断） ----------

    def record_success(self, key: str, latency: float | None = None) -> None:
        """记一次成功；给了 `latency`（秒）就顺手进延迟滑动窗口（见 3.3.5 第一条）。"""
        health = self.health(key)
        with self._health_lock:
            health.note_success(latency)

    def record_failure(self, key: str, error: Exception | str = "") -> None:
        health = self.health(key)
        with self._health_lock:
            health.note_failure(error)
            if health.consecutive_failures >= DEGRADE_AFTER:
                health.degraded_until = time.time() + DEGRADE_SECONDS

    def degraded_keys(self) -> list[str]:
        now = time.time()
        return [key for key in self.order if self.health(key).is_degraded(now)]

    def reset_health(self) -> None:
        for key in self.order:
            self._health[key] = SourceHealth()

    # ---------- 排序开关（`video/sort_by_speed`） ----------

    @property
    def sort_by_speed(self) -> bool:
        """是否按测速评分排序（对应设置键 `video/sort_by_speed`，默认 False）。

        核心层不直接读 QSettings（那会让离线测试依赖 Qt 配置），
        由设置页/源设置对话框把用户选择同步到这里；默认 False 保证
        「搜索先后、去重保留谁、换源顺序」与旧版本完全一致。
        """
        return self._sort_by_speed

    @sort_by_speed.setter
    def sort_by_speed(self, enabled: bool) -> None:
        self._sort_by_speed = bool(enabled)

    def set_sort_by_speed(self, enabled: bool) -> None:
        """`sort_by_speed` 的方法写法（界面里 `.connect` 用着更顺手）。"""
        self.sort_by_speed = enabled

    # ---------- 测速（v3-e，计划 3.3.5） ----------

    def select_speed_test_keys(self, keys: Iterable[str] | None = None, *,
                               limit: int = SPEED_TEST_MAX_SOURCES,
                               top: int = SPEED_TEST_TOP_SOURCES) -> list[str]:
        """挑出要测的源：**用户顺序最靠前的 `top` 个 + 其余随机补足 `limit` 个**。

        这是计划里写的 LunaTV-EE 口径（权重最高前 5 + 剩余随机 15）。
        随机用的是实例级 RNG（构造时可注入固定种子），但注意**每调用一次 RNG 流就往前走**：
        同一实例连续两次调用，"随机补足的那 15 个"可能不同；前 5 个永远不变。
        返回顺序已按用户顺序排好（便于并发结果落库时保持可读顺序）。
        """
        ordered = self.ordered_keys(enabled_only=False)
        if keys is not None:
            wanted = [key for key in keys if key in self._providers]
            # 显式指定时按用户顺序归一，避免调用方给的顺序把优先级打乱
            ordered = [key for key in ordered if key in set(wanted)]
        limit = max(0, int(limit))
        if limit == 0 or not ordered:
            return []
        top = max(0, min(int(top), limit))
        chosen = ordered[:top]
        rest = ordered[top:]
        room = limit - len(chosen)
        if room > 0 and rest:
            chosen.extend(self._rng.sample(rest, min(room, len(rest))))
        picked = set(chosen)
        return [key for key in ordered if key in picked]

    def measure_sources(self, keys: Iterable[str] | None = None, *,
                        concurrency: int = SPEED_TEST_CONCURRENCY,
                        limit: int = SPEED_TEST_MAX_SOURCES,
                        keyword: str = DEFAULT_PROBE_KEYWORD,
                        timeout: float = PROBE_TIMEOUT,
                        cancel=None, on_progress: Callable | None = None) -> List[SpeedResult]:
        """批量测速：并发 `concurrency`（默认 3）、最多 `limit`（默认 20）个源。

        结果同时写进 `SourceHealth`（延迟进滑动窗口、吞吐与分辨率进测速字段），
        因此之后的 `ordered_keys(by_speed=True)` 与源设置界面都能直接用。

        - 跑在 `ThreadPoolExecutor` 里，每个源的失败只体现在它自己的 `SpeedResult.error`，
          **一个坏源不会让整批失败**，也从 `measure_sources` 里抛不出去；
        - 任务是"取一个跑一个"（不是一次性全提交）：取消一旦发生，还没开工的源根本不会
          发请求，被跳过的源以 `error="已取消"` 返回，UI 能交代清楚"哪些没测"；
        - `on_progress(result, done, total)` 每完成一个源回调一次，回调抛异常不影响其他源；
        - 不传 `keys` 时按 `select_speed_test_keys()` 的"前 5 + 随机 15"挑选。
        """
        picked = self.select_speed_test_keys(keys, limit=limit, top=SPEED_TEST_TOP_SOURCES)
        results: list[SpeedResult] = []
        if not picked:
            return results
        index = {key: position for position, key in enumerate(self.order)}
        total = len(picked)
        done = 0
        workers = max(1, min(int(concurrency), total))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="video-speed") as pool:
            for key in picked:
                if cancel is not None and cancel.is_set():
                    skipped = SpeedResult(key=key, label=self._label(key), error="已取消")
                    results.append(skipped)
                    if on_progress is not None:
                        self._notify(on_progress, skipped, len(results), total)
                    continue
                provider = self._providers.get(key)
                if provider is None:
                    continue
                try:
                    result = pool.submit(self._probe_one, key, provider, keyword,
                                         timeout, cancel).result()
                except Exception as error:  # noqa: BLE001  兜底：绝不让一个源炸掉整批
                    result = SpeedResult(key=key, label=self._label(key),
                                         error=str(error)[:200])
                results.append(result)
                done += 1
                if on_progress is not None:
                    self._notify(on_progress, result, done, total)
        # 按用户顺序返回（完成的先后是随机的，界面与测试都需要稳定顺序）
        results.sort(key=lambda item: index.get(item.key, len(index)))
        return results

    def _probe_one(self, key: str, provider: VideoSource, keyword: str,  # noqa: ANN001
                   timeout: float, cancel) -> SpeedResult:  # noqa: ANN001
        """单个源的测速 + 落库（在池里的工作线程上跑，所以这里可以自由阻塞）。"""
        probe: ProbeResult = probe_source(
            provider, keyword, http=getattr(provider, "http", None),
            timeout=timeout, cancel=cancel,
        )
        weight = self.weight_of(key)
        health = self.health(key)
        with self._health_lock:
            if probe.latency_ms > 0:
                health.note_success(probe.latency_ms / 1000.0,
                                    speed_mbps=probe.mbit_per_s, height=probe.height)
            elif probe.mbit_per_s > 0:
                # 有吞吐但没拿到延迟样本（理论上不会发生）：仍算成功，只是不进延迟窗口
                health.note_success(speed_mbps=probe.mbit_per_s, height=probe.height)
            else:
                health.note_failure(probe.error or "测速失败")
                if health.consecutive_failures >= DEGRADE_AFTER:
                    health.degraded_until = time.time() + DEGRADE_SECONDS
            health.measured_height = probe.height or health.measured_height
            score = health.health_score(weight=weight)
        return SpeedResult(
            key=key, label=self._label(key), ok=probe.ok,
            latency_ms=probe.latency_ms, mbit_per_s=probe.mbit_per_s,
            height=probe.height, resolution=probe.resolution,
            weight=weight, score=score, error=probe.error,
        )

    @staticmethod
    def _notify(on_progress: Callable, result: SpeedResult, done: int, total: int) -> None:
        """进度回调：`on_progress(result, done, total)`；回调异常不影响测速本身。"""
        try:
            on_progress(result, done, total)
        except Exception:  # noqa: BLE001  界面槽函数出问题不该毁掉后台任务
            pass

    # ---------- 搜索 ----------

    def search_one(self, key: str, keyword: str, kind: str = "all", limit: int = 30,
                   page: int = 1) -> List[RemoteVideo]:
        provider = self.require(key)
        provider.require_available()
        started = time.monotonic()
        try:
            videos = provider.search(keyword, kind=kind, limit=limit, page=page)
        except SourceError as error:
            self.record_failure(key, error)
            raise
        # 耗时顺手记进延迟窗口（3.3.5 第一条）：不额外发探针请求，成本为零
        self.record_success(key, latency=time.monotonic() - started)
        return videos

    def search_all(self, keyword: str, kind: str = "all", limit: int = 20,
                   sources: Iterable[str] | None = None,
                   include_degraded: bool = False,
                   page: int = 1,
                   per_source_timeout: float | None = DEFAULT_SEARCH_TIMEOUT,
                   on_partial=None, workers: int | None = None,
                   ) -> tuple[List[RemoteVideo], List[str]]:
        """跨源搜索：返回 (结果, 错误说明列表)。

        默认跳过被熔断的源；显式指定 sources 时按指定来（忽略熔断）。
        结果按「标题+年份」去重、**保留在用户源顺序里更靠前的那一条**。

        **并发 + 每个源独立时间预算**（默认 `DEFAULT_SEARCH_TIMEOUT`）。

        为什么非改不可（真实故障）：原实现是**顺序遍历**每个源且没有单源超时。
        本机实测 `archive.org` 完全不可达，它自己的客户端要"15s × 3 次 + 退避"，
        于是整条搜索被它一个人拖住约 50 秒 —— 界面上就是一直"搜索中"、什么都不出。
        现在：并发跑 + 单源按时放弃 + 每完成一个源就通过 `on_partial` 回报，
        慢源/死源再也拖不住已经拿到结果的源。

        `on_partial(done, total, collected, errors)` 在每个源结束后各调一次
        （**从工作线程调用**，调用方自己负责切回 UI 线程）。

        顺序语义保持不变：结果按 `chosen` 的源顺序归并，而不是按完成先后，
        这样 `sort_by_speed` 与"用户顺序优先去重"的行为与改前完全一致。
        """
        errors: List[str] = []
        now = time.time()
        if sources is None:
            # 直链类源只在「关键词本身就是一个地址」时才参与聚合，
            # 否则每次搜索都会多出一条「请输入 http(s):// 开头的地址」的噪音错误
            keyword_is_url = (keyword or "").strip().lower().startswith(("http://", "https://"))
            chosen = [key for key in self.ordered_keys(by_speed=self._sort_by_speed)
                      if (self._providers[key].supports_keyword_search() or keyword_is_url)
                      and (include_degraded or not self.health(key).is_degraded(now))]
        else:
            chosen = [key for key in sources if key in self._providers]

        # 可用性筛选放在开线程之前（开线程了才发现"这个源不可用"很浪费）
        runnable: list[tuple[int, str, object]] = []
        for position, key in enumerate(chosen):
            provider = self._providers.get(key)
            if provider is None or not provider.available():
                continue
            runnable.append((position, key, provider))
        if not runnable:
            return [], []

        budget = 0.0 if per_source_timeout is None else float(per_source_timeout)
        pool_size = workers or min(DEFAULT_SEARCH_WORKERS, len(runnable))
        slot: dict[str, tuple[list, str | None]] = {}
        done = 0
        timed_out: list[str] = []

        def _run(key: str, provider) -> tuple[list, str | None]:  # noqa: ANN001
            started = time.monotonic()
            try:
                found = provider.search(keyword, kind=kind, limit=limit, page=page)
            except SourceError as error:
                self.record_failure(key, error)
                return [], f"{provider.label}：{error}"
            except Exception as error:  # noqa: BLE001  单个源异常不能拖垮聚合搜索
                self.record_failure(key, error)
                return [], f"{provider.label}：{error}"
            self.record_success(key, latency=time.monotonic() - started)
            return list(found or []), None

        # 注意：**不要用 `with ThreadPoolExecutor(...)`**。
        # `__exit__` 会 `shutdown(wait=True)` —— 那个卡住的源（实测 archive.org 要 ~50 秒
        # 才放弃）会把我们已经算好的结果**再拖回去**。实测：9 秒预算判定完成、
        # 但 `with` 退出时又等了 84 秒，总耗时 93.8s，用户端依旧是"卡住"。
        # 这里改成显式 shutdown(wait=False)：线程自己在后台收尾，调用方立刻拿到结果。
        pool = ThreadPoolExecutor(max_workers=max(1, pool_size),
                                  thread_name_prefix="video-search")
        futures = {pool.submit(_run, key, provider): key
                   for _position, key, provider in runnable}
        try:
            for future in as_completed(futures, timeout=budget if budget > 0 else None):
                key = futures[future]
                try:
                    slot[key] = future.result()
                except Exception as error:  # noqa: BLE001  理论到不了；防御性兜底
                    label = getattr(self._providers.get(key), "label", key)
                    slot[key] = ([], f"{label}：{error}")
                done += 1
                if on_partial is not None:
                    _safe_partial(on_partial, done, len(runnable),
                                  self._merge(slot, runnable), self._partial_errors(slot))
        except FuturesTimeoutError:
            # 超预算：还没回来的源记一次失败并**直接放弃**（不 join，避免又被拖住）
            for future, key in futures.items():
                if key in slot:
                    continue
                timed_out.append(key)
                label = getattr(self._providers.get(key), "label", key)
                message = (f"{label}：搜索超过 {budget:g} 秒仍未返回，已跳过"
                           "（该源可能不可达；可在「源设置」里关掉或换地址）")
                slot[key] = ([], message)
                self.record_failure(key, message)
                done += 1
            if on_partial is not None:
                _safe_partial(on_partial, done, len(runnable),
                              self._merge(slot, runnable), self._partial_errors(slot))
        finally:
            # 不等待未完成的源；同时取消还没开始跑的（队列里排队的那些）
            pool.shutdown(wait=False, cancel_futures=True)

        for _position, key, _provider in runnable:
            found, error = slot.get(key, ([], None))
            if error:
                errors.append(error)
        return self._merge(slot, runnable), errors

    @staticmethod
    def _partial_errors(slot: dict) -> List[str]:
        return [error for _found, error in slot.values() if error]

    @staticmethod
    def _merge(slot: dict, runnable: list) -> List[RemoteVideo]:
        """按**源顺序**归并已完成的结果（与顺序遍历的语义一致）。"""
        merged: List[RemoteVideo] = []
        seen: set[str] = set()
        for _position, key, _provider in runnable:
            found, _error = slot.get(key, ([], None))
            for video in found:
                fingerprint = f"{video.title.strip().lower()}|{video.year}"
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                merged.append(video)
        return merged

    # ---------- 详情 ----------

    def detail(self, video: RemoteVideo) -> RemoteVideo:
        """补齐分集信息（失败时退回原结果，不阻断播放）。"""
        provider = self._providers.get(video.source)
        if provider is None or not provider.available():
            return video
        if video.episodes:
            return video
        try:
            merged = provider.detail(video)
        except SourceError as error:
            self.record_failure(video.source, error)
            return video
        except Exception as error:  # noqa: BLE001
            self.record_failure(video.source, error)
            return video
        self.record_success(video.source)
        return merged or video

    # ---------- 跨源兜底解析 ----------

    def resolve(
        self,
        video: RemoteVideo,
        episode: Episode | None = None,
        quality: Quality | None = None,
        *,
        allow_cross_source: bool = True,
        sources: Iterable[str] | None = None,
        exclude: Iterable[str] = (),
        min_score: float = 0.62,
    ) -> ResolvedPlay:
        """解析可播放地址：本源优先，失败则换源搜索同一部片。

        `exclude` 用于「播放/下载失败后再次换源」场景：把已经试过的源排除掉。
        """
        excluded = {key for key in exclude}
        primary_error = ""
        provider = self._providers.get(video.source)
        target_episode = episode or (video.episodes[0] if video.episodes else None)

        if target_episode is None and provider is not None and provider.available() and video.source not in excluded:
            # 还没拉详情：先补分集再解析（用户直接点「播放」的场景）
            try:
                detailed = provider.detail(video)
                if detailed is not None and detailed.episodes:
                    video = detailed
                    target_episode = video.episodes[0]
            except Exception as error:  # noqa: BLE001
                primary_error = str(error)
                self.record_failure(video.source, error)

        if target_episode is not None and provider is not None and provider.available() and video.source not in excluded:
            try:
                url = provider.play_url(video, target_episode, quality)
                if not url:
                    raise SourceError("该集没有可用的播放地址")
                self.record_success(provider.key)
                return ResolvedPlay(
                    url=url, source=provider.key, video=video, episode=target_episode,
                    quality=quality, is_hls=looks_like_hls(url),
                    referer=self._referer_of(provider, url),
                )
            except SourceError as error:
                primary_error = str(error)
                self.record_failure(provider.key, error)
            except Exception as error:  # noqa: BLE001
                primary_error = str(error)
                self.record_failure(provider.key, error)
        elif provider is not None and video.source not in excluded:
            primary_error = provider.unavailable_reason()

        if not allow_cross_source:
            raise SourceError(primary_error or "该条目没有可用的播放地址")

        candidate_keys = [
            key for key in (sources if sources is not None
                            else self.ordered_keys(by_speed=self._sort_by_speed))
            if key != video.source and key in self._providers
            and self.is_enabled(key) and key not in excluded
        ]
        reasons: list[str] = [f"{self._label(video.source)}：{primary_error}"] if primary_error else []
        for key in candidate_keys:
            candidate_source = self._providers[key]
            if not candidate_source.available():
                continue
            try:
                candidates = candidate_source.search(video.title, kind="all", limit=10)
            except SourceError as error:
                self.record_failure(key, error)
                reasons.append(f"{key}：{error}")
                continue
            except Exception as error:  # noqa: BLE001
                self.record_failure(key, error)
                reasons.append(f"{key}：{error}")
                continue
            matched = best_match(video, candidates, threshold=min_score)
            if matched is None:
                continue
            try:
                if not matched.episodes:
                    matched = candidate_source.detail(matched)
                switched_episode = self._pick_episode(target_episode, matched)
                if switched_episode is None:
                    continue
                url = candidate_source.play_url(matched, switched_episode, None)
                if not url:
                    raise SourceError("该集没有可用的播放地址")
            except SourceError as error:
                self.record_failure(key, error)
                reasons.append(f"{key}：{error}")
                continue
            except Exception as error:  # noqa: BLE001
                self.record_failure(key, error)
                reasons.append(f"{key}：{error}")
                continue
            self.record_success(key)
            return ResolvedPlay(
                url=url, source=key, video=matched, episode=switched_episode,
                quality=None, is_hls=looks_like_hls(url),
                switched_from=video.source,
                note=f"{self._label(video.source)} 不可用，已改用{self._label(key)}",
                referer=self._referer_of(candidate_source, url),
            )
        raise SourceError("；".join(reasons) or "所有视频源都无法解析该条目")

    def _pick_episode(self, wanted: Episode | None, matched: RemoteVideo) -> Episode | None:
        """换源后定位到「同一集」：优先集序号，其次集名，最后退回首集。"""
        if not matched.episodes:
            return None
        if wanted is None:
            return matched.episodes[0]
        for episode in matched.episodes:
            if episode.index == wanted.index:
                return episode
        wanted_name = (wanted.name or "").split(" · ")[-1]
        if wanted_name:
            for episode in matched.episodes:
                if episode.name == wanted.name:
                    return episode
            for episode in matched.episodes:
                if wanted_name and wanted_name in episode.name:
                    return episode
        return matched.episodes[0]

    def _referer_of(self, provider: VideoSource, url: str) -> str:
        try:
            headers = provider.download_headers(url)
        except Exception:  # noqa: BLE001
            headers = {}
        return str(headers.get("Referer") or guess_referer(url) or "")

    def _label(self, key: str) -> str:
        provider = self._providers.get(key)
        return provider.label if provider else key

    # ---------- 配置持久化 ----------

    def load_settings(self, storage) -> None:  # noqa: ANN001  VideoStorage
        version = storage.get_setting(SETTING_VERSION, "")
        if version == str(SOURCES_VERSION):
            enabled = storage.get_setting(SETTING_ENABLED, "")
            if enabled:
                keys = {key for key in enabled.split(",") if key}
                if keys:
                    self._enabled = keys
            order = storage.get_setting(SETTING_ORDER, "")
            if order:
                self.set_order([key for key in order.split(",") if key])
        else:
            # 源清单已升级：旧的启用集合里可能有已下线的源、也会漏掉新加入的源，
            # 因此回到「新清单的默认启用集合 + 默认顺序」，并记下新版本号。
            self._enabled = {key for key in DEFAULT_PROVIDER_ORDER if key in self._providers}
            storage.set_setting(SETTING_VERSION, str(SOURCES_VERSION))
        # 用户改过的采集接口地址：覆盖到对应源实例
        raw = storage.get_setting(SETTING_SOURCE_URLS, "")
        if raw:
            try:
                mapping = json.loads(raw)
            except ValueError:
                mapping = {}
            for key, value in (mapping or {}).items():
                provider = self._providers.get(key)
                if provider is not None and isinstance(value, str) and value.strip():
                    provider.set_credential(value)

    def save_settings(self, storage) -> None:  # noqa: ANN001  VideoStorage
        storage.set_setting(SETTING_ENABLED, ",".join(sorted(self._enabled)))
        storage.set_setting(SETTING_ORDER, ",".join(self.order))
        storage.set_setting(SETTING_VERSION, str(SOURCES_VERSION))
        urls = {
            key: provider.credential
            for key, provider in self._providers.items()
            if provider.info.editable and provider.credential
        }
        storage.set_setting(SETTING_SOURCE_URLS, json.dumps(urls, ensure_ascii=False))


def looks_like_hls(url: str) -> bool:
    """判断地址是否需要 HLS 处理（m3u8 清单）。"""
    lowered = (url or "").lower().split("?", 1)[0]
    return lowered.endswith(".m3u8") or ".m3u8/" in lowered
