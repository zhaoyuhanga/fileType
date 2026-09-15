"""iTunes 试听音源（Apple 公开搜索接口，无需密钥，30 秒片段 + 完整元数据）。"""
from __future__ import annotations

import os
from typing import List

from ...models import RemoteTrack
from ..base import KIND_PREVIEW, MusicSource, SourceInfo

_ENTITY = {"song": "song", "artist": "song", "album": "album", "genre": "song"}
_ATTRIBUTE = {"artist": "artistTerm", "genre": "genreTerm"}


class ItunesSource(MusicSource):
    info = SourceInfo(
        key="itunes",
        label="iTunes 试听",
        note="Apple 公开接口，提供 30 秒试听片段与完整元数据；无需密钥，长期稳定的兜底音源。",
        kind=KIND_PREVIEW,
        homepage="https://www.apple.com/itunes/",
    )
    BASE = "https://itunes.apple.com"

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        params = {
            "term": keyword,
            "media": "music",
            "entity": _ENTITY.get(kind, "song"),
            "limit": max(1, min(limit, 50)),
        }
        attribute = _ATTRIBUTE.get(kind)
        if attribute:
            params["attribute"] = attribute
        country = os.environ.get("MODU_ITUNES_COUNTRY", "").strip()
        if country:
            params["country"] = country

        # 偶发空响应 / CN 商店检索为空：去掉 country 再试一次
        variants = [dict(params)]
        variants.append({key: value for key, value in params.items() if key != "country"})
        for variant in variants:
            tracks = self._parse(self.http.get_json(f"{self.BASE}/search", params=variant), limit)
            if tracks:
                return tracks
        return []

    def _parse(self, payload: dict, limit: int) -> List[RemoteTrack]:
        tracks: List[RemoteTrack] = []
        for item in payload.get("results") or []:
            if len(tracks) >= limit:
                break
            preview = item.get("previewUrl")
            if not preview:
                continue
            tracks.append(RemoteTrack(
                source=self.key,
                remote_id=str(item.get("trackId") or item.get("collectionId") or ""),
                title=str(item.get("trackName") or item.get("collectionName") or ""),
                artist=str(item.get("artistName") or ""),
                album=str(item.get("collectionName") or ""),
                duration_ms=int(item.get("trackTimeMillis") or 0),
                url=str(preview),
                cover_url=str(item.get("artworkUrl100") or ""),
                category=str(item.get("primaryGenreName") or ""),
                extra={"preview": True, "track_id": str(item.get("trackId") or "")},
            ))
        return tracks
