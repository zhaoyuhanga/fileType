"""Audius 音源（去中心化音乐平台公开 API，免费、无需密钥，完整曲目）。"""
from __future__ import annotations

import time
from typing import List

from ...models import RemoteTrack
from ..base import KIND_CC, MusicSource, SourceInfo
from ..http import SourceError

APP_NAME = "ModuWorkbench"
_DISCOVERY_URL = "https://api.audius.co"


class AudiusSource(MusicSource):
    info = SourceInfo(
        key="audius",
        label="Audius（自由音乐）",
        note="去中心化音乐平台公开 API，无需密钥即可搜索与完整播放/下载，多为独立音乐人作品。",
        kind=KIND_CC,
        homepage="https://audius.co",
    )
    _host: str = ""
    _host_at: float = 0.0

    def _node(self) -> str:
        """发现节点（缓存 10 分钟）：api.audius.co 会返回可用节点列表。"""
        now = time.time()
        if self._host and now - self._host_at < 600:
            return self._host
        payload = self.http.get_json(_DISCOVERY_URL)
        hosts = payload.get("data") or []
        if not hosts:
            raise SourceError("Audius 未返回可用节点")
        self._host = str(hosts[0]).rstrip("/")
        self._host_at = now
        return self._host

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        node = self._node()
        payload = self.http.get_json(
            f"{node}/v1/tracks/search",
            params={"query": keyword, "app_name": APP_NAME, "limit": max(1, min(limit, 50))},
        )
        tracks: List[RemoteTrack] = []
        for item in payload.get("data") or []:
            track_id = item.get("id")
            if not track_id:
                continue
            user = item.get("user") or {}
            artwork = item.get("artwork") if isinstance(item.get("artwork"), dict) else {}
            tracks.append(RemoteTrack(
                source=self.key,
                remote_id=str(track_id),
                title=str(item.get("title") or ""),
                artist=str(user.get("name") or item.get("artist") or ""),
                album=str(item.get("album_name") or (item.get("playlist_name") or "")),
                duration_ms=int(item.get("duration") or 0) * 1000,
                url=f"{node}/v1/tracks/{track_id}/stream?app_name={APP_NAME}",
                cover_url=str(artwork.get("480x480") or artwork.get("150x150") or ""),
                category=str(item.get("genre") or "Audius"),
                extra={"node": node, "track_id": str(track_id)},
            ))
        if kind == "artist" and tracks:
            artist = tracks[0].artist
            tracks = [t for t in tracks if t.artist == artist] or tracks
        return tracks

    def download_url(self, track: RemoteTrack) -> str:
        node = str(track.extra.get("node") or "") or self._node()
        track_id = track.remote_id or track.extra.get("track_id")
        if not track_id:
            raise SourceError("缺少 Audius 曲目 ID")
        return f"{node}/v1/tracks/{track_id}/stream?app_name={APP_NAME}"
