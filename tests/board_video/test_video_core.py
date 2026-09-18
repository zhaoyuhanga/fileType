"""墨软影视核心测试：模型 / m3u8 / 数据源 / 注册表（多源·熔断·换源）/ 存储 / 下载。"""
from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import pytest
import requests

from modu_workbench.core.video import (
    Episode,
    Quality,
    RemoteVideo,
    Video,
    VideoLibrary,
    VideoStorage,
    apply_quality_choice,
    build_filename,
    classify_kind,
    format_duration,
    kind_label,
    looks_like_video,
    match_quality,
    parse_m3u8,
    parse_video_name,
    quality_rank,
    remote_to_video,
    safe_filename,
    scan_video_files,
)
from modu_workbench.core.video.sources import (
    ArchiveOrgVideoSource,
    DirectUrlVideoSource,
    SourceError,
    VideoRegistry,
    VideoSource,
    WikimediaVideoSource,
    best_match,
    match_score,
)
from modu_workbench.core.video.hls import HlsVariant, list_qualities, quality_label

# --------------------------------------------------------------------------- 夹具


@pytest.fixture()
def storage(tmp_path: Path) -> VideoStorage:
    return VideoStorage(str(tmp_path / "video.db"))


@pytest.fixture()
def library(storage: VideoStorage, tmp_path: Path) -> VideoLibrary:
    return VideoLibrary(storage, tmp_path / "videos")


def make_video_file(path: Path, size: int = 2048) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # MPEG-TS 同步字节开头，能通过 looks_like_video 的容器嗅探
    path.write_bytes(b"\x47" + b"\x00" * (size - 1))
    return path


class FakeResponse:
    def __init__(self, *, payload=None, text: str = "", url: str = "https://x/",
                 headers: dict | None = None):
        self._payload = payload
        self.text = text
        self.url = url
        self.headers = headers or {"Content-Type": "application/json"}
        self.status_code = 200
        if payload is not None and not text:
            self.text = json.dumps(payload, ensure_ascii=False)

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
    """替身 HttpClient：按 URL 片段路由，并记录调用。"""

    def __init__(self, routes: dict | None = None, default: FakeResponse | None = None):
        self.routes = routes or {}
        self.default = default
        self.calls: list[tuple[str, str, dict]] = []

    def _route(self, method: str, url: str, kwargs: dict) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        for (want_method, fragment), response in self.routes.items():
            if want_method in (method, "*") and fragment in url:
                if callable(response):
                    return response(url, kwargs)
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
        # 与真实 HttpClient 同构：非 JSON 响应要转成 SourceError，
        # 否则调用方拿到裸 ValueError，错误提示会很难看
        response = self.get(url, **kwargs)
        try:
            return response.json()
        except ValueError as error:
            raise SourceError(f"{url} 返回内容不是有效 JSON") from error

    def get_text(self, url: str, **kwargs) -> str:
        return self.get(url, **kwargs).text


class StubSource(VideoSource):
    """可控的测试源：预设返回值或异常，用于验证注册表行为。"""

    def __init__(self, key: str, results=None, error: Exception | None = None,
                 label: str = "", kind: str = "full", episodes=None,
                 editable: bool = False):  # noqa: ANN001
        from modu_workbench.core.video.sources import SourceInfo

        self.info = SourceInfo(key=key, label=label or key, note="test", kind=kind, editable=editable)
        self._results = list(results or [])
        self._error = error
        self._episodes = episodes
        self.detail_calls = 0
        self.downloadable = True
        super().__init__(http=FakeHttp({}))

    def search(self, keyword: str, kind: str = "all", limit: int = 30, page: int = 1):  # noqa: ANN201
        if self._error is not None:
            raise self._error
        return list(self._results)[:limit]

    def detail(self, video: RemoteVideo) -> RemoteVideo:  # noqa: D102
        self.detail_calls += 1
        if self._episodes is not None:
            video.episodes = [Episode(**item) for item in self._episodes]
        return video

    def supports_download(self) -> bool:
        return self.downloadable


# --------------------------------------------------------------------------- 模型


def test_safe_filename_and_duration() -> None:
    assert safe_filename('a/b:c*d?"e') == "a_b_c_d_e"
    assert safe_filename("   ") == "video"
    assert len(safe_filename("x" * 300)) <= 120
    assert format_duration(0) == "--:--"
    assert format_duration(65_000) == "01:05"
    assert format_duration(3_725_000) == "01:02:05"


def test_classify_kind_and_labels() -> None:
    assert classify_kind("科幻片") == "movie"
    assert classify_kind("国产剧") == "tv"
    assert classify_kind("日韩动漫") == "anime"
    assert classify_kind("综艺") == "variety"
    assert classify_kind("纪录片") == "doc"
    assert classify_kind("") == "other"
    # 动漫优先于「剧」判断（「动漫剧」不应被判成电视剧）
    assert classify_kind("动漫剧") == "anime"
    assert kind_label("movie") == "电影"
    assert kind_label("unknown-x") == "其他"


def test_quality_rank_orders_clearly() -> None:
    assert quality_rank("4K") > quality_rank("1080P") > quality_rank("720P") > quality_rank("480P")
    assert quality_rank("1080P") > quality_rank("")
    assert quality_label(2160) == "4K"
    assert quality_label(1080) == "1080P"
    assert quality_label(720) == "720P"
    assert quality_label(0) == "原画"


def test_parse_video_name_variants() -> None:
    assert parse_video_name("庆余年 S01E03") == ("庆余年", "S01E03", 3)
    assert parse_video_name("庆余年 第03集") == ("庆余年", "第03集", 3)
    assert parse_video_name("庆余年 EP03") == ("庆余年", "第03集", 3)
    assert parse_video_name("独立电影") == ("独立电影", "", 0)


def test_scan_video_files(tmp_path: Path) -> None:
    make_video_file(tmp_path / "a.mp4")
    make_video_file(tmp_path / "sub" / "b.mkv")
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    found = scan_video_files([str(tmp_path)])
    assert [Path(p).name for p in found] == ["a.mp4", "b.mkv"]
    assert scan_video_files([str(tmp_path / "a.mp4"), str(tmp_path / "a.mp4")]) == [str(tmp_path / "a.mp4")]


def test_video_playability_flags(tmp_path: Path) -> None:
    path = make_video_file(tmp_path / "ok.mp4")
    local = Video(file_path=str(path), title="本地")
    assert local.exists and local.playable and not local.online_only

    missing = Video(file_path=str(tmp_path / "gone.mp4"), title="丢失")
    assert not missing.exists and not missing.playable

    online = Video(title="在线", source="cms", remote_id="7", remote_url="https://x/a.m3u8")
    assert online.online_only and online.playable
    assert online.playback_target == "https://x/a.m3u8"

    assert local.playback_target == str(path)
    assert Video(title="剧", episode_label="第01集").display() == "剧 · 第01集"


def test_video_to_remote_roundtrip() -> None:
    video = Video(
        title="流浪地球", kind="movie", source="cms_360", remote_id="71142",
        episode_label="正片", episode_index=0, remote_url="https://x/1.m3u8",
        series_key="cms_360:71142", year="2019",
    )
    remote = video.to_remote()
    assert remote.title == video.title
    assert remote.source == "cms_360" and remote.remote_id == "71142"
    assert len(remote.episodes) == 1
    assert remote.episodes[0].url == "https://x/1.m3u8"
    assert remote.series_key == "cms_360:71142"


def test_match_quality_and_apply_quality_choice() -> None:
    qualities = [Quality(label="1080P", url="u1"), Quality(label="720P", url="u2")]
    assert match_quality(qualities, "720P").url == "u2"
    # 精确命中不到时按清晰度等级取最接近的
    assert match_quality(qualities, "1080").label == "1080P"
    assert match_quality(qualities, "4K").label == "1080P"
    assert match_quality(qualities, "360P").label == "720P"
    assert match_quality(qualities, "") in qualities
    assert match_quality([], "1080P") is None
    # 只有一路画质时直接返回它
    single = [Quality(label="原画", url="u")]
    assert match_quality(single, "4K").url == "u"

    episode = Episode(name="正片", url="u", qualities=list(qualities))
    assert apply_quality_choice(episode, "720P").url == "u2"
    assert apply_quality_choice(None, "720P") is None


def test_build_filename_includes_year_and_episode() -> None:
    remote = RemoteVideo(source="cms", remote_id="1", title="流浪地球", year="2019")
    assert build_filename(remote) == "流浪地球 (2019).mp4"
    episode = Episode(name="第01集", url="u")
    assert build_filename(remote, episode, ".ts") == "流浪地球 (2019) 第01集.ts"
    # 非法字符被清理
    bad = RemoteVideo(source="cms", remote_id="2", title='a/b:c*d?')
    assert "/" not in build_filename(bad)


def test_remote_to_video_keeps_episode_and_series() -> None:
    remote = RemoteVideo(
        source="cms_360", remote_id="9", title="剧", kind="tv", year="2024",
        episodes=[Episode(name="第01集", url="https://x/1.m3u8", index=0)],
        series_key="cms_360:9",
    )
    video = remote_to_video(remote, remote.episodes[0], quality="1080P")
    assert video.series_key == "cms_360:9"
    assert video.episode_label == "第01集"
    assert video.quality == "1080P"
    assert video.remote_url == "https://x/1.m3u8"
    assert video.kind == "tv"


# --------------------------------------------------------------------------- m3u8


MASTER_PLAYLIST = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,NAME="1080P"
1080/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
720/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
360/index.m3u8
"""

MEDIA_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:9.009,
seg-1.ts
#EXTINF:9.009,
https://cdn.example.com/seg-2.ts
#EXTINF:3.003,
seg-3.ts
#EXT-X-ENDLIST
"""


def test_parse_master_playlist_lists_qualities() -> None:
    playlist = parse_m3u8(MASTER_PLAYLIST, "https://cdn.example.com/hls/master.m3u8")
    assert playlist.is_master
    assert not playlist.is_media
    labels = [item.display for item in playlist.variants]
    assert labels == ["1080P", "720P", "360P"]
    # 相对地址被补全为绝对地址
    assert playlist.variants[0].url == "https://cdn.example.com/hls/1080/index.m3u8"
    best = playlist.best_variant()
    assert best is not None and best.height == 1080
    # 主清单里没有分片，不是直播
    assert playlist.segments == []
    assert not playlist.live


def test_parse_media_playlist_segments_and_duration() -> None:
    playlist = parse_m3u8(MEDIA_PLAYLIST, "https://cdn.example.com/hls/720/index.m3u8")
    assert playlist.is_media and not playlist.is_master
    assert len(playlist.segments) == 3
    assert playlist.segments[0] == "https://cdn.example.com/hls/720/seg-1.ts"
    assert playlist.segments[1] == "https://cdn.example.com/seg-2.ts"
    assert playlist.target_duration == 9.009
    assert not playlist.live
    assert not playlist.encrypted


def test_parse_encrypted_and_live_playlist() -> None:
    encrypted = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="key.bin"
#EXTINF:5.0,
seg.ts
"""
    playlist = parse_m3u8(encrypted, "https://x/")
    assert playlist.encrypted and playlist.is_media

    live = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:12
#EXTINF:6.0,
seg.ts
"""
    assert parse_m3u8(live, "https://x/").live is True


def test_parse_m3u8_rejects_non_playlist() -> None:
    playlist = parse_m3u8("<html>403 Forbidden</html>", "https://x/")
    assert not playlist.is_master and not playlist.is_media and playlist.segments == []


def test_list_qualities_uses_http_client() -> None:
    http = FakeHttp({("GET", "master.m3u8"): FakeResponse(
        payload=None, text=MASTER_PLAYLIST,
        headers={"Content-Type": "application/vnd.apple.mpegurl"})})
    variants = list_qualities("https://cdn.example.com/master.m3u8", http=http, referer="https://site/")
    assert [item.display for item in variants] == ["1080P", "720P", "360P"]
    # 探测清晰度时必须带上 Referer（视频站普遍校验），走 headers 传
    assert (http.calls[0][2].get("headers") or {}).get("Referer") == "https://site/"

    empty = list_qualities("https://cdn.example.com/master.m3u8", http=FakeHttp({}))
    assert empty == []


def test_hls_variant_fallback_label() -> None:
    variant = HlsVariant(url="u", bandwidth=1_200_000)
    assert variant.display == "1200 kbps"
    assert variant.rank == 1200


# --------------------------------------------------------------------------- 苹果CMS 源


def cms_payload() -> dict:
    return {
        "code": 1, "page": 1, "pagecount": 1, "limit": "20", "total": 2,
        "class": [
            {"type_id": 1, "type_name": "电影", "type_pid": 0},
            {"type_id": 6, "type_name": "动漫", "type_pid": 0},
        ],
        "list": [
            {
                "vod_id": 71142, "vod_name": "流浪地球2", "type_name": "科幻片",
                "vod_year": "2023", "vod_area": "中国大陆", "vod_class": "科幻,冒险",
                "vod_actor": "吴京,刘德华", "vod_director": "郭帆",
                "vod_content": "<p>太阳危机</p>", "vod_pic": "https://x/p.jpg",
                "vod_remarks": "正片", "vod_score": "8.3", "vod_duration": "173",
                "vod_play_from": "360zy$$$lzm3u8",
                "vod_play_url": (
                    "正片$https://vod1.maowushi.com/1/index.m3u8$$$"
                    "正片$https://vod2.maowushi.com/2/index.m3u8#预告$https://vod2.maowushi.com/2/t.mp4"
                ),
            },
            {
                "vod_id": 9, "vod_name": "某剧", "type_name": "国产剧",
                "vod_play_from": "lzm3u8",
                "vod_play_url": "第01集$https://v/1.m3u8#第02集$https://v/2.m3u8#坏集$https://v/2.mp3",
            },
        ],
    }


def build_cms_source(**routes):  # noqa: ANN003
    from modu_workbench.core.video.sources.providers.cms_vod import build_source

    source = build_source("cms_360", "360资源", "https://360zy.com")
    source.http = FakeHttp(routes or {("GET", "provide/vod"): FakeResponse(payload=cms_payload())})
    return source


def test_cms_search_parses_list_and_metadata() -> None:
    source = build_cms_source()
    videos = source.search("流浪地球", limit=10)
    assert len(videos) == 2
    first = videos[0]
    assert first.title == "流浪地球2"
    assert first.remote_id == "71142"
    assert first.year == "2023"
    assert first.kind == "movie"          # 由「科幻片」推断
    assert first.actors == "吴京,刘德华"
    assert first.description == "太阳危机"   # HTML 已清洗
    assert first.duration_ms == 173 * 60 * 1000
    assert first.series_key == "cms_360:71142"
    # 多线路展开：2 个线路 × 集数
    assert len(first.episodes) == 3
    assert all(episode.url.startswith("http") for episode in first.episodes)


def test_cms_episode_parsing_filters_audio_and_keeps_names() -> None:
    source = build_cms_source()
    videos = source.search("某剧", limit=10)
    second = videos[1]
    assert second.kind == "tv"
    # mp3 集被过滤掉
    assert [episode.name for episode in second.episodes] == ["第01集", "第02集"]
    assert [episode.index for episode in second.episodes] == [0, 1]


def test_cms_multi_route_names_include_route_label() -> None:
    source = build_cms_source()
    first = source.search("x", limit=1)[0]
    names = [episode.name for episode in first.episodes]
    assert "360zy · 正片" in names
    assert "lzm3u8 · 正片" in names
    # 提示线路画质（lzm3u8 不含画质线索，360zy 也不含 → 无画质标签，但不应报错）
    assert isinstance(first.episodes[0].qualities, list)


def test_cms_route_quality_hint() -> None:
    from modu_workbench.core.video.sources.providers.cms_vod import guess_quality_label, parse_play_urls

    assert guess_quality_label("1080p") == "1080P"
    assert guess_quality_label("hd") == "高清"
    assert guess_quality_label("sd") == "标清"
    assert guess_quality_label("lzm3u8") == ""
    episodes = parse_play_urls("正片$https://v/a.m3u8", "1080p")
    assert episodes[0].qualities[0].label == "1080P"


def test_cms_search_rejects_error_code() -> None:
    source = build_cms_source()
    source.http = FakeHttp({("GET", "provide/vod"): FakeResponse(payload={"code": 0, "msg": "关键词不能为空"})})
    with pytest.raises(SourceError) as exc:
        source.search("x")
    assert "关键词不能为空" in str(exc.value)


def test_cms_search_rejects_non_json() -> None:
    source = build_cms_source()
    source.http = FakeHttp({("GET", "provide/vod"): FakeResponse(payload=None, text="<html>502</html>")})
    with pytest.raises(SourceError) as exc:
        source.search("x")
    assert "JSON" in str(exc.value) or "有效" in str(exc.value)


def test_cms_type_filter_uses_class_list() -> None:
    source = build_cms_source()
    source.search("流浪地球", kind="movie", limit=5)
    # 分类表可用时带上 t 参数过滤（电影 → type_id=1）
    search_calls = [call for call in source.http.calls if "provide/vod" in call[1]]
    assert any((call[2].get("params") or {}).get("t") == "1" for call in search_calls)


def test_cms_unavailable_without_base_url() -> None:
    from modu_workbench.core.video.sources.providers.cms_vod import build_source

    source = build_source("x", "X站", "")
    assert source.available() is False
    assert "配置" in source.unavailable_reason()
    with pytest.raises(SourceError):
        source.search("y")


def test_cms_credential_override_switches_domain() -> None:
    source = build_cms_source()
    source.set_credential("https://new-domain.example")
    assert source.credential == "https://new-domain.example"
    assert "new-domain.example" in source._api()  # noqa: SLF001
    assert source._api().endswith("/api.php/provide/vod")  # noqa: SLF001


def test_cms_credential_reset_restores_default_domain() -> None:
    """「源设置 → 恢复默认」要真的把改过的接口地址还原（空字符串不算还原）。"""
    source = build_cms_source()
    source.set_credential("https://broken.example")
    assert source.credential == "https://broken.example"
    source.reset_credential()
    assert source.credential == "https://360zy.com"


# --------------------------------------------------------------------------- 分享页解析
# 背景：不少采集站（非凡 / 电影天堂 / U酷…）给的是 `…/share/<hash>` 这类 HTML 播放页，
# 真实 m3u8 写在页面脚本里。不解析就会「播放和下载都失败」（把 HTML 当视频解）。

SHARE_PAGE = """<!DOCTYPE html><html><head><script>
        const vid = "b9e4ce50f6c1ba80d31aa4826948a5a6";
        const url = "/20241031/173690_b9e4ce50/index.m3u8?sign=abc123";
        const pic = "/20241031/173690_b9e4ce50/1.jpg";
</script></head><body></body></html>"""


def build_share_source(page_text: str = SHARE_PAGE, **routes):  # noqa: ANN003
    """构造「分集地址是分享页」的采集源。"""
    from modu_workbench.core.video.sources.providers.cms_vod import build_source

    source = build_source("cms_ffzy", "非凡资源", "https://api.ffzyapi.com")
    source.http = FakeHttp(routes or {
        ("GET", "provide/vod"): FakeResponse(payload={
            "code": 1,
            "list": [{
                "vod_id": 1, "vod_name": "样例电影", "type_name": "科幻片",
                "vod_play_from": "ffm3u8",
                "vod_play_url": "正片$https://vip.ffzy-play8.com/share/b9e4ce50f6c1ba80d31aa4826948a5a6",
            }],
        }),
        ("GET", "/share/"): FakeResponse(text=page_text, url="https://vip.ffzy-play8.com/share/hash",
                                        headers={"Content-Type": "text/html"}),
    })
    return source


def test_looks_like_media_url_only_accepts_files() -> None:
    from modu_workbench.core.video.sources import looks_like_media_url

    assert looks_like_media_url("https://v/x/index.m3u8") is True
    assert looks_like_media_url("https://v/x/index.m3u8?sign=1") is True
    assert looks_like_media_url("https://v/x/movie.MP4") is True
    assert looks_like_media_url("https://vip.ffzy-play8.com/share/b9e4ce50") is False
    assert looks_like_media_url("") is False


def test_extract_media_url_prefers_m3u8_and_handles_escapes() -> None:
    from modu_workbench.core.video.sources.providers.cms_vod import extract_media_url

    assert extract_media_url(SHARE_PAGE, "https://vip.ffzy-play8.com/share/hash") == \
        "https://vip.ffzy-play8.com/20241031/173690_b9e4ce50/index.m3u8?sign=abc123"
    # 转义写法（JSON 里的 \/）与相对路径都要能取到；m3u8 优先于 mp4
    escaped = '{"url":"https:\\/\\/cdn\\/a\\/index.m3u8?sign=9","backup":"https://cdn/a/x.mp4"}'
    assert extract_media_url(escaped, "https://cdn/page") == "https://cdn/a/index.m3u8?sign=9"
    assert extract_media_url("<html>没有地址</html>", "https://cdn/page") == ""


def test_cms_play_url_resolves_share_page() -> None:
    source = build_share_source()
    video = source.search("x", limit=1)[0]
    episode = video.episodes[0]
    assert episode.url.endswith("/share/b9e4ce50f6c1ba80d31aa4826948a5a6")   # 采集接口给的是页面
    assert source.play_url(video, episode) == \
        "https://vip.ffzy-play8.com/20241031/173690_b9e4ce50/index.m3u8?sign=abc123"


def test_cms_play_url_caches_share_page() -> None:
    source = build_share_source()
    video = source.search("x", limit=1)[0]
    episode = video.episodes[0]
    source.play_url(video, episode)
    before = len([call for call in source.http.calls if "/share/" in call[1]])
    source.play_url(video, episode)
    after = len([call for call in source.http.calls if "/share/" in call[1]])
    assert before == 1
    assert after == 1          # 第二次直接用缓存，不再抓页面


def test_cms_direct_media_url_is_not_fetched() -> None:
    source = build_share_source()
    source.http = FakeHttp({("GET", "provide/vod"): FakeResponse(payload={
        "code": 1,
        "list": [{"vod_id": 2, "vod_name": "直链片", "vod_play_from": "lzm3u8",
                  "vod_play_url": "正片$https://vod1.example.com/1/index.m3u8"}],
    })})
    video = source.search("x", limit=1)[0]
    assert source.play_url(video, video.episodes[0]) == "https://vod1.example.com/1/index.m3u8"
    # 直链不应触发任何额外请求（除搜索外）
    assert all("provide/vod" in call[1] for call in source.http.calls)


def test_cms_share_page_without_address_raises() -> None:
    source = build_share_source(page_text="<html><body>该视频已下架</body></html>")
    video = source.search("x", limit=1)[0]
    with pytest.raises(SourceError) as exc:
        source.play_url(video, video.episodes[0])
    assert "播放页" in str(exc.value)


def test_cms_resolved_media_uses_page_referer() -> None:
    source = build_share_source()
    video = source.search("x", limit=1)[0]
    real = source.play_url(video, video.episodes[0])
    # 解析出来的 CDN 地址要用「播放页所在站点」当 Referer（部分 CDN 校验）
    assert source.download_headers(real)["Referer"] == "https://vip.ffzy-play8.com/"


def test_default_sources_match_order_and_are_configured() -> None:
    """内置源清单自检：顺序表里的每个 key 都要真的注册，且都有接口地址。

    采集站会失效，这里只保证「清单与实现一致」；接口地址本身是否可用由维护时实测。
    """
    from modu_workbench.core.video.sources import DEFAULT_PROVIDER_ORDER, build_default_providers

    providers = {provider.key: provider for provider in build_default_providers()}
    assert set(DEFAULT_PROVIDER_ORDER) == set(providers)
    for key, provider in providers.items():
        if key.startswith("cms_"):
            assert provider.available(), f"{key} 缺少接口地址"


def test_registry_migrates_source_settings_on_version_bump(storage: VideoStorage) -> None:
    """源清单升级后，旧的启用集合不能把新增源默认关掉。"""
    from modu_workbench.core.video.sources import (
        DEFAULT_PROVIDER_ORDER,
        SETTING_ENABLED,
        SETTING_ORDER,
        SETTING_VERSION,
        SOURCES_VERSION,
    )

    # 模拟旧版本留下的数据：有启用集合与排序，但没有版本号
    storage.set_setting(SETTING_ENABLED, "cms_360,archive")
    storage.set_setting(SETTING_ORDER, "archive,cms_360")

    registry = VideoRegistry()
    registry.load_settings(storage)
    assert registry.is_enabled("cms_ikunzy") is True       # 新增源默认启用
    assert registry.order[:3] == list(DEFAULT_PROVIDER_ORDER)[:3]
    assert storage.get_setting(SETTING_VERSION, "") == str(SOURCES_VERSION)

    # 用户主动停用后要被尊重（版本一致时不再回退默认）
    registry.set_enabled("cms_ikunzy", False)
    registry.save_settings(storage)
    restored = VideoRegistry()
    restored.load_settings(storage)
    assert restored.is_enabled("cms_ikunzy") is False


# --------------------------------------------------------------------------- 自由授权源


def test_archive_search_and_detail() -> None:
    http = FakeHttp({
        ("GET", "advancedsearch"): FakeResponse(payload={"response": {"docs": [
            {"identifier": "night-of-the-living-dead", "title": "Night of the Living Dead",
             "year": "1968", "creator": "George A. Romero", "description": "classic"},
        ]}}),
        ("GET", "/metadata/"): FakeResponse(payload={
            "files": [
                {"name": "thumb.jpg", "format": "JPEG"},
                {"name": "movie.mp4", "format": "h.264", "size": "1000"},
                {"name": "movie.ogv", "format": "Ogg Video", "size": "900"},
            ],
            "metadata": {"description": "meta description"},
        }),
    })
    source = ArchiveOrgVideoSource(http=http)
    videos = source.search("night", limit=5)
    assert len(videos) == 1
    assert videos[0].remote_id == "night-of-the-living-dead"
    assert videos[0].year == "1968"
    assert videos[0].director == "George A. Romero"

    detailed = source.detail(videos[0])
    assert [episode.name for episode in detailed.episodes] == ["movie.mp4", "movie.ogv"]
    assert detailed.episodes[0].url.endswith("/night-of-the-living-dead/movie.mp4")
    assert source.play_url(detailed, detailed.episodes[0]).endswith("movie.mp4")


def test_wikimedia_search_filters_to_video() -> None:
    http = FakeHttp({
        ("GET", "commons.wikimedia.org"): FakeResponse(payload={"query": {"pages": {
            "1": {"pageid": 1, "title": "File:Clip.webm",
                  "imageinfo": [{"url": "https://upload.wikimedia.org/Clip.webm", "mime": "video/webm",
                                 "height": 1080, "extmetadata": {"Artist": {"value": "Someone"}}}]},
            "2": {"pageid": 2, "title": "File:Photo.jpg",
                  "imageinfo": [{"url": "https://upload.wikimedia.org/Photo.jpg", "mime": "image/jpeg"}]},
        }}}),
    })
    source = WikimediaVideoSource(http=http)
    videos = source.search("clip", limit=5)
    assert len(videos) == 1                      # 图片被过滤
    assert videos[0].title == "Clip.webm"
    assert videos[0].episodes[0].qualities[0].label == "1080P"
    assert videos[0].director == "Someone"


def test_direct_url_source_accepts_links_only() -> None:
    source = DirectUrlVideoSource()
    videos = source.search("https://cdn.example.com/a/b/movie.m3u8")
    assert videos[0].title == "movie.m3u8"
    assert videos[0].episodes[0].url.endswith("movie.m3u8")
    assert videos[0].extra["is_hls"] is True
    with pytest.raises(SourceError):
        source.search("不是链接")


# --------------------------------------------------------------------------- 注册表：聚合 / 去重 / 熔断 / 换源


def make_remote(source: str, remote_id: str, title: str, *, year: str = "",
                episodes: int = 1, kind: str = "movie") -> RemoteVideo:
    video = RemoteVideo(source=source, remote_id=remote_id, title=title, year=year, kind=kind)
    video.episodes = [
        Episode(name=f"第{index + 1:02d}集", url=f"https://{source}/{remote_id}/{index}.m3u8", index=index)
        for index in range(episodes)
    ]
    return video


def test_registry_search_all_dedupes_keeps_first_source() -> None:
    a = StubSource("a", [make_remote("a", "1", "流浪地球", year="2023")])
    b = StubSource("b", [
        make_remote("b", "2", "流浪地球", year="2023"),        # 与 a 重复 → 丢弃
        make_remote("b", "3", "流浪地球2", year="2023"),       # 保留
    ])
    registry = VideoRegistry([a, b])
    videos, errors = registry.search_all("流浪地球", sources=["a", "b"])
    assert [video.title for video in videos] == ["流浪地球", "流浪地球2"]
    assert videos[0].source == "a"
    assert errors == []


def test_registry_search_all_collects_errors() -> None:
    registry = VideoRegistry([
        StubSource("ok", [make_remote("ok", "1", "片")]),
        StubSource("bad", error=SourceError("接口 404")),
    ])
    videos, errors = registry.search_all("x", sources=["ok", "bad"])
    assert len(videos) == 1
    assert errors and "404" in errors[0]


def test_registry_search_all_skips_link_only_source() -> None:
    """直链源不参与关键词聚合搜索（否则每次搜索都多一条噪音报错），但粘贴地址仍可用。"""
    registry = VideoRegistry([StubSource("ok", [make_remote("ok", "1", "片")]), DirectUrlVideoSource()])
    videos, errors = registry.search_all("电影")
    assert [video.source for video in videos] == ["ok"]
    assert errors == []

    videos, errors = registry.search_all("https://cdn.example.com/a/index.m3u8")
    assert "url" in [video.source for video in videos]
    assert errors == []


def test_registry_health_degrades_after_repeated_failures() -> None:
    registry = VideoRegistry([StubSource("bad", error=SourceError("挂了"))])
    for _ in range(3):
        registry.search_all("x", sources=["bad"])
    assert "bad" in registry.degraded_keys()
    assert registry.status_text("bad") == "临时降级"
    # 自动模式下跳过被熔断的源
    videos, errors = registry.search_all("x")
    assert videos == [] and errors == []
    # 显式指定仍然尝试（用户主动选择）
    _, errors = registry.search_all("x", sources=["bad"])
    assert errors

    registry.record_success("bad")
    assert "bad" not in registry.degraded_keys()
    assert registry.status_text("bad") == "可用"


def test_registry_cross_source_fallback_picks_same_episode() -> None:
    """主源解析失败 → 自动换到能解析的源，并定位到同一集。"""
    broken = StubSource("broken", [make_remote("broken", "1", "流浪地球", episodes=3)])
    broken.play_url = lambda video, episode, quality=None: (_ for _ in ()).throw(  # type: ignore[assignment]
        SourceError("线路已失效")
    )
    healthy = StubSource("healthy", [make_remote("healthy", "9", "流浪地球", episodes=3)])

    registry = VideoRegistry([broken, healthy])
    base = make_remote("broken", "1", "流浪地球", episodes=3)
    target = base.episodes[1]      # 第02集

    resolved = registry.resolve(base, target)
    assert resolved.switched is True
    assert resolved.switched_from == "broken"
    assert resolved.source == "healthy"
    assert resolved.episode is not None and resolved.episode.index == 1
    assert "healthy" in resolved.url
    assert "改用" in resolved.note


def test_registry_resolve_without_cross_source_raises() -> None:
    broken = StubSource("broken", [make_remote("broken", "1", "片")])
    broken.play_url = lambda video, episode, quality=None: (_ for _ in ()).throw(  # type: ignore[assignment]
        SourceError("失效")
    )
    registry = VideoRegistry([broken])
    with pytest.raises(SourceError):
        registry.resolve(make_remote("broken", "1", "片"), None, allow_cross_source=False)


def test_registry_exclude_prevents_looping_back() -> None:
    source = StubSource("only", [make_remote("only", "1", "片")])
    registry = VideoRegistry([source])
    with pytest.raises(SourceError):
        registry.resolve(make_remote("only", "1", "片"), None, exclude=["only"])


def test_registry_resolve_fetches_detail_when_episodes_missing() -> None:
    """用户直接点播放（还没拉详情）时，注册表应先补详情再解析。"""
    bare = RemoteVideo(source="s", remote_id="1", title="片")
    source = StubSource("s", [make_remote("s", "1", "片", episodes=2)],
                        episodes=[{"name": "第01集", "url": "https://s/1.m3u8", "index": 0}])
    registry = VideoRegistry([source])
    resolved = registry.resolve(bare)
    assert source.detail_calls == 1
    assert resolved.url == "https://s/1.m3u8"
    assert resolved.episode is not None and resolved.episode.name == "第01集"


def test_registry_prefers_local_source_over_cross_source() -> None:
    primary = StubSource("primary", [make_remote("primary", "1", "片", episodes=2)])
    other = StubSource("other", [make_remote("other", "2", "片", episodes=2)])
    registry = VideoRegistry([primary, other])
    base = make_remote("primary", "1", "片", episodes=2)
    resolved = registry.resolve(base, base.episodes[0])
    assert resolved.source == "primary"
    assert resolved.switched is False


def test_registry_settings_roundtrip(storage: VideoStorage) -> None:
    a = StubSource("a", [])
    b = StubSource("b", [], editable=True)
    registry = VideoRegistry([a, b])
    registry.set_enabled("a", False)
    registry.set_order(["b", "a"])
    b.set_credential("https://custom.example")
    registry.save_settings(storage)

    restored = VideoRegistry([StubSource("a", []), StubSource("b", [], editable=True)])
    restored.load_settings(storage)
    assert restored.is_enabled("a") is False
    assert restored.order[:2] == ["b", "a"]
    assert restored.get("b").credential == "https://custom.example"


def test_registry_enabled_filter_and_infos() -> None:
    a = StubSource("a", [make_remote("a", "1", "A")])
    b = StubSource("b", [make_remote("b", "1", "B")])
    registry = VideoRegistry([a, b])
    registry.set_enabled("b", False)
    assert [provider.key for provider in registry.providers()] == ["a"]
    assert [info.key for info in registry.infos(enabled_only=True)] == ["a"]
    # 自动搜索只走启用的源
    videos, _errors = registry.search_all("x")
    assert [video.source for video in videos] == ["a"]
    registry.set_enabled_keys(["a", "b"])
    assert registry.is_enabled("b") is True


# --------------------------------------------------------------------------- 匹配


def test_match_score_and_best_match() -> None:
    base = RemoteVideo(source="a", remote_id="1", title="流浪地球", year="2019", kind="movie",
                       actors="吴京,屈楚萧")
    same = RemoteVideo(source="b", remote_id="2", title="流浪地球 高清", year="2019", kind="movie",
                       actors="吴京")
    other = RemoteVideo(source="b", remote_id="3", title="流浪地球2", year="2023", kind="movie")
    unrelated = RemoteVideo(source="b", remote_id="4", title="完全不相干的片子")

    assert match_score(base, same) > match_score(base, other)
    assert match_score(base, unrelated) < 0.4
    assert best_match(base, [unrelated, other, same]) is same
    assert best_match(base, [unrelated], threshold=0.9) is None
    assert best_match(base, []) is None


def test_match_score_penalizes_year_conflict() -> None:
    base = RemoteVideo(source="a", remote_id="1", title="同名片", year="1990")
    newer = RemoteVideo(source="b", remote_id="2", title="同名片", year="2024")
    matching = RemoteVideo(source="b", remote_id="3", title="同名片", year="1990")
    assert match_score(base, matching) > match_score(base, newer)


# --------------------------------------------------------------------------- 存储


def test_video_upsert_dedupe_and_filters(storage: VideoStorage, tmp_path: Path) -> None:
    path = make_video_file(tmp_path / "a.mp4")
    first = Video(file_path=str(path), title="流浪地球", kind="movie", format="mp4")
    video_id = storage.upsert_video(first)
    assert video_id > 0
    first.title = "流浪地球（重制）"
    assert storage.upsert_video(first) == video_id      # 同路径为更新

    storage.upsert_video(Video(title="某剧", kind="tv", source="cms", remote_id="9"))
    assert len(storage.list_videos()) == 2
    assert [v.title for v in storage.list_videos(keyword="重制")] == ["流浪地球（重制）"]
    assert [v.title for v in storage.list_videos(kind="tv")] == ["某剧"]
    assert len(storage.list_videos(local_only=True)) == 1
    assert dict(storage.list_kinds())["tv"] == 1

    storage.set_category([video_id], "科幻")
    assert [v.id for v in storage.list_videos(category="科幻")] == [video_id]
    assert dict(storage.list_categories())["科幻"] == 1
    assert storage.set_favorite([video_id], True) == 1
    assert [v.id for v in storage.list_videos(favorite_only=True)] == [video_id]
    storage.mark_played(video_id)
    assert storage.get_video(video_id).play_count == 1


def test_online_video_dedupe_by_source_and_episode(storage: VideoStorage) -> None:
    first = Video(title="剧", source="cms", remote_id="9", episode_index=0, episode_label="第01集")
    second = Video(title="剧", source="cms", remote_id="9", episode_index=1, episode_label="第02集")
    a = storage.upsert_video(first)
    b = storage.upsert_video(second)
    assert a != b                                        # 不同集各占一行
    again = Video(title="剧（改名）", source="cms", remote_id="9", episode_index=0)
    assert storage.upsert_video(again) == a              # 同源同集为更新
    assert storage.find_remote_video("cms", "9", 0).id == a
    assert [v.id for v in storage.list_series_episodes("")] == []


def test_favorite_playlist_sync(storage: VideoStorage) -> None:
    video_id = storage.upsert_video(Video(title="A", source="s", remote_id="1"))
    favorite_id = storage.favorite_playlist_id
    assert [p.id for p in storage.list_playlists()][0] == favorite_id
    assert storage.toggle_favorite(video_id) is True
    assert [v.id for v in storage.list_playlist_videos(favorite_id)] == [video_id]

    storage.remove_from_playlist(favorite_id, [video_id])
    assert storage.get_video(video_id).favorited is False


def test_category_crud_and_membership(storage: VideoStorage) -> None:
    playlist_id = storage.create_playlist("科幻")
    assert storage.create_playlist("科幻") == playlist_id        # 同名复用
    assert storage.rename_playlist(playlist_id, "科幻片") is True
    assert storage.rename_playlist(storage.favorite_playlist_id, "x") is False

    ids = [storage.upsert_video(Video(title=f"片{i}", source="s", remote_id=str(i))) for i in range(3)]
    assert storage.add_to_playlist(playlist_id, ids) == 3
    assert storage.add_to_playlist(playlist_id, ids) == 0        # 去重
    assert [v.id for v in storage.list_playlist_videos(playlist_id)] == ids
    assert storage.get_playlist(playlist_id).video_count == 3
    assert storage.delete_playlist(playlist_id) is True
    assert storage.delete_playlist(storage.favorite_playlist_id) is False


def test_history_keeps_title_after_video_deleted(storage: VideoStorage) -> None:
    video_id = storage.upsert_video(Video(title="流浪地球", source="s", remote_id="1"))
    storage.add_history(video_id, "play", episode_label="第01集", quality="1080P", source="360资源")
    storage.add_history(video_id, "download")
    entries = storage.list_history()
    assert [e.action for e in entries] == ["download", "play"]
    assert entries[0].title == "流浪地球"
    assert entries[1].episode_label == "第01集" and entries[1].quality == "1080P"

    storage.delete_videos([video_id])
    after = storage.list_history(action="play")
    assert after[0].title == "流浪地球"           # 快照保留
    storage.clear_history()
    assert storage.list_history() == []


def test_play_records_roundtrip(storage: VideoStorage) -> None:
    video_id = storage.upsert_video(Video(title="剧", source="s", remote_id="1", episode_index=0))
    storage.save_play_record(video_id, "第01集", "1080P", "https://x/1.m3u8", "360资源")
    record = storage.get_play_record(video_id, "第01集")
    assert record is not None and record.url == "https://x/1.m3u8" and record.quality == "1080P"
    # 同一集再次保存为更新
    storage.save_play_record(video_id, "第01集", "720P", "https://x/1b.m3u8", "黑木耳")
    updated = storage.get_play_record(video_id, "第01集")
    assert updated.url.endswith("1b.m3u8") and updated.quality == "720P"
    assert storage.get_play_record(video_id, "第99集") is None
    # 删条目时清理播放记录
    storage.delete_videos([video_id])
    assert storage.get_play_record(video_id, "第01集") is None


def test_settings_roundtrip(storage: VideoStorage) -> None:
    storage.set_setting("download_dir", "D:/video")
    assert storage.get_setting("download_dir") == "D:/video"
    assert storage.get_setting("missing", "默认") == "默认"


def test_storage_persists_across_reopen(tmp_path: Path) -> None:
    db = str(tmp_path / "video.db")
    first = VideoStorage(db)
    video_id = first.upsert_video(Video(title="片", source="s", remote_id="1"))
    first.close()
    second = VideoStorage(db)
    try:
        assert second.get_video(video_id).title == "片"
    finally:
        second.close()


# --------------------------------------------------------------------------- 本地库


def test_library_import_and_probe(library: VideoLibrary, tmp_path: Path) -> None:
    make_video_file(tmp_path / "流浪地球 (2019).mp4")
    make_video_file(tmp_path / "sub" / "庆余年 S01E03.mkv")
    videos = library.import_paths([str(tmp_path)])
    assert len(videos) == 2
    by_title = {video.title: video for video in videos}
    assert "流浪地球" in by_title
    drama = by_title["庆余年"]
    assert drama.episode_label == "S01E03"
    assert drama.episode_index == 3
    assert all(video.id > 0 for video in videos)
    assert all(video.source == "local" for video in videos)


def test_library_ensure_video_is_idempotent_and_keeps_user_state(library: VideoLibrary) -> None:
    remote = make_remote("cms", "9", "剧", episodes=3)
    first = library.ensure_video(remote, remote.episodes[0])
    assert first.id > 0 and first.online_only
    library.storage.set_category([first.id], "我的剧")
    library.storage.set_favorite([first.id], True)

    again = library.ensure_video(remote, remote.episodes[0])
    assert again.id == first.id                    # 不重复入库
    assert again.category == "我的剧"               # 用户分类保留
    assert again.favorited is True

    other_episode = library.ensure_video(remote, remote.episodes[1])
    assert other_episode.id != first.id


def test_library_record_play_and_history(library: VideoLibrary) -> None:
    video = library.ensure_video(make_remote("cms", "1", "片"))
    library.record_play(video, quality="1080P", source="360资源")
    assert library.storage.get_video(video.id).play_count == 1
    entries = library.storage.list_history(action="play")
    assert entries[0].title == "片" and entries[0].quality == "1080P"


def test_library_series_episodes(library: VideoLibrary) -> None:
    remote = make_remote("cms", "9", "剧", episodes=3)
    videos = [library.ensure_video(remote, episode) for episode in remote.episodes]
    series = library.series_episodes(videos[0])
    assert [video.id for video in series] == [video.id for video in videos]


def test_library_refresh_durations_without_ffprobe(library: VideoLibrary, tmp_path: Path) -> None:
    path = make_video_file(tmp_path / "a.mp4")
    video = library.import_paths([str(path)])[0]
    # 没有 ffprobe 时应当安全返回 0，而不是抛错
    assert library.refresh_durations([video.id]) >= 0


def test_library_missing_files_and_delete(library: VideoLibrary, tmp_path: Path) -> None:
    path = make_video_file(tmp_path / "a.mp4")
    video = library.import_paths([str(path)])[0]
    assert library.missing_files() == []
    Path(video.file_path).unlink()
    assert [item.id for item in library.missing_files()] == [video.id]

    assert library.delete_with_files([video.id], remove_file=False) == 1
    assert library.storage.get_video(video.id) is None


def test_library_convert_guards(library: VideoLibrary, tmp_path: Path) -> None:
    path = make_video_file(tmp_path / "a.mp4")
    video = library.import_paths([str(path)])[0]
    with pytest.raises(ValueError):
        library.convert(video.id, "xyz")
    with pytest.raises(ValueError):
        library.convert(video.id, "mp4")          # 源已经是 mp4
    with pytest.raises(ValueError):
        library.convert(9999, "mkv")              # 不存在


def test_library_convert_needs_ffmpeg(library: VideoLibrary, tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_video_file(tmp_path / "a.mp4")
    video = library.import_paths([str(path)])[0]
    import modu_workbench.core.video.library as library_module

    monkeypatch.setattr(library_module, "find_ffmpeg", lambda: None)
    with pytest.raises(ValueError) as exc:
        library.convert(video.id, "mkv")
    assert "ffmpeg" in str(exc.value)


# --------------------------------------------------------------------------- 下载


class _FakeStream:
    def __init__(self, chunks: list[bytes], url: str = "https://cdn/movie.mp4",
                 content_type: str = "video/mp4", total: int | None = None,
                 text: str = ""):
        self._chunks = chunks
        self.url = url
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(total if total is not None else sum(len(c) for c in chunks)),
        }
        # requests.Response 同时提供 .content 与 .text，替身保持同构
        self.content = b"".join(chunks)
        self.text = text or self.content.decode("utf-8", "ignore")
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size: int = 0):  # noqa: ANN001, ARG002
        yield from self._chunks

    def close(self) -> None:
        pass

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *args) -> None:  # noqa: ANN002
        return None


def ts_payload(size: int = 200_000) -> list[bytes]:
    return [b"\x47" + b"\x00" * (size - 1)]


def test_looks_like_video_accepts_containers(tmp_path: Path) -> None:
    ts = tmp_path / "a.ts"
    ts.write_bytes(b"\x47" + b"\x00" * 200_000)
    assert looks_like_video(ts, "video/mp2t")

    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 200_000)
    assert looks_like_video(mp4, "video/mp4")

    assert not looks_like_video(ts, "text/html")
    tiny = tmp_path / "tiny.mp4"
    tiny.write_bytes(b"ftyp" + b"a" * 10)
    assert not looks_like_video(tiny)


def test_download_episode_direct_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from modu_workbench.core.video.downloader import download_episode

    monkeypatch.setattr(
        "modu_workbench.core.video.downloader.requests.get",
        lambda *a, **k: _FakeStream(ts_payload(), url="https://cdn/movie.mp4"),
    )
    remote = RemoteVideo(source="url", remote_id="https://cdn/movie.mp4", title="测试片", year="2024")
    episode = Episode(name="正片", url="https://cdn/movie.mp4", index=0)
    progress: list[tuple[int, int, str]] = []

    path, resolved = download_episode(
        remote, episode, tmp_path,
        on_progress=lambda w, t, m: progress.append((w, t, m)),
    )
    assert path.name == "测试片 (2024) 正片.mp4"
    assert path.stat().st_size == 200_000
    assert resolved.source == "url"
    assert progress and progress[-1][0] == 200_000


def test_download_rejects_html_error_page(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from modu_workbench.core.video.downloader import download_episode
    from modu_workbench.core.video.sources import SourceError as VideoSourceError

    page = b"<html><body>VIP only</body></html>" + b" " * 200_000
    monkeypatch.setattr(
        "modu_workbench.core.video.downloader.requests.get",
        lambda *a, **k: _FakeStream([page], content_type="text/html"),
    )
    remote = RemoteVideo(source="url", remote_id="u", title="坏片")
    episode = Episode(name="正片", url="https://cdn/bad.mp4", index=0)
    with pytest.raises(VideoSourceError) as exc:
        download_episode(remote, episode, tmp_path)
    assert "不是有效视频" in str(exc.value)
    assert not list(tmp_path.glob("*.mp4"))


def test_download_cancel(tmp_path: Path) -> None:
    from modu_workbench.core.video.downloader import download_episode

    cancel = threading.Event()
    cancel.set()
    remote = RemoteVideo(source="url", remote_id="u", title="片")
    episode = Episode(name="正片", url="https://cdn/a.mp4", index=0)
    with pytest.raises(RuntimeError):
        download_episode(remote, episode, tmp_path, cancel=cancel)


def test_download_hls_segments_without_ffmpeg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """没有 ffmpeg 时退化为「抓分片 → 拼接为 .ts」，仍然可用。"""
    from modu_workbench.core.video import downloader as downloader_module
    from modu_workbench.core.video.downloader import download_episode

    playlist = "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:9.0,\nseg-1.ts\n#EXTINF:9.0,\nseg-2.ts\n#EXT-X-ENDLIST\n"
    segment = b"\x47" + b"\x00" * 90_000      # 90001 字节/片

    def fake_get(url, **kwargs):  # noqa: ANN001
        if url.endswith(".m3u8"):
            return _FakeStream([playlist.encode()], url=url,
                               content_type="application/vnd.apple.mpegurl", text=playlist)
        return _FakeStream([segment], url=url, content_type="video/mp2t")

    monkeypatch.setattr(downloader_module, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(downloader_module.requests, "get", fake_get)

    remote = RemoteVideo(source="cms", remote_id="1", title="剧", year="2024")
    episode = Episode(name="第01集", url="https://cdn/hls/index.m3u8", index=0)
    path, _resolved = download_episode(remote, episode, tmp_path)
    assert path.suffix == ".ts"
    assert path.stat().st_size == 2 * len(segment)


def test_ffmpeg_download_command_passes_binary_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """回归：ffmpeg 命令行里 ffmpeg 路径只能出现一次。

    曾经 `args` 以 ffmpeg 路径开头，`_run_ffmpeg_download` 又拼了一次
    （`[ffmpeg, *args, ...]`）→ ffmpeg 把多出来的那个当成**输出文件**，
    报「Error initializing the muxer for ...\\ffmpeg.exe: Invalid argument」，
    所有走 ffmpeg 的 HLS 下载必然失败（用户看到的"下载完成：成功 0，失败 1"）。
    """
    from modu_workbench.core.video import downloader as downloader_module

    calls: list[list[str]] = []

    class _FakeProcess:
        def __init__(self, command, **_kwargs):  # noqa: ANN001
            calls.append(list(command))
            self.returncode = 0
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")

        def poll(self):  # noqa: ANN201
            return 0

        def wait(self):  # noqa: ANN201
            return 0

        def kill(self):  # noqa: ANN201
            return None

    monkeypatch.setattr(downloader_module.subprocess, "Popen", _FakeProcess)

    ffmpeg = r"C:\app\_internal\tools\ffmpeg\ffmpeg.exe"
    target = tmp_path / "影片.mp4"
    downloader_module._ffmpeg_download(
        ffmpeg, "https://cdn/a.mp4", target, headers={}, on_progress=None, cancel=None
    )

    assert calls, "应当调用了一次 ffmpeg"
    command = calls[0]
    assert command[0] == ffmpeg
    assert command.count(ffmpeg) == 1, "ffmpeg 路径重复出现会被 ffmpeg 当成输出文件"
    assert command[-1] == str(target), "输出文件必须是最后一个参数"
    assert command.index("-progress") < command.index("-i"), "全局进度参数应在输入之前"
    assert not target.exists(), "这里只是记录命令，不应真的产出文件"


def test_find_higher_quality_picks_the_sharpest_other_source(storage: VideoStorage, tmp_path: Path) -> None:
    """换源找高清：去其他源找同一部片，挑分辨率最高的线路。

    背景（实测）：cms_360 的《流浪地球》主清单只有 1280x538 / 706 kbps，
    用户看到的就是「只有 540P、很模糊」。这里锁住「能自动找到更清晰的源」这条路径。
    """
    from modu_workbench.core.video.library import VideoLibrary
    from modu_workbench.core.video.sources import SourceInfo, VideoRegistry, VideoSource

    master_1080 = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\n"
        "1080/index.m3u8\n"
    )

    class _ListHttp:
        def __init__(self, text: str) -> None:
            self.text = text

        def get_text(self, url, **_kwargs):  # noqa: ANN001
            return self.text

    def _source(key: str, label: str, playlist: str, url: str):  # noqa: ANN202
        class _Source(VideoSource):
            info = SourceInfo(key=key, label=label, note="test")

            def search(self, keyword, kind="all", limit=30, page=1):  # noqa: ANN001, ANN003
                return [RemoteVideo(source=key, remote_id="1", title=keyword, year="2019",
                                    episodes=[Episode(name="正片", url=url, index=0)])]

            def play_url(self, video, episode, quality=None):  # noqa: ANN001, ANN003
                return episode.url

        return _Source(http=_ListHttp(playlist), key=key)

    low = _source("low", "低画质源", "#EXTM3U\n", "https://low/540.m3u8")
    hd = _source("hd", "高清源", master_1080, "https://hd/index.m3u8")
    video = RemoteVideo(source="low", remote_id="1", title="流浪地球", year="2019",
                        episodes=[Episode(name="正片", url="https://low/540.m3u8", index=0)])

    library = VideoLibrary(storage, tmp_path / "videos", registry=VideoRegistry([low, hd]))
    candidate = library.find_higher_quality(video, video.episodes[0], current_height=538)

    assert candidate is not None, "应当能从另一个源找到 1080P"
    assert candidate.source_key == "hd"
    assert candidate.height == 1080
    assert candidate.bandwidth == 5_000_000
    assert candidate.episode.name == "正片"


def test_find_higher_quality_returns_none_when_nothing_is_sharper(
    storage: VideoStorage, tmp_path: Path
) -> None:
    """其他源都不比当前清晰时返回 None —— 不要为了「有反馈」切到更差的源。"""
    from modu_workbench.core.video.library import VideoLibrary
    from modu_workbench.core.video.sources import SourceInfo, VideoRegistry, VideoSource

    master_720 = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720\n"
        "720/index.m3u8\n"
    )

    class _ListHttp:
        def get_text(self, url, **_kwargs):  # noqa: ANN001
            return master_720

    class _Other(VideoSource):
        info = SourceInfo(key="other", label="其他源", note="test")

        def search(self, keyword, kind="all", limit=30, page=1):  # noqa: ANN001, ANN003
            return [RemoteVideo(source="other", remote_id="2", title=keyword, year="2019",
                                episodes=[Episode(name="正片", url="https://other/index.m3u8", index=0)])]

        def play_url(self, video, episode, quality=None):  # noqa: ANN001, ANN003
            return episode.url

    video = RemoteVideo(source="low", remote_id="1", title="流浪地球", year="2019",
                        episodes=[Episode(name="正片", url="https://low/540.m3u8", index=0)])
    registry = VideoRegistry([_Other(http=_ListHttp(), key="other")])
    library = VideoLibrary(storage, tmp_path / "videos", registry=registry)

    assert library.find_higher_quality(video, video.episodes[0], current_height=1080) is None
    assert library.find_higher_quality(video, video.episodes[0], current_height=400) is not None


def test_best_variant_url_locks_the_sharpest_child_playlist() -> None:
    """主清单 → 最高清晰度子清单：不让播放器/ffmpeg 自己挑（挑错就是"有高清却很糊"）。"""
    from modu_workbench.core.video.hls import best_variant_url

    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=706000,RESOLUTION=1280x538\n540/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\n1080/index.m3u8\n"
    )

    class _Http:
        def get_text(self, url, **_kwargs):  # noqa: ANN001
            return master

    assert best_variant_url("https://cdn/index.m3u8", http=_Http()) == "https://cdn/1080/index.m3u8"
    # 单清晰度/探测不可用时原样返回，不能让"锦上添花"影响播放
    assert best_variant_url("https://cdn/a.mp4") == "https://cdn/a.mp4"


def test_resolve_playback_upgrades_master_playlist_to_best_variant(
    monkeypatch: pytest.MonkeyPatch, storage: VideoStorage, tmp_path: Path
) -> None:
    """播放解析：源给的是主清单时，锁定到最高清晰度并标出画质标签。"""
    from modu_workbench.core.video import library as library_module
    from modu_workbench.core.video.library import VideoLibrary
    from modu_workbench.core.video.sources import SourceInfo, VideoRegistry, VideoSource

    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=706000,RESOLUTION=1280x538\n540/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\n1080/index.m3u8\n"
    )

    class _Http:
        def get_text(self, url, **_kwargs):  # noqa: ANN001
            return master

    class _Source(VideoSource):
        info = SourceInfo(key="one", label="单源", note="test")

        def search(self, keyword, kind="all", limit=30, page=1):  # noqa: ANN001, ANN003
            return []

        def play_url(self, video, episode, quality=None):  # noqa: ANN001, ANN003
            return episode.url

    monkeypatch.setattr(library_module, "_probe_client", lambda provider: _Http())

    remote = RemoteVideo(source="one", remote_id="1", title="片", year="2024",
                         episodes=[Episode(name="正片", url="https://cdn/index.m3u8", index=0)])
    registry = VideoRegistry([_Source(key="one")])
    library = VideoLibrary(storage, tmp_path / "videos", registry=registry)

    resolved, _video = library.resolve_playback(remote, remote.episodes[0])
    assert resolved.url == "https://cdn/1080/index.m3u8"
    assert resolved.quality is not None and resolved.quality.label == "1080P"


def test_download_hls_rejects_unsupported_encryption(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """SAMPLE-AES 之类无法自行解密的流，要明确报错而不是产出坏文件。"""
    from modu_workbench.core.video import downloader as downloader_module
    from modu_workbench.core.video.downloader import download_episode
    from modu_workbench.core.video.sources import SourceError as VideoSourceError

    playlist = ('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="k.bin"\n'
                "#EXTINF:9.0,\nseg-1.ts\n#EXT-X-ENDLIST\n")

    monkeypatch.setattr(downloader_module, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(
        downloader_module.requests, "get",
        lambda url, **k: _FakeStream([playlist.encode()], url=url,
                                     content_type="application/vnd.apple.mpegurl",
                                     text=playlist),
    )
    remote = RemoteVideo(source="cms", remote_id="1", title="加密剧")
    episode = Episode(name="第01集", url="https://cdn/enc/index.m3u8", index=0)
    with pytest.raises(VideoSourceError) as exc:
        download_episode(remote, episode, tmp_path)
    assert "SAMPLE-AES" in str(exc.value) or "ffmpeg" in str(exc.value)


def test_download_hls_decrypts_aes128_without_ffmpeg(monkeypatch: pytest.MonkeyPatch,
                                                    tmp_path: Path) -> None:
    """AES-128 加密流应当能自行解密下载（不依赖 ffmpeg）。"""
    import binascii

    from helpers.aes_encrypt_ref import aes128_cbc_encrypt_ref

    from modu_workbench.core.video import downloader as downloader_module
    from modu_workbench.core.video.aes import aes128_cbc_decrypt
    from modu_workbench.core.video.downloader import download_episode

    key = binascii.unhexlify("2b7e151628aed2a6abf7158809cf4f3c")
    iv = b"\x00" * 16                      # 未给 IV 时 HLS 用分片序号(0) 作为 IV
    # 伪造一段真实分片明文（TS 同步字节开头，长度超过最小校验阈值）
    plain = b"\x47" + bytes((i * 7 + 11) % 256 for i in range(200_000))
    assert aes128_cbc_decrypt  # 保证导入被使用（参考实现与产品实现相互独立）
    cipher = aes128_cbc_encrypt_ref(plain, key, iv)
    assert len(cipher) % 16 == 0

    playlist = (
        "#EXTM3U\n"
        f'#EXT-X-KEY:METHOD=AES-128,URI="https://cdn/key.bin"\n'
        "#EXTINF:9.0,\nseg-1.ts\n#EXT-X-ENDLIST\n"
    )

    def fake_get(url, **kwargs):  # noqa: ANN001
        if url.endswith(".m3u8"):
            return _FakeStream([playlist.encode()], url=url,
                               content_type="application/vnd.apple.mpegurl", text=playlist)
        if url.endswith("key.bin"):
            return _FakeStream([key], url=url, content_type="application/octet-stream")
        return _FakeStream([cipher], url=url, content_type="video/mp2t")

    monkeypatch.setattr(downloader_module, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(downloader_module.requests, "get", fake_get)

    remote = RemoteVideo(source="cms", remote_id="1", title="加密剧", year="2024")
    episode = Episode(name="第01集", url="https://cdn/enc/index.m3u8", index=0)
    path, _resolved = download_episode(remote, episode, tmp_path)

    assert path.name == "加密剧 (2024) 第01集.ts"
    # 关键断言：下载到的就是「解密后的明文」，与原始分片逐字节一致
    assert path.read_bytes() == plain


def test_download_many_isolates_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from modu_workbench.core.video import downloader as downloader_module
    from modu_workbench.core.video.downloader import download_many

    def fake_get(url, **kwargs):  # noqa: ANN001
        if "bad" in url:
            raise requests.ConnectionError("getaddrinfo failed")
        return _FakeStream(ts_payload(), url=url)

    monkeypatch.setattr(downloader_module.requests, "get", fake_get)
    monkeypatch.setattr(downloader_module.time, "sleep", lambda _s: None)

    good = (RemoteVideo(source="url", remote_id="g", title="好片"),
            Episode(name="正片", url="https://cdn/good.mp4", index=0), None)
    bad = (RemoteVideo(source="url", remote_id="b", title="坏片"),
           Episode(name="正片", url="https://cdn/bad.mp4", index=0), None)

    results = download_many([good, bad], tmp_path)
    assert len(results) == 2
    assert results[0].ok is True and results[0].path
    assert results[1].ok is False and results[1].message


def test_download_result_labels() -> None:
    from modu_workbench.core.video.downloader import DownloadResult

    result = DownloadResult(
        video=RemoteVideo(source="cms", remote_id="1", title="剧"),
        episode=Episode(name="第01集", url="u", index=0),
        quality=Quality(label="1080P", url="u"),
        ok=True,
    )
    assert result.title == "剧"
    assert result.episode_label == "第01集"
    assert result.quality_label == "1080P"


def test_unique_path_avoids_overwrite(tmp_path: Path) -> None:
    from modu_workbench.core.video.downloader import unique_path

    target = unique_path(tmp_path, "movie.mp4")
    assert target.name == "movie.mp4"
    target.write_bytes(b"1")
    assert unique_path(tmp_path, "movie.mp4").name == "movie (2).mp4"


# --------------------------------------------------------------------------- 板块集成


def test_video_board_registered() -> None:
    from modu_workbench.app.registry import ACTIVE_BOARDS, get_board

    spec = get_board("video")
    assert spec is not None
    assert spec.title == "墨软影视"
    assert spec.icon
    assert "video" in [board.key for board in ACTIVE_BOARDS]
    # 前四个板块顺序固定；后续板块（图库等）追加在后面
    assert [board.key for board in ACTIVE_BOARDS][:4] == ["book", "convert", "music", "video"]


def test_media_server_proxy_helpers() -> None:
    from modu_workbench.services import media_server

    # 已经是本机地址时不再二次代理
    local = media_server.media_url(str(Path("C:/tmp/a.mp4")))
    assert media_server.is_local_url(local)
    assert media_server.stream_url(local) == local
    assert media_server.stream_url("") == ""
    # 远程地址被包装成本机绝对地址（QMediaPlayer / <video> 都不接受相对路径）
    wrapped = media_server.stream_url("https://cdn.example.com/a.mp4", referer="https://site/")
    assert wrapped.startswith("http://127.0.0.1:")
    assert "proxy" in wrapped
    assert "cdn.example.com" in wrapped
    assert "r=https%3A%2F%2Fsite%2F" in wrapped
    hls_wrapped = media_server.hls_url("https://cdn.example.com/a.m3u8", referer="https://site/")
    assert hls_wrapped.startswith("http://127.0.0.1:")
    assert "hls" in hls_wrapped


def test_media_server_rewrites_playlist_uris() -> None:
    """HLS 代理必须把清单内的 URI 改写成继续走本机代理（否则分片拿不到请求头）。"""
    from modu_workbench.services.media_server import _rewrite_playlist  # noqa: PLC2701

    rewritten = _rewrite_playlist(
        MEDIA_PLAYLIST, "https://cdn.example.com/hls/720/index.m3u8",
        "https://site/", "",
    )
    # 注释行保持原样
    assert "#EXT-X-TARGETDURATION:10" in rewritten
    assert "#EXTINF:9.009," in rewritten
    # 相对地址被改写为本机 hls 代理地址（带上 referer）
    assert "seg-1.ts\n" not in rewritten
    assert "/hls/" in rewritten
    assert "cdn.example.com" in rewritten
    # 绝对地址同样被改写（整条原始 URL 不再作为独立行出现）
    assert "https://cdn.example.com/seg-2.ts\n" not in rewritten
    assert "%2Fseg-2.ts" in rewritten


def test_media_server_proxy_url_keeps_file_extension() -> None:
    """代理地址必须以真实扩展名结尾。

    ffmpeg 的 HLS 解复用器按扩展名判断分片类型：形如 `/proxy/?u=...` 的地址
    会被判为不在 allowed_segment_extensions 而直接拒绝（实测报 Invalid data）。
    """
    from modu_workbench.services.media_server import _hls_path, _proxy_path  # noqa: PLC2701

    seg = _hls_path("https://cdn.example.com/a/b/seg-9.ts")
    assert "/seg-9.ts?" in seg, seg
    key = _proxy_path("https://cdn.example.com/keys/key.bin")
    assert "/key.bin?" in key, key
    # 取不到扩展名时不应编造（退回没有文件名的形式）
    plain = _hls_path("https://cdn.example.com/no-extension")
    assert plain.startswith("/") and "?" in plain


def test_media_server_proxy_response_is_length_delimited() -> None:
    """代理响应必须能界定长度 —— 这是「播放 fragLoadError / 下载卡 0 字节」的根因。

    真实源站常用 chunked 且不带 Content-Length；若本机代理原样转发（既无
    Content-Length 也无 Transfer-Encoding），客户端永远等不到响应结束。
    """
    import http.server
    import socketserver
    import threading
    import urllib.request

    from modu_workbench.services import media_server

    payload = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nseg.ts\n#EXT-X-ENDLIST\n" * 200

    class Upstream(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"          # 故意不给 Content-Length（旧式连接关闭定界）

        def do_GET(self) -> None:  # noqa: N802
            body = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:  # noqa: ANN002
            return

    upstream = socketserver.TCPServer(("127.0.0.1", 0), Upstream)
    port = upstream.server_address[1]
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    try:
        target = f"http://127.0.0.1:{port}/list.m3u8"
        proxied = media_server.stream_url(target)
        req = urllib.request.Request(proxied, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            length = resp.headers.get("Content-Length")
        # 关键：必须显式给出 Content-Length，且与正文一致
        assert length is not None, "代理响应缺少 Content-Length（客户端会一直等）"
        assert int(length) == len(body)
        # 内容为清单时应被改写（分片地址指向本机代理）
        text = body.decode("utf-8")
        assert "#EXT-X-ENDLIST" in text
        seg_lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
        assert seg_lines and seg_lines[0].startswith("http://127.0.0.1:")
    finally:
        upstream.shutdown()
        upstream.server_close()


def test_media_server_hls_playlist_rewritten_via_proxy() -> None:
    """经 /proxy/ 取到的内容若是清单，也必须改写内部地址。

    真实源站的主清单常指向子清单；子清单经 /proxy/ 返回时若原样透传，
    相对路径会以「本机代理」为基准解析，最终 404（实测 ffmpeg 报
    `Failed to open segment 0`）。
    """
    from modu_workbench.services.media_server import _rewrite_playlist  # noqa: PLC2701

    sub = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\n/20230718/abc/hls/a1.ts\n#EXT-X-ENDLIST\n"
    rewritten = _rewrite_playlist(sub, "https://cdn.example.com/20230718/abc/hls/index.m3u8",
                                  "https://site/", "")
    seg_lines = [ln for ln in rewritten.splitlines()
                 if ln.strip() and not ln.startswith("#")]
    assert seg_lines, "改写后应当仍有分片行"
    # 相对地址必须变成绝对的本机代理地址（否则会以代理为基准解析成 404）
    assert seg_lines[0].startswith("http://127.0.0.1:")
    assert "/a1.ts?" in seg_lines[0]
    assert "#EXT-X-ENDLIST" in rewritten


def test_media_server_rewrites_key_uri_for_aes128() -> None:
    """AES-128 的密钥 URI 也要走本机代理，否则 hls.js 报 keyLoadError。"""
    from modu_workbench.services.media_server import _rewrite_playlist  # noqa: PLC2701

    playlist = (
        "#EXTM3U\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x00\n'
        "#EXTINF:9.0,\nseg-1.ts\n#EXT-X-ENDLIST\n"
    )
    rewritten = _rewrite_playlist(playlist, "https://cdn.example.com/hls/index.m3u8",
                                  "https://site/", "")
    key_line = next(line for line in rewritten.splitlines() if line.startswith("#EXT-X-KEY"))
    assert 'METHOD=AES-128' in key_line
    assert 'IV=0x00' in key_line                      # 其余属性保持不变
    # 密钥地址被换成本机代理（相对 key.bin 已按清单地址补全）
    assert 'URI="http://127.0.0.1:' in key_line
    assert "cdn.example.com%2Fhls%2Fkey.bin" in key_line
    assert 'URI="key.bin"' not in key_line
