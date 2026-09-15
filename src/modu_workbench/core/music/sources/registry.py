"""音源注册表：统一搜索、健康度（熔断）、跨源兜底解析与配置持久化。

设计要点（对齐 listen1 / MusicFree 的多源思路，但全部本地 Python 实现）：
- 多源：任意音源实现 `MusicSource` 即可注册，默认顺序见 `DEFAULT_PROVIDER_ORDER`；
- 重试：HTTP 层带退避重试，注册表层做“换源重试”；
- 熔断：某音源连续失败会被临时降级，聚合搜索自动跳过，避免每次都在坏源上耗时；
- 跨源兜底：某个音源解析不出地址（VIP/接口变更）时，自动到其他音源找同一首歌。
"""
from __future__ import annotations

import time
from typing import Iterable, List

from ..models import RemoteTrack
from ..storage import MusicStorage
from .base import MusicSource, ResolvedAudio, SourceHealth, SourceInfo
from .http import SourceError
from .matcher import best_match
from .providers import DEFAULT_PROVIDER_ORDER, build_default_providers

SETTING_ENABLED = "sources/enabled"
SETTING_ORDER = "sources/priority"
SETTING_CREDENTIALS = "sources/credentials"

# 熔断参数：连续失败 3 次 → 降级 90 秒
DEGRADE_AFTER = 3
DEGRADE_SECONDS = 90.0


class MusicRegistry:
    """多音源注册表（应用级单例，见 services.app_context.music_registry）。"""

    def __init__(self, providers: Iterable[MusicSource] | None = None):
        self._providers: dict[str, MusicSource] = {}
        self._order: list[str] = []
        self._enabled: set[str] = set(DEFAULT_PROVIDER_ORDER)
        self._health: dict[str, SourceHealth] = {}
        for provider in (providers if providers is not None else build_default_providers()):
            self.register(provider)

    # ---------- 注册与配置 ----------

    def register(self, provider: MusicSource, *, position: int | None = None) -> None:
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

    def providers(self, *, enabled_only: bool = True) -> list[MusicSource]:
        items = [self._providers[key] for key in self.order if key in self._providers]
        if enabled_only:
            items = [item for item in items if item.key in self._enabled]
        return items

    def get(self, key: str) -> MusicSource | None:
        return self._providers.get(key)

    def require(self, key: str) -> MusicSource:
        provider = self._providers.get(key)
        if provider is None:
            raise SourceError(f"未知音源：{key}")
        return provider

    def is_enabled(self, key: str) -> bool:
        return key in self._enabled

    def set_enabled(self, key: str, enabled: bool) -> None:
        if enabled:
            self._enabled.add(key)
        else:
            self._enabled.discard(key)

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

    def record_success(self, key: str) -> None:
        health = self.health(key)
        health.successes += 1
        health.consecutive_failures = 0
        health.degraded_until = 0.0
        health.last_ok_at = time.time()
        health.last_error = ""

    def record_failure(self, key: str, error: Exception | str = "") -> None:
        health = self.health(key)
        health.failures += 1
        health.consecutive_failures += 1
        health.last_error = str(error)[:200]
        if health.consecutive_failures >= DEGRADE_AFTER:
            health.degraded_until = time.time() + DEGRADE_SECONDS

    def degraded_keys(self) -> list[str]:
        now = time.time()
        return [key for key in self.order if self.health(key).is_degraded(now)]

    def reset_health(self) -> None:
        for key in self.order:
            self._health[key] = SourceHealth()

    # ---------- 搜索 ----------

    def search(self, key: str, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        provider = self.require(key)
        provider.require_available()
        try:
            tracks = provider.search(keyword, kind=kind, limit=limit)
        except SourceError as error:
            self.record_failure(key, error)
            raise
        self.record_success(key)
        return tracks

    def search_all(self, keyword: str, kind: str = "song", limit: int = 20,
                   sources: Iterable[str] | None = None,
                   include_degraded: bool = False) -> tuple[List[RemoteTrack], List[str]]:
        """跨音源搜索：返回 (结果, 错误信息列表)。

        默认跳过被熔断的音源；若显式指定 sources 则按指定来（忽略熔断）。
        """
        results: List[RemoteTrack] = []
        errors: List[str] = []
        now = time.time()
        if sources is None:
            chosen = [p.key for p in self.providers() if include_degraded or not self.health(p.key).is_degraded(now)]
        else:
            chosen = [key for key in sources if key in self._providers]
        for key in chosen:
            provider = self._providers.get(key)
            if provider is None:
                continue
            if not provider.available():
                continue
            try:
                results.extend(provider.search(keyword, kind=kind, limit=limit))
                self.record_success(key)
            except SourceError as error:
                self.record_failure(key, error)
                errors.append(f"{provider.label}：{error}")
            except Exception as error:  # noqa: BLE001
                self.record_failure(key, error)
                errors.append(f"{provider.label}：{error}")
        return results, errors

    # ---------- 跨源兜底解析 ----------

    def resolve(self, track: RemoteTrack, *, allow_cross_source: bool = True,
                sources: Iterable[str] | None = None,
                exclude: Iterable[str] = (),
                min_score: float = 0.62) -> ResolvedAudio:
        """解析可下载地址：本音源优先，失败则换源搜索同一首歌。

        `exclude` 用于“下载失败后再次换源”场景：把已经试过的音源排除掉。
        """
        excluded = {key for key in exclude}
        primary_error = ""
        provider = self._providers.get(track.source)
        if provider is not None and provider.available() and track.source not in excluded:
            try:
                url = provider.download_url(track)
                self.record_success(provider.key)
                return ResolvedAudio(url=url, source=provider.key, track=track,
                                     lyrics=self._safe_lyrics(provider, track))
            except SourceError as error:
                primary_error = str(error)
                self.record_failure(provider.key, error)
            except Exception as error:  # noqa: BLE001
                primary_error = str(error)
                self.record_failure(provider.key, error)
        elif provider is not None and track.source not in excluded:
            primary_error = provider.unavailable_reason()

        if not allow_cross_source:
            raise SourceError(primary_error or "该曲目没有可用下载地址")

        candidate_keys = [
            key for key in (sources if sources is not None else self.order)
            if key != track.source and key in self._providers
            and self.is_enabled(key) and key not in excluded
        ]
        reasons: list[str] = [f"{track.source}：{primary_error}"] if primary_error else []
        keyword = f"{track.artist} {track.title}".strip() or track.title
        for key in candidate_keys:
            candidate_source = self._providers[key]
            if not candidate_source.available():
                continue
            try:
                candidates = candidate_source.search(keyword, kind="song", limit=10)
            except SourceError as error:
                self.record_failure(key, error)
                reasons.append(f"{key}：{error}")
                continue
            matched = best_match(track, candidates, threshold=min_score)
            if matched is None:
                continue
            try:
                url = candidate_source.download_url(matched)
            except SourceError as error:
                self.record_failure(key, error)
                reasons.append(f"{key}：{error}")
                continue
            self.record_success(key)
            return ResolvedAudio(
                url=url, source=key, track=matched, switched_from=track.source,
                note=f"{self._label(track.source)} 不可用，已改用{self._label(key)}",
                lyrics=self._safe_lyrics(candidate_source, matched),
            )
        raise SourceError("；".join(reasons) or "所有音源都无法解析该曲目")

    def _safe_lyrics(self, provider: MusicSource, track: RemoteTrack) -> str:
        try:
            return provider.lyrics(track) or ""
        except Exception:  # noqa: BLE001
            return ""

    def _label(self, key: str) -> str:
        provider = self._providers.get(key)
        return provider.label if provider else key

    # ---------- 配置持久化 ----------

    def load_settings(self, storage: MusicStorage) -> None:
        enabled = storage.get_setting(SETTING_ENABLED, "")
        if enabled:
            keys = {key for key in enabled.split(",") if key}
            self._enabled = keys or self._enabled
        order = storage.get_setting(SETTING_ORDER, "")
        if order:
            self.set_order([key for key in order.split(",") if key])
        credentials = storage.get_setting(SETTING_CREDENTIALS, "")
        if credentials:
            import json

            try:
                mapping = json.loads(credentials)
            except ValueError:
                mapping = {}
            for key, value in (mapping or {}).items():
                provider = self._providers.get(key)
                if provider is not None and isinstance(value, str):
                    provider.set_credential(value)

    def save_settings(self, storage: MusicStorage) -> None:
        import json

        storage.set_setting(SETTING_ENABLED, ",".join(sorted(self._enabled)))
        storage.set_setting(SETTING_ORDER, ",".join(self.order))
        credentials = {
            key: provider.credential
            for key, provider in self._providers.items()
            if provider.credential
        }
        storage.set_setting(SETTING_CREDENTIALS, json.dumps(credentials, ensure_ascii=False))


def clean_lyrics(text: str) -> str:
    """去掉 LRC 时间标签，得到纯文本歌词。"""
    import re

    return re.sub(r"\[\d{1,2}:\d{1,2}(?:[.:]\d{1,3})?\]", "", text or "").strip()
