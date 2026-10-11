"""Bangumi 元数据（<https://api.bgm.tv>）：**只读** + 缓存。

只做四件事：封面、简介、评分、放送日期。**不写任何数据、不登录、不提交收藏**
—— 用户看番用的是收藏夹，不是我们。

走哪条路：

- 主路径是**旧版** `GET /search/subject/{keywords}?type=2&responseGroup=large`。
  理由很实际：它一个请求就能拿到 `name_cn / summary / images / rating / date`，
  而 v0 的 `POST /v0/search/subjects` 要 POST + 分页 + 再补详情，代码量翻倍、
  请求数翻倍（而 bangumi 对请求频率很敏感）。旧版接口至今可用。
- 旧版返回 404（被下线）时自动回退到 `POST /v0/search/subjects`，
  `GET /v0/subjects/{id}` 负责补详情。

频率：**主动限制到约 1 请求/秒**（`_LIMIT_INTERVAL_SECONDS`）。bangumi 没有公开的
配额文档，但社区里因为高频抓取被临时封 IP 的案例不少（Kazumi #2133 记录过
域名被拦），所以宁可慢一点。

可靠性：元数据是**可选增强**，调用方必须当它是可选的 —— 拿不到封面/评分时
界面照常显示，不要弹错误框。本模块所有公开方法都不抛异常：失败返回
`None` / `[]`，原因写在 `last_note` 里（中文）。内存里做一层 TTL 缓存
（`cache_ttl_seconds`，默认 6 小时），同一部番短时间内重复查询不会再打网络。
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote

from ..platform.http import HttpClient, SourceError, describe_network_error, host_of
from .models import normalize_title

#: Bangumi 主站 API 根地址
BANGUMI_BASE = "https://api.bgm.tv"

#: 两次网络请求之间的最小间隔（秒）。约 1 req/s，见模块 docstring。
_LIMIT_INTERVAL_SECONDS = 1.1

#: 内存缓存默认有效期（秒），6 小时 —— 封面/评分这类数据一天一变都算频繁了
DEFAULT_CACHE_TTL_SECONDS = 6 * 3600

#: `type=2` 是"动画"（1=书籍 2=动画 3=音乐 4=游戏 6=三次元）
SUBJECT_TYPE_ANIME = 2

#: 简介里的 HTML 标签（bangumi 的 summary 带 `<br>` 与 `<a>`）
_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


@dataclass
class BangumiSubject:
    """一个条目（动画）。字段全部有默认值，构造时不依赖接口的完整性。"""

    id: int = 0
    name: str = ""                 # 原名（多为日文）
    name_cn: str = ""              # 中文名
    summary: str = ""
    cover: str = ""
    score: float = 0.0
    rank: int = 0
    date: str = ""                 # 放送日期 YYYY-MM-DD
    air_weekday: int = -1          # 0=周一 … 6=周日，-1=未知
    total_episodes: int = 0
    tags: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        """展示用标题：中文名优先（用户看得懂），没有才退回原名。"""
        return self.name_cn or self.name

    @property
    def cover_url(self) -> str:
        """兼容叫法（UI 里两种写法都有）。"""
        return self.cover

    def year(self) -> str:
        """放送年份（4 位数字），拿不到返回空串。"""
        text = (self.date or "").strip()
        match = re.match(r"(\d{4})", text)
        return match.group(1) if match else ""


class _TTLCache:
    """极简内存 TTL 缓存（带条数上限，防止长时间运行后无限长）。

    缓存的是"接口返回的原始结构"，所以 `search` / `subject` 都能复用；
    进程重启即失效 —— 元数据不值得为它落盘维护一套失效逻辑。
    """

    def __init__(self, ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
                 max_items: int = 256) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.max_items = max(1, int(max_items))
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any:
        if self.ttl_seconds <= 0:
            self.misses += 1
            return None
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            stamp, value = entry
            if time.time() - stamp >= self.ttl_seconds:
                self._store.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            return value

    def put(self, key: str, value: Any) -> None:
        if self.ttl_seconds <= 0 or value is None:
            return
        with self._lock:
            if len(self._store) >= self.max_items:
                # 简单的 FIFO 淘汰：元数据条目之间没有冷热差异，不值得上 LRU
                oldest = min(self._store.items(), key=lambda pair: pair[1][0])[0]
                self._store.pop(oldest, None)
            self._store[key] = (time.time(), value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


class BangumiClient:
    """Bangumi 只读客户端。任何失败都降级为 `None` / `[]`，不抛异常。"""

    def __init__(self, *, http_factory: Any = None, timeout: float = 15.0,
                 user_agent: str = "ModuWorkbench/1.1 (https://github.com/zhaoyuhanga/fileType)",
                 base_url: str = BANGUMI_BASE,
                 cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS) -> None:
        self.base_url = (base_url or BANGUMI_BASE).rstrip("/")
        self.timeout = float(timeout or 15.0)
        self.user_agent = user_agent or ""
        self._http_factory = http_factory
        self._http: Any = None
        #: 最近一次失败的中文说明（成功时清空）
        self.last_note = ""
        self._last_request_at = 0.0
        self.cache = _TTLCache(cache_ttl_seconds)

    # ------------------------------------------------------------------ 基础设施

    def _client(self) -> Any:
        if self._http is None:
            self._http = self._build_http()
        return self._http

    def _build_http(self) -> Any:
        factory = self._http_factory
        if factory is None:
            headers = {"Accept": "application/json"}
            if self.user_agent:
                headers["User-Agent"] = self.user_agent
            return HttpClient(headers=headers, timeout=self.timeout, attempts=2,
                              hint="封面/评分等元数据为可选内容，拿不到不影响观看")
        if hasattr(factory, "get") or hasattr(factory, "request"):
            # 直接传客户端实例（测试最省事）
            return factory
        return factory()

    def _throttle(self) -> None:
        """限速到约 1 请求/秒（`time.sleep` 抽成方法便于测试替换）。"""
        elapsed = time.time() - self._last_request_at
        wait = _LIMIT_INTERVAL_SECONDS - elapsed
        if self._last_request_at and wait > 0:
            self._sleep(wait)
        self._last_request_at = time.time()

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(max(0.0, float(seconds)))

    def _get_json(self, path: str, *, params: dict | None = None,
                  method: str = "GET", json_body: dict | None = None,
                  need_throttle: bool = True) -> Any:
        if need_throttle:
            self._throttle()
        url = self.base_url + path
        headers = {"Accept": "application/json", "User-Agent": self.user_agent}
        client = self._client()
        kw: dict[str, Any] = {"headers": headers, "timeout": self.timeout}
        if params:
            kw["params"] = params
        if json_body is not None:
            kw["json"] = json_body
        response = client.request(method, url, **kw)
        return response.json()

    # ------------------------------------------------------------------ 搜索

    def search(self, keyword: str, *, limit: int = 5) -> list[BangumiSubject]:
        """按名字搜索动画条目（最多 `limit` 条）。失败返回 `[]`。"""
        self.last_note = ""
        text = (keyword or "").strip()
        if not text:
            return []
        wanted = max(0, int(limit or 0))
        if wanted <= 0:
            return []
        cache_key = f"search:{text.casefold()}"
        cached = self.cache.get(cache_key)
        if cached is None:
            cached = self._fetch_search(text)
            self.cache.put(cache_key, cached)
        subjects = [subject for subject in cached if isinstance(subject, BangumiSubject)]
        return subjects[:wanted]

    def _fetch_search(self, keyword: str) -> list[BangumiSubject]:
        """先走旧版 GET，没结果或失败再走 v0 POST。两条都失败返回 `[]`。"""
        try:
            payload = self._get_json(
                f"/search/subject/{_quote(keyword)}",
                params={"type": SUBJECT_TYPE_ANIME, "responseGroup": "large"})
            legacy = parse_legacy_search(payload)
            if legacy:
                return legacy
            # 旧版"返回了但没结果"也算失败：用户可能是用中文名搜日文条目，
            # v0 的检索算法不一样，值得再试一次（失败时 last_note 会被覆盖）
            self.last_note = _legacy_empty_note(payload)
        except Exception as error:  # noqa: BLE001 - 元数据可选，绝不外抛
            self.last_note = self._describe(error, "/search/subject")
        try:
            payload = self._get_json(
                "/v0/search/subjects",
                method="POST",
                json_body={"keyword": keyword,
                           "filter": {"type": [SUBJECT_TYPE_ANIME]}})
            results = parse_v0_search(payload)
            if results:
                self.last_note = ""
                return results
            self.last_note = (f"{self.last_note}；v0 搜索也没有结果"
                              if self.last_note else "Bangumi v0 搜索没有结果")
            return []
        except Exception as error:  # noqa: BLE001
            fallback_note = self._describe(error, "/v0/search/subjects")
            self.last_note = (f"{self.last_note}；回退 v0 也失败：{fallback_note}"
                              if self.last_note else fallback_note)
            return []

    def subject(self, subject_id: int) -> BangumiSubject | None:
        """`GET /v0/subjects/{id}` 取详情；失败返回 `None`。"""
        self.last_note = ""
        try:
            ident = int(subject_id)
        except (TypeError, ValueError):
            return None
        if ident <= 0:
            return None
        cache_key = f"subject:{ident}"
        cached = self.cache.get(cache_key)
        if cached is None:
            try:
                payload = self._get_json(f"/v0/subjects/{ident}")
            except Exception as error:  # noqa: BLE001
                self.last_note = self._describe(error, f"/v0/subjects/{ident}")
                return None
            cached = parse_subject(payload)
            self.cache.put(cache_key, cached)
        return cached if isinstance(cached, BangumiSubject) else None

    def best_match(self, title: str, *, year: str = "") -> BangumiSubject | None:
        """在搜索结果里挑最像的一部；没有一个像到阈值就返回 `None`。

        评分口径与弹幕匹配一致（归一化相等 = 1.0，否则 `SequenceMatcher`），
        另有两点偏好：`name_cn` 命中优先于 `name` 命中（用户输入的基本是中文名）；
        年份一致 +0.05、冲突 -0.15（同名不同年基本是不同作品）。
        """
        self.last_note = ""
        wanted = normalize_title(title)
        if not wanted:
            return None
        candidates = self.search(title, limit=8)
        if not candidates:
            return None
        want_year = _year_of(year)
        best: BangumiSubject | None = None
        best_score = 0.0
        for subject in candidates:
            score = max(similarity(title, subject.name_cn), similarity(title, subject.name))
            subject_year = subject.year()
            if want_year and subject_year:
                score += 0.05 if want_year == subject_year else -0.15
            if score > best_score:
                best, best_score = subject, score
        if best is None or best_score < 0.6:
            self.last_note = (f"Bangumi 没有足够接近「{(title or '').strip()}」的条目"
                              "（元数据为可选内容，已按无元数据处理）")
            return None
        return best

    # ------------------------------------------------------------------ 放送日

    @staticmethod
    def air_weekday_of(subject: BangumiSubject | None) -> int:
        """推断放送星期：0=周一 … 6=周日，推不出返回 -1。

        条目自带 `air_weekday` 时直接用（`parse_subject` 已经从接口的
        `air_date` / `eps[].airdate` 推过一遍了）；否则现算 `date`。
        `date.weekday()` 本身就是周一=0，与我们的约定一致，不需要换算 ——
        自己去写蔡勒公式只会引入 off-by-one。
        """
        if subject is None:
            return -1
        try:
            explicit = int(subject.air_weekday)
        except (TypeError, ValueError):
            explicit = -1
        if 0 <= explicit <= 6:
            return explicit
        return weekday_from_text(subject.date)

    # ------------------------------------------------------------------ 缓存

    def cached_search(self, keyword: str, *, limit: int = 5) -> list[BangumiSubject]:
        """只查内存缓存（不打网络）：命中返回列表，未命中返回 `[]`。"""
        cached = self.cache.get(f"search:{(keyword or '').strip().casefold()}")
        if cached is None:
            return []
        subjects = [item for item in cached if isinstance(item, BangumiSubject)]
        return subjects[:max(0, int(limit or 0))]

    def cached_subject(self, subject_id: int) -> BangumiSubject | None:
        """只查内存缓存（不打网络）。"""
        try:
            ident = int(subject_id)
        except (TypeError, ValueError):
            return None
        value = self.cache.get(f"subject:{ident}")
        return value if isinstance(value, BangumiSubject) else None

    def clear_cache(self) -> None:
        self.cache.clear()

    def _describe(self, error: BaseException, path: str) -> str:
        """可读的中文失败说明（提到"元数据可选"和曾经被拦的域名问题）。

        `describe_network_error` 只在部分分支里带上 hint（DNS/超时/403 等）。
        这里统一补一句"元数据是可选的" —— 界面上不该因为封面拉不到而报警。
        """
        text = describe_network_error(
            error, host_of(self.base_url + path),
            hint="元数据（封面/评分）为可选项，拿不到不影响观看")
        if "可选项" not in text:
            text = f"{text}（元数据为可选项，拿不到不影响观看）"
        if isinstance(error, SourceError) and "403" in str(error):
            # Kazumi #2133：bangumi 会对异常流量的域名做拦截，403 不一定是写错了
            text = f"{text}；bangumi 有过按来源拦截的记录，可稍后再试"
        return text


# --------------------------------------------------------------------------- 解析

def parse_subject(payload: Any) -> BangumiSubject | None:
    """解析单个条目（v0 详情或搜索结果里的条目都用这一套，字段存在即取）。"""
    if not isinstance(payload, dict):
        return None
    images = payload.get("images")
    cover = ""
    if isinstance(images, dict):
        # large > common > medium > small > grid，取分辨率最高的那个
        for key in ("large", "common", "medium", "small", "grid"):
            value = images.get(key)
            if value:
                cover = str(value)
                break
    elif isinstance(images, str):
        cover = images
    rating = payload.get("rating")
    rank = 0
    if isinstance(rating, dict):
        rank = _to_int(rating.get("rank") or rating.get("rank_total"))
    subject = BangumiSubject(
        id=_to_int(payload.get("id")),
        name=str(payload.get("name") or "").strip(),
        name_cn=str(payload.get("name_cn") or "").strip(),
        summary=clean_summary(payload.get("summary")),
        cover=cover,
        score=_score_of(rating),
        rank=rank,
        date=str(payload.get("date") or "").strip(),
        air_weekday=-1,
        total_episodes=_episode_count_of(payload),
        tags=_tags_of(payload.get("tags")),
    )
    # 有些响应会直接给 air_date（"2023-09-29"），能推出星期就用它
    hint = str(payload.get("air_date") or "").strip()
    weekday = weekday_from_text(hint) if hint else -1
    if weekday < 0 and isinstance(payload.get("eps"), list):
        for entry in payload["eps"]:
            if isinstance(entry, dict):
                weekday = weekday_from_text(str(entry.get("airdate") or ""))
                if weekday >= 0:
                    break
    subject.air_weekday = weekday
    if not (subject.id or subject.name or subject.name_cn):
        return None
    return subject


def parse_legacy_search(payload: Any) -> list[BangumiSubject]:
    """旧版 `GET /search/subject/{kw}` 的响应：`{"results": N, "list": [...]}`。"""
    if not isinstance(payload, dict):
        return []
    raw_list = payload.get("list")
    if not isinstance(raw_list, list):
        return []
    return [item for item in (parse_subject(entry) for entry in raw_list) if item]


def parse_v0_search(payload: Any) -> list[BangumiSubject]:
    """v0 `POST /v0/search/subjects` 的响应：`{"data": [...]}`。"""
    if not isinstance(payload, dict):
        return []
    raw_list = payload.get("data")
    if not isinstance(raw_list, list):
        return []
    return [item for item in (parse_subject(entry) for entry in raw_list) if item]


def clean_summary(text: Any) -> str:
    """简介去 HTML 标签并压掉多余空白（bangumi 的简介里 `<br>` 很多）。"""
    value = _HTML_TAG.sub("", str(text or "")).replace("&nbsp;", " ")
    return _WS.sub(" ", value).strip()


def weekday_from_text(text: Any) -> int:
    """从文本里找 `YYYY-MM-DD` 并返回星期（0=周一）；找不到返回 -1。

    用 `date.fromisoformat` + `weekday()`：Python 的 weekday() 就是周一=0，
    与 `air_weekday` 的约定一致；自己算"蔡勒公式"只会引入 off-by-one。
    """
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(text or ""))
    if not match:
        return -1
    try:
        parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        # 2 月 30 日之类的脏数据：当作没有信息，不要抛
        return -1
    return parsed.weekday()


def similarity(left: str, right: str) -> float:
    """标题相似度（与弹幕匹配同一口径：归一化相等 = 1.0）。"""
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _score_of(rating: Any) -> float:
    if isinstance(rating, dict):
        try:
            return round(float(rating.get("score") or 0.0), 2)
        except (TypeError, ValueError):
            return 0.0
    try:
        return round(float(rating or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def _episode_count_of(payload: dict) -> int:
    """总集数：`total_episodes` 优先，退回 `eps`（可能是数字，也可能是集列表）。

    v0 详情里的 `eps` 是"剧集对象数组"，直接 `int()` 会抛；列表长度也**不能**
    当集数用（可能只返回了前 100 条），所以列表一律忽略。
    """
    for key in ("total_episodes", "eps", "eps_count"):
        value = payload.get(key)
        if isinstance(value, (int, float, str)) and str(value).strip():
            count = _to_int(value)
            if count > 0:
                return count
        elif isinstance(value, dict):
            count = _to_int(value.get("total") or value.get("count"))
            if count > 0:
                return count
    return 0


def _tags_of(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    names: list[str] = []
    for entry in tags:
        if isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
        else:
            name = str(entry or "").strip()
        if name:
            names.append(name)
    return names


def _year_of(text: Any) -> str:
    match = re.search(r"(\d{4})", str(text or ""))
    return match.group(1) if match else ""


def _legacy_empty_note(payload: Any) -> str:
    """旧版接口 200 但没有结果时，给一句能看懂的说明。"""
    if isinstance(payload, dict) and payload.get("results") in (0, "0"):
        return "Bangumi 旧版搜索没有结果，已回退到 v0 搜索"
    return "Bangumi 旧版搜索响应结构不认识，已回退到 v0 搜索"


def _quote(text: str) -> str:
    """关键词转成可放进 URL 路径的片段（bangumi 的旧版搜索是路径参数）。"""
    return quote(str(text or "").strip(), safe="")


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


__all__ = [
    "BANGUMI_BASE", "DEFAULT_CACHE_TTL_SECONDS", "SUBJECT_TYPE_ANIME",
    "BangumiClient", "BangumiSubject", "clean_summary", "parse_legacy_search",
    "parse_subject", "parse_v0_search", "similarity", "weekday_from_text",
]
