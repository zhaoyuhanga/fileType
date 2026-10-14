"""规则执行引擎：搜索 → 详情（线路/剧集）→ 播放地址解析。

设计取舍（为什么长这样）：

- **一份逻辑跑两种模式**：XPath 模式与 API 模式的差异只在"怎么取值"，而取值统一交给
  `selectors.Selector`，所以这里不再写两套流程，只在取数据处分支；
- **限速是"每规则"的**：`rateLimit.qps` 是站点方的礼貌约束，同一规则串行请求才需要间隔；
  用 `time.monotonic()` 而不是 `time.time()`（后者会被系统对时/夏令时往回跳，
  真机上表现为"偶发不做限速"，也可能反过来卡住很久）；
- **搜索失败不中断**：一条规则挂掉（站点改版、被墙、解析器匹配不到）不能拖垮整个搜索，
  失败原因按规则记在 `note_for()` 里，UI 直接展示"哪条规则、为什么没结果"；
- **嗅探器单独一份**：`resolve()` 只负责"从便宜到昂贵"地调用它，嗅探细节全在 `sniffer.py`。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

from ..platform.http import HttpClient, host_of, looks_like_media_url, origin_of
from .models import (
    AnimeEpisode,
    AnimeRule,
    AnimeSeries,
    Road,
    SeriesDetail,
    SniffedMedia,
    parse_episode_number,
)
from .rules import RuleError, substitute, substitute_url
from .selectors import Selector, SelectorError, looks_like_xpath, parse_json
from .sniffer import Sniffer

#: 详情里"什么都没匹配到"的提示（**可读原因**比空列表重要）
EMPTY_HITS = "选择器没有匹配到任何结果：站点可能已改版，或该规则的 searchList 选择器需要更新"
EMPTY_CHAPTERS = "章节选择器没有匹配到任何剧集：站点可能已改版，或该规则的 chapterRoads/chapterResult 需要更新"

#: 播放地址解析的提示（嗅探失败时拼进 SniffedMedia.note）
RESOLVE_NOTE = ("规则给出的地址是播放页而非媒体文件，且静态嗅探没找到真实地址："
                "该站可能依赖 JS 渲染（我们无浏览器内核），"
                "可尝试在浏览器中打开该页并复制地址后手动播放。")


@dataclass
class SearchHit:
    """一条搜索结果。

    `remote_id` 是**规则自己的作品标识**（API 模式的 `sourcePath` 取值，或详情页 URL），
    换源、详情、进度都靠它定位；`url` 是已知的绝对详情页地址（XPath 模式才有）。
    """

    rule_key: str = ""
    rule_name: str = ""
    title: str = ""
    remote_id: str = ""
    url: str = ""
    cover: str = ""
    year: str = ""
    note: str = ""

    def key(self) -> str:
        return f"{self.rule_key}:{self.remote_id}"


#: 门面包照旧导出 `SniffResult`（`core/anime/__init__.py` 里的名字），
#: 我们内部统一用 `models.SniffedMedia`：同一个对象，避免两套字段。
SniffResult = SniffedMedia


class Throttle:
    """按 QPS 的串行限速器（每规则一个）。

    只保证"相邻两次请求之间至少间隔 `1/qps` 秒"——不做令牌桶、不并发，
    因为采集站的礼貌要求就是别把人家打满，简单够用。
    """

    def __init__(self, qps: float, *, clock=time.monotonic, sleep=time.sleep) -> None:
        self.interval = 1.0 / qps if qps and qps > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        # 用 None 而不是 0.0 当"还没发过请求"的哨兵：`time.monotonic()` 完全可能是
        # 一个很小的值，用 0.0 判断第一次会让限速在进程刚起来时静默失效。
        self._last: float | None = None

    def wait(self) -> float:
        """必要时等待；返回实际等待秒数（测试据此断言，不靠 `time.sleep` 计时）。"""
        if self.interval <= 0:
            return 0.0
        now = self._clock()
        if self._last is not None:
            remain = self.interval - (now - self._last)
            if remain > 0:
                self._sleep(remain)
                now = self._clock()
                self._last = now
                return remain
        self._last = now
        return 0.0


class AnimeEngine:
    """执行规则的引擎。

    `http_factory(rule) -> 类 HttpClient 对象` 与 `sniffer` 都可注入（测试离线跑）；
    默认按规则的 `userAgent`/`referer`/`timeout`/`retries` 建客户端。

    `rules` 可以省略并改用 `rule_manager`（`library.AnimeLibrary` 就是这么建的）：
    规则集是可变的，业务层只管把 manager 交进来，本类在需要时自己去发现规则。
    """

    def __init__(self, rules: list[AnimeRule] | None = None, *,
                 http_factory=None, sniffer=None, rule_manager=None) -> None:
        self.rules = list(rules or [])
        self.rule_manager = rule_manager
        self._http_factory = http_factory
        self._sniffer = sniffer
        self._clients: dict[str, Any] = {}
        self._throttles: dict[str, Throttle] = {}
        self._notes: dict[str, str] = {}
        #: `chapterApiConfig.variables` 最近一次收集到的响应变量（episodePage 模板要用）
        self._last_variables: dict[str, Any] = {}

    # ------------------------------------------------------------------ 查询辅助

    def rule(self, key: str) -> AnimeRule | None:
        for item in self.available_rules():
            if item.key == key:
                return item
        return None

    def available_rules(self) -> list[AnimeRule]:
        """当前规则集；自己没拿到规则时向 `rule_manager` 要一次（并缓存）。"""
        if not self.rules and self.rule_manager is not None:
            discover = getattr(self.rule_manager, "discover", None)
            if callable(discover):
                try:
                    self.rules = list(discover() or [])
                except Exception:  # noqa: BLE001  规则目录读不了不该炸掉搜索
                    self.rules = []
        return self.rules

    def find_rule(self, rule_or_key) -> AnimeRule | None:
        """把 `AnimeRule` / 规则 key / 带 `rule_key` 的对象统一换成规则。"""
        if isinstance(rule_or_key, AnimeRule):
            return rule_or_key
        key = str(getattr(rule_or_key, "rule_key", "") or rule_or_key or "")
        if not key:
            return None
        return self.rule(key)

    def note_for(self, rule_key: str) -> str:
        """该规则最近一次搜索的失败/空结果原因（空串 = 正常）。"""
        return self._notes.get(str(rule_key or ""), "")

    @property
    def notes(self) -> dict[str, str]:
        return dict(self._notes)

    def _mix(self, rule: AnimeRule, note: str) -> None:
        if note:
            self._notes[rule.key] = note

    # ------------------------------------------------------------------ 搜索

    def search(self, keyword: str, *, rule_keys=None, limit: int = 30) -> list[SearchHit]:
        """多规则搜索：单条规则失败只记原因并继续，**不向外抛异常**。"""
        text = str(keyword or "").strip()
        if not text:
            return []
        wanted = {str(item) for item in rule_keys} if rule_keys else None

        hits: list[SearchHit] = []
        for rule in self.available_rules():
            if wanted is not None and rule.key not in wanted:
                continue
            if not self._usable(rule):
                # 被跳过的规则也要留下原因：用户会问"为什么我这条规则没有结果"
                self._mix(rule, self._skip_reason(rule))
                continue
            try:
                found = self._search_rule(rule, text, limit=limit)
            except Exception as error:  # noqa: BLE001  单条规则的任何失败都要转成可读原因
                self._mix(rule, self._explain(rule, error))
                continue
            self._mix(rule, "" if found else EMPTY_HITS)
            hits.extend(found)
        # `limit` 是**每条规则**的上限：合并去重是业务层（library）的事，
        # 在这里按总数截断会丢掉后面规则的结果。
        return hits

    def search_rule(self, rule: AnimeRule, keyword: str, *, limit: int = 30) -> list[SearchHit]:
        """单规则搜索；失败时记原因并返回空列表（调用方拿 `note_for()` 展示）。"""
        text = str(keyword or "").strip()
        if not text:
            return []
        if not self._usable(rule):
            self._mix(rule, self._skip_reason(rule))
            return []
        try:
            hits = self._search_rule(rule, text, limit=limit)
        except Exception as error:  # noqa: BLE001
            self._mix(rule, self._explain(rule, error))
            return []
        self._mix(rule, "" if hits else EMPTY_HITS)
        return hits

    def _usable(self, rule: AnimeRule) -> bool:
        return rule.supported and not rule.deprecated and rule.type == "anime"

    def _skip_reason(self, rule: AnimeRule) -> str:
        if not rule.supported:
            return f"该规则需要更新的客户端（api {rule.api_level}），已跳过"
        if rule.deprecated:
            return "该规则已被标记为退役，默认不参与搜索"
        if rule.type != "anime":
            return f"该规则类型是 {rule.type}，动漫板块只跑 anime"
        return ""

    def _search_rule(self, rule: AnimeRule, keyword: str, *, limit: int) -> list[SearchHit]:
        if rule.kind == "api":
            return self._search_api(rule, keyword, limit=limit)
        return self._search_xpath(rule, keyword, limit=limit)

    # -- XPath 模式 ---------------------------------------------------------

    def _search_xpath(self, rule: AnimeRule, keyword: str, *, limit: int) -> list[SearchHit]:
        if not rule.search_url:
            raise RuleError("XPath 模式缺少 searchURL，无法搜索")
        variables = {"keyword": keyword}
        query = substitute_url(rule.search_url, variables)
        parts = urlsplit(query)

        if rule.use_post or not parts.query:
            # Kazumi 的 usePost 语义：searchURL 的**查询串就是表单字段**，
            # 提交到 baseURL + searchURL 的路径（不是整条 URL）。
            fields = dict(parse_qsl(parts.query, keep_blank_values=True))
            base = rule.base_url or parts._replace(query="", fragment="").geturl()
            post_url = urljoin(base, parts.path or base)
            text = self._require_text(rule, post_url, method="POST", data=fields)
            page_url = post_url
        else:
            # 关键词经 substitute_url 编码后又过了一遍 parse_qsl/urlencode：
            # 目的是把 `&`、空格这类字符规范化，避免站点端解析出多余的参数。
            page_url = parts._replace(
                query=urlencode(parse_qsl(parts.query, keep_blank_values=True), doseq=True)
            ).geturl()
            text = self._require_text(rule, page_url)

        list_selector = Selector(rule.search_list) if rule.search_list else Selector("")
        nodes = list_selector.nodes(text)

        hits: list[SearchHit] = []
        for node in nodes:
            title = _node_text(node, rule.search_name)
            if not title:
                continue
            href = _node_text(node, rule.search_result)
            target = urljoin(page_url, href) if href else ""
            remote = target or href
            if not remote:
                continue
            hits.append(SearchHit(
                rule_key=rule.key, rule_name=rule.label, title=title, remote_id=remote,
                url=target or href,
            ))
            if limit and len(hits) >= limit:
                break
        return hits

    # -- API 模式 -----------------------------------------------------------

    def _search_api(self, rule: AnimeRule, keyword: str, *, limit: int) -> list[SearchHit]:
        config = rule.search_api if isinstance(rule.search_api, dict) else {}
        request = config.get("request") if isinstance(config.get("request"), dict) else {}
        url_template = str(_pick(request, "url", "request.url", default="") or "")
        if not url_template:
            raise RuleError("API 模式缺少 searchApiConfig.request.url，无法搜索")

        variables = {"keyword": keyword}
        url = substitute_url(url_template, variables)
        query = _template_dict(_pick(request, "query", "request.query", default={}), variables)
        body = _template_dict(_pick(request, "body", "request.body", default=None), variables)
        headers = _plain_dict(_pick(request, "headers", "request.headers", default={}))
        method = str(_pick(request, "method", "request.method", default="GET") or "GET").upper()
        # Kazumi 的 `bodyType`：决定 POST 的 body 怎么发。真实规则三种都出现过
        # （xfdmnext 用 json、部分站点用 form、少数直接把 JSON 文本当 raw body），
        # 所以必须显式读它，而不是"有 dict 就按 json 发"。
        body_type = str(_pick(request, "bodyType", "body_type", default="") or "").lower()

        if method == "POST":
            kwargs = {"headers": headers} if headers else {}
            sent = _post_body_kwargs(body_type, body, query)
            kwargs.update(sent)
            payload = self._require_json(rule, url, method="POST", **kwargs)
        else:
            if not url.startswith(("http://", "https://")):
                url = urljoin(rule.base_url, url)
            kwargs = {"params": query} if query else {}
            if headers:
                kwargs["headers"] = headers
            payload = self._require_json(rule, url, **kwargs)

        list_expression = str(_pick(config, "listPath", "list_path", default="") or "")
        if not list_expression:
            raise RuleError("API 模式缺少 searchApiConfig.listPath，无法解析搜索列表")
        name_expression = str(_pick(config, "namePath", "name_path", default="") or "")
        source_expression = str(_pick(config, "sourcePath", "source_path", default="") or "")

        list_selector = Selector(list_expression)
        items = list_selector.nodes(payload)
        hits: list[SearchHit] = []
        for item in items:
            title = _node_text(item, name_expression, allow_json=True)
            if not title:
                continue
            source = _node_text(item, source_expression, allow_json=True)
            remote = source
            if not remote and isinstance(item, dict):
                # sourcePath 没配或没取到时的兜底：接口里常见的 id/url 字段。
                # 不猜别的名字——猜错了会把"详情请求"打到莫名其妙的地址上。
                remote = str(item.get("url") or item.get("id") or "")
            if not remote:
                continue
            detail_url = remote if remote.startswith(("http://", "https://")) else ""
            hits.append(SearchHit(
                rule_key=rule.key, rule_name=rule.label, title=title, remote_id=remote,
                url=detail_url,
                cover=_node_text(item, str(_pick(config, "coverPath", "cover_path", default="") or ""),
                                 allow_json=True),
                year=_node_text(item, str(_pick(config, "yearPath", "year_path", default="") or ""),
                                allow_json=True),
                note=_node_text(item, str(_pick(config, "notePath", "note_path", default="") or ""),
                                allow_json=True),
            ))
            if limit and len(hits) >= limit:
                break
        return hits

    # ------------------------------------------------------------------ 详情

    def detail(self, rule: AnimeRule | None, hit_or_series=None) -> SeriesDetail:
        """拉详情：线路 + 剧集。播放地址不在这里解析（`resolve()` 负责）。

        入参有两种写法，都是包里真实存在的调用方式：

            detail(rule, hit_or_series)      # 试跑面板：规则 + 搜索结果
            detail(series_or_hit)            # library：从对象的 rule_key 反查规则
        """
        if hit_or_series is None:
            hit_or_series = rule
            rule = None
        resolved = self.find_rule(rule if rule is not None else hit_or_series)
        if resolved is None:
            raise RuleError("找不到该结果对应的规则（rule_key 已失效？请重新搜索）")
        series = self._as_series(resolved, hit_or_series)
        if resolved.chapter_mode == "api":
            return self._detail_api(resolved, series)
        return self._detail_xpath(resolved, series)

    def _detail_xpath(self, rule: AnimeRule, series: AnimeSeries) -> SeriesDetail:
        url = series.remote_id or ""
        if not url.startswith(("http://", "https://")):
            url = urljoin(rule.base_url, url)
        text = self._require_text(rule, url)

        detail = SeriesDetail(series=series, roads=[])
        road_expression = rule.chapter_roads
        episode_expression = rule.chapter_result
        if not episode_expression:
            raise RuleError("XPath 模式缺少 chapterResult，无法取出剧集链接")

        road_selector = Selector(road_expression) if road_expression else Selector("")
        road_nodes = road_selector.nodes(text) if road_expression else []

        if road_nodes:
            for index, node in enumerate(road_nodes):
                episodes = self._episodes_from_node(node, episode_expression, url, index, rule,
                                                    name=f"线路 {index + 1}")
                if episodes:
                    detail.roads.append(Road(index=len(detail.roads), name=episodes[0].road_name,
                                             episodes=episodes))
        else:
            # chapterRoads 匹配不到时**不要直接判失败**：很多站只有一个播放列表，
            # 用整页按 chapterResult 再取一次，能救回这类规则。
            episodes = self._episodes_from_node(text, episode_expression, url, 0, rule,
                                                name=f"线路 1")
            if episodes:
                detail.roads.append(Road(index=0, name="线路 1", episodes=episodes))
            else:
                detail.note = EMPTY_CHAPTERS

        # 主干道序号重排（跳过空线路后索引才是连续的）
        for index, road in enumerate(detail.roads):
            road.index = index
            road.name = road.name or f"线路 {index + 1}"
            for episode in road.episodes:
                episode.road_index = index
                episode.road_name = road.name
        if not detail.note and not detail.roads:
            detail.note = EMPTY_CHAPTERS
        return detail

    def _episodes_from_node(self, node, episode_expression: str, page_url: str, road_index: int,
                            rule: AnimeRule, *, name: str = "") -> list[AnimeEpisode]:
        selector = Selector(episode_expression)
        episodes: list[AnimeEpisode] = []
        road_name = self._road_label(node) or name or f"线路 {road_index + 1}"
        for item in selector.nodes(node):
            label = _node_text(item, "", allow_json=False)
            href = _attr_of(item, "href") or _attr_of(item, "data-href") or ""
            target = urljoin(page_url, href) if href else ""
            if not label and not target:
                continue
            episodes.append(self._make_episode(len(episodes), road_index, road_name,
                                               label, target, rule))
        return episodes

    #: 线路标题：`following::a` 保证标题排在剧集链接**之前**的那一个（后面的 h3 属于别的区块）
    _ROAD_LABEL_XPATH = (".//h1[following::a]|.//h2[following::a]"
                         "|.//h3[following::a]|.//h4[following::a]")

    @classmethod
    def _road_label(cls, node) -> str:
        """线路名：取容器里排在剧集链接之前的第一个标题元素文本。

        为什么不直接用容器的 `text_content()`：那会把所有剧集名拼成一串（"第1集第2集…"），
        既不是线路名也没法展示。为什么限 `h1`~`h4`：站点用 `<h3>量子线路</h3>` 这类结构；
        没有标题就退回 `线路 N`——**不猜别的选择器**（猜错了名字就是一段乱码）。
        """
        try:
            headings = node.xpath(cls._ROAD_LABEL_XPATH)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001  字符串/JSON 节点没有 xpath()
            return ""
        return _stringify_node(headings[0]) if headings else ""

    def _detail_api(self, rule: AnimeRule, series: AnimeSeries) -> SeriesDetail:
        config = rule.chapter_api if isinstance(rule.chapter_api, dict) else {}
        request = config.get("request") if isinstance(config.get("request"), dict) else {}
        url_template = str(_pick(request, "url", "request.url", default="") or "")
        if not url_template:
            raise RuleError("API 模式缺少 chapterApiConfig.request.url，无法取选集")
        url = substitute_url(url_template, {"source": series.remote_id})
        payload = self._require_json(rule, url)

        variables: dict[str, Any] = dict(_collect(config.get("variables"), payload))
        detail = SeriesDetail(series=series, roads=[])
        roads_expression = str(_pick(config, "roadsPath", "roads_path", default="") or "")
        episodes_expression = str(_pick(config, "episodesPath", "episodes_path", default="") or "")
        road_name_expression = str(_pick(config, "roadNamePath", "road_name_path", default="") or "")
        format_name = str(_pick(config, "format", "chapterFormat", default="flat") or "flat").lower()
        # flat/nested 的差异只在"剧集列表在哪一层"：nested 是每条线路各带一份，
        # flat 是整份响应只有一份列表（我们把它当成唯一一条线路）。
        roads_expression = roads_expression if format_name == "nested" else episodes_expression or roads_expression

        if format_name == "nested":
            road_nodes = Selector(roads_expression).nodes(payload) if roads_expression else []
            for index, road_node in enumerate(road_nodes):
                road_name = _node_text(road_node, road_name_expression, allow_json=True) \
                    or f"线路 {index + 1}"
                nodes = Selector(episodes_expression).nodes(road_node) if episodes_expression else []
                episodes = self._api_episodes(nodes, config, rule, index, road_name)
                if episodes:
                    detail.roads.append(Road(index=len(detail.roads), name=road_name,
                                             episodes=episodes))
        else:
            nodes = Selector(roads_expression).nodes(payload) if roads_expression else []
            road_name = "线路 1"
            if road_name_expression:
                road_name = _node_text(payload, road_name_expression, allow_json=True) or road_name
            episodes = self._api_episodes(nodes, config, rule, 0, road_name)
            if episodes:
                detail.roads.append(Road(index=0, name=road_name, episodes=episodes))

        for index, road in enumerate(detail.roads):
            road.index = index
            for episode in road.episodes:
                episode.road_index = index
                episode.road_name = road.name
        if not detail.roads:
            detail.note = EMPTY_CHAPTERS
        self._last_variables = variables
        return detail

    def _api_episodes(self, nodes: list, config: dict, rule: AnimeRule, road_index: int,
                      road_name: str) -> list[AnimeEpisode]:
        name_expression = str(_pick(config, "episodeNamePath", "episode_name_path", default="") or "")
        url_expression = str(_pick(config, "episodeUrlPath", "episode_url_path", default="") or "")
        episodes: list[AnimeEpisode] = []
        for node in nodes:
            label = _node_text(node, name_expression, allow_json=True)
            target = _node_text(node, url_expression, allow_json=True)
            if not label and not target:
                continue
            episodes.append(self._make_episode(len(episodes), road_index, road_name, label,
                                               target, rule))
        return episodes

    @staticmethod
    def _make_episode(index: int, road_index: int, road_name: str, name: str, url: str,
                      rule: AnimeRule) -> AnimeEpisode:
        return AnimeEpisode(
            index=index, name=name, url=url, road_index=road_index, road_name=road_name,
            number=parse_episode_number(name, rule.episode_parse),
        )

    # ------------------------------------------------------------------ 播放地址

    def resolve(self, rule: AnimeRule | None, series=None, episode=None) -> SniffedMedia:
        """三步解析播放地址（从便宜到昂贵），返回第一个成功的结果。

        1. 规则给的地址本身就是媒体文件 → 直接用（零额外请求）；
        2. 嗅探剧集地址；
        3. API 模式且配了 `episodePage` → 用变量模板构造播放页再嗅探。

        入参有两种写法（包里都真实存在）：

            resolve(rule, series, episode)     # 试跑面板
            resolve(detail, road, episode)     # library：第一条是 SeriesDetail
        """
        if isinstance(rule, SeriesDetail):
            # `resolve(detail, road, episode)`：从详情里取番剧，再按 rule_key 反查规则。
            # **注意第三个参数才是剧集**：曾经这里写成 `rule, series, episode = None,
            # rule.series, series`，把 road 当成了剧集，于是 `_episode_url` 永远返回空串
            # （Road 没有 url），表现为"该集没有可用的播放页地址"——集成测试才暴露出来。
            detail = rule
            rule, series, episode = None, rule.series, episode
        else:
            detail = None
        resolved = self.find_rule(rule if rule is not None else series)
        if resolved is None:
            raise RuleError("找不到这条番剧对应的规则（rule_key 已失效？请重新搜索）")
        rule = resolved
        if episode is None:
            raise RuleError("没有指定要播放的剧集")
        if not isinstance(series, AnimeSeries):
            series = self._as_series(rule, series)

        page_url = self._episode_url(rule, series, episode)
        target = page_url or ""
        if target and (looks_like_media_url(target) or _is_media(target)):
            return SniffedMedia(url=target, strategy="direct", referer=rule.referer or origin_of(target),
                                note="规则直接给出媒体地址")

        last_error = ""
        if target:
            try:
                result = self._sniff(rule, target)
            except Exception as error:  # noqa: BLE001
                last_error = str(error)
            else:
                if result.ok:
                    return result

        if rule.chapter_mode == "api" and _api_play_page(rule.chapter_api):
            try:
                play_page = self._play_page_url(rule, series, episode)
            except Exception as error:  # noqa: BLE001
                play_page = ""
                last_error = str(error)
            if play_page:
                try:
                    result = self._sniff(rule, play_page)
                except Exception as error:  # noqa: BLE001
                    last_error = str(error)
                else:
                    if result.ok:
                        # 嗅探器自己的 note 写的是"第几层 iframe"这类细节，
                        # 这里补一句"地址是模板拼出来的"，排查时能区分两种来源。
                        result.note = f"由 episodePage 模板构造播放页后嗅探：{play_page}"
                        return result

        note = RESOLVE_NOTE if not last_error else f"{RESOLVE_NOTE}（最后一次错误：{last_error}）"
        if not target and not _api_play_page(rule.chapter_api):
            note = "该集没有可用的播放页地址（规则既没给地址，也没有 episodePage 模板）"
        return SniffedMedia(url="", strategy="failed",
                            referer=rule.referer or origin_of(target), note=note)

    def _episode_url(self, rule: AnimeRule, series, episode) -> str:
        url = str(getattr(episode, "url", "") or "")
        if url and not url.startswith(("http://", "https://", "//")):
            base = self._series_url(rule, series)
            url = urljoin(base, url)
        if url.startswith("//"):
            url = "https:" + url
        return url

    def _series_url(self, rule: AnimeRule, series) -> str:
        remote = str(getattr(series, "remote_id", "") or "")
        if remote.startswith(("http://", "https://")):
            return remote
        base = rule.base_url or ""
        return urljoin(base, remote) if remote else base

    def _play_page_url(self, rule: AnimeRule, series, episode) -> str:
        """按 `episodePage` 模板构造播放页地址（服务端渲染的播放页最靠谱）。"""
        config = rule.chapter_api if isinstance(rule.chapter_api, dict) else {}
        page = _pick(config, "episodePage", "episode_page", default=None)
        if not isinstance(page, dict):
            return ""
        variables = dict(getattr(self, "_last_variables", {}) or {})
        variables.update({
            "source": str(getattr(series, "remote_id", "") or ""),
            "seriesId": str(getattr(series, "id", "") or ""),
            "roadIndex": str(getattr(episode, "road_index", 0)),
            "episodeIndex": str(getattr(episode, "index", 0)),
            "roadNumber": str(int(getattr(episode, "road_index", 0)) + 1),
            "episodeNumber": str(getattr(episode, "number", 0) or int(getattr(episode, "index", 0)) + 1),
            "episodeName": str(getattr(episode, "name", "") or ""),
        })
        url = substitute_url(str(_pick(page, "url", default="") or ""), variables)
        if not url:
            return ""
        if not url.startswith(("http://", "https://")):
            url = urljoin(rule.base_url, url)
        query = _template_dict(_pick(page, "query", default={}), variables)
        query = {key: value for key, value in query.items() if value}
        if query:
            joiner = "&" if urlsplit(url).query else "?"
            url = f"{url}{joiner}{urlencode(query)}"
        return url

    def _sniff(self, rule: AnimeRule, url: str) -> SniffedMedia:
        result = self._sniffer_instance(rule).sniff(url)
        if result.ok and not result.referer:
            result.referer = rule.referer or origin_of(url)
        return result

    # ------------------------------------------------------------------ 依赖

    def _requester(self, rule: AnimeRule):
        if rule.key not in self._clients:
            self._clients[rule.key] = self._build_http(rule)
        return self._clients[rule.key]

    def _build_http(self, rule: AnimeRule):
        if self._http_factory is not None:
            return self._http_factory(rule)
        headers: dict[str, str] = {}
        if rule.user_agent:
            headers["User-Agent"] = rule.user_agent
        if rule.referer:
            headers["Referer"] = rule.referer
        return HttpClient(headers=headers, timeout=rule.timeout,
                          attempts=max(1, int(rule.retries) or 1),
                          hint="可在浏览器中打开该站确认是否改版")

    def _sniffer_instance(self, rule: AnimeRule) -> Any:
        if self._sniffer is not None:
            return self._sniffer
        return Sniffer(http=self._requester(rule), referer=rule.referer,
                       user_agent=rule.user_agent, timeout=rule.timeout)

    def _throttle(self, rule: AnimeRule) -> Throttle:
        if rule.key not in self._throttles:
            self._throttles[rule.key] = Throttle(rule.qps)
        return self._throttles[rule.key]

    def _get(self, rule: AnimeRule, url: str, **kwargs):
        return self._requester(rule).get(url, **kwargs)

    def _get_text(self, rule: AnimeRule, url: str, **kwargs) -> str:
        return self._requester(rule).get_text(url, **kwargs)

    def _post(self, rule: AnimeRule, url: str, **kwargs):
        return self._requester(rule).post(url, **kwargs)

    def _require_text(self, rule: AnimeRule, url: str, **kwargs) -> str:
        if not url:
            raise RuleError(f"{rule.label}：请求地址为空（规则里的 URL 模板没拼出来）")
        try:
            if kwargs.get("method") == "POST":
                kwargs.pop("method", None)
                return self._post(rule, url, **kwargs).text
            return self._get_text(rule, url, **kwargs)
        except Exception as error:  # noqa: BLE001  SourceError / requests 异常统一收口
            raise RuleError(f"{rule.label} 请求失败（{host_of(url) or url}）：{error}") from error

    def _require_json(self, rule: AnimeRule, url: str, **kwargs) -> Any:
        if not url:
            raise RuleError(f"{rule.label}：请求地址为空（规则里的 URL 模板没拼出来）")
        try:
            if kwargs.get("method") == "POST":
                kwargs.pop("method", None)
                response = self._post(rule, url, **kwargs)
                payload = _response_json(response, url)
            else:
                self._throttle(rule).wait()
                payload = self._requester(rule).get_json(url, **kwargs)
        except Exception as error:  # noqa: BLE001
            raise RuleError(f"{rule.label} 请求失败（{host_of(url) or url}）：{error}") from error
        if isinstance(payload, str):
            payload = parse_json(payload)
        if payload is None:
            raise RuleError(f"{rule.label}：{host_of(url) or url} 返回的不是有效 JSON"
                            "（站点可能改版或返回了验证页）")
        return payload

    # ------------------------------------------------------------------ 其它

    def _as_series(self, rule: AnimeRule, hit_or_series) -> AnimeSeries:
        if isinstance(hit_or_series, AnimeSeries):
            series = hit_or_series
            if not series.rule_key:
                series.rule_key = rule.key
            return series
        hit = hit_or_series
        remote = str(getattr(hit, "remote_id", "") or getattr(hit, "url", "") or "")
        if not remote:
            raise RuleError("这条搜索结果没有 remote_id（sourcePath/href），无法取详情")
        return AnimeSeries(
            rule_key=rule.key, remote_id=remote,
            title=str(getattr(hit, "title", "") or ""),
            cover=str(getattr(hit, "cover", "") or ""),
            year=str(getattr(hit, "year", "") or ""),
        )

    @staticmethod
    def _explain(rule: AnimeRule, error: Exception) -> str:
        if isinstance(error, (RuleError, SelectorError)):
            return str(error)
        return f"{rule.label} 搜索失败：{error}"


# --------------------------------------------------------------------------- 小工具


def _pick(data: dict | None, *names: str, default=None):
    """按候选键取值（兼容 camelCase 与 `request.url` 这种点号写法）。"""
    if not isinstance(data, dict):
        return default
    for name in names:
        if not name:
            continue
        if name in data and data[name] not in (None, ""):
            return data[name]
        if "." in name:
            current: Any = data
            for part in name.split("."):
                if not isinstance(current, dict) or part not in current:
                    current = None
                    break
                current = current[part]
            if current not in (None, ""):
                return current
    return default


def _post_body_kwargs(body_type: str, body, query: dict) -> dict:  # noqa: ANN001
    """按 Kazumi 的 `bodyType` 决定 POST body 的发送方式。

    | `bodyType` | 发送方式 |
    |---|---|
    | `json`，或未写且 body 是 dict | `json=body`（`Content-Type: application/json`） |
    | `form` / `form-data` / `urlencoded` | `data=body`（表单编码） |
    | `text` / `raw` / `plain` | `data=` 原始文本（body 是 dict 时序列化成 JSON 文本） |
    | 都没有 | 退回 `query` 当表单字段（与旧行为一致，不破坏既有规则） |

    **为什么要显式判 `bodyType`**：早先的实现是"body 是 dict 就按 json 发"，
    对 `bodyType: "form"`/`"text"` 的规则会发错 Content-Type —— 这类站点通常直接 400/415。
    `bodyType` 缺失时保持旧行为，因此不影响已经在用的规则。
    """
    import json as _json

    kind = (body_type or "").strip().lower()
    if kind in ("form", "form-data", "formdata", "urlencoded", "x-www-form-urlencoded"):
        return {"data": body if body else query}
    if kind in ("text", "raw", "plain"):
        if isinstance(body, (dict, list)):
            body = _json.dumps(body, ensure_ascii=False)
        return {"data": body if body not in (None, "") else query}
    if kind in ("json", "application/json"):
        return {"json": body if body is not None else {}}
    # 未写 bodyType：body 优先、退回 query（旧行为）
    if isinstance(body, dict) and body:
        return {"json": body}
    return {"data": query} if query else {}


def _template_dict(value, variables: dict, renderer=substitute) -> dict:
    """替换模板字典里的 `@变量`。

    两条约定（都是真机踩过的坑）：

    - URL 用 `substitute_url`（`@keyword` 要 URL 编码），query/body 用 `substitute`
      ——**不能**编码：表单字段交给 requests 编码，手工再编一次就变成双重编码，
      站点收到 `%2520` 这种垃圾，搜索永远为空；
    - **没有占位符的标量保留原类型**（bool/int/float）。真实规则里有
      `"enabled": true`、`"sortDesc": false`、`"page": 1`
      （如 KazumiRules 的 sorani），一律转字符串会把它们送成 `"false"`/`"1"`，
      严格校验类型的接口会直接 400 或行为异常。
    """
    if not isinstance(value, dict):
        return {}
    rendered: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (dict, list)):
            continue
        text = str(item)
        if isinstance(item, str) and "@" in item:
            rendered[str(key)] = renderer(text, variables)
        elif isinstance(item, (bool, int, float)) or not isinstance(item, str):
            rendered[str(key)] = item
        else:
            rendered[str(key)] = text
    return rendered


def _plain_dict(value) -> dict:
    return {str(key): str(item) for key, item in value.items()} if isinstance(value, dict) else {}


def _collect(expressions, data) -> dict[str, Any]:
    """按 `{变量名: JSONPath}` 从响应里收集变量（供 episodePage 模板使用）。"""
    result: dict[str, Any] = {}
    if not isinstance(expressions, dict):
        return result
    for name, expression in expressions.items():
        if not expression:
            continue
        try:
            result[str(name)] = Selector(str(expression)).first(data)
        except SelectorError:
            continue
    return result


def _node_text(node, expression: str, *, allow_json: bool = True) -> str:
    """对节点求值取文本。

    取值前先把节点准备成"该选择器能吃的形态"：**JSONPath 要 Python 对象**，
    XPath/CSS 要 HTML 文本或 lxml 元素。这一步不做，就会出现那类最难查的 bug——
    `JsonPathSelector` 在 `.json()` 已经是 dict 的**字符串**上做键查找，永远返回空。
    """
    if not expression:
        if isinstance(node, str):
            return node.strip()
        return _stringify_node(node)
    if looks_like_xpath(expression):
        expression = _scoped(expression, node)
    try:
        selector = Selector(expression, allow_json=allow_json)
    except SelectorError:
        return ""
    return selector.first(_as_data(node, json_mode=selector.kind == "jsonpath"))


def _stringify_node(node) -> str:
    for name in ("text_content", "get_text"):
        method = getattr(node, name, None)
        if callable(method):
            try:
                return str(method() or "").strip()
            except TypeError:
                try:
                    return str(method(strip=True) or "").strip()
                except Exception:  # noqa: BLE001
                    continue
            except Exception:  # noqa: BLE001
                continue
    value = getattr(node, "text", None)
    if isinstance(value, str):
        return value.strip()
    return str(node).strip() if node is not None else ""


def _as_data(node, *, json_mode: bool):
    """把节点准备成选择器能吃的形态。

    `json_mode` 必须由**表达式类型**决定，不能凭"值长得像 JSON"猜：
    页面里的 `class="module-item-title"` 含 `[`，`parse_json()` 会把它啃成垃圾。
    """
    if json_mode:
        if isinstance(node, str):
            parsed = parse_json(node)
            return parsed if parsed is not None else node
        return node
    if isinstance(node, str):
        return node
    if isinstance(node, (dict, list, tuple)):
        return _json_text(node)
    return node


def _scoped(expression: str, node) -> str:
    """把作用在**容器节点**上的 XPath 从 `//x` 改成 `.//x`。

    这是真机踩过的坑，不是洁癖：XPath 的 `//x` 是**从文档根**开始找的，即使在元素上求值
    也一样（`Selector.nodes(element)` → `root.xpath("//a")` 返回全文档的 `a`）。
    规则的 `chapterResult` 常写成 `//a/@href`，不改成相对路径的话，每条线路都会取到
    **第一条线路的第一个链接**——表现就是"所有线路的剧集一模一样"。
    """
    text = (expression or "").strip()
    if not isinstance(node, str) and text.startswith("//"):
        return "." + text
    return expression


def _json_text(node) -> str:
    """把节点转成"选择器可吃"的文本。

    lxml 元素必须用 `etree.tostring` 序列化：`str(element)` 得到的是
    `<Element div at 0x...>` 这种 repr，拿它再用 XPath 求值只会返回空。
    """
    if isinstance(node, str):
        return node
    if isinstance(node, (dict, list, tuple)) or node is None:
        try:
            return json.dumps(node, ensure_ascii=False) if node is not None else ""
        except (TypeError, ValueError):
            return str(node)
    try:
        from lxml import etree

        return etree.tostring(node, encoding="unicode")
    except Exception:  # noqa: BLE001
        return _stringify_node(node)


def _attr_of(node, name: str) -> str:
    """取属性 / JSON 同名字段（`href`、`data-href`、`url`）。"""
    if isinstance(node, dict):
        value = node.get(name)
        return str(value).strip() if isinstance(value, (str, int, float)) else ""
    if isinstance(node, str):
        return ""
    getter = getattr(node, "get", None)
    if callable(getter):
        try:
            value = getter(name)
        except Exception:  # noqa: BLE001
            value = None
        if value:
            return str(value).strip()
    return ""


def _is_media(url: str) -> bool:
    """比 `looks_like_media_url` 宽松一点：查询串里带后缀也算（`/a.m3u8?sign=1`）。"""
    from .selectors import looks_like_media

    return looks_like_media(url)


def _api_play_page(config) -> bool:
    if not isinstance(config, dict):
        return False
    page = _pick(config, "episodePage", "episode_page", default=None)
    return isinstance(page, dict) and bool(_pick(page, "url", default=""))


def _response_json(response, url: str):
    """从 fake/真实响应里取 JSON（两者都提供 `.json()`）。"""
    try:
        return response.json()
    except Exception as error:  # noqa: BLE001
        raise RuleError(f"{host_of(url) or url} 返回内容不是有效 JSON：{error}") from error


__all__ = [
    "EMPTY_CHAPTERS", "EMPTY_HITS", "RESOLVE_NOTE", "AnimeEngine", "SearchHit",
    "SniffResult", "Throttle",
]
# 注：`SearchGroup`（跨规则合并后的搜索结果）在 `library.py` —— 合并是业务层的取舍，
# 引擎只管"按一条规则抓站点"，两个名字不要混。
