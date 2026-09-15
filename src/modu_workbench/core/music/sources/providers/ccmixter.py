"""ccMixter 音源（CC 授权音乐社区，公开 API，免费无密钥，完整曲目）。"""
from __future__ import annotations

from typing import List

from ...models import RemoteTrack
from ..base import KIND_CC, MusicSource, SourceInfo
from ..http import SourceError


class CcmixterSource(MusicSource):
    info = SourceInfo(
        key="ccmixter",
        label="ccMixter（CC 音乐）",
        note="CC 授权音乐社区公开 API，无需密钥即可搜索与下载完整曲目（混音/器乐/伴奏居多）。",
        kind=KIND_CC,
        homepage="http://ccmixter.org",
    )
    QUERY_URL = "http://ccmixter.org/api/query"

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        params = {
            "f": "json",
            "limit": max(1, min(limit, 50)),
            "lic": "open",
            "tags": keyword,
        }
        payload = self.http.get_json(self.QUERY_URL, params=params)
        records = payload if isinstance(payload, list) else (payload.get("records") or [])
        tracks: List[RemoteTrack] = []
        for record in records:
            if len(tracks) >= limit:
                break
            url = self._pick_file(record)
            if not url:
                continue
            tracks.append(RemoteTrack(
                source=self.key,
                remote_id=str(record.get("upload_id") or record.get("id") or url),
                title=str(record.get("upload_name") or record.get("name") or ""),
                artist=str(record.get("user_name") or record.get("artist") or ""),
                album=str(record.get("upload_extra", {}).get("album") if isinstance(record.get("upload_extra"), dict) else ""),
                duration_ms=int(float(record.get("files")[0].get("file_rawsize") or 0) if isinstance(record.get("files"), list) and record.get("files") else 0),
                url=url,
                category="ccMixter",
                extra={"license": str(record.get("license_name") or "")},
            ))
        return tracks

    @staticmethod
    def _pick_file(record: dict) -> str:
        files = record.get("files") or []
        if not isinstance(files, list):
            return ""
        for item in files:
            if not isinstance(item, dict):
                continue
            url = str(item.get("download_url") or item.get("file_url") or "")
            if url.lower().endswith(".mp3"):
                return url
        for item in files:
            if isinstance(item, dict) and item.get("download_url"):
                return str(item["download_url"])
        return ""

    def download_url(self, track: RemoteTrack) -> str:
        if not track.url:
            raise SourceError("ccMixter 未提供下载地址")
        return track.url

    def lyrics(self, track: RemoteTrack) -> str:
        return ""
