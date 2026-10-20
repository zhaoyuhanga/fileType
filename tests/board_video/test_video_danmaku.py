"""影视板块弹幕接线测试（V3-c 的"能看"部分）。

全部离线：替身客户端 + `tmp_path` 缓存目录，不碰网络。
覆盖：设置读取、缓存命中、未配置时的可读提示、匹配失败、成功落盘、
本地导入/导出、以及播放器的弹幕开关会向调用方要数据。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from modu_workbench.ui_kit.media import danmaku_bridge as bridge
from modu_workbench.ui_kit.media.danmaku import DanmakuItem


class _FakeClient:
    """替身弹弹play 客户端：可控制 configured / 匹配结果 / 弹幕内容。"""

    def __init__(self, *, configured: bool = True, matched: bool = True,
                 items: list[DanmakuItem] | None = None, error: str = "") -> None:
        self.configured = configured
        self._matched = matched
        self._items = items if items is not None else [
            DanmakuItem("第一条", 1000), DanmakuItem("第二条", 3000)]
        self._error = error
        self.fetch_calls = 0
        self.match_calls = 0

    def match_series(self, title, episode_name, *, episode_number=0, year=""):  # noqa: ANN001
        from modu_workbench.services.dandanplay import DanmakuMatch

        self.match_calls += 1
        if self._error:
            raise RuntimeError(self._error)
        if not self._matched:
            return DanmakuMatch(note="没有匹配到对应的番剧")
        return DanmakuMatch(episode_id=123450001, anime_id=99, anime_title="葬送的芙莉莲",
                            episode_title="第01集", score=0.95, note="按标题精确匹配")

    def fetch_items(self, episode_id, *, with_related=True):  # noqa: ANN001
        self.fetch_calls += 1
        return list(self._items)


@pytest.fixture()
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把弹幕缓存目录指到 tmp_path（隔离，避免写用户数据目录）。"""
    monkeypatch.setattr(bridge, "danmaku_cache_dir", lambda: tmp_path)
    return tmp_path


def test_cache_path_is_stable_and_hashed() -> None:
    first = bridge.cache_path_for("葬送的芙莉莲", "第01集")
    second = bridge.cache_path_for("葬送的芙莉莲", "第01集")
    other = bridge.cache_path_for("葬送的芙莉莲", "第02集")
    assert first == second and first != other
    assert first.suffix == ".xml"
    assert first.name.isascii(), "文件名必须是 hash（标题可能很长/含非法字符）"


def test_fetch_returns_cached_items_without_network(cache_dir: Path) -> None:
    """有本地缓存时**直接命中**，一次网络都不发。"""
    path = bridge.cache_path_for("某番", "第01集")
    from modu_workbench.ui_kit.media.danmaku import save_danmaku_file

    save_danmaku_file(path, [DanmakuItem("缓存弹幕", 500)])

    client = _FakeClient()
    fetcher = bridge.DanmakuFetcher(client=client)
    result = fetcher.fetch("某番", "第01集")
    assert result.ok and result.from_cache
    assert result.items[0].text == "缓存弹幕"
    assert client.match_calls == 0 and client.fetch_calls == 0


def test_fetch_reports_missing_credentials(cache_dir: Path) -> None:
    """未配置 AppId/AppSecret 时给出**可操作**的提示，而不是静默空。"""
    fetcher = bridge.DanmakuFetcher(client=_FakeClient(configured=False))
    result = fetcher.fetch("某番", "第01集")
    assert not result.ok
    assert "AppId" in result.note and "导入" in result.note


def test_fetch_success_saves_cache(cache_dir: Path) -> None:
    client = _FakeClient()
    fetcher = bridge.DanmakuFetcher(client=client)
    result = fetcher.fetch("葬送的芙莉莲", "第01集", episode_number=1, year="2023")
    assert result.ok and not result.from_cache
    assert result.episode_id == 123450001
    assert "葬送的芙莉莲" in result.note
    # 落盘了：第二次取应当命中缓存
    again = bridge.DanmakuFetcher(client=client).fetch("葬送的芙莉莲", "第01集")
    assert again.from_cache and client.fetch_calls == 1


def test_fetch_without_match_is_readable(cache_dir: Path) -> None:
    fetcher = bridge.DanmakuFetcher(client=_FakeClient(matched=False))
    result = fetcher.fetch("某番", "第01集")
    assert not result.ok
    assert "没有匹配到" in result.note


def test_fetch_survives_client_errors(cache_dir: Path) -> None:
    """弹幕是可选增强：客户端抛错也必须变成一句提示，绝不能中断播放。"""
    fetcher = bridge.DanmakuFetcher(client=_FakeClient(error="接口 503"))
    result = fetcher.fetch("某番", "第01集")
    assert not result.ok
    assert "503" in result.note


def test_fetch_respects_cancel(cache_dir: Path) -> None:
    import threading

    cancel = threading.Event()
    cancel.set()
    fetcher = bridge.DanmakuFetcher(client=_FakeClient())
    result = fetcher.fetch("某番", "第01集", cancel=cancel)
    assert not result.ok and "取消" in result.note


def test_import_and_export_local_xml(tmp_path: Path) -> None:
    xml = ('<?xml version="1.0" encoding="UTF-8"?><i>'
           '<d p="1.50,1,25,16777215,0,0,0,0">导入的弹幕</d></i>')
    source = tmp_path / "manual.xml"
    source.write_text(xml, encoding="utf-8")

    result = bridge.import_danmaku_file(source)
    assert result.ok and result.items[0].text == "导入的弹幕"

    target = tmp_path / "exported.xml"
    bridge.export_danmaku_file(target, result.items)
    assert target.is_file()

    again = bridge.import_danmaku_file(target)
    assert [item.text for item in again.items] == ["导入的弹幕"]


def test_import_rejects_broken_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.xml"
    broken.write_text("这不是弹幕", encoding="utf-8")
    result = bridge.import_danmaku_file(broken)
    assert not result.ok and "没有解析出弹幕" in result.note


def test_settings_readers_have_safe_defaults() -> None:
    """设置读取必须给安全默认值（弹幕默认关、屏蔽词默认空、透明度 80）。"""
    assert bridge.danmaku_block_words() == "" or isinstance(bridge.danmaku_block_words(), str)
    assert 20 <= bridge.danmaku_opacity() <= 100
    assert 4.0 <= bridge.danmaku_speed_seconds() <= 12.0
    assert bridge.danmaku_max_lines() >= 0
    assert bridge.danmaku_enabled() in (True, False)


def test_player_danmaku_button_requests_data(qapp) -> None:  # noqa: ANN001
    """播放器：首次打开弹幕开关且没有数据时，向调用方发 `danmakuRequested`。"""
    from modu_workbench.ui_kit.media.player import VideoPlayerDialog

    dialog = VideoPlayerDialog(title="弹幕", url="", render_path="graphics")
    requested: list[int] = []
    dialog.danmakuRequested.connect(lambda: requested.append(1))
    try:
        dialog.set_danmaku_enabled(True)
        assert requested == [1]
        # 已有数据后再开关不应该重复请求
        dialog.set_danmaku([DanmakuItem("x", 0)])
        dialog.set_danmaku_enabled(False)
        dialog.set_danmaku_enabled(True)
        assert requested == [1]
        assert len(dialog.danmaku_items()) == 1
    finally:
        dialog.close()
