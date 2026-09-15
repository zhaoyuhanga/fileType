"""网易云音乐音源（公开 Web 接口，个人学习用途）。"""
from __future__ import annotations

from typing import Iterable, List

from ...models import RemoteTrack
from ..base import KIND_FULL, MusicSource, SourceInfo
from ..http import SourceError

NETEASE_HEADERS = {
    "Referer": "https://music.163.com/",
    "Accept": "application/json, text/plain, */*",
}


class NeteaseSource(MusicSource):
    info = SourceInfo(
        key="netease",
        label="网易云音乐",
        note="公开 Web 接口，可直接下载非 VIP 曲目；版权归平台与权利人所有，仅限个人学习。",
        kind=KIND_FULL,
        homepage="https://music.163.com",
    )
    BASE = "https://music.163.com"

    def default_headers(self) -> dict:
        return dict(NETEASE_HEADERS)

    # ---------- 搜索 ----------

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        if kind == "artist":
            return self._search_artist_tracks(keyword, limit)
        if kind == "genre":
            return self._search_playlist_tracks(keyword, limit)
        search_type = 10 if kind == "album" else 1
        payload = self.http.post(
            f"{self.BASE}/api/search/get/web",
            data={"s": keyword, "type": search_type, "offset": 0, "limit": max(1, min(limit, 50))},
        ).json()
        result = payload.get("result") or {}
        if kind == "album":
            return self._albums_to_tracks(result.get("albums") or [], limit)
        return self._songs_to_tracks(result.get("songs") or [], limit)

    def _songs_to_tracks(self, songs: Iterable[dict], limit: int) -> List[RemoteTrack]:
        tracks: List[RemoteTrack] = []
        for song in songs:
            if len(tracks) >= limit:
                break
            song_id = song.get("id")
            if song_id is None:
                continue
            artists = song.get("artists") or song.get("ar") or []
            album = song.get("album") or song.get("al") or {}
            tracks.append(RemoteTrack(
                source=self.key,
                remote_id=str(song_id),
                title=str(song.get("name") or ""),
                artist="、".join(str(a.get("name") or "") for a in artists if a),
                album=str(album.get("name") or ""),
                duration_ms=int(song.get("duration") or song.get("dt") or 0),
                url=f"{self.BASE}/song/media/outer/url?id={song_id}.mp3",
                cover_url=str(album.get("picUrl") or ""),
                category="网易云",
                extra={"song_id": str(song_id)},
            ))
        return tracks

    def _albums_to_tracks(self, albums: Iterable[dict], limit: int) -> List[RemoteTrack]:
        tracks: List[RemoteTrack] = []
        for album in albums:
            album_id = album.get("id")
            if album_id is None:
                continue
            try:
                detail = self.http.get(f"{self.BASE}/api/album/{album_id}").json()
            except SourceError:
                continue
            for song in (detail.get("songs") or []):
                if len(tracks) >= limit:
                    break
                tracks.append(RemoteTrack(
                    source=self.key,
                    remote_id=str(song.get("id")),
                    title=str(song.get("name") or ""),
                    artist="、".join(str(a.get("name") or "") for a in (song.get("ar") or []) if a),
                    album=str((song.get("al") or {}).get("name") or album.get("name") or ""),
                    duration_ms=int(song.get("dt") or 0),
                    url=f"{self.BASE}/song/media/outer/url?id={song.get('id')}.mp3",
                    cover_url=str((song.get("al") or {}).get("picUrl") or ""),
                    category="网易云",
                    extra={"song_id": str(song.get("id"))},
                ))
            if len(tracks) >= limit:
                break
        return tracks

    def _search_artist_tracks(self, keyword: str, limit: int) -> List[RemoteTrack]:
        payload = self.http.post(
            f"{self.BASE}/api/search/get/web",
            data={"s": keyword, "type": 100, "offset": 0, "limit": 5},
        ).json()
        artists = ((payload.get("result") or {}).get("artists") or [])
        if not artists:
            return []
        top = self.http.get(f"{self.BASE}/api/artist/top/song",
                            params={"id": artists[0].get("id")}).json()
        return self._songs_to_tracks(top.get("songs") or [], limit)

    def _search_playlist_tracks(self, keyword: str, limit: int) -> List[RemoteTrack]:
        payload = self.http.post(
            f"{self.BASE}/api/search/get/web",
            data={"s": keyword, "type": 1000, "offset": 0, "limit": 5},
        ).json()
        playlists = ((payload.get("result") or {}).get("playlists") or [])
        if not playlists:
            return []
        playlist_id = playlists[0].get("id")
        detail = self.http.post(f"{self.BASE}/api/v6/playlist/detail",
                                data={"id": playlist_id}).json()
        playlist = detail.get("playlist") or {}
        tracks = self._songs_to_tracks(playlist.get("tracks") or [], limit)
        for track in tracks:
            track.category = str(playlist.get("name") or keyword)
        return tracks

    # ---------- 下载地址 ----------

    def download_url(self, track: RemoteTrack) -> str:
        """多候选解析：播放地址接口（CDN 直链）优先，其次 outer 直链（https/http）。"""
        song_id = track.remote_id or track.extra.get("song_id") or ""
        candidates: list[tuple[str, str, dict]] = []
        if song_id:
            candidates.append((
                "POST", f"{self.BASE}/api/song/enhance/player/url",
                {"data": {"ids": f"[{song_id}]", "br": "320000", "id": song_id}},
            ))
        outer = f"{self.BASE}/song/media/outer/url?id={song_id or track.remote_id}.mp3"
        candidates.append(("GET", outer, {}))
        candidates.append(("GET", outer.replace("https://", "http://", 1), {}))

        network_errors: list[str] = []
        for method, url, extra in candidates:
            try:
                response = self.http.request(method, url, stream=True, allow_redirects=True, **extra)
            except SourceError as error:
                network_errors.append(str(error))
                continue
            with response:
                resolved = self._resolved_from(response, url, method)
                if resolved:
                    return resolved
        if network_errors:
            raise SourceError(network_errors[0])
        raise SourceError("该曲目在网易云不可下载（可能为 VIP、需付费或已下架）")

    def _resolved_from(self, response, fallback: str, method: str) -> str:
        if method == "POST":
            try:
                payload = response.json()
            except ValueError:
                return ""
            items = payload.get("data") or []
            if isinstance(items, dict):
                items = [items]
            for item in items:
                if isinstance(item, dict) and item.get("url"):
                    return str(item["url"])
            return ""
        # outer 直链：必须真的是音频；不可用曲目会 302 到占位/错误页
        content_type = str(response.headers.get("Content-Type", "")).lower()
        length = int(response.headers.get("Content-Length") or 0)
        if "audio" in content_type and (not length or length >= 64 * 1024):
            return response.url or fallback
        if "audio" not in content_type:
            return ""
        return response.url or fallback

    # ---------- 歌词 ----------

    def lyrics(self, track: RemoteTrack) -> str:
        song_id = track.remote_id or track.extra.get("song_id") or ""
        if not song_id:
            return ""
        try:
            payload = self.http.get(
                f"{self.BASE}/api/song/lyric",
                params={"id": song_id, "lv": -1, "kv": -1, "tv": -1},
            ).json()
        except (SourceError, ValueError):
            return ""
        return str((payload.get("lrc") or {}).get("lyric") or "")
