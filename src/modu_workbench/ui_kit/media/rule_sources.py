"""把「规则」包装成「源」：`RuleProvider` / `RuleRegistry`。

为什么要有这一层：`core/video` 的播放器、下载器、代理已经是成熟共享层，它们只认
`VideoSource`（`search/detail/play_url`）与 `RemoteVideo/Episode/Quality`。动漫板块
不必重造一套播放链路——**番剧站规则**只要能被包装成同样的接口，就能直接复用。

映射关系（一一对应，不做多余抽象）：

| 动漫侧 | 影视侧 |
|---|---|
| `AnimeRule` | `VideoSource`（key = `anime:<规则key>`） |
| `SearchHit` | `RemoteVideo`（`remote_id` = 规则的 `sourcePath`/详情页 URL） |
| `SeriesDetail` + `Road` | `RemoteVideo.episodes`（线路名进 `Episode.route`） |
| `AnimeEpisode` | `Episode`（`url` 是播放页，`qualities` 留空） |
| `AnimeEngine.resolve()` | `play_url()`：嗅探出真实媒体地址后才返回 |

**两条必须说清楚的边界**：

- `play_url()` 返回的是**嗅探后的真实媒体地址**，不是播放页地址——影视侧的下载器会
  直接把返回值当媒体文件下载，给一个 HTML 播放页只会下回一坨网页；
- 嗅探依赖站点能被静态解析（见 `sniffer.py` 的能力边界）。嗅探失败时抛
  `SourceError` 并把可读原因带出来，**不返回空串**：空串在影视侧等价于"没有地址"，
  会把"该站需要 JS 渲染"这个真实原因吞掉。
"""
from __future__ import annotations

from typing import Iterable, List

from modu_workbench.core.video.models import Episode, RemoteVideo
from modu_workbench.core.video.sources.base import KIND_FULL, SourceInfo, VideoSource
from modu_workbench.core.video.sources.http import SourceError
from modu_workbench.core.anime.engine import AnimeEngine, SearchHit
from modu_workbench.core.anime.models import (
    AnimeEpisode,
    AnimeRule,
    AnimeSeries,
    SeriesDetail,
    normalize_title,
    strip_html,
)
from modu_workbench.core.anime.sniffer import Sniffer

#: 源 key 前缀：影视侧的源是全站聚合（`cms:…`），动漫侧是"一条规则 = 一个源"，
#: 前缀能让设置页一眼区分两者，也避免规则名与影视源 key 撞车。
KEY_PREFIX = "anime:"


def provider_key_for(rule_key: str) -> str:
    """规则 key → 源 key（规则 key 允许为空，兜底成 `rule`）。"""
    return f"{KEY_PREFIX}{rule_key or 'rule'}"


def provider_key(rule: AnimeRule) -> str:
    return provider_key_for(rule.key or rule.name)


def series_remote_id(video: RemoteVideo) -> str:
    """从 `RemoteVideo` 取回规则的 `sourcePath`：`extra` 优先，其次 `remote_id`。"""
    extra = getattr(video, "extra", None) or {}
    return str(extra.get("anime_remote_id") or video.remote_id or "")


class RuleProvider(VideoSource):
    """一条动画规则包装成的视频源。

    `engine` 可注入（测试离线跑）；不传时按规则自己建一个，**多条规则共用一个
    engine**（见 `RuleRegistry`）——否则每条规则都会各自建 HTTP 客户端与限速器，
    站点侧看到的并发会翻倍。
    """

    def __init__(self, rule: AnimeRule, *, engine: AnimeEngine | None = None,
                 sniffer: Sniffer | None = None) -> None:
        self.rule = rule
        self.engine = engine if engine is not None else AnimeEngine([rule], sniffer=sniffer)
        self.info = SourceInfo(
            key=provider_key(rule),
            label=rule.label,
            note=rule.describe(),
            kind=KIND_FULL,
            homepage=rule.base_url,
            # 动漫站的媒体地址大多要带 Referer 才能下载，但地址是嗅探出来的、不是源给的，
            # 所以"能下载"由解析结果决定：这里保守地报可下载，下载失败时会有真实错误。
            downloadable=True,
        )
        self._resolved: dict[str, tuple[str, str]] = {}   # episode.url -> (媒体地址, referer)
        super().__init__(key=self.info.key)

    # ---------- 元信息 ----------

    def default_headers(self) -> dict:
        headers: dict[str, str] = {}
        if self.rule.user_agent:
            headers["User-Agent"] = self.rule.user_agent
        if self.rule.referer:
            headers["Referer"] = self.rule.referer
        return headers

    def available(self) -> bool:
        return bool(self.rule.supported and not self.rule.deprecated)

    def unavailable_reason(self) -> str:
        if not self.rule.supported:
            return f"该规则需要更新的客户端（api {self.rule.api_level}）"
        if self.rule.deprecated:
            return "该规则已被标记为退役（站点多半已改版）"
        return ""

    # ---------- 能力 ----------

    def search(self, keyword: str, kind: str = "all", limit: int = 30,
               page: int = 1) -> List[RemoteVideo]:
        """关键词搜索；`kind`/`page` 对规则站点没有意义（规则里已写死），只认 `limit`。"""
        self.require_available()
        keyword = (keyword or "").strip()
        if not keyword:
            raise SourceError(f"{self.label}：请输入搜索关键词")
        hits = self.engine.search_rule(self.rule, keyword, limit=limit)
        if not hits:
            note = self.engine.note_for(self.rule.key)
            # 空结果是**正常业务结果**（站点没有这部番），不该抛异常；
            # 但"解析失败"必须让用户看见原因，否则界面上就是"转了一圈什么都没有"。
            if note:
                raise SourceError(f"{self.label}：{note}")
            raise SourceError(f"{self.label}：没有搜索到「{keyword}」的结果")
        return [self._to_video(hit) for hit in hits]

    def detail(self, video: RemoteVideo) -> RemoteVideo:
        """补齐线路/剧集（`RemoteVideo.episodes` 在搜索阶段为空，详情阶段才拉）。"""
        self.require_available()
        remote = series_remote_id(video)
        if not remote:
            return video
        series = self.series_of(video)
        detail = self.engine.detail(self.rule, series)
        self._remember_detail(detail)
        merged = self._to_video_from_series(series, video)
        merged.episodes = self._to_episodes(detail)
        merged.extra = dict(video.extra or {})
        merged.extra["anime_note"] = detail.note
        return merged

    def play_url(self, video: RemoteVideo, episode: Episode,
                 quality=None) -> str:
        """返回**真实媒体地址**（嗅探后），失败抛 `SourceError` 并带可读原因。"""
        self.require_available()
        episode_url = str(getattr(episode, "url", "") or "")
        cached = self._resolved.get(episode_url)
        if cached:
            return cached[0]

        series = self.series_of(video)
        target = self._episode_of(episode, detail=getattr(self, "_detail", None))
        media = self.engine.resolve(self.rule, series, target)
        if not media.ok:
            raise SourceError(f"{self.label}：{media.note or '没有解析出播放地址'}")
        self._resolved[episode_url] = (media.url, media.referer)
        return media.url

    def download_headers(self, url: str = "") -> dict:
        """下载分片时带 Referer：**必须**用嗅探到的那一个（站点校验的就是它）。"""
        for media_url, referer in self._resolved.values():
            if url and media_url == url and referer:
                return {"Referer": referer}
        headers = super().download_headers(url)
        if self.rule.referer:
            headers["Referer"] = self.rule.referer
        return headers

    # ---------- 映射 ----------

    def series_of(self, video: RemoteVideo) -> AnimeSeries:
        return AnimeSeries(
            rule_key=self.rule.key,
            remote_id=series_remote_id(video),
            title=str(video.title or ""),
            cover=str(video.cover_url or ""),
            year=str(video.year or ""),
            summary=str(video.description or ""),
            latest_episode=str(video.remarks or ""),
            total_episodes=len(video.episodes or []),
        )

    def _to_video(self, hit: SearchHit) -> RemoteVideo:
        return RemoteVideo(
            source=self.key,
            remote_id=hit.remote_id,
            title=hit.title,
            kind="anime",
            year=str(hit.year or ""),
            cover_url=str(hit.cover or ""),
            remarks=str(hit.note or ""),
            series_key=f"{self.key}:{hit.remote_id}",
            extra={"anime_remote_id": hit.remote_id, "anime_url": hit.url},
        )

    def _to_video_from_series(self, series: AnimeSeries, origin: RemoteVideo) -> RemoteVideo:
        merged = self._to_video(SearchHit(
            rule_key=self.rule.key, rule_name=self.label, title=series.title,
            remote_id=series.remote_id, url=str((origin.extra or {}).get("anime_url") or ""),
            cover=series.cover, year=series.year,
        ))
        merged.description = series.summary
        merged.remarks = origin.remarks or series.latest_episode
        return merged

    def _to_episodes(self, detail: SeriesDetail) -> list[Episode]:
        """线路 → `Episode.route`；剧集名在多线路时前缀线路名（与影视侧一致）。"""
        episodes: list[Episode] = []
        multi = len([road for road in detail.roads if road.episodes]) > 1
        for road in detail.roads:
            for item in road.episodes:
                label = item.label
                if multi:
                    label = f"{road.label} · {label}"
                episodes.append(Episode(
                    name=label,
                    url=item.url,
                    index=len(episodes),
                    route=road.label,
                    # 真实媒体地址要嗅探才知道，这里**不给假地址**：
                    # 影视侧的下载器会把 quality.url 当直链用。
                    qualities=[],
                ))
        return episodes

    def _remember_detail(self, detail: SeriesDetail) -> None:
        # 只留最近一次详情：`play_url()` 需要 `AnimeEpisode`（含 road_index/number），
        # 而影视侧的 `Episode` 里没有这两个字段。
        self._detail = detail

    def _episode_of(self, episode: Episode, *, detail: SeriesDetail | None) -> AnimeEpisode:
        """把影视侧的 `Episode` 映射回 `AnimeEpisode`（按地址匹配，其次按序号）。"""
        url = str(getattr(episode, "url", "") or "")
        index = int(getattr(episode, "index", -1))
        if detail is not None:
            flat: list[AnimeEpisode] = [item for road in detail.roads for item in road.episodes]
            for item in flat:
                if url and item.url == url:
                    return item
            if 0 <= index < len(flat):
                return flat[index]
        return AnimeEpisode(
            index=max(0, index), name=str(getattr(episode, "name", "") or ""), url=url,
            road_index=0, road_name=str(getattr(episode, "route", "") or ""),
        )


class RuleRegistry:
    """一组规则源的集合（动漫板块内的"源列表"，不等同于影视侧的 `VideoRegistry`）。

    只做三件事：登记/查询、按规则 key 搜索一条、跨规则聚合搜索。熔断降级、测速、
    权重排序这些**不在这里做**——它们是影视侧的源治理能力，动漫这边交给规则页的
    "试跑"与 `library.py` 的编排，避免两套治理逻辑各说各话。
    """

    def __init__(self, rules: Iterable[AnimeRule] | None = None, *,
                 engine: AnimeEngine | None = None, sniffer: Sniffer | None = None,
                 library=None) -> None:
        self._providers: dict[str, RuleProvider] = {}
        self._order: list[str] = []
        self.engine = engine if engine is not None else AnimeEngine([])
        self.sniffer = sniffer
        #: 可选：`library.AnimeLibrary`。给了就用它的编排结果（它比这里更懂跨规则合并与
        #: 去重）；没给就自己拼——两条路都只在 `search_all()`/`detail()` 里用到，
        #: `local_rule`/`rule` 这类真正执行规则的路径始终走本模块的 `RuleProvider`。
        self.library = library
        for rule in rules or []:
            self.register(rule)

    # ---------- 登记 ----------

    def register(self, rule_or_provider) -> RuleProvider:
        provider = (rule_or_provider if isinstance(rule_or_provider, RuleProvider)
                    else RuleProvider(rule_or_provider, engine=self.engine, sniffer=self.sniffer))
        key = provider.key
        self._providers[key] = provider
        if key not in self._order:
            self._order.append(key)
        self._sync_engine()
        return provider

    def _sync_engine(self) -> None:
        """把规则集同步给共用的引擎（引擎按自身规则列表搜索）。"""
        self.engine.rules = [provider.rule for provider in self._providers.values()]

    def unregister(self, key: str) -> bool:
        if key not in self._providers:
            return False
        self._providers.pop(key, None)
        self._order = [item for item in self._order if item != key]
        self._sync_engine()
        return True

    @property
    def order(self) -> list[str]:
        return [key for key in self._order if key in self._providers]

    # ---------- 查询 ----------

    def providers(self, *, enabled_only: bool = True) -> list[RuleProvider]:
        return [self._providers[key] for key in self.order]

    def get(self, key: str) -> RuleProvider | None:
        return self._providers.get(key)

    def require(self, key: str) -> RuleProvider:
        provider = self._providers.get(key)
        if provider is None:
            raise SourceError(f"未知动漫源：{key}")
        return provider

    def keys(self) -> list[str]:
        return self.order

    def rules(self) -> list[AnimeRule]:
        return [provider.rule for provider in self.providers()]

    # ---------- 搜索 ----------

    def search_one(self, key: str, keyword: str, *, limit: int = 30) -> list[RemoteVideo]:
        return self.require(key).search(keyword, limit=limit)

    def search_all(self, keyword: str, *, limit: int = 30, per_rule: int = 10,
                   keys: Iterable[str] | None = None,
                   on_error=None) -> list[RemoteVideo]:
        """跨规则搜索并按**归一化片名**合并。

        一条规则失败不影响其它规则（与 `AnimeEngine.search()` 同样的取舍）；
        `on_error(provider, error)` 让调用方把失败原因展示到界面上。合并时保留
        优先级最高的那条记录，其余来源塞进 `extra["anime_sources"]`（换源用）。
        """
        wanted = {str(item) for item in keys} if keys else None
        merged: dict[str, RemoteVideo] = {}
        order: list[str] = []
        for provider in self.providers():
            if wanted is not None and provider.key not in wanted:
                continue
            if not provider.available():
                if on_error is not None:
                    on_error(provider, SourceError(provider.unavailable_reason()))
                continue
            videos = self._local_search(provider, keyword, per_rule)
            if isinstance(videos, Exception):
                if on_error is not None:
                    on_error(provider, videos)
                continue
            for video in videos:
                key = normalize_title(video.title)
                if not key:
                    continue
                if key not in merged:
                    merged[key] = video
                    order.append(key)
                    continue
                sources = merged[key].extra.setdefault("anime_sources", [])
                sources.append({"source": video.source, "remote_id": video.remote_id,
                                "cover": video.cover_url, "remarks": video.remarks})
        results = [merged[key] for key in order]
        # `strip_html`：规则站点的标题/备注里常带标签，影视侧直接展示，这里先洗干净
        for video in results:
            video.title = strip_html(video.title)
            video.remarks = strip_html(video.remarks)
        return results[:limit] if limit and limit > 0 else results

    def _local_search(self, provider: RuleProvider, keyword: str, per_rule: int):
        """搜一条规则；返回 `RemoteVideo` 列表，或返回一个异常对象（由调用方上报）。"""
        try:
            return provider.search(keyword, limit=per_rule)
        except Exception as error:  # noqa: BLE001
            # 有 library 就让业务层去兜（它知道规则启用状态、去重策略与测试记录）
            if self.library is not None:
                try:
                    hits = self.library.search(keyword, rule_keys=[provider.rule.key])
                except Exception:  # noqa: BLE001
                    return error
                items = [self._video_from_library(hit) for hit in hits]
                if items:
                    return items
            return error

    def _video_from_library(self, hit) -> RemoteVideo:
        """`library.SearchHit` → `RemoteVideo`（字段名不同：rule_label / remarks）。"""
        remote = str(getattr(hit, "remote_id", "") or getattr(hit, "url", "") or "")
        return RemoteVideo(
            source=provider_key_for(str(getattr(hit, "rule_key", "") or "")),
            remote_id=remote,
            title=str(getattr(hit, "title", "") or ""),
            kind="anime",
            year=str(getattr(hit, "year", "") or ""),
            cover_url=str(getattr(hit, "cover", "") or ""),
            remarks=str(getattr(hit, "remarks", "") or getattr(hit, "note", "") or ""),
            series_key=f"{provider_key_for(str(getattr(hit, 'rule_key', '') or ''))}:{remote}",
            extra={"anime_remote_id": remote},
        )

    def __len__(self) -> int:
        return len(self._providers)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<RuleRegistry rules={len(self._providers)}>"


__all__ = ["KEY_PREFIX", "RuleProvider", "RuleRegistry", "provider_key",
           "provider_key_for", "series_remote_id"]
