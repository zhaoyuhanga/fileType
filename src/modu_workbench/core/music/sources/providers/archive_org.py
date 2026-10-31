"""Internet Archive 音源（公共领域 / CC 授权的音频，免费无密钥，完整曲目）。"""
from __future__ import annotations

from typing import List

from ...models import RemoteTrack
from ..base import KIND_CC, MusicSource, SourceInfo
from ..http import SourceError

_AUDIO_FORMATS = ("vbr mp3", "mp3", "ogg vorbis", "flac", "64kbps mp3", "128kbps mp3")


class ArchiveOrgSource(MusicSource):
    info = SourceInfo(
        key="archive",
        label="Internet Archive",
        note="公共领域 / CC 授权音频库，无需密钥即可搜索与下载完整曲目（多为现场录音、老唱片、自由音乐）。",
        kind=KIND_CC,
        homepage="https://archive.org",
    )
    SEARCH_URL = "https://archive.org/advancedsearch.php"
    META_URL = "https://archive.org/metadata"
    DOWNLOAD_URL = "https://archive.org/download"

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        query = f'({keyword}) AND mediatype:(audio)'
        payload = self.http.get_json(self.SEARCH_URL, params={
            "q": query,
            "fl[]": ["identifier", "title", "creator", "year"],
            "rows": max(1, min(limit, 30)),
            "page": 1,
            "output": "json",
        })
        docs = ((payload.get("response") or {}).get("docs")) or []
        tracks: List[RemoteTrack] = []
        for doc in docs:
            identifier = doc.get("identifier")
            if not identifier:
                continue
            track = RemoteTrack(
                source=self.key,
                remote_id=str(identifier),
                title=str(doc.get("title") or identifier),
                artist=str(doc.get("creator") or "Internet Archive"),
                album=str(doc.get("year") or ""),
                url=f"{self.DOWNLOAD_URL}/{identifier}",
                category="Internet Archive",
                extra={"identifier": str(identifier)},
            )
            tracks.append(track)
        return tracks

    def _audio_file(self, identifier: str) -> str:
        """在条目文件列表里挑一个可下载的音频文件（优先 mp3）。"""
        payload = self.http.get_json(f"{self.META_URL}/{identifier}")
        files = payload.get("files") or []
        best_name = ""
        best_rank = len(_AUDIO_FORMATS) + 1
        for item in files:
            name = str(item.get("name") or "")
            if not name.lower().endswith((".mp3", ".ogg", ".flac", ".m4a", ".wav")):
                continue
            fmt = str(item.get("format") or "").lower()
            rank = next((index for index, want in enumerate(_AUDIO_FORMATS) if want in fmt),
                        len(_AUDIO_FORMATS))
            if rank < best_rank:
                best_name, best_rank = name, rank
        if not best_name:
            raise SourceError("该条目没有可下载的音频文件")
        return best_name

    def download_url(self, track: RemoteTrack) -> str:
        identifier = track.remote_id or track.extra.get("identifier") or ""
        if not identifier:
            raise SourceError("缺少 Internet Archive 条目 ID")
        name = self._audio_file(identifier)
        from urllib.parse import quote

        return f"{self.DOWNLOAD_URL}/{identifier}/{quote(name)}"

    def lyrics(self, track: RemoteTrack) -> str:
        return ""
