"""墨读音乐在线音源：搜索与下载地址解析。

设计：统一 `MusicSource` 接口 + 注册表，前端按 key 选择音源。
- netease：网易云公开 Web 接口（搜索 / 歌手热门），个人学习与试听用途；
- itunes：Apple iTunes 公开搜索接口（30 秒试听片段 + 完整元数据，无需密钥）；
- jamendo：Jamendo 自由音乐（完整曲目，需免费 client_id）；
- url：直接粘贴音频直链。

抓取仅用于个人学习、试听与自有内容备份，界面提供合规声明开关。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable, List

import requests

from .models import RemoteTrack

UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}
NETEASE_HEADERS = {
    **UA_HEADERS,
    "Referer": "https://music.163.com/",
}

SEARCH_KINDS = ("song", "artist", "album", "genre")
SEARCH_KIND_LABELS = {
    "song": "歌曲",
    "artist": "歌手",
    "album": "专辑",
    "genre": "类型/风格",
}


class SourceError(RuntimeError):
    """音源不可用或抓取失败。"""


@dataclass(frozen=True)
class SourceInfo:
    key: str
    label: str
    note: str
    needs_key: bool = False


class MusicSource:
    """音源基类。"""

    info: SourceInfo

    @property
    def key(self) -> str:
        return self.info.key

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        raise NotImplementedError

    def download_url(self, track: RemoteTrack) -> str:
        """返回可直接下载的音频地址。"""
        return track.url

    def lyrics(self, track: RemoteTrack) -> str:
        """可选：返回歌词文本（无则空串）。"""
        return ""


def _request_json(url: str, *, params: dict | None = None, headers: dict | None = None,
                   timeout: float = 12.0, data: dict | None = None) -> dict:
    try:
        response = requests.post(url, data=data, params=params, headers=headers or UA_HEADERS,
                                 timeout=timeout) if data else requests.get(
            url, params=params, headers=headers or UA_HEADERS, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as error:
        raise SourceError(f"网络请求失败：{error}") from error
    except ValueError as error:
        raise SourceError("接口返回内容不是有效 JSON") from error


# --------------------------------------------------------------------------- 网易云

class NeteaseSource(MusicSource):
    info = SourceInfo(
        key="netease",
        label="网易云音乐（个人学习）",
        note="公开 Web 接口，可试听/下载非 VIP 曲目；版权归平台与权利人所有，仅限个人学习。",
    )
    BASE = "https://music.163.com"

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        if kind == "artist":
            return self._search_artist_tracks(keyword, limit)
        if kind == "genre":
            return self._search_playlist_tracks(keyword, limit)
        search_type = 10 if kind == "album" else 1
        payload = _request_json(
            f"{self.BASE}/api/search/get/web",
            data={"s": keyword, "type": search_type, "offset": 0, "limit": max(1, min(limit, 50))},
            headers=NETEASE_HEADERS,
        )
        result = payload.get("result") or {}
        if kind == "album":
            return self._albums_to_tracks(result.get("albums") or [], limit)
        return self._songs_to_tracks(result.get("songs") or [], limit)

    def _songs_to_tracks(self, songs: Iterable[dict], limit: int) -> List[RemoteTrack]:
        tracks: List[RemoteTrack] = []
        for song in songs:
            if len(tracks) >= limit:
                break
            artists = song.get("artists") or song.get("ar") or []
            album = song.get("album") or song.get("al") or {}
            song_id = song.get("id")
            if song_id is None:
                continue
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
            ))
        return tracks

    def _albums_to_tracks(self, albums: Iterable[dict], limit: int) -> List[RemoteTrack]:
        tracks: List[RemoteTrack] = []
        for album in albums:
            album_id = album.get("id")
            if album_id is None:
                continue
            try:
                detail = _request_json(
                    f"{self.BASE}/api/album/{album_id}", headers=NETEASE_HEADERS
                )
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
                ))
            if len(tracks) >= limit:
                break
        return tracks

    def _search_artist_tracks(self, keyword: str, limit: int) -> List[RemoteTrack]:
        payload = _request_json(
            f"{self.BASE}/api/search/get/web",
            data={"s": keyword, "type": 100, "offset": 0, "limit": 5},
            headers=NETEASE_HEADERS,
        )
        artists = ((payload.get("result") or {}).get("artists") or [])
        if not artists:
            return []
        artist_id = artists[0].get("id")
        top = _request_json(
            f"{self.BASE}/api/artist/top/song", params={"id": artist_id}, headers=NETEASE_HEADERS
        )
        return self._songs_to_tracks(top.get("songs") or [], limit)

    def _search_playlist_tracks(self, keyword: str, limit: int) -> List[RemoteTrack]:
        payload = _request_json(
            f"{self.BASE}/api/search/get/web",
            data={"s": keyword, "type": 1000, "offset": 0, "limit": 5},
            headers=NETEASE_HEADERS,
        )
        playlists = ((payload.get("result") or {}).get("playlists") or [])
        if not playlists:
            return []
        playlist_id = playlists[0].get("id")
        detail = _request_json(
            f"{self.BASE}/api/v6/playlist/detail", data={"id": playlist_id}, headers=NETEASE_HEADERS
        )
        playlist = detail.get("playlist") or {}
        tracks = self._songs_to_tracks(playlist.get("tracks") or [], limit)
        for track in tracks:
            track.category = str(playlist.get("name") or keyword)
        return tracks

    def download_url(self, track: RemoteTrack) -> str:
        url = track.url or f"{self.BASE}/song/media/outer/url?id={track.remote_id}.mp3"
        # outer 链接会 302 到真实音频；校验是否被平台降级为 404/试听限制
        try:
            response = requests.get(url, headers=NETEASE_HEADERS, timeout=15, stream=True, allow_redirects=True)
            if response.status_code >= 400:
                raise SourceError("该曲目暂不可下载（可能为 VIP 或已下架）")
            response.close()
            return response.url or url
        except requests.RequestException as error:
            raise SourceError(f"解析下载地址失败：{error}") from error

    def lyrics(self, track: RemoteTrack) -> str:
        try:
            payload = _request_json(
                f"{self.BASE}/api/song/lyric",
                params={"id": track.remote_id, "lv": -1, "kv": -1, "tv": -1},
                headers=NETEASE_HEADERS,
            )
        except SourceError:
            return ""
        return str((payload.get("lrc") or {}).get("lyric") or "")


# --------------------------------------------------------------------------- iTunes

class ItunesSource(MusicSource):
    info = SourceInfo(
        key="itunes",
        label="iTunes 试听（公开接口）",
        note="Apple 公开搜索接口，返回 30 秒试听片段与完整元数据，无需密钥，可放心使用。",
    )
    BASE = "https://itunes.apple.com"

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        entity = {"song": "song", "artist": "song", "album": "album", "genre": "song"}.get(kind, "song")
        params = {
            "term": keyword,
            "media": "music",
            "entity": entity,
            "limit": max(1, min(limit, 50)),
        }
        attribute = {"artist": "artistTerm", "genre": "genreTerm"}.get(kind)
        if attribute:
            params["attribute"] = attribute
        # 默认不限定商店：CN 商店对该检索接口常返回空，限定后反而搜不到（可用 MODU_ITUNES_COUNTRY 覆盖）
        country = os.environ.get("MODU_ITUNES_COUNTRY", "").strip()
        if country:
            params["country"] = country

        # Apple 接口偶发空响应：先按设定参数查询，失败则去掉 country 或重试一次
        variants = [dict(params)]
        variants.append({key: value for key, value in params.items() if key != "country"})
        for variant in variants:
            tracks = self._parse(_request_json(f"{self.BASE}/search", params=variant), limit)
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
                extra={"preview": True},
            ))
        return tracks

    def download_url(self, track: RemoteTrack) -> str:
        if not track.url:
            raise SourceError("该结果没有可下载的试听地址")
        return track.url


# --------------------------------------------------------------------------- Jamendo

class JamendoSource(MusicSource):
    info = SourceInfo(
        key="jamendo",
        label="Jamendo 自由音乐（完整曲目）",
        note="CC 授权的独立音乐，可完整下载；需免费 client_id（设置 MODU_JAMENDO_CLIENT_ID 或界面填写）。",
        needs_key=True,
    )
    BASE = "https://api.jamendo.com/v3.0"

    def __init__(self, client_id: str | None = None):
        self._client_id = client_id or os.environ.get("MODU_JAMENDO_CLIENT_ID", "")

    def set_client_id(self, client_id: str) -> None:
        self._client_id = (client_id or "").strip()

    def available(self) -> bool:
        return bool(self._client_id)

    def unavailable_reason(self) -> str:
        return "需要在设置中填写 Jamendo client_id（免费申请）"

    def search(self, keyword: str, kind: str = "song", limit: int = 30) -> List[RemoteTrack]:
        if not self.available():
            raise SourceError(self.unavailable_reason())
        params = {
            "client_id": self._client_id,
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
        payload = _request_json(f"{self.BASE}/tracks", params=params)
        tracks: List[RemoteTrack] = []
        for item in payload.get("results") or []:
            audio = item.get("audio") or item.get("audiodownload") or ""
            if not audio:
                continue
            tracks.append(RemoteTrack(
                source=self.key,
                remote_id=str(item.get("id") or ""),
                title=str(item.get("name") or ""),
                artist=str(item.get("artist_name") or ""),
                album=str(item.get("album_name") or ""),
                duration_ms=int(item.get("duration") or 0) * 1000,
                url=str(audio),
                cover_url=str(item.get("image") or ""),
                category=str((item.get("musicinfo") or {}).get("tags", {}).get("genres", [""])[0]
                             if (item.get("musicinfo") or {}).get("tags") else ""),
            ))
        return tracks

    def lyrics(self, track: RemoteTrack) -> str:
        return str(track.extra.get("lyrics") or "")


# --------------------------------------------------------------------------- 直链

class DirectUrlSource(MusicSource):
    info = SourceInfo(
        key="url",
        label="音频直链",
        note="粘贴 http(s) 音频直链（mp3/m4a/flac 等）直接下载。",
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


# --------------------------------------------------------------------------- 注册表

_SOURCES: dict[str, MusicSource] = {
    "netease": NeteaseSource(),
    "itunes": ItunesSource(),
    "jamendo": JamendoSource(),
    "url": DirectUrlSource(),
}


def get_source(key: str) -> MusicSource:
    source = _SOURCES.get(key)
    if source is None:
        raise SourceError(f"未知音源：{key}")
    return source


def list_sources() -> List[SourceInfo]:
    return [source.info for source in _SOURCES.values()]


def search_all(keyword: str, kind: str = "song", limit: int = 20,
               sources: Iterable[str] | None = None) -> tuple[List[RemoteTrack], List[str]]:
    """跨音源搜索：返回 (结果, 错误信息列表)。"""
    results: List[RemoteTrack] = []
    errors: List[str] = []
    for key in (sources or list(_SOURCES.keys())):
        source = _SOURCES.get(key)
        if source is None:
            continue
        try:
            results.extend(source.search(keyword, kind=kind, limit=limit))
        except SourceError as error:
            errors.append(f"{source.info.label}：{error}")
        except Exception as error:  # noqa: BLE001
            errors.append(f"{source.info.label}：{error}")
    return results, errors


def clean_lyrics(text: str) -> str:
    """去掉 LRC 时间标签，得到纯文本歌词。"""
    return re.sub(r"\[\d{1,2}:\d{1,2}(?:[.:]\d{1,3})?\]", "", text or "").strip()
