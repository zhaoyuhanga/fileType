"""弹弹play 开放弹幕网络：搜索 / 匹配 / 拉取弹幕 / 本地缓存。

对接 <https://doc.dandanplay.com/open/> 的 API v2，只用三个端点：

    GET /api/v2/search?keyword=...                 搜索番剧（含节目列表）
    GET /api/v2/comment/{episodeId}?withRelated=1  取某一集的弹幕
    GET /api/v2/match?fileName=...                 按文件名识别（可选，未启用）

**不包含发送弹幕**。官方把"应用弹幕"(`/api/v2/comment/{episodeId}/app`) 归为
*受限接口*，新申请的 AppId 拿不到权限（需要用户 JWT），所以本客户端只读不写 ——
UI 上不要出现"发送弹幕"入口，否则用户点了只会拿到 401。

身份验证（见官方「请求头配置」）：

- 签名模式（默认，桌面端推荐）：`X-AppId` + `X-Timestamp` + `X-Signature`，
  其中 `X-Signature = base64(sha256(AppId + Timestamp + Path + AppSecret))`，
  `Path` **不含域名、不含 `?` 后的查询串**（例如 `/api/v2/comment/123450001`）；
- 凭证模式：`X-AppId` + `X-AppSecret`。

签名与时间戳强绑定，所以**每个请求都要重算**，绝不能缓存签名字符串；
AppSecret 只参与拼串，不进日志、不进异常消息（`describe_network_error` 只打印
URL 里的域名，这一点由 `platform.http` 保证）。

匹配策略：官方推荐的流程是「文件识别 `/api/v2/match` → 拿 episodeId」，或
「搜索 → 人工/自动挑节目」。我们**刻意不学 Kazumi 那套未公开的
`bangumiId + 补零集数` 拼 episodeId 的写法** —— 那个规律没有官方文档背书，
一旦弹弹play 调整编号规则就会静默匹配到别的番，用户看到的是"弹幕驴唇不对马嘴"。
这里老老实实走 search + 标题相似度，并且**分数不够就返回 ok=False**，
宁可没有弹幕，也不给错弹幕。

缓存是硬要求（官方《使用约定》第 9 条与第 10 节"缓存建议"）：不缓存会被
检测机制限流。默认 TTL 见 `DANMAKU_CACHE_TTL_HOURS` / `DANMAKU_OLD_TTL_HOURS`。
网络失败时一律降级为"返回已有缓存 / 空列表"，不抛异常 —— 弹幕挂了不该影响播放。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from ...ui_kit.media.danmaku import (
    DanmakuItem,
    load_danmaku_file,
    parse_danmaku_xml,
    to_danmaku_xml,
)
from ..platform.http import HttpClient, SourceError, describe_network_error, host_of
from ..platform.paths import app_data_dir
from .models import normalize_title, parse_episode_number

#: 弹弹play 开放弹幕网络服务器（v2 API）
DANDANPLAY_BASE = "https://api.dandanplay.net"

#: 常规缓存有效期（小时）。官方建议 2–6 小时，取中间值。
DANMAKU_CACHE_TTL_HOURS = 6
#: 老番缓存有效期（小时）。官方说老番可以放到 2–7 天，取保守的 3 天。
DANMAKU_OLD_TTL_HOURS = 24 * 3

#: 匹配阈值：低于这个相似度宁可不给弹幕（0.6 大致是"片名主干相同、后缀有出入"）
MATCH_THRESHOLD = 0.6

#: 命中"这是动画"的类型关键词（弹弹play 的 type/typeDescription 里会出现）
_ANIME_KIND_WORDS = ("动画", "番剧", "动漫")

#: 限流/被拒时退避多久再试一次（秒）。官方没给 Retry-After，只能自己定。
_RATE_LIMIT_BACKOFF_SECONDS = 2.0

#: 判定"被限流/被拒绝"的响应特征（HttpClient 会把状态码写进异常文本）
_RATE_LIMIT_MARKS = ("429", "too many requests", "403", "forbidden", "request limit")

#: 缓存目录相对 `app_data_dir()` 的位置
_CACHE_SUBDIR = ("anime", "danmaku")

#: 文件名非法字符（Windows 比 POSIX 严格，统一按 Windows 清洗）
_ILLEGAL_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS = re.compile(r"\s+")

#: 弹幕 XML 单条元素：p 属性 + 元素体（体里可能还有未转义的 `<` 或 CDATA）
_D_ELEMENT = re.compile(
    r"<d\s+p=([\"'])(.*?)\1[^>]*>(.*?)</d\s*>", re.DOTALL | re.IGNORECASE)
#: 元素体里的 CDATA 段（弹弹play 偶发用 CDATA 包正文）
_CDATA_SEGMENT = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_TAG = re.compile(r"<[^>]*>")
#: HTML 实体（正文里 `&amp;` 之类必须先还原再展示）
_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"),
             ("&#39;", "'"), ("&nbsp;", " "), ("&amp;", "&"))


# --------------------------------------------------------------------------- 签名


def sign_dandanplay(app_id: str, app_secret: str, path: str,
                    timestamp: int | None = None) -> tuple[str, str]:
    """生成 `X-Timestamp` / `X-Signature`，返回 `(时间戳字符串, 签名)`。

    纯函数：只依赖入参，便于用固定向量做单测。`path` 传完整 URL 也没关系 ——
    这里会把域名和查询串剥掉（官方明确要求 `Path` 不含这两部分，传错了会 403
    `Invalid Signature`，而且报错信息里不会告诉你差在哪）。
    """
    request_path = api_path_of(path)
    stamp = int(time.time()) if timestamp is None else int(timestamp)
    payload = f"{app_id}{stamp}{request_path}{app_secret}"
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return str(stamp), base64.b64encode(digest).decode("ascii")


def api_path_of(url_or_path: str) -> str:
    """从 URL 或路径里取出"参与签名的 API 路径"（去域名、去查询串、去末尾斜杠）。"""
    text = (url_or_path or "").strip()
    if "://" in text:
        parts = urlsplit(text)
        text = parts.path or "/"
    else:
        text = text.split("?", 1)[0].split("#", 1)[0]
    if not text.startswith("/"):
        text = "/" + text
    return text.rstrip("/") or "/"


def looks_like_rate_limit(error: BaseException) -> bool:
    """异常是否属于"被限流 / 被拒绝"（429/403）。"""
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and status in (403, 429):
        return True
    if isinstance(error, SourceError):
        text = str(error)
    else:
        text = f"{type(error).__name__}: {error}"
    lowered = text.lower()
    return any(mark in lowered for mark in _RATE_LIMIT_MARKS)


def cache_root() -> Path:
    """弹幕缓存根目录（`%APPDATA%\\ModuWorkbench\\anime\\danmaku`）。"""
    path = app_data_dir()
    for part in _CACHE_SUBDIR:
        path = path / part
    return path


def sanitize_key(key: str, *, fallback: str = "episode", limit: int = 80) -> str:
    """把剧集标识清洗成安全文件名。

    剧集名里带 `:` `?` `*` 是常态（"第1话 但是…"），Windows 上直接抛
    `OSError`；另外补一个长度上限，避免路径超过 MAX_PATH 让写入失败。
    """
    text = unicodedata.normalize("NFKC", str(key or ""))
    text = _ILLEGAL_NAME.sub("_", text)
    text = _WS.sub("_", text).strip("._ ")
    if not text:
        text = fallback
    return text[:limit]


# --------------------------------------------------------------------------- 数据模型


@dataclass
class DanmakuAnime:
    """`/api/v2/search` 返回的一部番剧（同一作品的多个季度算不同条目）。"""

    anime_id: int = 0
    anime_title: str = ""
    type: str = ""
    type_description: str = ""
    episodes: list[dict] = field(default_factory=list)   # [{episodeId, episodeTitle}]

    @property
    def title(self) -> str:
        return self.anime_title

    @property
    def episode_count(self) -> int:
        return len(self.episodes)

    def episode_ids(self) -> list[int]:
        """全部节目编号（跳过解析不出的项）。"""
        ids: list[int] = []
        for item in self.episodes:
            try:
                ids.append(int((item or {}).get("episodeId") or 0))
            except (TypeError, ValueError):
                continue
        return [value for value in ids if value > 0]


@dataclass
class DanmakuMatch:
    """匹配结果：命中的节目编号 + 置信度 + 人能看懂的原因。"""

    episode_id: int = 0
    anime_id: int = 0
    anime_title: str = ""
    episode_title: str = ""
    score: float = 0.0
    note: str = ""

    @property
    def ok(self) -> bool:
        """是否可以直接拿来拉弹幕（没有节目编号或分数不够都不算命中）。"""
        return self.episode_id > 0 and self.score >= MATCH_THRESHOLD


@dataclass
class DanmakuComment:
    """服务端返回的原始弹幕条目（诊断/展示用；渲染仍走 `DanmakuItem`）。"""

    cid: int = 0
    text: str = ""
    time_ms: int = 0
    mode: int = 1
    color: int = 0xFFFFFF
    size: int = 25

    def to_item(self, *, source: str = "dandanplay") -> DanmakuItem:
        return DanmakuItem(text=self.text, time_ms=self.time_ms, mode=self.mode,
                           color=self.color, size=self.size, source=source)


# --------------------------------------------------------------------------- 客户端


class DandanplayClient:
    """弹弹play 只读客户端（搜索 / 匹配 / 取弹幕 / 本地缓存）。

    - 没配 AppId/AppSecret 时 `configured` 为 False，所有网络方法直接返回空，
      不抛异常也不打日志刷屏（未配置不是错误，是默认状态）；
    - 所有网络 + 解析异常在内部消化，降级为「缓存 → 空」；
    - 失败的简短中文说明放在 `last_note` 里，UI 想显示就显示，不想显示就算了。
    """

    def __init__(self, *, app_id: str = "", app_secret: str = "",
                 auth_mode: str = "signature", base_url: str = DANDANPLAY_BASE,
                 http_factory: Any = None, cache_dir: Any = None,
                 timeout: float = 15.0, user_agent: str = "",
                 enabled: bool | None = None) -> None:
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        # 未知的 auth_mode 一律退回签名模式（拼错模式名不该变成"不发认证头"）
        self.auth_mode = auth_mode if auth_mode in ("signature", "credential") else "signature"
        self.base_url = (base_url or DANDANPLAY_BASE).rstrip("/")
        self.timeout = float(timeout or 15.0)
        self.user_agent = user_agent or ""
        self._explicit_enabled = enabled
        self._http_factory = http_factory
        self._http: Any = None
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        #: 最近一次失败的中文说明（成功时清空）
        self.last_note = ""

    # ------------------------------------------------------------------ 配置

    @property
    def configured(self) -> bool:
        """是否具备调用开放接口的条件（两个凭证都非空）。"""
        return bool(self.app_id and self.app_secret)

    @property
    def enabled(self) -> bool:
        """总开关：显式设置优先，否则"配了凭证才算开"。"""
        if self._explicit_enabled is not None:
            return bool(self._explicit_enabled) and self.configured
        return self.configured

    @property
    def cache_dir(self) -> Path:
        if self._cache_dir is not None:
            return self._cache_dir
        self._cache_dir = cache_root()
        return self._cache_dir

    def signature(self, path: str, timestamp: int | None = None) -> tuple[str, str]:
        """返回 `(timestamp, signature)`；`timestamp` 为空时取当前 UTC 秒。"""
        return sign_dandanplay(self.app_id, self.app_secret, path, timestamp)

    def auth_headers(self, path: str) -> dict[str, str]:
        """按 `auth_mode` 生成认证头（签名每个请求重算，不能缓存）。"""
        if not self.configured:
            return {}
        if self.auth_mode == "credential":
            # 凭证模式由服务端校验 AppSecret，风险是密钥会随请求头落地到日志/抓包
            return {"X-AppId": self.app_id, "X-AppSecret": self.app_secret}
        stamp, signature = self.signature(path)
        return {"X-AppId": self.app_id, "X-Timestamp": stamp, "X-Signature": signature}

    # ------------------------------------------------------------------ 网络

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
                              hint="请检查网络或稍后重试；弹幕源不可用不影响播放")
        if hasattr(factory, "get") or hasattr(factory, "request"):
            # 直接传进来一个客户端实例也能用（测试里最省事）
            return factory
        return factory()

    def _get_json(self, path: str, *, params: dict | None = None) -> Any:
        url = self.base_url + path
        response = self._client().get(url, headers=self.auth_headers(path),
                                      params=params or None, timeout=self.timeout)
        return response.json()

    def _get_text(self, path: str, *, params: dict | None = None,
                  accept: str = "") -> str:
        url = self.base_url + path
        headers = self.auth_headers(path)
        if accept:
            headers["Accept"] = accept
        response = self._client().get(url, headers=headers, params=params or None,
                                      timeout=self.timeout)
        return response.text or ""

    # ------------------------------------------------------------------ 搜索

    def search(self, keyword: str) -> list[DanmakuAnime]:
        """按关键词搜索番剧；未配置 / 离线 / 被限流都返回 `[]`。"""
        self.last_note = ""
        text = (keyword or "").strip()
        if not text:
            self.last_note = "搜索关键词为空"
            return []
        if not self.enabled:
            self.last_note = ("弹弹play 弹幕网络未启用（缺少 AppId/AppSecret），"
                              "已跳过弹幕搜索")
            return []
        path = "/api/v2/search"
        try:
            payload = self._get_json(path, params={"keyword": text})
            return self.parse_search(payload)
        except Exception as error:  # noqa: BLE001 - 弹幕是可选功能，绝不外抛
            self.last_note = self._describe(error, path)
            return []

    @staticmethod
    def parse_search(payload: Any) -> list[DanmakuAnime]:
        """宽松解析搜索响应：字段缺失、类型不对都跳过该条，而不是整批失败。"""
        if not isinstance(payload, dict):
            return []
        raw_list = payload.get("animes")
        if not isinstance(raw_list, list):
            return []
        results: list[DanmakuAnime] = []
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            anime = DanmakuAnime(
                anime_id=_to_int(raw.get("animeId")),
                anime_title=str(raw.get("animeTitle") or "").strip(),
                type=str(raw.get("type") or "").strip(),
                type_description=str(raw.get("typeDescription") or "").strip(),
            )
            episodes = raw.get("episodes")
            if isinstance(episodes, list):
                for entry in episodes:
                    if not isinstance(entry, dict):
                        continue
                    episode_id = _to_int(entry.get("episodeId"))
                    if episode_id <= 0:
                        continue
                    anime.episodes.append({
                        "episodeId": episode_id,
                        "episodeTitle": str(entry.get("episodeTitle") or "").strip(),
                    })
            if anime.anime_id <= 0 and not anime.anime_title and not anime.episodes:
                continue
            results.append(anime)
        return results

    # ------------------------------------------------------------------ 匹配

    def match_series(self, title: str, episode_name: str, *,
                     episode_number: int = 0, year: str = "") -> DanmakuMatch:
        """在搜索结果里挑出"这一部番的这一集"，挑不出就明确说挑不出。

        打分维度（0~1）：

        - 标题相似度（主）：归一化后完全相等给 1.0，否则 `SequenceMatcher` 比例；
        - 集数对齐（强加分）：候选节目名里含同一集数 → +0.15；
        - 节目名相似（弱加分）：与传进来的集名有一定相似 → 最多 +0.05；
        - 类型是"动画/番剧" → +0.03（同一片名有真人版时靠这个区分）。

        阈值 `MATCH_THRESHOLD`：低于它时 `episode_id` 置 0（等于"没匹配到"），
        `score` 保留"最像的候选有多像"供排查，`note` 说明为什么放弃 ——
        **不会**退化成"随便给一集"。
        """
        result = DanmakuMatch()
        if not self.configured:
            result.note = "未配置弹弹play AppId/AppSecret，无法匹配弹幕"
            return result
        wanted = normalize_title(title)
        if not wanted:
            result.note = "番剧名为空，无法匹配弹幕"
            return result
        number = int(episode_number or 0) or parse_episode_number(episode_name or "")
        candidates = self.search(title)
        if not candidates:
            result.note = (self.last_note
                           or f"弹弹play 没有搜到「{(title or '').strip()}」的条目")
            return result

        best_episode: dict | None = None
        best_anime: DanmakuAnime | None = None
        best_score = 0.0
        best_title_score = 0.0
        best_episode_score = 0.0
        best_hit_number = False
        for anime in candidates:
            title_score = title_similarity(title, anime.anime_title)
            bonus = 0.03 if _is_anime_kind(anime) else 0.0
            for entry in anime.episodes:
                episode_title = str(entry.get("episodeTitle") or "")
                hit_number = bool(number) and _mentions_number(episode_title, number)
                score = title_score + bonus
                # 集数只做"命中就加分"，**不因为没写集数就扣分**：剧场版、总集篇、
                # "第X话"写成别的形式的都很多，扣分会把本来正确的候选压到阈值下。
                if hit_number:
                    score += 0.15
                if episode_title:
                    score += 0.05 * episode_similarity(episode_name, episode_title)
                if score > best_score:
                    best_score, best_anime, best_episode = score, anime, entry
                    best_title_score = title_score
                    best_episode_score = episode_similarity(episode_name, episode_title)
                    best_hit_number = hit_number
        if best_episode is None:
            result.note = (f"弹弹play 里「{candidates[0].anime_title}」等 "
                           f"{len(candidates)} 个条目都没有可用的节目列表")
            return result

        result.anime_id = best_anime.anime_id if best_anime else 0
        result.anime_title = best_anime.anime_title if best_anime else ""
        result.episode_title = str(best_episode.get("episodeTitle") or "")
        result.score = round(min(1.0, max(0.0, best_score)), 4)

        if result.score < MATCH_THRESHOLD:
            result.episode_id = 0
            result.note = (f"没找到足够可信的节目（最像的是「{result.anime_title}」，"
                           f"相似度 {best_title_score:.0%}，低于阈值 "
                           f"{MATCH_THRESHOLD:.0%}），已放弃匹配以免弹幕对不上")
            return result

        result.episode_id = _to_int(best_episode.get("episodeId"))
        year_hint = f"，{str(year).strip()} 年" if str(year or "").strip() else ""
        number_hint = ""
        if number:
            number_hint = (f"，节目名对上了第 {number} 集" if best_hit_number
                           else f"，但节目名没写第 {number} 集（按标题相似度取的）")
        title_hint = (f"，集名相似度 {best_episode_score:.0%}"
                      if best_episode_score >= 0.5 and episode_name else "")
        result.note = (f"匹配到「{result.anime_title}」{year_hint}"
                       f"的「{result.episode_title or '未命名节目'}」"
                       f"（标题相似度 {best_title_score:.0%}{number_hint}{title_hint}，"
                       f"综合可信度 {result.score:.0%}）")
        return result

    # ------------------------------------------------------------------ 弹幕

    def fetch_comments(self, episode_id: int, *,
                       with_related: bool = True) -> list[DanmakuComment]:
        """取某节目的弹幕。默认只要 XML（弹弹play 的默认格式），无数据返回 `[]`。

        被限流/拒绝时退避一次重试；仍失败就当作"没有弹幕"，并把中文说明写进
        `last_note`。
        """
        self.last_note = ""
        try:
            episode = int(episode_id)
        except (TypeError, ValueError):
            episode = 0
        if episode <= 0:
            self.last_note = "节目编号无效，无法获取弹幕"
            return []
        if not self.enabled:
            self.last_note = "弹幕网络未启用（缺少 AppId/AppSecret）"
            return []
        path = f"/api/v2/comment/{episode}"
        params = {"withRelated": "true"} if with_related else None
        text = ""
        for attempt in (1, 2):
            try:
                text = self._get_text(path, params=params,
                                      accept="application/xml, text/xml, */*")
                break
            except Exception as error:  # noqa: BLE001
                self.last_note = self._describe(error, path)
                if attempt == 1 and looks_like_rate_limit(error):
                    # 被限流时立刻重试只会火上浇油，退避一次；再失败就认了
                    self._sleep(_RATE_LIMIT_BACKOFF_SECONDS)
                    continue
                return []
        try:
            comments = self.parse_comments(text)
        except Exception as error:  # noqa: BLE001
            self.last_note = f"弹幕内容解析失败：{type(error).__name__}"
            return []
        if not comments:
            # 200 但一条都没解析出来：要么弹幕库是空的，要么格式变了 ——
            # 两种都要给用户一个说法，不能静默返回空列表
            self.last_note = ("弹弹play 返回了空弹幕库（该节目可能还没有弹幕）"
                              if not (text or "").strip()
                              else "弹幕响应里没有可识别的条目（接口格式可能已变化）")
        return comments

    @staticmethod
    def parse_comments(text: str) -> list[DanmakuComment]:
        """解析弹幕 XML（弹弹play / BiliBili / AcFun 同一套 `<d p="...">`）。

        比共享的 `parse_danmaku_xml` 多处理两件事：CDATA 包裹的正文（共享层会
        把 `<![CDATA[...]]>` 当字面文本原样留下）与正文里未转义的 `<` / `>`。
        两者在真实弹幕库里都出现过（"<3"、"<b>文案</b>"），所以这里自己剥一层。
        """
        comments: list[DanmakuComment] = []
        for match in _D_ELEMENT.finditer(text or ""):
            parts = (match.group(2) or "").split(",")
            if len(parts) < 4:
                continue
            try:
                seconds = float(parts[0])
                mode = int(float(parts[1]))
                size = int(float(parts[2]))
                color = int(float(parts[3]))
            except (TypeError, ValueError):
                continue
            body = _body_text(match.group(3))
            if not body:
                continue
            comments.append(DanmakuComment(
                text=body, time_ms=int(max(0.0, seconds) * 1000), mode=mode,
                color=color & 0xFFFFFF, size=max(8, min(64, size))))
        comments.sort(key=lambda entry: entry.time_ms)
        return comments

    def fetch_items(self, episode_id: int, *,
                    with_related: bool = True) -> list[DanmakuItem]:
        """拉弹幕 → 落缓存 → 返回渲染用条目（XML）。

        任何一步失败都**返回已有的缓存**（可能是过期的）；再没有就返回 `[]`。
        调用方不需要 try/except。缓存只按节目编号落盘（`<cache_dir>/0/epN.xml`），
        因为本模块拿不到 series_id；需要"按番剧分目录"的缓存请显式用
        `load_cached` / `save_cache`，那是 UI 层拿得到 series_id 的地方。
        """
        raw = self._fetch_raw(episode_id, with_related=with_related, use_cache=True)
        return parse_danmaku_xml(raw, source="dandanplay") if raw else []

    def load_cached_for_episode(self, episode_id: int) -> list[DanmakuItem]:
        """按节目编号读 `fetch_items` 写下的缓存（不打网络，UI 先调这个）。"""
        try:
            episode = int(episode_id)
        except (TypeError, ValueError):
            episode = 0
        if episode <= 0:
            return []
        path = self.cache_path(0, f"ep{episode}")
        try:
            return load_danmaku_file(path) if path.is_file() else []
        except Exception:  # noqa: BLE001
            return []

    def _fetch_raw(self, episode_id: int, *, with_related: bool, use_cache: bool) -> str:
        """取弹幕原始 XML：先看缓存，再打网络；网络失败才回退到缓存。"""
        try:
            episode = int(episode_id)
        except (TypeError, ValueError):
            episode = 0
        if episode <= 0:
            self.last_note = "节目编号无效，无法获取弹幕"
            return ""
        path = self.cache_path(0, f"ep{episode}")
        cached = ""
        if use_cache:
            try:
                cached = path.read_text(encoding="utf-8") if path.is_file() else ""
            except OSError:
                cached = ""
        if cached and self.cache_fresh(path):
            return cached
        if not self.enabled:
            self.last_note = self.last_note or "弹幕网络未启用（缺少 AppId/AppSecret）"
            return cached
        fetched = self._download_comments_xml(episode, with_related=with_related)
        if fetched:
            try:
                self._write_cache(path, fetched, episode_id=episode)
            except OSError as error:
                # 缓存写不进去（磁盘满/权限）不该让用户看不到弹幕
                self.last_note = f"弹幕缓存写入失败：{error.strerror or error}"
            return fetched
        # 没拿到新数据（失败或空弹幕库）：宁可给过期缓存，也不要让画面突然空掉
        return cached

    def _download_comments_xml(self, episode_id: int, *, with_related: bool) -> str:
        """下载弹幕 XML；**拿不到新数据就返回空串**，与"空弹幕库"同等对待。

        `last_note` 会说明到底是网络失败还是对面库是空的 —— 调用方凭这个区分
        "该不该把空结果写进缓存"（空结果不写，免得把已有缓存覆盖掉）。
        """
        request_path = f"/api/v2/comment/{int(episode_id)}"
        params = {"withRelated": "true"} if with_related else None
        for attempt in (1, 2):
            try:
                text = self._get_text(request_path, params=params,
                                      accept="application/xml, text/xml, */*")
            except Exception as error:  # noqa: BLE001
                self.last_note = self._describe(error, request_path)
                if attempt == 1 and looks_like_rate_limit(error):
                    self._sleep(_RATE_LIMIT_BACKOFF_SECONDS)
                    continue
                return ""
            if not (text or "").strip():
                # 200 + 空体是"这个节目还没有弹幕"，不是网络故障，别重试也别报错
                self.last_note = "弹弹play 返回了空弹幕库（该节目可能还没有弹幕）"
                return ""
            return text.lstrip("\ufeff")
        return ""

    # ------------------------------------------------------------------ 缓存

    def cache_path(self, series_id: int, episode_key: str) -> Path:
        """缓存文件路径：`<cache_dir>/<series_id>/<清洗后的 episode_key>.xml`。"""
        try:
            series = int(series_id)
        except (TypeError, ValueError):
            series = 0
        folder = self.cache_dir / str(series if series > 0 else 0)
        return folder / f"{sanitize_key(episode_key)}.xml"

    @staticmethod
    def _meta_path(path: Path) -> Path:
        return path.with_suffix(path.suffix + ".meta.json")

    def load_cached(self, series_id: int, episode_key: str, *,
                    provider: str = "dandanplay") -> list[DanmakuItem]:
        """读本地缓存（UI 应该先调这个，命中就不用打网络）。失败返回 `[]`。"""
        path = self.cache_path(series_id, episode_key)
        try:
            if not path.is_file():
                return []
            return load_danmaku_file(path)
        except Exception:  # noqa: BLE001 - 缓存坏了不该影响播放
            return []

    def save_cache(self, series_id: int, episode_key: str, items: Iterable[Any], *,
                   episode_id: int = 0, provider: str = "dandanplay") -> Path:
        """写缓存：弹幕 XML + `.meta.json` 旁车（记录来源与时间，便于判断新旧）。"""
        path = self.cache_path(series_id, episode_key)
        entries = self._as_items(items)
        self._write_cache(path, to_danmaku_xml(entries), episode_id=episode_id,
                          provider=provider, count=len(entries))
        return path

    def _write_cache(self, path: Path, xml: str, *, episode_id: int = 0,
                     provider: str = "dandanplay", count: int = -1) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(xml or "", encoding="utf-8")
        if count < 0:
            count = len(parse_danmaku_xml(xml or ""))
        meta = {
            "episode_id": _to_int(episode_id),
            "fetched_at": int(time.time()),
            "count": int(count),
            "provider": provider or "dandanplay",
        }
        self._meta_path(path).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_meta(self, path: Any) -> dict:
        """读旁车元数据；缺失/损坏返回 `{}`（缓存仍可用）。"""
        target = Path(path)
        meta_file = self._meta_path(target)
        try:
            payload = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
        return payload if isinstance(payload, dict) else {}

    def cache_fresh(self, path: Any, *, ttl_hours: float | None = None) -> bool:
        """缓存是否还在有效期内。

        优先看旁车 `fetched_at`，没有旁车就退回文件 mtime —— 旧版本写下的缓存
        也要能判新旧。没有任何时间信息时按"不新鲜"处理（宁可多请求一次，
        也不要拿着不知道多久以前的弹幕）。
        """
        target = Path(path)
        if not target.is_file():
            return False
        hours = DANMAKU_CACHE_TTL_HOURS if ttl_hours is None else float(ttl_hours)
        if hours <= 0:
            return False
        stamp = self.read_meta(target).get("fetched_at")
        try:
            fetched_at = float(stamp)
        except (TypeError, ValueError):
            try:
                fetched_at = target.stat().st_mtime
            except OSError:
                return False
        return (time.time() - fetched_at) < hours * 3600.0

    # ------------------------------------------------------------------ 内部工具

    def _as_items(self, items: Iterable[Any]) -> list[DanmakuItem]:
        """把 `DanmakuComment` / `DanmakuItem` 混着的可迭代对象统一成 item 列表。"""
        entries: list[DanmakuItem] = []
        for entry in items or ():
            if isinstance(entry, DanmakuItem):
                entries.append(entry)
            elif isinstance(entry, DanmakuComment):
                entries.append(entry.to_item())
            elif isinstance(entry, dict):
                entries.append(DanmakuItem(
                    text=str(entry.get("text") or ""),
                    time_ms=_to_int(entry.get("time_ms")),
                    mode=_to_int(entry.get("mode")) or 1,
                    color=_to_int(entry.get("color")) or 0xFFFFFF,
                    size=_to_int(entry.get("size")) or 25))
        return entries

    def _describe(self, error: BaseException, path: str) -> str:
        """把异常翻成可读中文；被限流时给出"缓一缓"的说法。"""
        if looks_like_rate_limit(error):
            return ("弹弹play 提示请求过于频繁或凭证被拒（429/403），"
                    "已按「稍后再试」处理；可先看本地缓存的弹幕")
        return describe_network_error(error, host_of(self.base_url + path),
                                      hint="弹幕源不可用不影响播放")

    @staticmethod
    def _sleep(seconds: float) -> None:
        """退避（单独抽出来，测试里直接替换掉，避免真的等 2 秒）。"""
        time.sleep(max(0.0, float(seconds)))


# --------------------------------------------------------------------------- 相似度


def title_similarity(left: str, right: str) -> float:
    """番剧名相似度：归一化后完全相等给 1.0，否则 `SequenceMatcher` 比例。

    刻意**不给包含关系加基线**（影视匹配那边有，这里没有）：「进击的巨人」和
    「进击的巨人 最终季」是两部不同的番、不同的弹幕库，包含关系不能算高分。
    """
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def episode_similarity(left: str, right: str) -> float:
    """剧集名相似度（归一化后比较，用于给候选节目做弱加分）。

    与 `title_similarity` 同一口径，单独留一个名字是因为调用点分两类：
    比"番剧名"和比"集名"，读代码时要能一眼分清比的是哪个。
    """
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _is_anime_kind(anime: DanmakuAnime) -> bool:
    text = f"{anime.type}{anime.type_description}"
    return any(word in text for word in _ANIME_KIND_WORDS)


def _mentions_number(episode_title: str, number: int) -> bool:
    """节目名里是否出现指定集数（"第 12 话" / "EP12" / "12." 都算）。"""
    if not episode_title or number <= 0:
        return False
    if parse_episode_number(episode_title) == number:
        return True
    # 弹弹play 的节目名常常只是"第 12 集"或"12"，再兜一层裸数字
    return any(int(token) == number
               for token in re.findall(r"\d{1,4}", episode_title)
               if token.isdigit())


def _body_text(raw: str) -> str:
    """取出 `<d>` 的正文：CDATA 优先，其次剥标签 + 还原实体。"""
    body = raw or ""
    chunks = _CDATA_SEGMENT.findall(body)
    if chunks:
        body = " ".join(chunk for chunk in chunks)
        return _unescape(body)
    body = _TAG.sub("", body)
    return _unescape(body)


def _unescape(text: str) -> str:
    value = (text or "").replace("<br>", "\n").replace("<br/>", "\n")
    for entity, char in _ENTITIES:
        value = value.replace(entity, char)
    return value.strip()


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


__all__ = [
    "DANDANPLAY_BASE", "DANMAKU_CACHE_TTL_HOURS", "DANMAKU_OLD_TTL_HOURS",
    "MATCH_THRESHOLD", "DanmakuAnime", "DanmakuComment", "DanmakuMatch",
    "DandanplayClient", "api_path_of", "cache_root", "episode_similarity",
    "looks_like_rate_limit", "sanitize_key", "sign_dandanplay",
    "title_similarity",
]
