"""酷我音乐音源（公开检索接口 + antiserver 直链，无需登录）。

实测：搜索与完整曲目直链均可用，是网易云失效/VIP 限制时最主要的替代音源。
"""
from __future__ import annotations

import html
import json
import re
from typing import List

from ...models import RemoteTrack
from ..base import KIND_FULL, MusicSource, SourceInfo
from ..http import SourceError

_KUWO_RID = re.compile(r"(\d+)")


class KuwoSource(MusicSource):
    info = SourceInfo(
        key="kuwo",
        label="酷我音乐",
        note="公开检索接口 + antiserver 直链，无需登录，通常可直接下载完整曲目（个人学习用途）。",
        kind=KIND_FULL,
        homepage="https://www.kuwo.cn",
    )
    SEARCH_URL = "http://search.kuwo.cn/r.s"
    RESOLVE_URLS = (
        "http://antiserver.kuwo.cn/anti.s",
        "https://antiserver.kuwo.cn/anti.s",
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
            tracks.append(track)
        if kind == "artist" and tracks:
            artist = tracks[0].artist
            tracks = [t for t in tracks if t.artist == artist] or tracks
        return tracks

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
        last_error = ""
        for url in self.RESOLVE_URLS:
            try:
                text = self.http.get(
                    url,
                    params={"type": "convert_url", "format": "mp3", "response": "url", "rid": rid},
                ).text.strip()
            except SourceError as error:
                last_error = str(error)
                continue
            if text.startswith("http") and "music.163.com" not in text:
                return text
            last_error = last_error or "酷我未返回可用地址"
        raise SourceError(last_error or "该曲目在酷我不可下载（可能为付费/下架曲目）")
