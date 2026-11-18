"""数据源体检：**坏在哪一步**（搜索 / 详情 / 取流 / 首片）+ 连续失败自动停用。

## 为什么需要

用户看到的都是同一句「下载失败」，但原因完全不同：

- 搜索接口挂了 → 换源或改地址；
- 详情能搜到、取流返回 403 → 防盗链 / 线路失效；
- 取流成功、首片拉不下来 → CDN 限速 / 分片 403。

没有阶段信息时，用户只能一个个源去点，最后得出"全部失效"的错觉。
这里的契约：

1. `probe_source` 必须把失败归到具体阶段（`failed_stage`），成功的阶段记为 ✓；
2. **连续 3 次**体检失败才自动停用（一次网络抖动不该关源），成功即恢复；
3. 自动停用名单与健康度**跨重启保留**（否则红标过一晚就消失）；
4. 用户手动勾选「启用」或调用 `restore_source` 即恢复（用户意图不丢）。

全部离线：HTTP 用 `_FakeHttp` 替身，不发真实请求。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.video import Episode, RemoteVideo
from modu_workbench.core.video.sources import (
    SourceError,
    SourceInfo,
    VideoRegistry,
)
from modu_workbench.core.video.sources.probe import probe_source

MEDIA_PLAYLIST = (
    "#EXTM3U\n"
    "#EXT-X-VERSION:3\n"
    "#EXT-X-TARGETDURATION:9\n"
    "#EXTINF:9.0,\n"
    "seg0.ts\n"
)


class _Response:
    def __init__(self, content: bytes = b"", text: str = "") -> None:
        self.content = content
        self.text = text or content.decode("utf-8", "ignore")


class _FakeHttp:
    """最小 HTTP 替身：只实现 probe 用到的三个方法。"""

    def __init__(self, *, playlist: str = MEDIA_PLAYLIST, segment_error: str = "") -> None:
        self._playlist = playlist
        self._segment_error = segment_error

    def request(self, method: str, url: str, headers=None, timeout=None):  # noqa: ANN001
        return _Response()

    def get_text(self, url: str, headers=None, timeout=None):  # noqa: ANN001
        return self._playlist

    def get(self, url: str, headers=None, timeout=None):  # noqa: ANN001
        if self._segment_error:
            raise RuntimeError(self._segment_error)
        return _Response(content=b"x" * 262144)


class _Provider:
    """桩源：每个阶段都可以单独注入故障。"""

    def __init__(
        self,
        *,
        key: str = "p1",
        search_error: str = "",
        detail_error: str = "",
        play_error: str = "",
        with_episodes: bool = True,
        segment_error: str = "",
    ) -> None:
        self.info = SourceInfo(key=key, label="桩源", note="test")
        self.http = _FakeHttp(segment_error=segment_error)
        self._search_error = search_error
        self._detail_error = detail_error
        self._play_error = play_error
        self._with_episodes = with_episodes

    @property
    def key(self) -> str:
        return self.info.key

    @property
    def label(self) -> str:
        return self.info.label

    def available(self) -> bool:
        return True

    def supports_keyword_search(self) -> bool:
        return True

    def download_headers(self, url: str) -> dict:  # noqa: ANN001
        return {}

    def search(self, keyword: str, kind: str = "all", limit: int = 30, page: int = 1):  # noqa: ANN001
        if self._search_error:
            raise SourceError(self._search_error)
        video = RemoteVideo(source=self.key, remote_id="1", title="片")
        if self._with_episodes:
            video.episodes = [Episode(name="第1集", url="https://cdn.test/index.m3u8", index=0)]
        return [video]

    def detail(self, video):  # noqa: ANN001
        if self._detail_error:
            raise SourceError(self._detail_error)
        video.episodes = [Episode(name="第1集", url="https://cdn.test/index.m3u8", index=0)]
        return video

    def play_url(self, video, episode, quality=None):  # noqa: ANN001
        if self._play_error:
            raise SourceError(self._play_error)
        return episode.url


# --------------------------------------------------------------------------- 阶段定位


def test_probe_marks_search_stage_failure() -> None:
    result = probe_source(_Provider(search_error="接口 404"), "电影", timeout=3.0)
    assert result.failed_stage == "search"
    assert "404" in result.error
    assert result.stage_text.startswith("搜索✗")


def test_probe_marks_detail_stage_failure() -> None:
    """能搜到、但详情拉不出来：必须报"详情"，而不是"没有分集地址"。"""
    result = probe_source(
        _Provider(with_episodes=False, detail_error="详情接口超时"), "电影", timeout=3.0
    )
    assert result.failed_stage == "detail", f"阶段判定不对：{result.failed_stage}（{result.error}）"
    assert "详情" in result.error


def test_probe_marks_resolve_stage_failure() -> None:
    """取流返回 403 / 解析失败：归到「取流」，这正是"能搜不能播"的典型。"""
    result = probe_source(_Provider(play_error="取流 403"), "电影", timeout=3.0)
    assert result.failed_stage == "resolve"
    assert "取流失败" in result.error


def test_probe_marks_segment_stage_failure() -> None:
    """清单拿到了、首个分片 403：归到「首片」——用户的"下载失败"多半卡在这里。"""
    result = probe_source(_Provider(segment_error="403 Forbidden"), "电影", timeout=3.0)
    assert result.failed_stage == "segment"
    assert "403" in result.error


def test_probe_success_marks_every_stage() -> None:
    result = probe_source(_Provider(), "电影", timeout=3.0)
    assert result.ok
    assert result.failed_stage == ""
    assert result.stage == "segment"
    assert result.stage_text == "搜索✓ 详情✓ 取流✓ 首片✓"


# --------------------------------------------------------------------------- 自动停用


def test_auto_disable_after_three_failed_checks() -> None:
    registry = VideoRegistry([_Provider()])
    registry.set_enabled("p1", True)
    registry._providers["p1"]._search_error = "接口挂了"  # noqa: SLF001  桩源注入故障

    for _ in range(3):
        registry.measure_sources(keys=["p1"], keyword="电影")

    assert registry.is_auto_disabled("p1"), "连续 3 次体检失败没有自动停用"
    assert registry.is_enabled("p1") is False
    assert registry.providers(enabled_only=True) == [], "自动停用的源仍参与了搜索"
    assert "疑似失效" in registry.status_text("p1")
    assert registry.auto_disabled_keys() == ["p1"]


def test_one_success_restores_auto_disabled_source() -> None:
    registry = VideoRegistry([_Provider()])
    registry.set_enabled("p1", True)
    registry._providers["p1"]._search_error = "接口挂了"  # noqa: SLF001
    for _ in range(3):
        registry.measure_sources(keys=["p1"], keyword="电影")
    assert registry.is_auto_disabled("p1")

    registry._providers["p1"]._search_error = ""  # noqa: SLF001
    registry.measure_sources(keys=["p1"], keyword="电影")

    assert registry.is_auto_disabled("p1") is False
    assert registry.is_enabled("p1") is True
    assert [p.key for p in registry.providers(enabled_only=True)] == ["p1"]


def test_manual_enable_clears_auto_disable() -> None:
    registry = VideoRegistry([_Provider()])
    registry.set_enabled("p1", True)
    registry._providers["p1"]._search_error = "接口挂了"  # noqa: SLF001
    for _ in range(3):
        registry.measure_sources(keys=["p1"], keyword="电影")

    registry.set_enabled("p1", True)  # 用户在源设置里重新勾选

    assert registry.is_auto_disabled("p1") is False
    assert registry.status_text("p1") != "" and "疑似失效" not in registry.status_text("p1")


def test_restore_source_clears_auto_disable() -> None:
    registry = VideoRegistry([_Provider()])
    registry.set_enabled("p1", True)
    registry._providers["p1"]._search_error = "接口挂了"  # noqa: SLF001
    for _ in range(3):
        registry.measure_sources(keys=["p1"], keyword="电影")

    registry.restore_source("p1")

    assert registry.auto_disabled_keys() == []
    assert registry.is_enabled("p1") is True


# --------------------------------------------------------------------------- 跨重启


class _FakeStorage:
    """只实现注册表用到的 get_setting / set_setting。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_setting(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self.values[key] = str(value)


def test_health_and_auto_disable_survive_restart() -> None:
    storage = _FakeStorage()
    first = VideoRegistry([_Provider()])
    first.set_enabled("p1", True)
    first._providers["p1"]._search_error = "接口挂了"  # noqa: SLF001
    for _ in range(3):
        first.measure_sources(keys=["p1"], keyword="电影")
    first.save_settings(storage)

    second = VideoRegistry([_Provider()])
    second.load_settings(storage)

    assert second.is_auto_disabled("p1"), "重启后自动停用名单丢了（红标会消失）"
    assert "疑似失效" in second.status_text("p1")
    assert second.health("p1").consecutive_failures >= 3


# --------------------------------------------------------------------------- 源设置界面


def test_source_dialog_marks_auto_disabled_and_can_restore(qapp) -> None:  # noqa: ANN001
    """源设置里要有红字「疑似失效」与一键「恢复自动停用」。"""
    from modu_workbench.boards.video import sources as sources_ui

    dialog = sources_ui.VideoSourceDialog()
    try:
        registry = dialog._registry  # noqa: SLF001
        key = registry.order[0]
        registry._auto_disabled.add(key)  # noqa: SLF001  模拟体检连续失败
        registry._unhealthy_streak[key] = 3  # noqa: SLF001
        registry._probe_issue[key] = "首片：取分片失败：403"  # noqa: SLF001
        dialog._reload()  # noqa: SLF001

        item = dialog._table.item(dialog._row_of(key), sources_ui.COLUMN_STATUS)  # noqa: SLF001
        assert item is not None and "疑似失效" in item.text()
        assert dialog._restore_auto_button.isEnabled() is True  # noqa: SLF001

        dialog._restore_auto_disabled()  # noqa: SLF001
        assert registry.is_auto_disabled(key) is False
        assert dialog._restore_auto_button.isEnabled() is False  # noqa: SLF001
    finally:
        dialog.deleteLater()


def test_source_dialog_has_select_all_and_none(qapp) -> None:  # noqa: ANN001
    """一键全选/全不选：13 个源不该靠手点，全选还要能解除自动停用。"""
    from modu_workbench.boards.video import sources as sources_ui

    dialog = sources_ui.VideoSourceDialog()
    registry = dialog._registry  # noqa: SLF001
    original = [key for key in registry.order if registry.is_enabled(key)]
    try:
        dialog._set_all_enabled(False)  # noqa: SLF001
        assert [key for key in registry.order if registry.is_enabled(key)] == []
        assert dialog._registry.auto_disabled_keys() == []

        key = registry.order[0]
        registry._auto_disabled.add(key)  # noqa: SLF001
        dialog._set_all_enabled(True)  # noqa: SLF001
        assert all(registry.is_enabled(item) for item in registry.order)
        assert registry.is_auto_disabled(key) is False, "全选没有解除自动停用"
    finally:
        for key in registry.order:
            registry.set_enabled(key, key in original)
        dialog.deleteLater()


def test_checkbox_click_also_selects_row(qapp) -> None:  # noqa: ANN001
    """点勾选框要顺带选中该行 —— 否则「测试选中源」永远提示没选中。"""
    from PySide6.QtCore import Qt

    from modu_workbench.boards.video import sources as sources_ui

    dialog = sources_ui.VideoSourceDialog()
    try:
        dialog._table.setCurrentCell(dialog._table.rowCount() - 1, 0)  # noqa: SLF001
        item = dialog._table.item(0, sources_ui.COLUMN_ENABLED)  # noqa: SLF001
        # 必须真的改变勾选状态才会发 itemChanged（默认第一行就是 Checked）
        item.setCheckState(Qt.CheckState.Unchecked)
        assert dialog._table.currentRow() == 0  # noqa: SLF001
        item.setCheckState(Qt.CheckState.Checked)  # 复原，别影响后续用例
    finally:
        dialog.deleteLater()

