"""在线曲目质量过滤测试：酷我「试听/片段」条目的识别、隐藏、排序与换源。

背景（用户反馈）：酷我音源返回大量 10~40 秒的片段/铃声/串烧条目，点下载还会提示
「当前歌曲只能在酷我手机端播放」。这里锁住三件事：
1. 能识别这类条目（纯本地规则：音源标注 / 时长 / 标题关键词）；
2. 完整曲目排在前面，界面默认把试听条目隐藏起来并说明隐藏了多少；
3. 下载试听条目时不再白下 10 秒，而是直接换源找完整版。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from modu_workbench.core.music import (
    MusicRegistry,
    RemoteTrack,
    SourceError,
    download_track,
)
from modu_workbench.core.music.sources import quality
from modu_workbench.core.music.sources.base import KIND_FULL, KIND_PREVIEW, MusicSource, SourceInfo
from modu_workbench.core.music.sources.providers.kuwo import KuwoSource

# --------------------------------------------------------------------------- 判定规则


@pytest.fixture(autouse=True)
def reset_threshold(monkeypatch: pytest.MonkeyPatch):
    """每个用例都在默认阈值下跑，避免互相影响。"""
    monkeypatch.delenv("MODU_MIN_FULL_SECONDS", raising=False)
    quality.set_min_full_seconds(quality.DEFAULT_MIN_FULL_SECONDS)
    yield
    quality.set_min_full_seconds(quality.DEFAULT_MIN_FULL_SECONDS)


def track(seconds: float = 200, **kwargs) -> RemoteTrack:  # noqa: ANN003
    data = {"source": "kuwo", "remote_id": "MUSIC_1", "title": "夜曲"}
    data.update(kwargs)
    data.setdefault("duration_ms", int(seconds * 1000))
    return RemoteTrack(**data)


def test_short_track_is_preview() -> None:
    reason = quality.preview_reason(track(22))
    assert "22" in reason and "片段" in reason
    assert quality.is_preview(track(22)) is True


def test_full_length_track_is_not_preview() -> None:
    assert quality.preview_reason(track(200)) == ""
    assert quality.is_preview(track(200)) is False
    # 边界：正好等于阈值算完整
    assert quality.is_preview(track(quality.min_full_seconds())) is False


def test_title_hint_marks_preview() -> None:
    """时长够长但标题写着"片段/铃声"的也要标出来。"""
    reason = quality.preview_reason(track(200, title="稻香（铃声版）"))
    assert "铃声" in reason
    assert quality.is_preview(track(180, title="某某现场片段")) is True
    # "Demo / 伴奏 / 现场"是正常版本，不该被当成片段
    assert quality.is_preview(track(112, title="淘汰 (Demo版)")) is False
    assert quality.is_preview(track(206, title="明年今日 (升调版伴奏)")) is False


def test_extra_flag_from_source() -> None:
    """音源自己标注的 preview（例如 iTunes 试听）优先。"""
    item = track(200, extra={"preview": True})
    assert quality.preview_reason(item) == "音源标注为试听片段"


def test_preview_source_kind_marks_short_results() -> None:
    """试听型音源的短结果：原因写成"该音源只提供试听片段"（而不是"时长仅 XX 秒"）。"""
    items = [track(30, remote_id="1"), track(30, remote_id="2")]
    quality.annotate_many(items, source_kind=KIND_PREVIEW)
    assert all(item.preview for item in items)
    assert quality.preview_reason(items[0]) == "该音源只提供试听片段"
    # 该音源若真给出完整时长，就不该硬标成试听
    long_item = track(200, remote_id="3")
    quality.annotate(long_item, source_kind=KIND_PREVIEW)
    assert long_item.preview is False


def test_threshold_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    assert quality.set_min_full_seconds(120) == 120
    assert quality.is_preview(track(100)) is True
    assert quality.is_preview(track(150)) is False
    # 环境变量优先级最高（便于临时放宽）
    monkeypatch.setenv("MODU_MIN_FULL_SECONDS", "20")
    assert quality.min_full_seconds() == 20
    assert quality.is_preview(track(30)) is False
    # 非法值被夹到安全区间
    assert quality.set_min_full_seconds(1) == 15
    assert quality.set_min_full_seconds(99999) == 600


def test_sort_full_first_is_stable() -> None:
    short_a = track(20, remote_id="a", title="A")
    long_a = track(200, remote_id="b", title="B")
    short_b = track(30, remote_id="c", title="C")
    long_b = track(210, remote_id="d", title="D")

    ordered = quality.sort_full_first([short_a, long_a, short_b, long_b])
    assert [item.remote_id for item in ordered] == ["b", "d", "a", "c"]   # 完整在前，组内保序
    full, previews = quality.split_by_quality([short_a, long_a, short_b, long_b])
    assert [item.remote_id for item in full] == ["b", "d"]
    assert [item.remote_id for item in previews] == ["a", "c"]


# --------------------------------------------------------------------------- 酷我音源


def kuwo_payload(*items: tuple[str, int]) -> str:
    """按酷我 search.kuwo.cn 的 jsonp 风格生成搜索结果。"""
    entries = [
        f"{{'MUSICRID':'MUSIC_{rid}','NAME':'{name}','ARTIST':'周杰伦',"
        f"'ALBUM':'','DURATION':'{seconds}','FORMATS':'MP3128','PAY':'0',"
        f"'web_albumpic_short':'120/86/x.jpg'}}"
        for rid, name, seconds in items
    ]
    return "(" + "{'abslist':[" + ",".join(entries) + "]}" + ")"


class FakeResponse:
    def __init__(self, text: str = "", payload=None, url: str = "https://x/"):
        self.text = text
        self._payload = payload
        self.url = url
        self.headers = {"Content-Type": "text/plain"}
        self.status_code = 200

    def json(self):  # noqa: ANN201
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakeHttp:
    def __init__(self, routes: dict, default: FakeResponse | None = None):
        self.routes = routes
        self.default = default
        self.calls: list[tuple[str, dict]] = []

    def _route(self, url: str, kwargs: dict) -> FakeResponse:
        self.calls.append((url, kwargs))
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        if self.default is not None:
            return self.default
        raise SourceError(f"未预设的请求：{url}")

    def get(self, url: str, **kwargs) -> FakeResponse:
        return self._route(url, kwargs)

    def post(self, url: str, **kwargs) -> FakeResponse:
        return self._route(url, kwargs)


def test_kuwo_marks_and_orders_previews() -> None:
    """搜索结果里 10~40 秒的条目被标注，且完整曲目排在前面。"""
    text = kuwo_payload(
        ("101", "夜曲", 22),          # 片段，排在前面也应被挪到后面
        ("102", "稻香", 187),         # 完整
        ("103", "演员", 30),          # 片段
        ("104", "晴天", 269),         # 完整
    )
    source = KuwoSource(http=FakeHttp({"search.kuwo.cn": FakeResponse(text=text)}))
    tracks = source.search("周杰伦", limit=10)

    assert [t.remote_id for t in tracks] == ["MUSIC_102", "MUSIC_104", "MUSIC_101", "MUSIC_103"]
    by_id = {t.remote_id: t for t in tracks}
    assert by_id["MUSIC_101"].preview is True
    assert "22" in by_id["MUSIC_101"].preview_reason
    assert by_id["MUSIC_102"].preview is False
    assert by_id["MUSIC_102"].preview_reason == ""


def test_kuwo_surfaces_restricted_message() -> None:
    """酷我返回「只能在手机端播放」时，把原文带给用户（而不是含糊的"没有地址"）。"""
    response = FakeResponse(text="当前歌曲只能在酷我手机端播放")
    source = KuwoSource(http=FakeHttp({"antiserver.kuwo.cn": response}))
    with pytest.raises(SourceError) as info:
        source.download_url(track(200, remote_id="MUSIC_1"))
    message = str(info.value)
    assert "手机" in message and "酷我" in message


def test_kuwo_parses_wrapped_url() -> None:
    """接口换成 JSON 包装也要能取到直链。"""
    response = FakeResponse(text='{"data":{"url":"https://kw-bj.kuwo.cn/a/b/song.mp3"}}')
    source = KuwoSource(http=FakeHttp({"antiserver.kuwo.cn": response}))
    assert source.download_url(track(200, remote_id="MUSIC_1")).endswith("song.mp3")


# --------------------------------------------------------------------------- 注册表


class ListSource(MusicSource):
    """测试用音源：返回固定列表，不联网。"""

    def __init__(self, key: str, label: str, kind: str, tracks: list[RemoteTrack]):
        self.info = SourceInfo(key=key, label=label, note="", kind=kind)
        self._tracks = tracks
        super().__init__(http=FakeHttp({}))

    def search(self, keyword: str, kind: str = "song", limit: int = 30):  # noqa: ANN201
        return list(self._tracks)

    def download_url(self, track: RemoteTrack) -> str:
        return "https://example.com/a.mp3"


def test_search_all_marks_preview_sources_and_sorts() -> None:
    """跨音源搜索：试听型音源整体标注，完整曲目统一排在前面。"""
    full_source = ListSource("kuwo", "酷我音乐", KIND_FULL, [
        RemoteTrack(source="kuwo", remote_id="s1", title="片段版", duration_ms=20_000),
        RemoteTrack(source="kuwo", remote_id="s2", title="完整版", duration_ms=200_000),
    ])
    preview_source = ListSource("itunes", "iTunes 试听", KIND_PREVIEW, [
        RemoteTrack(source="itunes", remote_id="p1", title="试听片段", duration_ms=30_000),
    ])
    registry = MusicRegistry([full_source, preview_source])
    tracks, errors = registry.search_all("夜曲", limit=10)

    assert errors == []
    ids = [item.remote_id for item in tracks]
    assert ids[0] == "s2"                     # 完整曲目第一
    assert ids[-1] == "p1"                    # 试听音源垫底
    assert all(item.preview for item in tracks if item.source == "itunes")
    assert next(item for item in tracks if item.remote_id == "s1").preview is True


# --------------------------------------------------------------------------- 下载


class FakeRegistry:
    """替身注册表：第一次给酷我，换源后给网易云。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def resolve(self, remote, *, allow_cross_source: bool = True, exclude=(), **kwargs):  # noqa: ANN001, ANN003, ARG002
        from modu_workbench.core.music.sources import ResolvedAudio

        self.calls.append(tuple(exclude))
        if not exclude:
            return ResolvedAudio(url="https://kuwo.example/1.mp3", source="kuwo", track=remote)
        return ResolvedAudio(url="https://netease.example/1.mp3", source="netease",
                             track=remote, switched_from="kuwo", note="酷我不可用，已改用网易云")

    def get(self, key: str):  # noqa: ANN201
        return None


def test_download_refuses_preview_without_network(monkeypatch: pytest.MonkeyPatch,
                                                  tmp_path: Path) -> None:
    """片段条目在"下载"这一步就拒绝，且**不发任何网络请求**。"""
    from modu_workbench.core.music import downloader
    from modu_workbench.core.music.sources import ResolvedAudio

    called: list[str] = []

    def fake_get(url, **kwargs):  # noqa: ANN001, ANN003, ARG001
        called.append(url)
        raise AssertionError("试听条目不应该发起下载请求")

    monkeypatch.setattr(downloader.requests, "get", fake_get)
    resolved = ResolvedAudio(url="https://kuwo.example/1.mp3", source="kuwo",
                             track=track(22))
    with pytest.raises(SourceError) as info:
        downloader._download_resolved(resolved, tmp_path, track=track(22),
                                      on_progress=None, cancel=None,
                                      save_cover=False, save_lyrics=False, timeout=5)
    assert "片段" in str(info.value)
    assert called == []


def test_download_track_switches_to_full_version(monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path) -> None:
    """下载片段时自动换源拿完整版（用户要的"完整下载"）。"""
    from modu_workbench.core.music import downloader

    attempts: list[str] = []

    def fake_download(resolved, dest_dir, **kwargs):  # noqa: ANN001, ANN003, ARG001
        attempts.append(resolved.source)
        if resolved.source == "kuwo":
            raise SourceError("时长仅 22 秒（试听/片段条目）；这不是完整曲目，已尝试换源获取完整版")
        target = Path(dest_dir) / "夜曲.mp3"
        target.write_bytes(b"x" * 2048)
        return target

    monkeypatch.setattr(downloader, "_download_resolved", fake_download)
    registry = FakeRegistry()
    path, resolved = download_track(track(22), tmp_path, registry=registry,
                                    allow_cross_source=True)

    assert attempts == ["kuwo", "netease"]
    assert resolved.source == "netease" and resolved.switched is True
    assert path.name == "夜曲.mp3"
    assert registry.calls[-1] == ("kuwo",)


def test_download_track_reports_reason_when_cross_source_disabled(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from modu_workbench.core.music import downloader

    def fake_download(resolved, dest_dir, **kwargs):  # noqa: ANN001, ANN003, ARG001
        raise SourceError("时长仅 22 秒（试听/片段条目）；这不是完整曲目，已尝试换源获取完整版")

    monkeypatch.setattr(downloader, "_download_resolved", fake_download)
    with pytest.raises(SourceError) as info:
        download_track(track(22), tmp_path, registry=FakeRegistry(), allow_cross_source=False)
    assert "片段" in str(info.value)


# --------------------------------------------------------------------------- 界面


@pytest.fixture()
def music_env(tmp_path: Path, qapp):  # noqa: ANN001, ARG001
    from modu_workbench.boards.music.search import MusicSearchPage
    from modu_workbench.core.music import MusicLibrary, MusicStorage
    from modu_workbench.ui_kit.toast import Toaster
    from PySide6.QtWidgets import QWidget

    storage = MusicStorage(str(tmp_path / "music.db"))
    library = MusicLibrary(storage, tmp_path / "library")
    host = QWidget()
    host.hide()
    page = MusicSearchPage(library, storage, Toaster(host))
    yield page
    page.shutdown()
    page.deleteLater()


def mixed_results() -> list[RemoteTrack]:
    return [
        RemoteTrack(source="kuwo", remote_id="1", title="夜曲", artist="周杰伦",
                    duration_ms=22_000),
        RemoteTrack(source="kuwo", remote_id="2", title="稻香", artist="周杰伦",
                    duration_ms=187_000),
        RemoteTrack(source="kuwo", remote_id="3", title="演员", duration_ms=30_000),
    ]


def test_search_page_hides_previews_by_default(music_env, qapp, monkeypatch) -> None:  # noqa: ANN001
    """默认勾选「隐藏试听/片段」：表格里只剩完整曲目，状态栏说明隐藏了多少。"""
    import modu_workbench.boards.music.search as search_mod

    monkeypatch.setattr(search_mod, "hide_preview_pref", lambda: True)
    page = music_env
    page._hide_preview.setChecked(True)

    page._on_results(mixed_results(), [])
    qapp.processEvents()

    assert page._table.rowCount() == 1
    assert page._table.item(0, 0).text() == "稻香"
    assert [item.title for item in page._hidden_previews] == ["夜曲", "演员"]
    status = page._fallback_status.text()
    assert "共 1 条结果" in status and "已隐藏 2 条试听/片段" in status


def test_search_page_shows_previews_when_unchecked(music_env, qapp, monkeypatch) -> None:  # noqa: ANN001
    """取消勾选后能看到全部结果，试听条目被标黄并在时长列注明。"""
    import modu_workbench.boards.music.search as search_mod

    saved: list[bool] = []
    monkeypatch.setattr(search_mod, "hide_preview_pref", lambda: False)
    monkeypatch.setattr(search_mod, "set_hide_preview_pref", saved.append)
    page = music_env
    page._hide_preview.setChecked(False)

    page._on_results(mixed_results(), [])
    qapp.processEvents()

    assert page._table.rowCount() == 3
    assert page._table.item(0, 3).text().endswith("试听")           # 时长列：00:22 · 试听
    assert page._table.item(0, 0).toolTip()                          # 悬停能看到原因
    assert "其中 2 条为试听/片段" in page._fallback_status.text()
    # 切换勾选会持久化偏好并立刻重排表格
    page._hide_preview.setChecked(True)
    qapp.processEvents()
    assert saved == [False, True]          # 取消勾选时也写了一次
    assert page._table.rowCount() == 1


def test_quality_prefs_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """隐藏开关与最短时长能持久化，并即时推给核心判定。"""
    from PySide6.QtCore import QSettings

    from modu_workbench.boards.music import widgets as widgets_mod

    store = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(widgets_mod, "music_settings", lambda: store)

    assert widgets_mod.hide_preview_pref() is True          # 默认隐藏
    widgets_mod.set_hide_preview_pref(False)
    assert widgets_mod.hide_preview_pref() is False

    widgets_mod.set_min_full_seconds_pref(90)
    assert widgets_mod.min_full_seconds_pref() == 90
    widgets_mod.apply_quality_prefs()
    assert quality.min_full_seconds() == 90
    assert quality.is_preview(track(60)) is True
    assert quality.is_preview(track(120)) is False


def test_should_hide_only_junk_from_full_sources() -> None:
    """默认隐藏只针对"完整音源里混进来的片段"；试听型音源（iTunes）保留。"""
    junk = track(22)
    quality.annotate(junk, source_kind=KIND_FULL)
    assert quality.should_hide(junk) is True

    itunes = track(30, source="itunes", title="晴天")
    quality.annotate(itunes, source_kind=KIND_PREVIEW)
    assert itunes.preview is True              # 仍然标注为试听（界面会标黄）
    assert quality.should_hide(itunes) is False  # 但不隐藏：音源本身就写着"试听"

    full = track(200)
    quality.annotate(full, source_kind=KIND_FULL)
    assert quality.should_hide(full) is False

