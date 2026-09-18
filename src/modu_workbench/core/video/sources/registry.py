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
import time
from typing import Iterable, List

from ..models import Episode, Quality, RemoteVideo
from .base import ResolvedPlay, SourceHealth, SourceInfo, VideoSource
from .http import SourceError, guess_referer
from .matcher import best_match
from .providers import DEFAULT_PROVIDER_ORDER, build_default_providers

SETTING_ENABLED = "sources/enabled"
SETTING_ORDER = "sources/priority"
SETTING_SOURCE_URLS = "sources/urls"      # 用户覆盖的采集接口地址 {key: url}
SETTING_VERSION = "sources/version"       # 源清单版本（升级时迁移用户的启用/排序设置）

# 源清单版本：改动内置源（下线失效源、新增可用源）时递增。
# 递增后，旧的「启用集合/排序」不再直接套用 —— 否则新加的源会默认处于停用状态，
# 用户会以为「源没了」。用户自己填的接口地址（sources/urls）始终保留。
SOURCES_VERSION = 3

# 熔断参数：连续失败 3 次 → 降级 120 秒（视频源抖动比音乐源更常见）
DEGRADE_AFTER = 3
DEGRADE_SECONDS = 120.0


class VideoRegistry:
    """多视频源注册表（应用级单例，见 services.app_context.video_registry）。"""

    def __init__(self, providers: Iterable[VideoSource] | None = None):
        self._providers: dict[str, VideoSource] = {}
        self._order: list[str] = []
        self._health: dict[str, SourceHealth] = {}
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

    def search_one(self, key: str, keyword: str, kind: str = "all", limit: int = 30,
                   page: int = 1) -> List[RemoteVideo]:
        provider = self.require(key)
        provider.require_available()
        try:
            videos = provider.search(keyword, kind=kind, limit=limit, page=page)
        except SourceError as error:
            self.record_failure(key, error)
            raise
        self.record_success(key)
        return videos

    def search_all(self, keyword: str, kind: str = "all", limit: int = 20,
                   sources: Iterable[str] | None = None,
                   include_degraded: bool = False,
                   page: int = 1) -> tuple[List[RemoteVideo], List[str]]:
        """跨源搜索：返回 (结果, 错误说明列表)。

        默认跳过被熔断的源；显式指定 sources 时按指定来（忽略熔断）。
        结果按「标题+年份」去重，保留先出现的源（即优先级更高的源）。
        """
        errors: List[str] = []
        now = time.time()
        if sources is None:
            # 直链类源只在「关键词本身就是一个地址」时才参与聚合，
            # 否则每次搜索都会多出一条「请输入 http(s):// 开头的地址」的噪音错误
            keyword_is_url = (keyword or "").strip().lower().startswith(("http://", "https://"))
            chosen = [p.key for p in self.providers()
                      if (p.supports_keyword_search() or keyword_is_url)
                      and (include_degraded or not self.health(p.key).is_degraded(now))]
        else:
            chosen = [key for key in sources if key in self._providers]

        collected: List[RemoteVideo] = []
        seen: set[str] = set()
        for key in chosen:
            provider = self._providers.get(key)
            if provider is None:
                continue
            if not provider.available():
                continue
            try:
                found = provider.search(keyword, kind=kind, limit=limit, page=page)
            except SourceError as error:
                self.record_failure(key, error)
                errors.append(f"{provider.label}：{error}")
                continue
            except Exception as error:  # noqa: BLE001  单个源异常不能拖垮聚合搜索
                self.record_failure(key, error)
                errors.append(f"{provider.label}：{error}")
                continue
            self.record_success(key)
            for video in found:
                fingerprint = f"{video.title.strip().lower()}|{video.year}"
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                collected.append(video)
        return collected, errors

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
            key for key in (sources if sources is not None else self.order)
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
