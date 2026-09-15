"""墨读音乐核心测试：存储 / 歌单 / 音源解析 / 下载 / 播放队列 / 转换入口。"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

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
from modu_workbench.core.music import sources as music_sources
from modu_workbench.core.music.downloader import download_track, guess_extension, unique_path
from modu_workbench.core.music.sources import (
    DirectUrlSource,
    ItunesSource,
    NeteaseSource,
    SourceError,
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
    from modu_workbench.core.convert.media_io import find_ffmpeg

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


def test_itunes_search(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict = {}

    def fake_request(url, params=None, headers=None, timeout=12.0, data=None):  # noqa: ANN001
        calls.update({"url": url, "params": params})
        return ITUNES_PAYLOAD

    monkeypatch.setattr(music_sources, "_request_json", fake_request)
    tracks = ItunesSource().search("周杰伦", kind="artist", limit=10)
    assert [t.title for t in tracks] == ["晴天"]  # 无 previewUrl 的结果被跳过
    assert tracks[0].artist == "周杰伦"
    assert tracks[0].category == "国语流行"
    assert calls["params"]["attribute"] == "artistTerm"
    assert "country" not in calls["params"]  # 默认不限定商店（CN 商店检索为空）


def test_itunes_search_falls_back_without_country(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []

    def fake_request(url, params=None, headers=None, timeout=12.0, data=None):  # noqa: ANN001
        seen.append(dict(params or {}))
        if params and params.get("country"):
            return {"results": []}
        return ITUNES_PAYLOAD

    monkeypatch.setenv("MODU_ITUNES_COUNTRY", "CN")
    monkeypatch.setattr(music_sources, "_request_json", fake_request)
    tracks = ItunesSource().search("周杰伦", kind="song", limit=5)
    assert [t.title for t in tracks] == ["晴天"]
    assert seen[0].get("country") == "CN" and "country" not in seen[1]


NETEASE_SEARCH = {"result": {"songs": [
    {"id": 11, "name": "稻香", "artists": [{"name": "周杰伦"}],
     "album": {"name": "魔杰座", "picUrl": "https://x/p.jpg"}, "duration": 223000},
]}}


def test_netease_search_and_download_url(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(url, params=None, headers=None, timeout=12.0, data=None):  # noqa: ANN001
        if "search/get" in url:
            return NETEASE_SEARCH
        return {}

    monkeypatch.setattr(music_sources, "_request_json", fake_request)
    tracks = NeteaseSource().search("稻香", kind="song", limit=5)
    assert len(tracks) == 1
    assert tracks[0].remote_id == "11"
    assert "outer/url?id=11" in tracks[0].url

    class FakeResponse:
        status_code = 200
        url = "https://m7.music.126.net/real.mp3"

        def close(self) -> None:
            pass

    monkeypatch.setattr(music_sources.requests, "get", lambda *a, **k: FakeResponse())
    resolved = NeteaseSource().download_url(tracks[0])
    assert resolved.endswith("real.mp3")


def test_netease_download_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    class Blocked:
        status_code = 404
        url = ""

        def close(self) -> None:
            pass

    monkeypatch.setattr(music_sources.requests, "get", lambda *a, **k: Blocked())
    with pytest.raises(SourceError):
        NeteaseSource().download_url(RemoteTrack(source="netease", remote_id="1", title="x"))


def test_direct_url_source() -> None:
    tracks = DirectUrlSource().search("https://cdn.example.com/a/b/song.flac")
    assert tracks[0].title == "song.flac"
    assert tracks[0].extra["format"] == "flac"
    assert DirectUrlSource().download_url(tracks[0]) == "https://cdn.example.com/a/b/song.flac"
    with pytest.raises(SourceError):
        DirectUrlSource().search("not-a-url")


def test_search_all_collects_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(keyword, kind="song", limit=20):  # noqa: ANN001
        raise SourceError("接口不可用")

    monkeypatch.setattr(music_sources.get_source("itunes"), "search", boom)
    tracks, errors = search_all("x", sources=["itunes", "url"])
    assert tracks == []
    assert errors and "接口不可用" in errors[0]


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
    path = download_track(remote, tmp_path, on_progress=lambda w, t, m: progress.append((w, t, m)),
                          save_cover=False, save_lyrics=False)
    assert path.name == "歌手 - 测试曲.mp3"
    assert path.read_bytes() == audio
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
