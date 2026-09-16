"""苹果 CMS (maccms V10) 采集源：一套代码适配所有 `api.php/provide/vod` 接口。

这类接口是影视聚合领域的事实标准（也是 LibreTV 等开源项目的数据来源），
返回结构统一：

    {"code":1,"page":1,"pagecount":1,"limit":"20","total":3,
     "list":[{"vod_id":1,"vod_name":"流浪地球","vod_play_from":"360zy",
              "vod_play_url":"正片$https://.../index.m3u8", ...}]}

关键约定：
- `vod_play_from` 用 `$$$` 分隔多个播放线路，`vod_play_url` 用 `$$$` 一一对应；
- 每个线路内部用 `#` 分隔分集，每集用 `$` 分隔「集名」与「地址」；
- 线路名（如 `lzm3u8`/`1080p`）常含画质线索，分片清单里还有真实清晰度。

因此「多线路」天然映射为「多清晰度选项」，本模块把每个线路都转成一个
`Episode`，并尽力从线路名推断画质；真实 m3u8 主清单的画质由 `hls.py` 再细化。
"""
from __future__ import annotations

import re
from typing import Iterable, List

from ...models import Episode, Quality, RemoteVideo, classify_kind, strip_html
from ..base import KIND_FULL, SourceInfo, VideoSource
from ..http import HttpClient, SourceError, host_of

# maccms 的 ac 参数
AC_VIDEOLIST = "videolist"
AC_DETAIL = "detail"

# 线路名里可能出现的画质线索：按清晰度从高到低匹配
_ROUTE_QUALITY_HINTS: tuple[tuple[str, str], ...] = (
    ("4k", "4K"), ("2160", "4K"), ("hd4", "4K"),
    ("2k", "2K"), ("1440", "2K"),
    ("1080", "1080P"), ("fhd", "1080P"), ("hd1080", "1080P"),
    ("720", "720P"), ("hd", "高清"), ("shd", "高清"),
    ("480", "480P"), ("sd", "标清"),
)


def guess_quality_label(text: str) -> str:
    """从线路名/备注里推断画质标签（识别不出时返回空串）。"""
    lowered = (text or "").lower()
    if not lowered:
        return ""
    for keyword, label in _ROUTE_QUALITY_HINTS:
        if keyword in lowered:
            return label
    return ""


def parse_play_urls(play_url: str, play_from: str = "") -> list[Episode]:
    """解析 `vod_play_url` 为分集列表（多线路时展开为「线路 · 集名」）。

    单线路：集名保持原样（正片 / 第01集）；
    多线路：集名前面加线路名，便于用户区分不同来源。
    """
    if not play_url:
        return []
    routes = [item.strip() for item in str(play_url).split("$$$")]
    froms = [item.strip() for item in str(play_from or "").split("$$$") if item.strip()]
    multi = len([r for r in routes if r]) > 1
    episodes: list[Episode] = []
    for route_index, route in enumerate(routes):
        if not route.strip():
            continue
        route_name = froms[route_index] if route_index < len(froms) else ""
        quality = guess_quality_label(route_name)
        for part in route.split("#"):
            part = part.strip()
            if not part:
                continue
            name, _, url = part.partition("$")
            name, url = name.strip(), url.strip()
            if not url:
                # 少数站点只给地址、用 $ 开头
                name, url = "", name
            if not url:
                continue
            if not _looks_playable(url):
                continue
            label = name or f"第{len(episodes) + 1:02d}集"
            if multi and route_name:
                label = f"{route_name} · {label}"
            episodes.append(Episode(
                name=label, url=url, index=len(episodes), route=route_name,
                qualities=([Quality(label=quality, url=url, source="")] if quality else []),
            ))
    return episodes


def _looks_playable(url: str) -> bool:
    """过滤掉音频/字幕等非视频条目。"""
    lowered = url.lower()
    if lowered.startswith(("http://", "https://", "//")):
        pass
    else:
        return False
    for suffix in (".mp3", ".m4a", ".aac", ".flac", ".srt", ".ass", ".vtt", ".jpg", ".png"):
        if lowered.endswith(suffix) or f"{suffix}?" in lowered:
            return False
    return True


class CmsVodSource(VideoSource):
    """苹果 CMS V10 采集源（同一个类可承载任意多个采集站实例）。"""

    KIND_MAP = {
        "movie": ("电影", "片"),
        "tv": ("剧",),
        "anime": ("动漫", "动画"),
        "variety": ("综艺",),
        "doc": ("纪录",),
    }

    def __init__(self, info: SourceInfo, *, http: HttpClient | None = None,
                 base_url: str = "", timeout: float = 15.0):
        self.info = info
        self.base_url = (base_url or info.homepage or "").rstrip("/")
        super().__init__(http=http, key="")
        if http is None:
            self.http = HttpClient(headers=self.default_headers(), timeout=timeout)

    # ---------- 元信息 ----------

    def default_headers(self) -> dict:
        headers = {}
        if self.base_url:
            from ..http import origin_of

            headers["Referer"] = origin_of(self.base_url)
        return headers

    def available(self) -> bool:
        return bool(self.base_url)

    def unavailable_reason(self) -> str:
        return "该视频源尚未配置接口地址"

    def set_credential(self, value: str) -> None:
        """允许用户在设置里覆盖接口地址（便于站点换域名）。"""
        value = (value or "").strip()
        if value:
            self.base_url = value.rstrip("/")

    @property
    def credential(self) -> str:
        return self.base_url

    # ---------- 请求 ----------

    def _api(self) -> str:
        if not self.base_url:
            raise SourceError(self.unavailable_reason())
        base = self.base_url
        if base.endswith("provide/vod"):
            return base
        if base.endswith("api.php"):
            return f"{base}/provide/vod"
        return f"{base}/api.php/provide/vod"

    def _query(self, *, keyword: str = "", kind: str = "all", page: int = 1,
               limit: int = 20, ids: str = "") -> dict:
        url = self._api()
        params: dict = {"ac": AC_VIDEOLIST if not ids else AC_DETAIL}
        if ids:
            params["ids"] = ids
        if keyword:
            params["wd"] = keyword
        if kind and kind != "all":
            type_id = self._type_id(kind)
            if type_id:
                params["t"] = type_id
        if page and page > 1:
            params["pg"] = page
        referer = self.default_headers().get("Referer", "")
        payload = self.http.get_json(url, params=params, headers={"Referer": referer} if referer else None)
        if not isinstance(payload, dict):
            raise SourceError(f"{host_of(url)} 返回结构异常（不是对象）")
        code = payload.get("code")
        if code is not None and int(code) not in (1, 200):
            message = payload.get("msg") or f"接口返回 code={code}"
            raise SourceError(f"{self.label}：{message}")
        return payload

    def _type_id(self, kind: str) -> str:
        """从分类表里挑出最匹配的一级分类 id（找不到就不过滤，宁可多给结果）。"""
        keywords = self.KIND_MAP.get(kind)
        if not keywords:
            return ""
        for item in self._classes():
            name = str(item.get("type_name") or "")
            if any(keyword in name for keyword in keywords):
                return str(item.get("type_id") or "")
        return ""

    def _classes(self) -> list[dict]:
        cached = getattr(self, "_class_cache", None)
        if cached is not None:
            return cached
        classes: list[dict] = []
        try:
            referer = self.default_headers().get("Referer", "")
            payload = self.http.get_json(
                self._api(), params={"ac": "list"},
                headers={"Referer": referer} if referer else None,
            )
            for item in (payload.get("class") or []) if isinstance(payload, dict) else []:
                if isinstance(item, dict) and not item.get("type_pid"):
                    classes.append(item)
        except Exception:  # noqa: BLE001  分类表拉不到不影响搜索
            classes = []
        self._class_cache = classes
        return classes

    # ---------- 能力 ----------

    def search(self, keyword: str, kind: str = "all", limit: int = 30,
               page: int = 1) -> List[RemoteVideo]:
        self.require_available()
        if not (keyword or "").strip() and kind == "all":
            raise SourceError("请输入搜索关键词")
        payload = self._query(keyword=keyword, kind=kind, page=page, limit=limit)
        return self.parse_list(payload.get("list") or [], limit=limit)

    def browse(self, kind: str = "all", page: int = 1, limit: int = 30) -> List[RemoteVideo]:
        """按分类浏览最新入库（无关键词）。"""
        self.require_available()
        payload = self._query(kind=kind, page=page, limit=limit)
        return self.parse_list(payload.get("list") or [], limit=limit)

    def detail(self, video: RemoteVideo) -> RemoteVideo:
        """拉详情补齐分集（搜索结果通常已带剧集，这里用于兜底与刷新）。"""
        self.require_available()
        if not video.remote_id:
            return video
        payload = self._query(ids=video.remote_id)
        items = payload.get("list") or []
        if not items:
            return video
        merged = self.parse_video(items[0])
        merged.source = video.source
        merged.remote_id = video.remote_id
        return merged

    # ---------- 解析 ----------

    def parse_list(self, items: Iterable, limit: int = 30) -> List[RemoteVideo]:
        results: List[RemoteVideo] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            video = self.parse_video(item)
            if not video.title:
                continue
            results.append(video)
            if len(results) >= limit:
                break
        return results

    def parse_video(self, item: dict) -> RemoteVideo:
        title = str(item.get("vod_name") or "").strip()
        type_name = str(item.get("type_name") or "")
        class_text = str(item.get("vod_class") or "")
        episodes = parse_play_urls(
            str(item.get("vod_play_url") or ""), str(item.get("vod_play_from") or "")
        )
        kind = self._infer_kind(type_name, class_text, str(item.get("vod_sub") or ""))
        return RemoteVideo(
            source=self.key,
            remote_id=str(item.get("vod_id") or ""),
            title=title,
            kind=kind,
            year=self._year_of(item),
            region=str(item.get("vod_area") or "").strip(),
            category=type_name.strip(),
            tags=class_text.strip(),
            actors=self._clean_people(item.get("vod_actor")),
            director=self._clean_people(item.get("vod_director")),
            description=strip_html(str(item.get("vod_content") or item.get("vod_blurb") or "")),
            cover_url=str(item.get("vod_pic") or "").strip(),
            remarks=str(item.get("vod_remarks") or "").strip(),
            score=str(item.get("vod_score") or item.get("vod_douban_score") or "").strip(),
            duration_ms=self._duration_ms(item),
            episodes=episodes,
            series_key=f"{self.key}:{item.get('vod_id')}",
        )

    def _infer_kind(self, type_name: str, class_text: str, sub: str) -> str:
        # 站点分类名优先（如「动漫片」「国产剧」），其次用标签/副标题兜底
        return classify_kind(type_name, class_text, sub)

    @staticmethod
    def _clean_people(value) -> str:  # noqa: ANN001
        text = str(value or "").strip()
        if not text:
            return ""
        # 演员列表常按逗号分隔，保留原样但去掉多余空白
        return re.sub(r"\s*,\s*", ",", text)

    @staticmethod
    def _year_of(item: dict) -> str:
        year = str(item.get("vod_year") or "").strip()
        if year:
            return year[:4]
        pubdate = str(item.get("vod_pubdate") or "")
        match = re.search(r"(19|20)\d{2}", pubdate)
        return match.group(0) if match else ""

    @staticmethod
    def _duration_ms(item: dict) -> int:
        """站点有时长（分钟）或时长（时:分:秒）两种写法。"""
        raw = str(item.get("vod_duration") or "").strip()
        if not raw:
            return 0
        if ":" in raw:
            parts = [p.strip() for p in raw.split(":")]
            try:
                numbers = [int(p) for p in parts]
            except ValueError:
                return 0
            seconds = 0
            for number in numbers:
                seconds = seconds * 60 + number
            return seconds * 1000
        try:
            return int(float(raw) * 60 * 1000)
        except ValueError:
            return 0

    # ---------- 下载头 ----------

    def download_headers(self, url: str = "") -> dict:
        headers = {}
        referer = self.default_headers().get("Referer", "")
        if referer:
            headers["Referer"] = referer
        return headers


def build_source(key: str, label: str, base_url: str, note: str = "",
                 kind: str = KIND_FULL, homepage: str = "") -> CmsVodSource:
    """便捷构造一个采集源实例（供 providers 注册表使用）。"""
    info = SourceInfo(
        key=key, label=label, note=note or f"{label} · 苹果CMS 采集接口",
        kind=kind, homepage=homepage or base_url, editable=True,
    )
    return CmsVodSource(info, base_url=base_url)
