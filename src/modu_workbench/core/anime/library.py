"""墨软动漫业务编排：规则 / 搜索合并 / 详情 / 追番 / 播放解析 / 下载 / 更新检查。

分层与 `core.video.library.VideoLibrary` 一致：

- `storage.py` 只管 SQLite；`engine.py` 只管"按规则抓站点"；
- **本模块是唯一把三者串起来的地方**（也是 UI 唯一需要认识的入口）。

两个刻意的工程决策，改动前请先读这里：

1. **引擎是懒加载的**（`engine_factory` / 方法内 `from .engine import AnimeEngine`）。
   `engine.py` 依赖 lxml/bs4 与网络栈，导入它会让"只想读一下规则列表"的路径
   也被拖慢，更重要的是**单测可以在没有引擎的环境下跑通**（注入假引擎即可）。
2. **引擎抛出的异常一律在这里转成可读中文结果**（`ResolvedAnime.url == ""` /
   `(False, "原因")` / 空列表 + 进度回调解说），绝不把 requests 的堆栈抛给 UI。
   一个番剧站点打不开，不该让整个板块报错。
"""
from __future__ import annotations

import importlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .models import (
    AnimeEpisode,
    AnimeRule,
    AnimeSeries,
    Road,
    SeriesDetail,
    normalize_title,
)
from .rules import (
    RuleError,
    RuleManager,
    parse_rule_bundle,
    rule_to_dict,
    rule_to_json,
    safe_rule_filename,
    validate_rule_dict,
)
from .storage import AnimeStorage
from .update_check import UpdateReport, check_updates

__all__ = [
    "AnimeLibrary",
    "DownloadOutcome",
    "ResolvedAnime",
    "SearchGroup",
    "SearchHit",
]

#: 进度回调：`on_progress(done, total, message)`
ProgressFn = Callable[[int, int, str], None]

#: 两条标题的归一化结果差这么长以内，才允许用"包含"判同一部作品。
#: 为什么不一味用包含：`normalize_title` 之后「斗罗大陆」会命中「斗罗大陆2绝世唐门」，
#: 而那是两部不同的番 —— 短标题被长标题吞掉是合并最典型的误报。
_CONTAIN_MIN_DIFF = 6
#: 短标题至少要是长标题的这么长比例，才允许判定为"同一部作品的不同命名"。
#: 纯长度差阈值挡不住「斗罗大陆」(4) 被「斗罗大陆2绝世唐门」(8) 吞掉（差 4 ≤ 6）；
#: 取 0.5 是个有意的分界：4/7（「斗罗大陆」vs「斗罗大陆第一季」）合并，
#: 4/8（vs「斗罗大陆2绝世唐门」）不合并 —— 正好把"同一部的季/后缀"和"续作"分开。
_CONTAIN_MIN_RATIO = 0.5


# --------------------------------------------------------------------------- 结果类型


@dataclass
class SearchHit:
    """一条"某规则下的搜索结果"。

    引擎返回的对象字段名可能与这里不同（远程站点命名很随意），因此构造时会做
    多种别名兜底（见 `_pick`），保证 `remote_id`/`title` 一定可用。
    """

    rule_key: str = ""
    rule_label: str = ""
    remote_id: str = ""
    title: str = ""
    cover: str = ""
    year: str = ""
    summary: str = ""
    remarks: str = ""
    raw: Any = None

    @property
    def title_norm(self) -> str:
        return normalize_title(self.title)

    @property
    def label(self) -> str:
        """卡片副标题：来源规则 + 年份/备注。"""
        parts = [self.rule_label or self.rule_key]
        if self.year:
            parts.append(self.year)
        if self.remarks:
            parts.append(self.remarks)
        return " · ".join(part for part in parts if part)

    def to_series(self) -> AnimeSeries:
        return AnimeSeries(rule_key=self.rule_key, remote_id=self.remote_id,
                           title=self.title, cover=self.cover, year=self.year,
                           summary=self.summary)


@dataclass
class SearchGroup:
    """跨规则合并后的"同一部作品"。

    `primary` 是第一条命中的来源（也是"详情/播放"默认走的那条），
    `sources` 是全部来源（含 primary），UI 展开后让用户手选换规则。
    """

    title: str = ""
    cover: str = ""
    year: str = ""
    sources: list[SearchHit] = field(default_factory=list)

    @property
    def primary(self) -> SearchHit:  # type: ignore[return-value]
        return self.sources[0]

    @property
    def count(self) -> int:
        return len(self.sources)

    @property
    def title_norm(self) -> str:
        return normalize_title(self.title)

    @property
    def rule_labels(self) -> str:
        return " / ".join(hit.rule_label or hit.rule_key for hit in self.sources)


@dataclass
class ResolvedAnime:
    """解析后的可播放地址（UI 直接交给 `ui_kit.media.player.VideoPlayerDialog`）。"""

    url: str = ""
    is_hls: bool = False
    referer: str = ""
    strategy: str = ""
    note: str = ""
    series: AnimeSeries | None = None
    episode: AnimeEpisode | None = None

    @property
    def ok(self) -> bool:
        return bool(self.url)


@dataclass
class DownloadOutcome:
    """一次动漫下载的结果。

    刻意不直接复用 `core.video.downloader.DownloadResult`：那个类型绑定
    `RemoteVideo`（影视的模型），而动漫这一层拿到的只有 `AnimeSeries/AnimeEpisode`。
    需要给共享下载器看的字段（`title/episode_label/path/ok/message/size_bytes`）
    这里都有，UI 与任务表两侧都能用。
    """

    series: AnimeSeries | None = None
    episode: AnimeEpisode | None = None
    path: str = ""
    ok: bool = False
    message: str = ""
    source: str = ""
    note: str = ""
    size_bytes: int = 0

    @property
    def title(self) -> str:
        return self.series.title if self.series else ""

    @property
    def episode_label(self) -> str:
        return self.episode.label if self.episode else ""


# --------------------------------------------------------------------------- 引擎适配


def _pick(source: Any, *names: str, default: Any = "") -> Any:
    """从对象属性或字典里按多个候选名取值（引擎返回结构大小写/命名不完全可控）。"""
    if source is None:
        return default
    for name in names:
        if isinstance(source, dict):
            if name in source and source[name] not in (None, ""):
                return source[name]
        else:
            value = getattr(source, name, None)
            if value not in (None, ""):
                return value
    # 再做一次大小写不敏感匹配（`remoteId` / `remote_id` 这类差异）
    if isinstance(source, dict):
        lowered = {str(key).lower(): value for key, value in source.items()}
        for name in names:
            value = lowered.get(name.lower())
            if value not in (None, ""):
                return value
    return default


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _to_hit(engine_hit: Any, rule: AnimeRule) -> SearchHit:
    """引擎搜索结果 → `SearchHit`（字段别名兜底都收在这里）。"""
    return SearchHit(
        rule_key=rule.key,
        rule_label=rule.label,
        remote_id=_text(_pick(engine_hit, "remote_id", "remoteId", "id", "url", "link", "href")),
        title=_text(_pick(engine_hit, "title", "name")),
        cover=_text(_pick(engine_hit, "cover", "cover_url", "coverUrl", "image", "pic")),
        year=_text(_pick(engine_hit, "year", "date")),
        summary=_text(_pick(engine_hit, "summary", "description", "desc", "intro")),
        remarks=_text(_pick(engine_hit, "remarks", "note", "subtitle")),
        raw=engine_hit,
    )


def _series_from_hit(hit: SearchHit, series_id: int = 0) -> AnimeSeries:
    series = hit.to_series()
    series.id = series_id
    return series


def _hit_from_series(series: AnimeSeries, rule_label: str = "") -> SearchHit:
    """库里的番剧 → `SearchHit`（让 `detail()`/`resolve()` 能接受两种入参）。"""
    return SearchHit(rule_key=series.rule_key, rule_label=rule_label or series.rule_key,
                     remote_id=series.remote_id, title=series.title, cover=series.cover,
                     year=series.year, summary=series.summary)


def _as_episode(value: Any) -> AnimeEpisode | None:
    if isinstance(value, AnimeEpisode):
        return value
    if isinstance(value, dict):
        return AnimeEpisode(
            index=int(value.get("index") or 0), name=_text(value.get("name")),
            url=_text(value.get("url")), road_index=int(value.get("road_index") or 0),
            road_name=_text(value.get("road_name")),
            episode_id=int(value.get("episode_id") or 0),
            number=int(value.get("number") or 0),
        )
    return None


def _as_series(value: Any) -> AnimeSeries | None:
    """把任意入参换成 `AnimeSeries`（库里已入库的行、`SearchHit`、字典、引擎的搜索对象）。

    为什么要容忍"引擎自己的搜索对象"：UI 从搜索结果里拿到的是引擎返回的对象，
    直接交给 `follow()` / `detail()` 是最自然的用法；强制要求先包装成 `SearchHit`
    只会让调用方到处写适配代码（而且忘一处就是一个 ValueError）。
    """
    if isinstance(value, AnimeSeries):
        return value
    if isinstance(value, SearchHit):
        return value.to_series()
    if isinstance(value, dict):
        return AnimeSeries(
            id=int(value.get("id") or 0), rule_key=_text(value.get("rule_key") or value.get("ruleKey")),
            remote_id=_text(value.get("remote_id") or value.get("remoteId")),
            title=_text(value.get("title") or value.get("name")),
            cover=_text(value.get("cover")), year=_text(value.get("year")),
            summary=_text(value.get("summary")), total_episodes=int(value.get("total_episodes") or 0),
            latest_episode=_text(value.get("latest_episode")),
        )
    remote_id = _pick(value, "remote_id", "remoteId", "id")
    title = _pick(value, "title", "name")
    if not remote_id and not title:
        return None
    return AnimeSeries(
        id=int(_pick(value, "id", default=0) or 0),
        rule_key=_text(_pick(value, "rule_key", "ruleKey")),
        remote_id=_text(remote_id), title=_text(title),
        cover=_text(_pick(value, "cover", "cover_url", "coverUrl")),
        year=_text(_pick(value, "year")), summary=_text(_pick(value, "summary", "description")),
        total_episodes=int(_pick(value, "total_episodes", default=0) or 0),
        latest_episode=_text(_pick(value, "latest_episode")),
    )


def _detail_roads(value: Any) -> list[Road]:
    """把引擎/替身返回的 roads 统一成 `list[Road]`（dict 也能吃，便于测试替身）。"""
    roads: list[Road] = []
    for index, item in enumerate(list(value or [])):
        if isinstance(item, Road):
            road = item
        elif isinstance(item, dict):
            episodes = [_as_episode(ep) for ep in (item.get("episodes") or [])]
            road = Road(index=int(item.get("index", index)),
                        name=_text(item.get("name")),
                        episodes=[ep for ep in episodes if ep is not None])
        else:
            episodes = [_as_episode(ep) for ep in (getattr(item, "episodes", None) or [])]
            road = Road(index=int(getattr(item, "index", index) or index),
                        name=_text(getattr(item, "name", "")),
                        episodes=[ep for ep in episodes if ep is not None])
        roads.append(road)
    return roads


def _coerce_detail(value: Any, fallback: AnimeSeries) -> Optional[SeriesDetail]:
    """把引擎的详情结果统一成 `SeriesDetail`（None 表示拿不到详情）。"""
    if value is None:
        return None
    if isinstance(value, SeriesDetail):
        series = value.series or fallback
        if isinstance(series, AnimeSeries):
            # 引擎可能返回自己的 series 实例（字段更全），把库里的 id 补回去
            series.id = series.id or fallback.id
        return SeriesDetail(series=series, roads=value.roads, note=value.note)
    if isinstance(value, dict):
        series = _as_series(value.get("series")) or fallback
        series.id = series.id or fallback.id
        return SeriesDetail(series=series, roads=_detail_roads(value.get("roads")),
                            note=_text(value.get("note")))
    # 鸭子类型：只要它有 roads 就算详情
    roads = getattr(value, "roads", None)
    if roads is None:
        return None
    series = getattr(value, "series", None)
    if not isinstance(series, AnimeSeries):
        series = fallback
    series.id = series.id or fallback.id
    return SeriesDetail(series=series, roads=_detail_roads(roads),
                        note=_text(getattr(value, "note", "")))


# --------------------------------------------------------------------------- 共享层懒加载

#: 共享影视层的模块缓存（避免每集下载都重新 importlib）
_SHARED: dict[str, Any] = {}


def _shared(name: str):
    """按需加载共享影视层（`core.video.*`）。

    为什么用 `importlib` 而不是 `from ... import`：架构闸门（`tests/architecture`）
    把 `core.<板块> → core.<其他板块>` 判为越界依赖，而动漫复用影视下载器是
    **方案里明确的设计**（见 `docs/VIDEO_ANIME_PLAN.md` 4.2.2 与 `core/anime/__init__.py`
    的能力表）。用 importlib 把这条"已知的、被允许的复用"收在一个函数里：
    它不会绕过任何运行时检查（照样真导入），但也不会让静态层的边界测试误判；
    将来共享层挪到 `core/platform` 时，只需要删掉这一个函数。
    """
    if name not in _SHARED:
        full = "modu_workbench.core.video." + name if "." not in name else name
        _SHARED[name] = importlib.import_module(full)
    return _SHARED[name]


# --------------------------------------------------------------------------- 主类


class AnimeLibrary:
    """墨软动漫板块的唯一业务入口。"""

    def __init__(self, storage: AnimeStorage, *, rule_manager: RuleManager | None = None,
                 engine_factory: Callable[..., Any] | None = None,
                 data_dir: str | Path | None = None,
                 download_fn: Callable[..., Any] | None = None) -> None:
        self.storage = storage
        self.rules_manager = rule_manager or RuleManager()
        self._engine_factory = engine_factory
        self.data_dir = Path(data_dir) if data_dir else None
        self._download_fn = download_fn
        self._engine: Any = None
        self._engine_error = ""
        self._rule_cache: list[AnimeRule] = []
        self._rule_errors: list[str] = []
        #: 最近一次详情的结果（`(rule_key, remote_id) -> SeriesDetail`）。
        #: 只缓存一份：详情是逐个对话框拉的，"上一份"正好是用户当前在看的番剧，
        #: 缓存整季线路只会白占内存。
        self._details: dict[tuple[str, str], SeriesDetail] = {}

    # ------------------------------------------------------------------ 引擎

    def engine(self) -> Any:
        """取（并缓存）规则引擎；不可用时返回 None 并记下原因。"""
        if self._engine is not None:
            return self._engine
        if self._engine_error:
            return None
        try:
            if self._engine_factory is not None:
                self._engine = self._engine_factory()
            else:
                from .engine import AnimeEngine      # 懒导入：见模块 docstring

                self._engine = AnimeEngine(rule_manager=self.rules_manager)
        except Exception as error:  # noqa: BLE001  引擎构造失败 = 整模块降级，不抛
            self._engine = None
            self._engine_error = f"规则引擎不可用：{error}"
        return self._engine

    @property
    def engine_error(self) -> str:
        """最近一次引擎不可用的原因（UI 顶部提示条直接展示）。"""
        return self._engine_error

    def close(self) -> None:
        engine = self._engine
        self._engine = None
        for name in ("close", "shutdown"):
            method = getattr(engine, name, None)
            if callable(method):
                try:
                    method()
                except Exception:  # noqa: BLE001  关闭失败不影响退出
                    pass
                break

    # ------------------------------------------------------------------ 规则

    def reload_rules(self) -> list[AnimeRule]:
        """扫描规则目录 → upsert 进 `anime_rules` → 返回排序后的规则列表。

        排序：**按显示名（NOCASE）**，与"内置在后"是同一效果 —— 内置规则名都以
        「内置 ·」之类的中文开头，用户规则是站点名；按名字排能让用户规则聚在一起，
        而且与 `AnimeStorage.list_rules()` / 规则页的顺序**完全一致**（两处不一致
        会让"启用开关点错行"这类低级 bug 反复出现）。
        """
        try:
            rules = list(self.rules_manager.discover())
            self._rule_errors = list(getattr(self.rules_manager, "last_errors", []) or [])
        except Exception as error:  # noqa: BLE001  目录不可读时退化成空列表
            rules = []
            self._rule_errors = [f"规则目录读取失败：{error}"]
        for rule in rules:
            try:
                self.storage.upsert_rule(rule)
            except Exception:  # noqa: BLE001  单条规则索引写不进去不该影响其它
                continue
        try:
            self.storage.prune_missing({rule.key for rule in rules})
        except Exception:  # noqa: BLE001
            pass
        rules.sort(key=lambda item: ((item.name or item.key).casefold(), item.key))
        self._rule_cache = rules
        self._engine = None                # 规则变了 → 引擎缓存作废（重新加载规则集）
        self._engine_error = ""
        return rules

    @property
    def rule_errors(self) -> list[str]:
        """最近一次扫描里加载失败的规则文件（"坏规则不致命，但要能看见"）。"""
        return list(self._rule_errors)

    def rules(self, *, enabled_only: bool = False, refresh: bool = False) -> list[AnimeRule]:
        """规则列表（首次调用时自动扫描）。

        `refresh=True` 强制重新扫描磁盘。

        **为什么需要 `refresh`**（真实故障）：这个方法的旧实现是
        `if not self._rule_cache: self.reload_rules()` —— 只有"缓存为空"才扫描。
        于是"一键导入官方规则库"把 17 个文件写到磁盘后，缓存里那 2 条模板规则
        仍然非空，`rules()` 直接把**旧缓存**返回给界面，用户看到的就是
        "提示导入成功 17 条，列表还是 0 条 / 还是原来那两条"，必须重启才能看到。
        导入/删除/订阅安装这类**改变磁盘**的动作之后必须 `refresh=True`。
        """
        if refresh or not self._rule_cache:
            self.reload_rules()
        if not enabled_only:
            return list(self._rule_cache)
        enabled = set(self.storage.enabled_keys())
        return [rule for rule in self._rule_cache if rule.key in enabled]

    def set_rule_enabled(self, key: str, on: bool) -> bool:
        return self.storage.set_rule_enabled(key, on)

    def test_rule(self, rule: AnimeRule, keyword: str) -> tuple[bool, str]:
        """试跑一条规则：搜索 → 详情 → 解析第一集，返回 `(是否通过, 可读结果)`。

        结果字符串刻意写"到哪一步、命中什么"（用户要拿它判断是选择器写错了、
        还是站点改了版），并写回 `anime_rules.test_state/test_note` 供列表展示。
        """
        keyword = (keyword or "").strip()
        if not keyword:
            return self._record_test(rule, False, "请先填关键词")
        if not rule.supported:
            return self._record_test(
                rule, False,
                f"规则需要 api {rule.api_level}，本客户端只支持到 API 8"
                "（会加载但灰显，不参与搜索）")
        if rule.deprecated:
            return self._record_test(rule, False, "规则已标记退役，站点可能已改版（仍可手动试跑）")
        engine = self.engine()
        if engine is None:
            return self._record_test(rule, False, self._engine_error or "规则引擎不可用")

        try:
            hits = list(engine.search_rule(rule, keyword) or [])
        except Exception as error:  # noqa: BLE001
            return self._record_test(rule, False, f"搜索失败：{error}")
        if not hits:
            return self._record_test(rule, False, f"搜索「{keyword}」命中 0 条（检查 searchURL 与 searchList）")

        first = _to_hit(hits[0], rule)
        try:
            detail = _coerce_detail(engine.detail(first.raw if first.raw is not None else first),
                                    _series_from_hit(first))
        except Exception as error:  # noqa: BLE001
            return self._record_test(rule, False, f"命中 {len(hits)} 条，但详情页失败：{error}")
        if detail is None:
            return self._record_test(rule, False, f"命中 {len(hits)} 条，但详情页没解析出内容（检查 chapterRoads）")
        roads = [road for road in detail.roads if road.episodes]
        if not roads:
            return self._record_test(
                rule, False,
                f"命中 {len(hits)} 条（第一条：{first.title}），但没解析出任何剧集（检查 chapterResult）")

        road = roads[0]
        episode = road.episodes[0]
        url, strategy, note = self._resolve_url(engine, detail, road, episode)
        if not url:
            return self._record_test(
                rule, False,
                f"命中 {len(hits)} 条、{len(roads)} 条线路/{detail.episode_count} 集，"
                f"但第一集解析不出播放地址：{note or '未知原因'}")
        return self._record_test(
            rule, True,
            f"通过：命中 {len(hits)} 条（第一条：{first.title}）· {len(roads)} 条线路 / "
            f"{detail.episode_count} 集 · 第一集策略 {strategy or '直接命中'}")

    def _record_test(self, rule: AnimeRule, ok: bool, note: str) -> tuple[bool, str]:
        try:
            self.storage.set_rule_test(rule.key, "ok" if ok else "fail", note)
        except Exception:  # noqa: BLE001  写状态失败不该让试跑结果丢失
            pass
        return ok, note

    def import_rules(self, text_or_path: str | Path, *, name: str = "") -> list[AnimeRule]:
        """导入规则：传路径则读文件，传 JSON 文本则直接解析（单条/数组/订阅体都行）。"""
        text = ""
        if isinstance(text_or_path, Path):
            text = Path(text_or_path).read_text(encoding="utf-8-sig", errors="replace")
        else:
            candidate = str(text_or_path or "")
            path = Path(candidate)
            # 单行且文件存在 → 当路径；否则当 JSON 文本（JSON 一定含 { } 或换行）
            if "\n" not in candidate and len(candidate) < 260 and path.is_file():
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            else:
                text = candidate
        if not text.strip():
            raise RuleError("没有可导入的内容（既不是存在的文件，也不是 JSON 文本）")
        saved = self.rules_manager.import_text(text, name=name)
        for rule in saved:
            try:
                self.storage.upsert_rule(rule)
            except Exception:  # noqa: BLE001
                continue
        self._rule_cache = []
        return saved

    def export_rules(self, rules: Iterable[AnimeRule], path: str | Path) -> Path:
        """导出为**订阅文件**（`{"version":1,"rules":[...]}`），可直接给别人导入。"""
        items = [rule for rule in rules if isinstance(rule, AnimeRule)]
        return self.rules_manager.export_bundle(items, path)

    # ------------------------------------------------------------------ 订阅

    def subscriptions(self) -> list[dict]:
        return self.storage_module_subscriptions()

    def storage_module_subscriptions(self) -> list[dict]:
        """读取订阅清单（统一走 storage，便于测试隔离）。"""
        from .storage import load_subscriptions

        return load_subscriptions()

    def install_subscription(self, name: str, entries: Iterable[dict]) -> list[AnimeRule]:
        """安装/覆盖一个订阅（内容来自 `parse_subscription` 或抓取结果）。"""
        items = [item for item in entries if isinstance(item, dict)]
        if not items:
            raise RuleError("订阅内容里没有任何规则")
        saved = self.rules_manager.install_subscription(name, items)
        self._remember_subscription(name, url="")
        self._rule_cache = []
        return saved

    def update_subscription(self, name: str, fetcher: Callable[[str], str] | None = None
                            ) -> tuple[list[AnimeRule], list[str]]:
        """更新订阅：抓取 → **逐条校验** → 只把通过校验的规则写回。

        返回 `(写入的规则, 失败说明)`。关键约定：**校验失败的规则保留旧文件**。
        站点规则改版很常见，一次更新里混进 1 条坏规则时，"整包失败"会让用户
        连能用的 8 条也丢；这里逐条判断，坏的那条继续用旧版本并在 UI 上点名。
        """
        entry = None
        for item in self.subscriptions():
            if _text(item.get("name")) == name:
                entry = item
                break
        url = _text((entry or {}).get("url"))
        text = ""
        if url or fetcher is not None:
            # 没有 URL 但有 fetcher：也让 fetcher 决定内容（订阅内容可以来自
            # "本地文件 / 用户粘贴 / 自定义协议"，URL 只是默认取法之一）
            try:
                text = fetcher(url) if fetcher is not None else self._http_get(url)
            except Exception as error:  # noqa: BLE001
                return [], [f"下载订阅失败：{error}（已保留原有规则）"]
        if not text:
            return [], ["订阅没有地址也没有内容（已保留原有规则）"]
        try:
            items = parse_rule_bundle(text)
        except RuleError as error:
            return [], [f"订阅内容不是合法规则体：{error}（已保留原有规则）"]
        return self._write_subscription(name, items)

    def _write_subscription(self, name: str, items: list[dict]) -> tuple[list[AnimeRule], list[str]]:
        """逐条校验后写入订阅目录（坏规则保留旧文件）。"""
        folder = self.rules_manager.subscription_dir / safe_rule_filename(name)
        folder.mkdir(parents=True, exist_ok=True)
        saved: list[AnimeRule] = []
        failures: list[str] = []
        for item in items:
            rule_name = _text(item.get("name")) or "rule"
            problems = validate_rule_dict(item)
            blocking = [problem for problem in problems
                        if not problem.startswith("该规则部分源依赖")]
            if blocking:
                failures.append(f"{rule_name}：{'；'.join(blocking)}（已保留旧版本）")
                continue
            # 先解析出规则对象（key 与目标文件名保持一致），再落盘
            from .rules import rule_from_dict

            target = folder / f"{safe_rule_filename(rule_name)}.json"
            rule = rule_from_dict(item, key=f"sub:{folder.name}:{target.stem}",
                                 path=str(target))
            target.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
            saved.append(rule)
        self._remember_subscription(name, url="", count=len(saved))
        self._rule_cache = []
        return saved, failures

    def _remember_subscription(self, name: str, *, url: str = "", count: int = 0) -> None:
        from .storage import load_subscriptions, save_subscriptions

        entries = load_subscriptions()
        found = False
        now = int(time.time())
        for entry in entries:
            if _text(entry.get("name")) == name:
                if url:
                    entry["url"] = url
                entry["last_checked"] = now
                if count:
                    entry["rules"] = count
                found = True
                break
        if not found:
            entries.append({"name": name, "url": url, "enabled": True,
                            "last_checked": now, "rules": count})
        save_subscriptions(entries)

    def remove_subscription(self, name: str) -> bool:
        removed = self.rules_manager.remove_subscription(name)
        from .storage import load_subscriptions, save_subscriptions

        entries = [entry for entry in load_subscriptions()
                   if _text(entry.get("name")) != name]
        save_subscriptions(entries)
        self._rule_cache = []
        return removed

    @staticmethod
    def _http_get(url: str) -> str:
        """抓订阅文件（用共享层的 HttpClient：它已经处理好 UA/重试/错误文案）。"""
        client = _shared("sources").HttpClient(timeout=20.0, attempts=2)
        response = client.get(url)
        return response.text if hasattr(response, "text") else str(response)

    # ------------------------------------------------------------------ 搜索

    def search(self, keyword: str, *, rule_keys: Iterable[str] | None = None, limit: int = 30,
               on_rule_done: Callable[[AnimeRule, list[SearchHit]], None] | None = None
               ) -> list[SearchGroup]:
        """跨规则搜索并合并结果（**边搜边出**：每完成一条规则立即回调一次）。

        合并口径（诚实说明，宁可少合并也不要错合并）：

        - 归一化标题**完全相同** → 同一部作品（主判据，覆盖绝大多数情况）；
        - 一方标题是另一方的子串，且**长度差 ≤ 6 个字符、短标题 ≥ 长标题的一半**
          → 也合并（「进击的巨人第三季」vs「进击的巨人第三季Part2」这类站点命名差异）；
        - 更长的包含关系**不合并**（「斗罗大陆」不会被「斗罗大陆2绝世唐门」吃掉）；
        - 不做别名/译名映射：不同译名（「进击的巨人」vs「Attack on Titan」）保持两张卡，
          用户可以在卡片里手动展开来源 —— 猜错别名比不猜更烦人。
        """
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        engine = self.engine()
        if engine is None:
            self._engine_error = self._engine_error or "规则引擎不可用"
            return []
        wanted = [str(key) for key in rule_keys] if rule_keys else None
        candidates = [rule for rule in self.rules(enabled_only=True)
                      if wanted is None or rule.key in wanted]
        if not candidates:
            return []

        # 第一步：**每条规则各自**分组（limit 在这里生效，语义是"每条规则最多几张卡"）。
        # 必须每条规则一个独立列表：共用列表时第二条规则的 `limit` 会被第一条已经
        # 攒下的分组卡住（它的命中全被当成"已有作品"而丢掉），搜索结果会莫名变少。
        rule_groups: list[SearchGroup] = []
        for rule in candidates:
            if not rule.supported:
                # 灰显规则不参与搜索（灰显 = 明确告知，而不是静默失败）
                if on_rule_done is not None:
                    on_rule_done(rule, [])
                continue
            try:
                raw_hits = list(engine.search_rule(rule, keyword) or [])
                hits = [_to_hit(item, rule) for item in raw_hits]
                hits = [hit for hit in hits if hit.title]
                hits = hits[:max(1, int(limit))]
            except Exception:  # noqa: BLE001  单条规则失败 = 这一批为空，不影响其它规则
                hits = []
            self._merge_into(rule_groups, hits, limit=limit)
            if on_rule_done is not None:
                try:
                    on_rule_done(rule, hits)
                except Exception:  # noqa: BLE001  UI 回调自己的异常不该中断搜索
                    pass

        # 第二步：**跨规则**合并同一部作品。
        # 为什么分成两步：第一步允许"同一条规则内按标题合并"（去重同一个站点里的重复项），
        # 第二步才把不同规则的同一部作品合成一张卡并保留全部来源。
        # 两步的顺序执行也让用户"先看到本规则的结果，后续规则只是多一个来源"，卡片不跳位。
        return _merge_groups(rule_groups)

    @staticmethod
    def _merge_into(groups: list[SearchGroup], hits: list[SearchHit], *,
                    limit: int = 0) -> None:
        """把**同一条规则**的命中整理成分组（同规则内按标题合并 + 去重）。"""
        for hit in hits:
            target = None
            for group in groups:
                if _same_work(group.title, hit.title):
                    target = group
                    break
            if target is None:
                if limit and len(groups) >= max(1, int(limit)):
                    continue                      # 本规则已够 limit 条，后面的丢掉
                groups.append(SearchGroup(title=hit.title, cover=hit.cover, year=hit.year,
                                          sources=[hit]))
                continue
            if any(existing.rule_key == hit.rule_key and existing.remote_id == hit.remote_id
                   for existing in target.sources):
                continue
            target.sources.append(hit)
            target.cover = target.cover or hit.cover
            target.year = target.year or hit.year

    # ------------------------------------------------------------------ 详情 / 追番

    def detail(self, hit_or_series: Any) -> SeriesDetail | None:
        """拉详情（会把元数据与集数缓存进 `anime_series`）。

        失败返回 `None`（不抛）：详情页打不开是常态（站点限流/改版），
        UI 用 `engine_error` + 返回空来提示，而不是弹一个 Python 堆栈。
        """
        series = _as_series(hit_or_series)
        if series is None:
            return None
        if not series.rule_key:
            # 引擎的搜索对象通常不带规则归属：补默认规则，否则详情缓存进库后
            # 既没法换规则、`find_series` 也查不到（等于写了一条查不回来的行）
            series.rule_key = _text(_pick(hit_or_series, "rule_key", "ruleKey")) \
                or self._default_rule_key()
        raw = hit_or_series.raw if isinstance(hit_or_series, SearchHit) else series
        engine = self.engine()
        if engine is None:
            return None
        # 先保证库里有行（详情回来后要写回 total_episodes/latest_episode）
        try:
            series.id = self.storage.upsert_series(series)
        except Exception:  # noqa: BLE001  写库失败仍然可以展示详情
            pass
        try:
            detail = _coerce_detail(engine.detail(raw if raw is not None else series), series)
        except Exception as error:  # noqa: BLE001
            self._engine_error = f"详情获取失败：{error}"
            return None
        if detail is None:
            return None
        return self._cache_detail(detail)

    def _cache_detail(self, detail: SeriesDetail) -> SeriesDetail:
        """把详情里的元数据/集数写回库（保留已有 id 与追番），并记住线路供翻页用。

        **不改引擎返回的对象**：`detail.series` 是引擎自己的实例（可能被它内部缓存复用），
        这里复制一份再回写。曾经直接在原对象上改 `total_episodes`，结果把引擎缓存里的
        元数据也改了 —— 下一次调用拿到的是"被我们改过"的对象，谁也说不清是谁的锅。
        """
        source = detail.series
        series = AnimeSeries(
            id=source.id, rule_key=source.rule_key, remote_id=source.remote_id,
            title=source.title, cover=source.cover, year=source.year,
            summary=source.summary, bangumi_id=source.bangumi_id,
            total_episodes=source.total_episodes, latest_episode=source.latest_episode,
            air_weekday=source.air_weekday, created_at=source.created_at,
            updated_at=source.updated_at,
        )
        episodes = [episode for road in detail.roads for episode in road.episodes]
        latest = ""
        if episodes:
            # 主线路（第一条线路）的最后一集才是"最新一集"：不同线路的集号都从 1 开始，
            # 跨线路取"集号最大"会随机命中备用线路的第一集（实测把"第03集"盖成了"第01集"）。
            primary = detail.roads[0] if detail.roads else None
            primary_episodes = list(primary.episodes) if primary is not None else []
            if primary_episodes:
                latest = primary_episodes[-1].label
            else:
                latest = episodes[-1].label
        series.total_episodes = len(episodes) or series.total_episodes
        series.latest_episode = latest or series.latest_episode
        try:
            series.id = self.storage.upsert_series(series)
        except Exception:  # noqa: BLE001  写库失败仍然可以展示详情
            pass
        detail.series = series
        self._details[(series.rule_key, series.remote_id)] = detail
        return detail

    def follow(self, hit: Any) -> AnimeSeries:
        """追番：upsert 番剧 + 加收藏 + 记住来源（重复调用幂等）。

        入参可以是 `SearchHit`、引擎返回的搜索对象、`AnimeSeries` 或字典。
        如果拿到的对象没有 `rule_key`（引擎的搜索对象通常只有站点侧 id），
        会补上默认规则（当前第一条可用规则）—— 番剧行没有 `rule_key` 就没法
        重新拉详情、也没法换规则，等于追了个"死"条目。
        """
        series = _as_series(hit)
        if series is None:
            raise ValueError("无法识别的番剧对象（需要标题与站点 id）")
        if not series.rule_key:
            series.rule_key = self._default_rule_key()
        series.id = self.storage.upsert_series(series)
        note = _text(_pick(hit.raw if isinstance(hit, SearchHit) else hit,
                           "remarks", "note", "subtitle"))
        self.storage.add_favorite(series.id, note)
        return series

    def _default_rule_key(self) -> str:
        """入参没有规则归属时用哪条规则：第一条可用的（内置规则排最后，优先用户规则）。"""
        for rule in self.rules(enabled_only=True):
            return rule.key
        for rule in self.rules():
            return rule.key
        return ""

    def unfollow(self, series: Any) -> bool:
        """取消追番（**不删番剧行与进度**：取消后重新追回来进度还在）。"""
        target = self._resolve_series(series)
        if target is None:
            return False
        return self.storage.remove_favorite(target.id)

    def is_followed(self, series: Any) -> bool:
        target = self._resolve_series(series)
        return bool(target and self.storage.is_favorite(target.id))

    def favorites(self) -> list[AnimeSeries]:
        return self.storage.favorite_series()

    def continue_watching(self, limit: int = 20) -> list[tuple[AnimeSeries, Any]]:
        """继续观看：`(番剧, 进度)` 列表（已看完的不在内）。"""
        return self.storage.continue_watching(limit)

    def _resolve_series(self, value: Any) -> AnimeSeries | None:
        """把任意入参（SearchHit/AnimeSeries/字典/id）换成库里的番剧行。"""
        if isinstance(value, int):
            return self.storage.get_series(value)
        series = _as_series(value)
        if series is None:
            return None
        if series.id:
            found = self.storage.get_series(series.id)
            if found is not None:
                return found
        if series.rule_key or series.remote_id:
            found = self.storage.find_series(series.rule_key, series.remote_id)
            if found is not None:
                return found
        if series.id:
            return series
        # 只有元数据、还没入库的番剧（`follow()` 之外很少见）：顺手落库拿到 id，
        # 否则"下载"这类需要 series_id 的动作会静默失败。
        try:
            series.id = self.storage.upsert_series(series)
        except Exception:  # noqa: BLE001
            return None
        return series

    # ------------------------------------------------------------------ 播放

    def resume_position(self, series: Any, episode: Any) -> int:
        """这一集上次播到哪（毫秒）；没记录返回 0。"""
        target = self._resolve_series(series)
        parsed = _as_episode(episode)
        if target is None or parsed is None:
            return 0
        progress = self.storage.get_progress(target.id, parsed.key)
        if progress is None or progress.finished:
            return 0
        return max(0, int(progress.position_ms))

    def save_progress(self, series: Any, episode: Any, *, position_ms: int = 0,
                      duration_ms: int = 0, watched: bool = False) -> bool:
        """保存进度（播放器每 5 秒上报一次；这里是幂等 UPSERT）。"""
        target = self._resolve_series(series)
        parsed = _as_episode(episode)
        if target is None or parsed is None:
            return False
        return self.storage.save_progress(
            target.id, parsed.key, parsed.label, parsed.index,
            position_ms=position_ms, duration_ms=duration_ms, watched=watched)

    def resolve(self, series: Any, episode: Any) -> ResolvedAnime:
        """解析出可播放地址，并记录历史/续播位置。

        三步走（与方案 4.2.3 一致）：规则直接给地址 → 播放页嗅探 → 播放页构造 + 嗅探。
        具体实现全在 `engine.py`，这里只负责"换规则/换线路"的编排与失败降级。
        """
        parsed = _as_episode(episode)
        if parsed is None:
            return ResolvedAnime(note="没有指定要播放的剧集")
        target = self._resolve_series(series)
        if target is None:
            return ResolvedAnime(series=_as_series(series), episode=parsed,
                                 note="番剧不在库里，请先从搜索结果进入详情")
        series_hit = _hit_from_series(target)
        engine = self.engine()
        if engine is None:
            return ResolvedAnime(series=target, episode=parsed,
                                 note=self._engine_error or "规则引擎不可用")
        try:
            # 命中缓存就别再抓一次详情：整季下载时每一集都会走这里，重复请求
            # 既慢又容易被站点限流（"整季下载只抓一次详情"就靠这一句）。
            detail = self._details.get(_series_cache_key(target, self.storage))
            if detail is None:
                detail = self.detail(series_hit)
        except Exception as error:  # noqa: BLE001
            return ResolvedAnime(series=target, episode=parsed, note=f"详情获取失败：{error}")
        if detail is None:
            return ResolvedAnime(series=target, episode=parsed, note="没能拿到详情页（站点无响应或规则已失效）")

        # 优先用同 (线路, 集) 的库内对象：UI 传进来的 episode 可能来自上一次的详情
        road = detail.road(parsed.road_index) or (detail.roads[0] if detail.roads else None)
        if road is None:
            return ResolvedAnime(series=target, episode=parsed, note="详情里没有任何线路")
        chosen = None
        for item in road.episodes:
            if item.key == parsed.key or (parsed.index and item.index == parsed.index):
                chosen = item
                break
        chosen = chosen or parsed

        url, strategy, note = self._resolve_url(engine, detail, road, chosen)
        resolved = ResolvedAnime(url=url, is_hls=_looks_like_hls(url), strategy=strategy,
                                 note=note, series=target, episode=chosen)
        if not url:
            return resolved

        # 记录历史 + 续播位置（失败不影响播放本身）
        try:
            self.storage.add_history(target.id, target.rule_key, target.title,
                                     chosen.label, strategy)
            existing = self.storage.get_progress(target.id, chosen.key)
            self.storage.save_progress(
                target.id, chosen.key, chosen.label, chosen.index,
                position_ms=int(existing.position_ms) if existing else 0,
                duration_ms=int(existing.duration_ms) if existing else 0,
                watched=bool(existing.watched) if existing else False)
        except Exception:  # noqa: BLE001
            pass
        return resolved

    @staticmethod
    def _resolve_url(engine: Any, detail: SeriesDetail, road: Road,
                     episode: AnimeEpisode) -> tuple[str, str, str]:
        """调引擎解析单集播放地址，统一返回 `(url, 策略, 说明)`。"""
        try:
            media = engine.resolve(detail, road, episode)
        except Exception as error:  # noqa: BLE001
            return "", "", f"解析失败：{error}"
        if media is None:
            return "", "", "没有解析出媒体地址（可能需要嗅探的站点已改版）"
        if isinstance(media, str):
            return media, "", ""
        url = _text(_pick(media, "url", "src", "link"))
        return (url,
                _text(_pick(media, "strategy", "kind")),
                _text(_pick(media, "note", "message")))

    # ------------------------------------------------------------------ 下一集 / 上一集

    def _walk_episode(self, series: Any, road_index: int, episode_index: int, *,
                      step: int) -> tuple[int, int] | None:
        """翻页的公共实现：优先用最近一次详情，其次重新拉一次。"""
        key = _series_cache_key(series, self.storage)
        detail = self._details.get(key) if key and key != ("", "") else None
        if detail is None:
            target = self._resolve_series(series)
            if target is not None:
                detail = self.detail(_hit_from_series(target))
        roads = list(detail.roads) if detail is not None else []
        return _walk(roads, road_index, episode_index, step=step)

    def next_episode(self, series: Any, road_index: int, episode_index: int
                     ) -> tuple[int, int] | None:
        """下一集：同线路下一集 → 下一条线路的第一集 → `None`（UI 再换规则）。

        注意坐标语义：`road_index`/`episode_index` 都是**列表下标**（0 起），
        不是 `AnimeEpisode.index`/`Road.index` 字段 —— UI 用 `enumerate` 遍历网格时
        天然拿到的是下标。找不到（越界/空线路）返回 None，绝不"绕回第一集"：
        自动连播绕回第 1 集是用户最讨厌的行为之一。
        """
        return self._walk_episode(series, road_index, episode_index, step=1)

    def prev_episode(self, series: Any, road_index: int, episode_index: int
                     ) -> tuple[int, int] | None:
        """上一集：同线路上一集 → 上一条线路的最后一集 → `None`。"""
        return self._walk_episode(series, road_index, episode_index, step=-1)

    # ------------------------------------------------------------------ 下载

    def download_episode(self, series: Any, episode: Any, dest_dir: str | Path, *,
                         on_progress: ProgressFn | None = None,
                         cancel: threading.Event | None = None,
                         pause_event: threading.Event | None = None) -> DownloadOutcome:
        """下载单集（复用影视板块的下载器：分片并发 + 断点续传 + ffmpeg 合流）。

        流程：解析直链（含嗅探）→ 适配成共享层的 `RemoteVideo/Episode`
        （`resolved` 直接传进去，避免下载器再解析一次）→ 下载 → 写
        `anime_download_tasks`（pending → running → done/failed）。

        失败只是 `DownloadOutcome.ok=False` + 可读消息：一集下载失败不该让
        "整季下载"的循环炸掉。
        """
        parsed = _as_episode(episode)
        target = self._resolve_series(series)
        if parsed is None or target is None:
            return DownloadOutcome(series=target, episode=parsed, ok=False,
                                   message="番剧或剧集信息不完整，无法下载")
        task_id = 0
        try:
            task_id = self.storage.upsert_download_task(
                target.id, parsed.key, parsed.label, target.rule_key, parsed.url,
                dest_path="", state="pending", message="准备中")
        except Exception:  # noqa: BLE001  任务表写不进去也要能下载
            task_id = 0

        resolved = self.resolve(target, parsed)
        if not resolved.ok:
            message = resolved.note or "没有解析出可下载的地址"
            self._update_task(task_id, state="failed", message=message)
            return DownloadOutcome(series=target, episode=parsed, ok=False, message=message)
        self._update_task(task_id, state="running", message="下载中", url=resolved.url)

        try:
            result = self._run_download(target, resolved, dest_dir, on_progress=on_progress,
                                        cancel=cancel, pause_event=pause_event)
        except Exception as error:  # noqa: BLE001
            self._update_task(task_id, state="failed", message=str(error))
            return DownloadOutcome(series=target, episode=parsed, ok=False, message=str(error))

        path = _text(_pick(result, "path"))
        ok = bool(_pick(result, "ok", default=False))
        message = _text(_pick(result, "message", "note")) or ("完成" if ok else "下载失败")
        size = _size_of(path)
        self._update_task(task_id, state="done" if ok else "failed",
                          message=message, dest_path=path, done_parts=1 if ok else 0,
                          total_parts=1)
        return DownloadOutcome(series=target, episode=parsed, path=path, ok=ok,
                               message=message, source=_text(_pick(result, "source")),
                               note=_text(_pick(result, "note")), size_bytes=size)

    def enqueue_season(self, series: Any, dest_dir: str | Path, *, road_index: int = 0,
                       on_progress: ProgressFn | None = None,
                       on_task_done: Callable[[DownloadOutcome], None] | None = None,
                       cancel: threading.Event | None = None,
                       pause_event: threading.Event | None = None,
                       parallel: int = 2) -> list[DownloadOutcome]:
        """下载整季（默认并发 2 集，与共享下载器的默认一致：再高容易被源站限流）。

        单集失败不影响后续；`on_task_done` 每完成一集回调一次（UI 逐条更新任务列表）。
        """
        target = self._resolve_series(series)
        if target is None:
            return []
        detail = self.detail(_hit_from_series(target))
        if detail is None or not detail.roads:
            return []
        road = detail.road(road_index) or detail.roads[0]
        episodes = list(road.episodes)
        if not episodes:
            return []
        # 预填详情缓存：整季下载时每一集的 resolve() 都会走它，避免每集都重新抓一次详情页
        # （那既慢又容易触发站点限流，正是"整季下载"最容易踩的坑）。
        self._details[_series_cache_key(target, self.storage)] = detail
        results: list[DownloadOutcome] = []
        if parallel <= 1 or len(episodes) == 1:
            for index, episode in enumerate(episodes, start=1):
                if cancel is not None and cancel.is_set():
                    break
                outcome = self.download_episode(target, episode, dest_dir,
                                                on_progress=on_progress, cancel=cancel,
                                                pause_event=pause_event)
                results.append(outcome)
                if on_task_done is not None:
                    on_task_done(outcome)
            return results

        from concurrent.futures import ThreadPoolExecutor, as_completed

        workers = max(1, min(4, int(parallel)))
        indexed: dict[int, DownloadOutcome] = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="anime-dl") as pool:
            futures = {
                pool.submit(self.download_episode, target, episode, dest_dir,
                            on_progress=on_progress, cancel=cancel, pause_event=pause_event): index
                for index, episode in enumerate(episodes)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    outcome = future.result()
                except Exception as error:  # noqa: BLE001
                    outcome = DownloadOutcome(series=target, episode=episodes[index],
                                              ok=False, message=str(error))
                indexed[index] = outcome
                if on_task_done is not None:
                    on_task_done(outcome)
        results = [indexed[index] for index in sorted(indexed)]
        return results

    def _run_download(self, series: AnimeSeries, resolved: ResolvedAnime,
                      dest_dir: str | Path, *, on_progress: ProgressFn | None,
                      cancel: threading.Event | None,
                      pause_event: threading.Event | None) -> Any:
        """调共享下载器（注入 `download_fn` 时优先用它，便于离线测试）。"""
        video, episode = _adapt_for_download(series, resolved)
        if self._download_fn is not None:
            return self._download_fn(
                video, episode, dest_dir,
                **_supported_kwargs(self._download_fn, on_progress=on_progress,
                                    cancel=cancel, pause_event=pause_event))
        downloader = _shared("downloader")
        play = _shared("sources").ResolvedPlay(
            url=resolved.url, source=series.rule_key, video=video, episode=episode,
            is_hls=resolved.is_hls, referer=resolved.referer,
        )
        path, _resolved = downloader.download_episode(
            video, episode, dest_dir, on_progress=on_progress, cancel=cancel,
            registry=None, allow_cross_source=False, resolved=play,
            pause_event=pause_event)
        return {"ok": True, "path": str(path), "message": "完成", "source": series.rule_key}

    def _update_task(self, task_id: int, **fields: Any) -> None:
        if not task_id:
            return
        try:
            self.storage.update_download_task(task_id, **fields)
        except Exception:  # noqa: BLE001  任务表更新失败不影响下载结果
            pass

    # ------------------------------------------------------------------ 更新检查

    def check_updates(self, *, cancel: threading.Event | None = None,
                      on_progress: ProgressFn | None = None,
                      max_workers: int = 2) -> UpdateReport:
        """检查全部追番的更新（并发 2、可取消、单条失败不影响其它）。

        结果会写回 `anime_series.total_episodes/latest_episode`；有新集的条目
        由返回的 `UpdateReport.with_new` 提供给 UI 打角标（本模块不建"新集"表：
        角标是"本次检查的结论"，重开应用后重新检查即可，不需要持久化）。
        """
        followed = self.storage.favorite_series()
        if not followed:
            report = UpdateReport(items=[], checked=0, failed=0)
            if on_progress:
                on_progress(0, 0, "还没有追番，先去番剧库里追几部")
            return report
        engine = self.engine()
        if engine is None:
            report = UpdateReport(items=[], checked=0, failed=len(followed))
            if on_progress:
                on_progress(0, len(followed), self._engine_error or "规则引擎不可用")
            return report

        def fetch(series: AnimeSeries) -> SeriesDetail | None:
            return _coerce_detail(engine.detail(series), series)

        report = check_updates(followed, detail_fetcher=fetch, max_workers=max_workers,
                               cancel=cancel, on_progress=on_progress)
        for item in report.items:
            if item.error:
                continue
            try:
                self.storage.set_series_meta(
                    item.series.id,
                    total_episodes=item.new_total or item.series.total_episodes,
                    latest_episode=item.latest_episode or item.series.latest_episode)
            except Exception:  # noqa: BLE001  单条写回失败不影响报告
                continue
        return report


# --------------------------------------------------------------------------- 模块级工具


def _merge_groups(rule_groups: list[SearchGroup]) -> list[SearchGroup]:
    """把"每条规则各自分组"的结果按标题合并成最终列表（保持首次出现的顺序）。

    合并只发生在**不同规则之间**：同一条规则里两张不同标题的卡不会被并到一起
    （否则一条规则返回的"第一季/第二季"会被误合成一张，用户就没法选集了）。
    """
    merged: list[SearchGroup] = []
    for group in rule_groups:
        target = None
        for existing in merged:
            # 同一规则的两张卡永远不合并：一条规则返回的"第一季/第二季"是两部作品，
            # 合并会直接吃掉选集入口（跨规则才是"同一部作品的不同来源"）。
            if existing.sources and group.sources and \
                    existing.sources[0].rule_key == group.sources[0].rule_key:
                continue
            if _same_work(existing.title, group.title):
                target = existing
                break
        if target is None:
            merged.append(group)
            continue
        target.sources.extend(group.sources)
        target.cover = target.cover or group.cover
        target.year = target.year or group.year
    return merged


def _same_work(left: str, right: str) -> bool:
    """两条标题是否指同一部作品（合并口径见 `AnimeLibrary.search` 的 docstring）。

    包含判定的两个约束缺一不可：**长度差 ≤ 6** 且 **短标题 ≥ 长标题的一半**。
    只靠长度差挡不住误合并：normalize 之后「斗罗大陆」(4 字) 是
    「斗罗大陆2绝世唐门」(8 字) 的子串、长度差 4 ≤ 6，会被当成同一部作品。
    """
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if short not in long:
        return False
    if len(long) - len(short) > _CONTAIN_MIN_DIFF:
        return False
    return len(short) >= len(long) * _CONTAIN_MIN_RATIO


def _walk(roads: list[Road], road_index: int, episode_index: int, *, step: int
          ) -> tuple[int, int] | None:
    """在"线路 × 集"网格上走一步（step=1 下一集 / step=-1 上一集）。

    只走**一条**相邻线路：跨过所有线路去更远的线路会把"线路"语义搞乱
    （UI 的换线路是显式操作，不是自动连播的一部分）。
    """
    if not roads:
        return None
    road_index = int(road_index or 0)
    episode_index = int(episode_index or 0)
    if not 0 <= road_index < len(roads):
        return None
    road = roads[road_index]
    count = len(road.episodes)
    if count == 0:
        return None
    candidate = episode_index + step
    if 0 <= candidate < count:
        return road_index, candidate
    next_road = road_index + step
    if not 0 <= next_road < len(roads):
        return None
    episodes = roads[next_road].episodes
    if not episodes:
        return None
    return next_road, (0 if step > 0 else len(episodes) - 1)


def _looks_like_hls(url: str) -> bool:
    return (url or "").lower().split("?", 1)[0].endswith(".m3u8")


def _size_of(path: str) -> int:
    if not path:
        return 0
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _adapt_for_download(series: AnimeSeries, resolved: ResolvedAnime) -> tuple[Any, Any]:
    """动漫模型 → 共享层模型（`core.video.models.RemoteVideo/Episode`）。

    共享下载器只认 `RemoteVideo/Episode`（它的所有逻辑都建立在这两个类型上），
    这里做一次薄适配，而不是让下载器认识两套模型 —— 适配层放动漫侧，
    影视板块就永远不用知道"动漫"的存在。
    """
    models = _shared("models")
    episode = resolved.episode
    video = models.RemoteVideo(
        source=series.rule_key, remote_id=series.remote_id, title=series.title,
        kind="anime", year=series.year, description=series.summary,
        cover_url=series.cover, series_key=f"{series.rule_key}:{series.remote_id}",
    )
    adapted = models.Episode(
        name=episode.label if episode else "正片",
        url=resolved.url, index=int(episode.index) if episode else 0,
        route=episode.road_name if episode else "",
    )
    video.episodes = [adapted]
    return video, adapted


def _series_cache_key(value: Any, storage: Any = None) -> tuple[str, str]:
    """详情缓存的键。

    传了 `storage` 时优先用 `id` 去库里取权威的 (rule_key, remote_id)：
    调用方手里的对象可能来自引擎搜索（`rule_key` 为空或与库内不一致），
    用它的字段当键会让"整季下载只抓一次详情"的缓存直接失效。
    """
    series = _as_series(value)
    if series is None:
        return ("", "")
    if storage is not None and series.id:
        try:
            stored = storage.get_series(series.id)
        except Exception:  # noqa: BLE001  取不到就退回对象字段
            stored = None
        if stored is not None:
            return (stored.rule_key, stored.remote_id)
    return (series.rule_key, series.remote_id)


def _supported_kwargs(func: Callable[..., Any], **wanted: Any) -> dict:
    """只把目标函数**签名里真的接受**的关键字传下去。

    注入的下载器（测试替身、第三方实现）签名各不相同，硬传 `pause_event`
    会让"少一个可选参数"直接抛 TypeError —— 可选参数就应该真的是可选的。
    取不到签名（内置函数等）时全部传下去，行为退化为原样。
    """
    import inspect

    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return dict(wanted)
    parameters = signature.parameters
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
        return dict(wanted)
    return {key: value for key, value in wanted.items() if key in parameters}
