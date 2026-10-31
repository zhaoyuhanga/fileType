"""音频直链音源：粘贴 http(s) 音频地址直接下载。"""
from __future__ import annotations

import os
from typing import List

from ...models import RemoteTrack
from ..base import KIND_DIRECT, MusicSource, SourceInfo
from ..http import SourceError


class DirectUrlSource(MusicSource):
    info = SourceInfo(
        key="url",
        label="音频直链",
        note="粘贴 http(s) 音频直链（mp3/m4a/flac/ogg…）直接下载，适用于自建或已授权地址。",
        kind=KIND_DIRECT,
    )

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        url = (keyword or "").strip()
        if not url.startswith(("http://", "https://")):
            raise SourceError("请输入以 http(s) 开头的音频直链")
        name = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        suffix = os.path.splitext(name)[1].lstrip(".").lower() or "mp3"
        return [RemoteTrack(
            source=self.key, remote_id=url, title=name or "网络音频",
            url=url, category="直链", extra={"format": suffix},
        )]

    def download_url(self, track: RemoteTrack) -> str:
        return track.remote_id or track.url
