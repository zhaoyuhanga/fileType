"""酷我音乐音源（公开检索接口 + antiserver 直链，无需登录）。

实测：搜索与完整曲目直链均可用，是网易云失效/VIP 限制时最主要的替代音源。

注意（用户反馈）：酷我搜索接口会把同一首歌的**片段/串烧/铃声**版本一起返回
（`DURATION` 只有 10~40 秒），点下载还会出现「当前歌曲只能在酷我手机端播放」这类
限制。因此这里做了两件事：
1. 每条结果都用 `quality` 规则标注「试听/片段」（界面默认隐藏、排在完整曲目之后）；
2. 解析不出直链时，把酷我返回的**原文**带进错误信息，用户能看到真实原因，
   同时注册表的「自动换源」会据此改用其他音源。
"""
from __future__ import annotations

import html
import json
import re
from typing import List

from ...models import RemoteTrack
from .. import quality
from ..base import KIND_FULL, MusicSource, SourceInfo
from ..http import SourceError

_KUWO_RID = re.compile(r"(\d+)")
#: 酷我这类"半截"返回（不是直链也没有可解释信息）时的兜底提示
_NOT_URL_HINT = "酷我未返回可播放地址"
#: 已知的受限提示（酷我对 VIP/版权受限曲目返回的文案），命中时直接透传给用户
_RESTRICTED_HINTS = ("只能", "手机", "会员", "VIP", "版权", "下架", "无法播放", "付费")


class KuwoSource(MusicSource):
    info = SourceInfo(
        key="kuwo",
        label="酷我音乐",
        note="公开检索接口 + antiserver 直链，无需登录，通常可直接下载完整曲目（个人学习用途）。"
             "搜索结果里的片段/铃声条目会被自动标注（界面默认隐藏）。",
        kind=KIND_FULL,
        homepage="https://www.kuwo.cn",
    )
    SEARCH_URL = "http://search.kuwo.cn/r.s"
    RESOLVE_URLS = (
        "http://antiserver.kuwo.cn/anti.s",
        "https://antiserver.kuwo.cn/anti.s",
    )
    #: 依次尝试的解析参数（先标准 128k mp3，再高码率，最后新版接口）
    RESOLVE_VARIANTS = (
        {"type": "convert_url", "format": "mp3", "response": "url"},
        {"type": "convert_url", "format": "mp3", "br": "128kmp3", "response": "url"},
        {"type": "convert_url2", "format": "mp3|aac", "response": "url"},
    )

    def default_headers(self) -> dict:
        return {"Referer": "https://www.kuwo.cn/"}

    # ---------- 搜索 ----------

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        params = {
            "all": keyword,
            "ft": "music",
            "itemset": "web_2013",
            "client": "kt",
            "pn": 0,
            "rn": max(1, min(limit, 50)),
            "rformat": "json",
            "encoding": "utf8",
        }
        text = self.http.get(self.SEARCH_URL, params=params).text
        # 接口返回的是 jsonp 风格（单引号 + \uXXXX 转义），需要先规整
        payload = self._loads(text)
        items = payload.get("abslist") or payload.get("musiclist") or []
        tracks: List[RemoteTrack] = []
        for item in items:
            if len(tracks) >= limit:
                break
            rid = item.get("MUSICRID") or item.get("DC_TARGETID") or ""
            match = _KUWO_RID.search(str(rid))
            if not match:
                continue
            track = RemoteTrack(
                source=self.key,
                remote_id=f"MUSIC_{match.group(1)}",
                title=html.unescape(str(item.get("NAME") or item.get("SONGNAME") or "")).replace("\u0026", "&"),
                artist=html.unescape(str(item.get("ARTIST") or "")).replace("\u0026", "&"),
                album=html.unescape(str(item.get("ALBUM") or "")),
                duration_ms=int(float(item.get("DURATION") or 0) * 1000),
                cover_url=self._cover_url(item),
                category="酷我",
                extra={"rid": f"MUSIC_{match.group(1)}"},
            )
            # 把「片段/铃声/试听」这类条目标出来（界面默认隐藏，排在完整曲目之后）
            quality.annotate(track)
            tracks.append(track)
        if kind == "artist" and tracks:
            artist = tracks[0].artist
            tracks = [t for t in tracks if t.artist == artist] or tracks
        return quality.sort_full_first(tracks)

    @staticmethod
    def _loads(text: str) -> dict:
        cleaned = text.strip()
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = cleaned[1:-1]
        try:
            return json.loads(cleaned)
        except ValueError:
            pass
        try:
            # 接口用单引号包裹字符串，替换后大多数情况下可解析
            return json.loads(cleaned.replace("'", '"'))
        except ValueError as error:
            raise SourceError("酷我检索接口返回格式异常（接口可能已变更）") from error

    @staticmethod
    def _cover_url(item: dict) -> str:
        short = item.get("web_albumpic_short") or item.get("web_artistpic_short") or ""
        if not short:
            return ""
        return f"https://img2.kuwo.cn/star/albumcover/{str(short).lstrip('/')}"

    # ---------- 下载地址 ----------

    def download_url(self, track: RemoteTrack) -> str:
        rid = track.remote_id or track.extra.get("rid") or ""
        if not rid:
            raise SourceError("缺少酷我曲目 ID")
        messages: list[str] = []
        for url in self.RESOLVE_URLS:
            for variant in self.RESOLVE_VARIANTS:
                params = dict(variant, rid=rid)
                try:
                    text = self.http.get(url, params=params).text.strip()
                except SourceError as error:
                    messages.append(str(error))
                    continue
                if text.startswith("http") and "music.163.com" not in text:
                    return text
                # 有些接口用 JSON 包一层：{"url": "..."} / {"data": {"url": "..."}}
                nested = self._extract_url(text)
                if nested:
                    return nested
                if text and len(text) < 200:
                    messages.append(text)
        # 酷我对受限曲目会返回「只能在酷我手机端播放」这类中文提示，原样透传给用户
        restricted = next((m for m in messages if any(h in m for h in _RESTRICTED_HINTS)), "")
        if restricted:
            raise SourceError(f"酷我限制：{restricted}")
        detail = messages[0] if messages else ""
        raise SourceError(
            f"{_NOT_URL_HINT}（可能为付费/下架曲目）" + (f"：{detail}" if detail else "")
        )

    @staticmethod
    def _extract_url(text: str) -> str:
        """从 JSON/键值响应里取出直链（接口偶尔换包装）。"""
        if not text or "http" not in text:
            return ""
        try:
            data = json.loads(text)
        except ValueError:
            match = re.search(r"https?://[^\s\"'<>]+", text)
            return match.group(0) if match else ""
        queue = [data]
        while queue:
            current = queue.pop(0)
            if isinstance(current, str) and current.startswith("http"):
                return current
            if isinstance(current, dict):
                queue.extend(current.values())
            elif isinstance(current, list):
                queue.extend(current)
        return ""
