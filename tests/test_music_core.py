"""墨软乐库核心测试：存储 / 歌单 / 音源解析 / 下载 / 播放队列 / 转换入口。"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
import requests

from modu_workbench.core.music import (
    MusicLibrary,
    MusicPlayer,
    MusicStorage,
    RemoteTrack,
    Track,
    format_duration,
    parse_track_name,
    safe_filename,
    scan_audio_files,
)
from modu_workbench.core.music.downloader import download_track, guess_extension, unique_path
from modu_workbench.core.music.sources import (
    ArchiveOrgSource,
    AudiusSource,
    CcmixterSource,
    DirectUrlSource,
    ItunesSource,
    KuwoSource,
    MusicRegistry,
    NeteaseSource,
    SourceError,
    best_match,
    describe_network_error,
    match_score,
    search_all,
)


@pytest.fixture()
def storage(tmp_path: Path) -> MusicStorage:
    return MusicStorage(str(tmp_path / "music.db"))


@pytest.fixture()
def library(storage: MusicStorage, tmp_path: Path) -> MusicLibrary:
    return MusicLibrary(storage, tmp_path / "library")


def make_audio(path: Path, size: int = 256) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    return path


# --------------------------------------------------------------- 模型 / 工具

def test_parse_track_name_variants() -> None:
    assert parse_track_name("周杰伦 - 晴天") == ("周杰伦", "晴天")
    assert parse_track_name("Adele-Hello") == ("Adele", "Hello")
    assert parse_track_name("纯曲名") == ("", "纯曲名")


def test_safe_filename_and_duration() -> None:
    assert safe_filename('a/b:c*d?"e') == "a_b_c_d_e"
    assert safe_filename("   ") == "track"
    assert len(safe_filename("x" * 300)) <= 120
    assert format_duration(0) == "--:--"
    assert format_duration(65_000) == "01:05"
    assert format_duration(3_600_000) == "60:00"


def test_scan_audio_files(tmp_path: Path) -> None:
    make_audio(tmp_path / "a.mp3")
    make_audio(tmp_path / "sub" / "b.flac")
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    found = scan_audio_files([str(tmp_path)])
    assert [Path(p).name for p in found] == ["a.mp3", "b.flac"]
    # 去重
    assert scan_audio_files([str(tmp_path / "a.mp3"), str(tmp_path / "a.mp3")]) == [str(tmp_path / "a.mp3")]


# --------------------------------------------------------------- 存储 / 歌单

def test_track_upsert_and_filters(storage: MusicStorage, tmp_path: Path) -> None:
    first = Track(path=str(tmp_path / "a.mp3"), title="晴天", artist="周杰伦", format="mp3")
    track_id = storage.upsert_track(first)
    assert track_id > 0
    # 相同路径再次写入为更新
    first.title = "晴天（重制）"
    assert storage.upsert_track(first) == track_id

    storage.upsert_track(Track(path=str(tmp_path / "b.flac"), title="Hello", artist="Adele", format="flac"))
    assert len(storage.list_tracks()) == 2
    assert [t.title for t in storage.list_tracks(keyword="晴")] == ["晴天（重制）"]
    assert [t.artist for t in storage.list_tracks(artist="Adele")] == ["Adele"]
    assert storage.list_artists()[0][0] in {"Adele", "周杰伦"}

    storage.set_category([track_id], "流行")
    assert [t.id for t in storage.list_tracks(category="流行")] == [track_id]
    assert storage.set_favorite([track_id], True) == 1
    assert [t.id for t in storage.list_tracks(favorite_only=True)] == [track_id]

    storage.mark_played(track_id)
    assert storage.get_track(track_id).play_count == 1

    storage.delete_tracks([track_id])
    assert storage.get_track(track_id) is None


def test_favorite_playlist_sync(storage: MusicStorage, tmp_path: Path) -> None:
    track_id = storage.upsert_track(Track(path=str(tmp_path / "a.mp3"), title="A", format="mp3"))
    favorite_id = storage.favorite_playlist_id
    assert [p.id for p in storage.list_playlists()][0] == favorite_id

    assert storage.toggle_favorite(track_id) is True
    assert [t.id for t in storage.list_playlist_tracks(favorite_id)] == [track_id]

    # 从收藏歌单移除应同时取消收藏标记
    storage.remove_from_playlist(favorite_id, [track_id])
    assert storage.get_track(track_id).favorited is False
    assert storage.list_playlist_tracks(favorite_id) == []


def test_playlist_crud_and_remote_pending(storage: MusicStorage, tmp_path: Path) -> None:
    playlist_id = storage.create_playlist("通勤")
    assert storage.create_playlist("通勤") == playlist_id  # 同名复用
    assert storage.rename_playlist(playlist_id, "通勤精选") is True
    assert storage.get_playlist(playlist_id).name == "通勤精选"
    assert storage.rename_playlist(storage.favorite_playlist_id, "x") is False

    ids = [storage.upsert_track(Track(path=str(tmp_path / f"{i}.mp3"), title=str(i), format="mp3"))
           for i in range(3)]
    assert storage.add_to_playlist(playlist_id, ids) == 3
    assert storage.add_to_playlist(playlist_id, ids) == 0  # 去重
    assert [t.id for t in storage.list_playlist_tracks(playlist_id)] == ids
    assert storage.get_playlist(playlist_id).track_count == 3

    remotes = [
        RemoteTrack(source="itunes", remote_id="1", title="待下载A", artist="X", url="http://x/1.m4a"),
        RemoteTrack(source="itunes", remote_id="2", title="待下载B", artist="Y", url="http://x/2.m4a"),
    ]
    assert storage.add_remotes_to_playlist(playlist_id, remotes) == 2
    assert storage.add_remotes_to_playlist(playlist_id, remotes) == 0
    stored = storage.list_playlist_remotes(playlist_id)
    assert [r.title for r in stored] == ["待下载A", "待下载B"]
    assert storage.get_playlist(playlist_id).pending_count == 2

    storage.remove_playlist_remotes(playlist_id, [stored[0].extra["remote_key"]])
    assert len(storage.list_playlist_remotes(playlist_id)) == 1

    assert storage.delete_playlist(playlist_id) is True
    assert storage.get_playlist(playlist_id) is None
    assert storage.delete_playlist(storage.favorite_playlist_id) is False


def test_history_and_settings(storage: MusicStorage, tmp_path: Path) -> None:
    track_id = storage.upsert_track(Track(path=str(tmp_path / "a.mp3"), title="A", artist="B", format="mp3"))
    storage.add_history(track_id, "play")
    storage.add_history(track_id, "download")
    entries = storage.list_history()
    assert [e.action for e in entries] == ["download", "play"]
    assert entries[0].title == "A"
    storage.clear_history()
    assert storage.list_history() == []

    storage.set_setting("download_dir", "/tmp/music")
    assert storage.get_setting("download_dir") == "/tmp/music"
    assert storage.get_setting("missing", "默认") == "默认"


# --------------------------------------------------------------- 本地曲库

def test_library_import_and_remote_import(library: MusicLibrary, tmp_path: Path) -> None:
    make_audio(tmp_path / "周杰伦 - 晴天.mp3")
    make_audio(tmp_path / "sub" / "Adele - Hello.flac")
    tracks = library.import_paths([str(tmp_path)])
    assert len(tracks) == 2
    by_title = {t.title: t for t in tracks}
    assert by_title["晴天"].artist == "周杰伦"
    assert by_title["Hello"].artist == "Adele"
    assert all(t.id > 0 for t in tracks)

    downloaded = make_audio(library.music_dir / "新歌.mp3")
    remote = RemoteTrack(source="itunes", remote_id="42", title="新歌", artist="歌手",
                         album="专辑", duration_ms=180_000, url="http://x/1.m4a")
    track = library.import_remote(remote, downloaded)
    assert track.artist == "歌手"
    assert track.source == "itunes"
    assert track.remote_id == "42"
    assert track.duration_ms == 180_000
    assert [e.action for e in library.storage.list_history()] == ["download"]

    assert library.resolve_playable(tracks) == tracks
    library.record_play(tracks[0].id)
    assert library.storage.get_track(tracks[0].id).play_count == 1


def test_library_convert_guards(library: MusicLibrary, tmp_path: Path) -> None:
    source = make_audio(tmp_path / "song.mp3")
    track = library.import_paths([str(source)])[0]
    with pytest.raises(ValueError):
        library.convert(track.id, "ogg") if track.format == "ogg" else library.convert(track.id, "mp3")
    with pytest.raises(ValueError):
        library.convert(track.id, "xyz")
    with pytest.raises(ValueError):
        library.convert(9999, "mp3")


def test_library_convert_with_ffmpeg(library: MusicLibrary, tmp_path: Path) -> None:
    from modu_workbench.core.platform.media import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        pytest.skip("未安装 ffmpeg")
    import subprocess

    wav = tmp_path / "tone.wav"
    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2", str(wav)],
                   capture_output=True, check=True)
    track = library.import_paths([str(wav)])[0]
    out_dir = tmp_path / "out"
    result = library.convert(track.id, "mp3", out_dir)
    assert result.exists() and result.stat().st_size > 0
    # 转换产物自动入库
    assert any(t.path == str(result) for t in library.storage.list_tracks())


# --------------------------------------------------------------- 在线音源（打桩）

ITUNES_PAYLOAD = {
    "results": [
        {"trackId": 1, "trackName": "晴天", "artistName": "周杰伦", "collectionName": "叶惠美",
         "trackTimeMillis": 269000, "previewUrl": "https://x/1.m4a", "artworkUrl100": "https://x/1.jpg",
         "primaryGenreName": "国语流行"},
        {"trackId": 2, "trackName": "无试听", "artistName": "X", "previewUrl": ""},
    ]
}


# --------------------------------------------------------------- 测试替身

class FakeResponse:
    def __init__(self, *, payload=None, text: str = "", url: str = "https://x/",
                 headers: dict | None = None):
        self._payload = payload
        self.text = text
        self.url = url
        self.headers = headers or {"Content-Type": "application/json"}
        self.status_code = 200
        if payload is not None and not text:
            import json as _json
            self.text = _json.dumps(payload, ensure_ascii=False)

    def json(self):  # noqa: ANN201
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *args) -> None:  # noqa: ANN002
        return None


class FakeHttp:
    """替身 HttpClient：按 (method, url 片段) 返回预设响应，并记录调用。"""

    def __init__(self, routes: dict, default: FakeResponse | None = None):
        self.routes = routes
        self.default = default
        self.calls: list[tuple[str, str, dict]] = []

    def _route(self, method: str, url: str, kwargs: dict) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        for (want_method, fragment), response in self.routes.items():
            if want_method in (method, "*") and fragment in url:
                return response
        if self.default is not None:
            return self.default
        raise SourceError(f"未预设的请求：{method} {url}")

    def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        return self._route(method.upper(), url, kwargs)

    def get(self, url: str, **kwargs) -> FakeResponse:
        return self._route("GET", url, kwargs)

    def post(self, url: str, **kwargs) -> FakeResponse:
        return self._route("POST", url, kwargs)

    def get_json(self, url: str, **kwargs):  # noqa: ANN201
        return self.get(url, **kwargs).json()

    def get_text(self, url: str, **kwargs) -> str:
        return self.get(url, **kwargs).text


# --------------------------------------------------------------- iTunes

def test_itunes_search() -> None:
    http = FakeHttp({("GET", "/search"): FakeResponse(payload=ITUNES_PAYLOAD)})
    tracks = ItunesSource(http=http).search("周杰伦", kind="artist", limit=10)
    assert [t.title for t in tracks] == ["晴天"]      # 无 previewUrl 的结果被跳过
    assert tracks[0].artist == "周杰伦"
    assert tracks[0].category == "国语流行"
    params = http.calls[0][2].get("params") or {}
    assert params["attribute"] == "artistTerm"
    assert "country" not in params                     # 默认不限定商店


def test_itunes_search_falls_back_without_country(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODU_ITUNES_COUNTRY", "CN")
    http = FakeHttp({})
    responses = [FakeResponse(payload={"results": []}), FakeResponse(payload=ITUNES_PAYLOAD)]

    # 第一次带 country 返回空 → 去掉 country 再查一次
    def route(url, **kwargs):  # noqa: ANN001
        http.calls.append(("GET", url, kwargs))
        return responses[min(len(http.calls) - 1, 1)]

    http.get = route  # type: ignore[assignment]
    tracks = ItunesSource(http=http).search("周杰伦", kind="song", limit=5)
    assert [t.title for t in tracks] == ["晴天"]
    assert http.calls[0][2]["params"].get("country") == "CN"
    assert "country" not in http.calls[1][2]["params"]


# --------------------------------------------------------------- 网易云

NETEASE_SEARCH = {"result": {"songs": [
    {"id": 11, "name": "稻香", "artists": [{"name": "周杰伦"}],
     "album": {"name": "魔杰座", "picUrl": "https://x/p.jpg"}, "duration": 223000},
]}}


def test_netease_search_and_download_url() -> None:
    http = FakeHttp({
        ("POST", "/api/search/get/web"): FakeResponse(payload=NETEASE_SEARCH),
        ("POST", "enhance/player/url"): FakeResponse(payload={"data": [{"url": "https://m7.music.126.net/real.mp3"}]}),
    })
    source = NeteaseSource(http=http)
    tracks = source.search("稻香", kind="song", limit=5)
    assert len(tracks) == 1
    assert tracks[0].remote_id == "11"
    assert "outer/url?id=11" in tracks[0].url
    assert source.download_url(tracks[0]).endswith("real.mp3")


def test_netease_download_url_falls_back_to_outer() -> None:
    """播放地址接口拿不到 URL 时，回退到 outer 直链跳转结果。"""
    http = FakeHttp({
        ("POST", "enhance/player/url"): FakeResponse(payload={"data": [{"url": None}]}),
        ("GET", "outer/url"): FakeResponse(
            payload=None, url="http://m701.music.126.net/2026/xyz/jdymusic/obj/w5.mp3",
            headers={"Content-Type": "audio/mpeg", "Content-Length": str(3 * 1024 * 1024)},
        ),
    })
    remote = RemoteTrack(source="netease", remote_id="11", title="x")
    assert NeteaseSource(http=http).download_url(remote).endswith(".mp3")
    assert [call[0] for call in http.calls] == ["POST", "GET"]


def test_netease_download_blocked() -> None:
    http = FakeHttp({
        ("POST", "enhance/player/url"): FakeResponse(payload={"data": [{"url": None}]}),
        ("GET", "outer/url"): FakeResponse(payload=None, text="", url="https://music.163.com/404",
                                           headers={"Content-Type": "text/html"}),
    })
    with pytest.raises(SourceError):
        NeteaseSource(http=http).download_url(RemoteTrack(source="netease", remote_id="1", title="x"))


# --------------------------------------------------------------- 酷我

KUWO_SEARCH_TEXT = (
    "{'abslist':[{'MUSICRID':'MUSIC_474678847','NAME':'花海','ARTIST':'周杰伦','ALBUM':'魔杰座',"
    "'DURATION':'210','web_albumpic_short':'120/86/x.jpg'}]}"
)


def test_kuwo_search_and_download_url() -> None:
    http = FakeHttp({
        ("GET", "search.kuwo.cn"): FakeResponse(payload=None, text=KUWO_SEARCH_TEXT),
        ("GET", "antiserver.kuwo.cn"): FakeResponse(payload=None, text="https://kw-bj.kuwo.cn/a/b/song.mp3"),
    })
    source = KuwoSource(http=http)
    tracks = source.search("周杰伦 花海", limit=5)
    assert len(tracks) == 1
    assert tracks[0].remote_id == "MUSIC_474678847"
    assert tracks[0].title == "花海"
    assert tracks[0].duration_ms == 210_000
    assert tracks[0].cover_url.endswith("120/86/x.jpg")
    assert source.download_url(tracks[0]).endswith("song.mp3")


def test_kuwo_rejects_empty_resolution() -> None:
    http = FakeHttp({
        ("GET", "antiserver.kuwo.cn"): FakeResponse(payload=None, text=""),
    })
    with pytest.raises(SourceError):
        KuwoSource(http=http).download_url(RemoteTrack(source="kuwo", remote_id="MUSIC_1", title="x"))


# --------------------------------------------------------------- Audius / Archive / ccMixter

def test_audius_search_and_stream() -> None:
    http = FakeHttp({
        ("GET", "api.audius.co"): FakeResponse(payload={"data": ["https://node.audius.co"]}),
        ("GET", "/v1/tracks/search"): FakeResponse(payload={"data": [
            {"id": "abc", "title": "Piano Fala", "duration": 180, "genre": "Electronic",
             "user": {"name": "wn6"}, "artwork": {"480x480": "https://x/cover.jpg"}},
        ]}),
    })
    source = AudiusSource(http=http)
    tracks = source.search("piano", limit=5)
    assert tracks[0].remote_id == "abc"
    assert tracks[0].artist == "wn6"
    assert tracks[0].duration_ms == 180_000
    assert source.download_url(tracks[0]).startswith("https://node.audius.co/v1/tracks/abc/stream")


def test_archive_search_and_download() -> None:
    http = FakeHttp({
        ("GET", "advancedsearch"): FakeResponse(payload={"response": {"docs": [
            {"identifier": "concert-2026", "title": "Live 2026", "creator": "Someband"},
        ]}}),
        ("GET", "/metadata/concert-2026"): FakeResponse(payload={"files": [
            {"name": "cover.jpg", "format": "JPEG"},
            {"name": "track01.ogg", "format": "Ogg Vorbis"},
            {"name": "track01_vbr.mp3", "format": "VBR MP3"},
        ]}),
    })
    source = ArchiveOrgSource(http=http)
    tracks = source.search("live", limit=3)
    assert tracks[0].remote_id == "concert-2026"
    assert tracks[0].artist == "Someband"
    assert source.download_url(tracks[0]).endswith("track01_vbr.mp3")


def test_ccmixter_search_and_download() -> None:
    http = FakeHttp({
        ("GET", "ccmixter.org/api/query"): FakeResponse(payload=[{
            "upload_id": "42", "upload_name": "Lost Roamin'", "user_name": "speck",
            "license_name": "cc-by",
            "files": [{"download_url": "https://ccmixter.org/content/speck/x.mp3", "file_rawsize": "100"}],
        }]),
    })
    source = CcmixterSource(http=http)
    tracks = source.search("piano", limit=3)
    assert tracks[0].title == "Lost Roamin'"
    assert tracks[0].artist == "speck"
    assert source.download_url(tracks[0]).endswith("x.mp3")


def test_describe_network_error_is_actionable() -> None:
    dns = describe_network_error(
        Exception("HTTPSConnectionPool(host='music.163.com', port=443): Max retries exceeded "
                  "(Caused by NameResolutionError(getaddrinfo failed))"),
        "music.163.com",
    )
    assert "无法解析" in dns and "music.163.com" in dns and "其他音源" in dns

    timeout = describe_network_error(Exception("HTTPSConnectionPool: Read timed out"), "x.com")
    assert "超时" in timeout


def test_direct_url_source() -> None:
    tracks = DirectUrlSource().search("https://cdn.example.com/a/b/song.flac")
    assert tracks[0].title == "song.flac"
    assert tracks[0].extra["format"] == "flac"
    assert DirectUrlSource().download_url(tracks[0]) == "https://cdn.example.com/a/b/song.flac"
    with pytest.raises(SourceError):
        DirectUrlSource().search("not-a-url")


# --------------------------------------------------------------- 注册表：多源 / 重试 / 熔断 / 换源

def build_registry(**routes) -> MusicRegistry:  # noqa: ANN003
    netease = NeteaseSource(http=FakeHttp(routes.get("netease", {})))
    kuwo = KuwoSource(http=FakeHttp(routes.get("kuwo", {})))
    itunes = ItunesSource(http=FakeHttp({("GET", "/search"): FakeResponse(payload=ITUNES_PAYLOAD)}))
    registry = MusicRegistry([netease, kuwo, itunes])
    return registry


def test_registry_search_all_collects_errors() -> None:
    registry = MusicRegistry([ItunesSource(http=FakeHttp({})), DirectUrlSource()])
    tracks, errors = registry.search_all("x", sources=["itunes", "url"])
    assert tracks == []
    assert errors and "iTunes" in errors[0]


def test_registry_health_degrades_after_repeated_failures() -> None:
    registry = MusicRegistry([ItunesSource(http=FakeHttp({}))])
    for _ in range(3):
        registry.search_all("x", sources=["itunes"])
    assert "itunes" in registry.degraded_keys()
    assert registry.status_text("itunes") == "临时降级"
    # 降级源在自动模式下被跳过
    tracks, errors = registry.search_all("x")
    assert tracks == [] and errors == []
    # 显式指定仍会尝试（用户主动选择）
    _, errors = registry.search_all("x", sources=["itunes"])
    assert errors


def test_registry_cross_source_fallback() -> None:
    """网易云解析失败 → 自动改用酷我同一首歌。"""
    netease_http = FakeHttp({
        ("POST", "enhance/player/url"): FakeResponse(payload={"data": [{"url": None}]}),
        ("GET", "outer/url"): FakeResponse(payload=None, text="", url="https://music.163.com/404",
                                           headers={"Content-Type": "text/html"}),
    })
    kuwo_http = FakeHttp({
        ("GET", "search.kuwo.cn"): FakeResponse(payload=None, text=KUWO_SEARCH_TEXT),
        ("GET", "antiserver.kuwo.cn"): FakeResponse(payload=None, text="https://kw-bj.kuwo.cn/song.mp3"),
    })
    registry = MusicRegistry([NeteaseSource(http=netease_http), KuwoSource(http=kuwo_http)])
    original = RemoteTrack(source="netease", remote_id="1", title="花海", artist="周杰伦", duration_ms=210_000)

    resolved = registry.resolve(original)
    assert resolved.source == "kuwo"
    assert resolved.switched_from == "netease"
    assert "酷我" in resolved.note
    assert resolved.url.endswith("song.mp3")


def test_registry_cross_source_no_match_reports_reasons() -> None:
    netease_http = FakeHttp({
        ("POST", "enhance/player/url"): FakeResponse(payload={"data": [{"url": None}]}),
        ("GET", "outer/url"): FakeResponse(payload=None, text="", url="https://music.163.com/404",
                                           headers={"Content-Type": "text/html"}),
    })
    kuwo_http = FakeHttp({
        ("GET", "search.kuwo.cn"): FakeResponse(payload=None, text=KUWO_SEARCH_TEXT),
    })
    registry = MusicRegistry([NeteaseSource(http=netease_http), KuwoSource(http=kuwo_http)])
    other = RemoteTrack(source="netease", remote_id="2", title="完全不同的歌", artist="某人")
    with pytest.raises(SourceError) as exc:
        registry.resolve(other)
    assert "netease" in str(exc.value) or "酷我" in str(exc.value)


def test_registry_exclude_prevents_looping_back() -> None:
    """下载失败后换源时，must 不再回到刚失败的音源。"""
    netease_http = FakeHttp({
        ("POST", "enhance/player/url"): FakeResponse(payload={"data": [{"url": "https://m7.music.126.net/a.mp3"}]}),
    })
    registry = MusicRegistry([NeteaseSource(http=netease_http)])
    track = RemoteTrack(source="netease", remote_id="1", title="x")
    with pytest.raises(SourceError):
        registry.resolve(track, exclude=["netease"])


def test_registry_settings_roundtrip(storage: MusicStorage) -> None:
    registry = MusicRegistry([NeteaseSource(http=FakeHttp({})), KuwoSource(http=FakeHttp({}))])
    registry.set_enabled("netease", False)
    registry.set_order(["kuwo", "netease"])
    registry.save_settings(storage)

    restored = MusicRegistry([NeteaseSource(http=FakeHttp({})), KuwoSource(http=FakeHttp({}))])
    restored.load_settings(storage)
    assert restored.is_enabled("netease") is False
    assert restored.order[:2] == ["kuwo", "netease"]


def test_matcher_scores_and_best_match() -> None:
    base = RemoteTrack(source="netease", remote_id="1", title="屋顶", artist="周杰伦", duration_ms=319_000)
    same = RemoteTrack(source="kuwo", remote_id="2", title="屋顶 (Live)", artist="周杰伦、温岚", duration_ms=317_000)
    other = RemoteTrack(source="kuwo", remote_id="3", title="稻香", artist="周杰伦", duration_ms=223_000)
    assert match_score(base, same) > match_score(base, other)
    assert best_match(base, [other, same]) is same
    assert best_match(base, [other], threshold=0.9) is None
    assert best_match(base, []) is None


# --------------------------------------------------------------- 下载

class _FakeStream:
    def __init__(self, chunks: list[bytes], url: str = "https://x/audio.mp3",
                 content_type: str = "audio/mpeg", total: int | None = None):
        self._chunks = chunks
        self.url = url
        self.headers = {"Content-Type": content_type,
                        "Content-Length": str(total if total is not None else sum(len(c) for c in chunks))}

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size: int = 0):  # noqa: ANN001, ARG002
        yield from self._chunks

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *args) -> None:  # noqa: ANN002
        return None


def test_guess_extension_and_unique_path(tmp_path: Path) -> None:
    assert guess_extension("http://x/a", {"Content-Type": "audio/flac"}) == ".flac"
    assert guess_extension("http://x/a.ogg?v=1") == ".ogg"
    assert guess_extension("http://x/a") == ".mp3"

    target = unique_path(tmp_path, "song.mp3")
    assert target.name == "song.mp3"
    target.write_bytes(b"1")
    assert unique_path(tmp_path, "song.mp3").name == "song (2).mp3"


def test_download_track_writes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    audio = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"a" * 20000
    payload = [audio[:10000], audio[10000:]]
    monkeypatch.setattr(
        "modu_workbench.core.music.downloader.requests.get",
        lambda *a, **k: _FakeStream(payload, url="https://cdn/song.mp3"),
    )
    progress: list[tuple[int, int, str]] = []
    remote = RemoteTrack(source="url", remote_id="https://cdn/song.mp3", title="测试曲",
                         artist="歌手", url="https://cdn/song.mp3")
    path, resolved = download_track(remote, tmp_path, on_progress=lambda w, t, m: progress.append((w, t, m)),
                                    save_cover=False, save_lyrics=False)
    assert path.name == "歌手 - 测试曲.mp3"
    assert path.read_bytes() == audio
    assert resolved.source == "url"
    assert progress[-1][0] == len(audio)


def test_looks_like_audio_rejects_fake_payloads(tmp_path: Path) -> None:
    from modu_workbench.core.music.downloader import looks_like_audio

    real = tmp_path / "real.mp3"
    real.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"a" * 20000)
    assert looks_like_audio(real, "audio/mpeg")

    tiny = tmp_path / "tiny.mp3"
    tiny.write_bytes(b"ID3" + b"a" * 100)
    assert not looks_like_audio(tiny, "audio/mpeg")

    page = tmp_path / "page.mp3"
    page.write_bytes(b"<!doctype html><html>VIP only</html>" + b" " * 20000)
    assert not looks_like_audio(page, "text/html")


def test_download_track_rejects_html_error_page(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """平台返回错误页时不能当成“下载完成”。"""
    page = b"<html><body>VIP only</body></html>" + b" " * 20000
    monkeypatch.setattr(
        "modu_workbench.core.music.downloader.requests.get",
        lambda *a, **k: _FakeStream([page], url="https://cdn/song.mp3", content_type="text/html"),
    )
    remote = RemoteTrack(source="url", remote_id="https://cdn/song.mp3", title="t", url="https://cdn/song.mp3")
    with pytest.raises(SourceError) as exc:
        download_track(remote, tmp_path, save_cover=False, save_lyrics=False)
    assert "不是有效音频" in str(exc.value)
    assert not list(tmp_path.glob("*.mp3"))


def test_download_track_retries_transient_network_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """网络抖动（DNS/连接重置）应自动重试，而不是直接失败。"""
    audio = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"a" * 20000
    attempts = {"count": 0}

    def flaky_get(*args, **kwargs):  # noqa: ANN002, ANN003
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise requests.ConnectionError("getaddrinfo failed")
        return _FakeStream([audio], url="https://cdn/song.mp3")

    monkeypatch.setattr("modu_workbench.core.music.downloader.requests.get", flaky_get)
    monkeypatch.setattr("modu_workbench.core.music.downloader.time.sleep", lambda _s: None)
    remote = RemoteTrack(source="url", remote_id="https://cdn/song.mp3", title="重试曲", url="https://cdn/song.mp3")
    path, _resolved = download_track(remote, tmp_path, save_cover=False, save_lyrics=False)
    assert attempts["count"] == 3
    assert path.read_bytes() == audio


def test_download_track_reports_dns_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def always_fail(*args, **kwargs):  # noqa: ANN002, ANN003
        raise requests.ConnectionError("Max retries exceeded with NameResolutionError(getaddrinfo failed)")

    monkeypatch.setattr("modu_workbench.core.music.downloader.requests.get", always_fail)
    monkeypatch.setattr("modu_workbench.core.music.downloader.time.sleep", lambda _s: None)
    remote = RemoteTrack(source="url", remote_id="https://music.163.com/song.mp3", title="x", url="https://music.163.com/song.mp3")
    with pytest.raises(SourceError) as exc:
        download_track(remote, tmp_path, save_cover=False, save_lyrics=False)
    assert "无法解析" in str(exc.value)


def test_download_track_cancel_and_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "modu_workbench.core.music.downloader.requests.get",
        lambda *a, **k: _FakeStream([b"x" * 10]),
    )
    cancel = threading.Event()
    cancel.set()
    remote = RemoteTrack(source="url", remote_id="https://cdn/song.mp3", title="t", url="https://cdn/song.mp3")
    with pytest.raises(RuntimeError):
        download_track(remote, tmp_path, cancel=cancel, save_cover=False, save_lyrics=False)

    monkeypatch.setattr(
        "modu_workbench.core.music.downloader.requests.get",
        lambda *a, **k: _FakeStream([]),
    )
    with pytest.raises(SourceError):
        download_track(remote, tmp_path, save_cover=False, save_lyrics=False)


# --------------------------------------------------------------- 播放器（静默模式）

def make_tracks(tmp_path: Path, count: int = 3) -> list[Track]:
    tracks = []
    for index in range(count):
        path = make_audio(tmp_path / f"song{index}.mp3")
        tracks.append(Track(id=index + 1, path=str(path), title=f"歌曲{index}", format="mp3", duration_ms=1000))
    return tracks


def test_player_queue_and_modes(tmp_path: Path) -> None:
    player = MusicPlayer(silent=True)
    tracks = make_tracks(tmp_path)
    player.set_queue(tracks, 0, autoplay=False)
    assert player.current.title == "歌曲0"

    player.next(autoplay=False)
    assert player.current.title == "歌曲1"
    player.previous()
    assert player.current.title == "歌曲0"

    # 顺序播放到末尾停止
    player.play_index(2)
    player.next(autoplay=False)
    assert player.state == "stopped"

    # 列表循环回到开头
    player.set_mode("loop-all")
    player.play_index(2)
    player.next(autoplay=False)
    assert player.current.title == "歌曲0"

    # 单曲循环 / 随机模式
    player.set_mode("loop-one")
    assert player.cycle_mode() == "shuffle"
    player.play_index(0)
    player.next(autoplay=False)
    assert player.current is not None and player.current.id != 0

    player.set_mode("order")
    assert player.mode == "order"
    player.set_volume(150)
    assert player.volume == 100
    player.clear()
    assert player.queue == [] and player.current is None


def test_player_skips_missing_files(tmp_path: Path) -> None:
    player = MusicPlayer(silent=True)
    missing = Track(id=1, path=str(tmp_path / "missing.mp3"), title="缺失", format="mp3")
    good = Track(id=2, path=str(make_audio(tmp_path / "ok.mp3")), title="正常", format="mp3")
    errors: list[str] = []
    player.errorOccurred.connect(errors.append)
    player.set_queue([missing, good], 0, autoplay=True)
    assert errors and "文件不存在" in errors[0]
    assert player.current is not None and player.current.id == 2


def test_player_play_track_and_favorite_state(tmp_path: Path) -> None:
    player = MusicPlayer(silent=True)
    tracks = make_tracks(tmp_path, 2)
    player.play_track(tracks[1], tracks)
    assert player.current.id == tracks[1].id
    assert player.current_index == 1
    player.toggle_pause()
    assert player.state in {"paused", "playing"}


def test_player_play_url_preview(tmp_path: Path) -> None:
    """在线试听：不进入队列，进度与错误仍由播放器统一上报。"""
    player = MusicPlayer(silent=True)
    changes: list = []
    player.trackChanged.connect(changes.append)

    track = player.play_url("https://cdn.example.com/a/song.m4a", title="试听曲", artist="歌手",
                            album="专辑", duration_ms=30_000)
    assert track is not None
    assert track.id == 0
    assert track.title == "试听曲"
    assert player.is_preview is True
    assert player.state == "playing"
    assert changes and changes[-1].title == "试听曲"
    assert player.queue == []          # 试听不污染播放队列

    player.stop_preview()
    assert player.is_preview is False
    assert player.state == "stopped"

    assert player.play_url("") is None  # 空地址直接报错


def test_player_preview_error_is_reported() -> None:
    player = MusicPlayer(silent=True)
    errors: list[str] = []
    player.errorOccurred.connect(errors.append)
    player.play_url("https://cdn.example.com/a/song.m4a", title="试听曲", artist="歌手")

    player._on_error(None, "boom")   # noqa: SLF001  模拟解码失败
    assert errors and "试听失败" in errors[0] and "试听曲" in errors[0]
    assert player.state == "error"
    assert player.is_preview is False
    assert player.queue == []


def test_player_error_skips_bad_track(tmp_path: Path) -> None:
    """坏文件必须报出曲名并自动跳到下一首，单曲队列才进入 error 状态。"""
    player = MusicPlayer(silent=True)
    errors: list[str] = []
    player.errorOccurred.connect(errors.append)
    bad = Track(id=1, path=str(make_audio(tmp_path / "bad.mp3")), title="坏文件", format="mp3")
    good = Track(id=2, path=str(make_audio(tmp_path / "good.mp3")), title="好文件", format="mp3")
    player.set_queue([bad, good], 0, autoplay=True)

    player._on_error(None, "boom")   # noqa: SLF001  模拟解码失败
    assert errors and "坏文件" in errors[0] and "无法播放" in errors[0]
    assert player.current is not None and player.current.id == 2

    single = MusicPlayer(silent=True)
    single.set_queue([bad], 0, autoplay=True)
    single._on_error(None, "boom")   # noqa: SLF001
    assert single.state == "error"


def test_player_and_library_duration_fill(tmp_path: Path, library: MusicLibrary) -> None:
    """播放时补全时长：播放器上报 → 曲库写回（无 ffprobe 也有正确时长）。"""
    source = make_audio(tmp_path / "song.mp3")
    track = library.import_paths([str(source)])[0]
    track.duration_ms = 0
    library.storage.upsert_track(track)
    assert library.storage.get_track(track.id).duration_ms == 0

    player = MusicPlayer(silent=True)
    events: list[tuple[int, int]] = []
    player.durationKnown.connect(lambda track_id, ms: events.append((track_id, ms)))
    player.set_queue([library.storage.get_track(track.id)], 0, autoplay=True)
    player._on_duration(187_000)   # noqa: SLF001

    assert events == [(track.id, 187_000)]
    assert library.update_duration(events[0][0], events[0][1]) is True
    assert library.storage.get_track(track.id).duration_ms == 187_000
