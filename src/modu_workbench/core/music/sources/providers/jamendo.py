"""Jamendo 音源（CC 授权完整曲目，需要免费 client_id）。"""
from __future__ import annotations

import os
from typing import List

from ...models import RemoteTrack
from ..base import KIND_CC, MusicSource, SourceInfo
from ..http import SourceError


class JamendoSource(MusicSource):
    info = SourceInfo(
        key="jamendo",
        label="Jamendo（CC 完整曲目）",
        note="CC 授权独立音乐，可完整下载；需免费 client_id（设置里填写或环境变量 MODU_JAMENDO_CLIENT_ID）。",
        kind=KIND_CC,
        needs_key=True,
        homepage="https://developer.jamendo.com",
    )
    BASE = "https://api.jamendo.com/v3.0"

    def __init__(self, *, http=None, key: str = ""):  # noqa: ANN001
        super().__init__(http=http, key=key or os.environ.get("MODU_JAMENDO_CLIENT_ID", ""))

    def available(self) -> bool:
        return bool(self.credential)

    def unavailable_reason(self) -> str:
        return "需要在设置中填写 Jamendo client_id（免费申请）"

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        self.require_available()
        params = {
            "client_id": self.credential,
            "format": "json",
            "limit": max(1, min(limit, 50)),
            "include": "musicinfo+lyrics",
            "audioformat": "mp32",
        }
        if kind == "genre":
            params["tags"] = keyword
        else:
            params["search"] = keyword
            if kind == "artist":
                params["artist_name"] = keyword
        payload = self.http.get_json(f"{self.BASE}/tracks", params=params)
        tracks: List[RemoteTrack] = []
        for item in payload.get("results") or []:
            audio = item.get("audio") or item.get("audiodownload") or ""
            if not audio:
                continue
            musicinfo = item.get("musicinfo") or {}
            genres = ((musicinfo.get("tags") or {}).get("genres") or [""])
            tracks.append(RemoteTrack(
                source=self.key,
                remote_id=str(item.get("id") or ""),
                title=str(item.get("name") or ""),
                artist=str(item.get("artist_name") or ""),
                album=str(item.get("album_name") or ""),
                duration_ms=int(item.get("duration") or 0) * 1000,
                url=str(audio),
                cover_url=str(item.get("image") or ""),
                category=str(genres[0] if genres else ""),
                extra={"lyrics": str((musicinfo.get("lyrics") or {}).get("text") or "")},
            ))
        return tracks

    def download_url(self, track: RemoteTrack) -> str:
        if not track.url:
            raise SourceError("Jamendo 未提供下载地址")
        return track.url

    def lyrics(self, track: RemoteTrack) -> str:
        return str(track.extra.get("lyrics") or "")
