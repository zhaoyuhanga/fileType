"""自愈选路的**回归测试**：卡顿降档必须"只降一档"，不能断崖。

背景（首版真实缺陷）：`_on_stall_detected` 里"当前档"是从
`session_video.quality` 反查的，而播主清单时这个字段往往是空的 →
退化成"把最高档当当前档"，于是 `next_lower` 把 1080P 直接跳到最低的 538P。
下面把这条钉住：**认不出当前档就不降档（去换源），认得出才降一档**。
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from modu_workbench.boards.video.board import VideoBoardPage
from modu_workbench.core.video.models import Episode, Quality, RemoteVideo


class _FakePlayer:
    """只实现自愈链路用到的几个方法/信号。"""

    def __init__(self) -> None:
        self.status = ""
        self.positions = [12_000]

    def current_position_ms(self) -> int:
        return self.positions[0]

    def set_status(self, message: str) -> None:
        self.status = message

    def close(self) -> None:          # 板块 shutdown 会关播放器
        self.closed = True


@pytest.fixture()
def board(qapp, monkeypatch, tmp_path):  # noqa: ANN001
    from modu_workbench.core.video import VideoLibrary, VideoRegistry, VideoStorage

    storage = VideoStorage(str(tmp_path / "v.db"))
    library = VideoLibrary(storage, tmp_path / "videos", VideoRegistry([]))
    page = VideoBoardPage(library=library)
    monkeypatch.setattr(page, "_resolve_and_play", lambda *a, **k: None)
    monkeypatch.setattr(page, "_on_quality_requested", lambda label: setattr(
        page, "_requested_quality", label))
    yield page
    page.shutdown()


def _episode_with_qualities() -> tuple[RemoteVideo, Episode]:
    episode = Episode(name="第01集", url="https://cdn/master.m3u8", index=0)
    episode.qualities = [
        Quality(label="538P", url="https://cdn/540/index.m3u8"),
        Quality(label="720P", url="https://cdn/720/index.m3u8"),
        Quality(label="1080P", url="https://cdn/1080/index.m3u8"),
    ]
    remote = RemoteVideo(source="cms", remote_id="1", title="片",
                         episodes=[episode])
    return remote, episode


def test_downgrade_is_one_step_when_current_is_known(board) -> None:  # noqa: ANN001
    """已知在播 1080P → 降到 720P（只降一档）。"""
    remote, episode = _episode_with_qualities()
    board._session_remote = remote
    board._session_episode = episode
    board._session_quality_label = "1080P"
    board._player = _FakePlayer()
    board._task_bar = type("_Bar", (), {"note": lambda self, m: None})()

    board._on_stall_detected("downgrade")
    assert getattr(board, "_requested_quality", None) == "720P"
    assert board._downgrade_count == 1


def test_downgrade_from_720p_goes_to_lowest(board) -> None:  # noqa: ANN001
    remote, episode = _episode_with_qualities()
    board._session_remote = remote
    board._session_episode = episode
    board._session_quality_label = "720P"
    board._player = _FakePlayer()
    board._task_bar = type("_Bar", (), {"note": lambda self, m: None})()
    board._on_stall_detected("downgrade")
    assert board._requested_quality == "538P"


def test_unknown_current_quality_does_not_jump_to_lowest(board) -> None:  # noqa: ANN001
    """**核心回归**：认不出当前档时不能降档（否则 1080P → 538P 断崖），而是去换源。"""
    remote, episode = _episode_with_qualities()
    board._session_remote = remote
    board._session_episode = episode
    board._session_quality_label = ""          # 播主清单时的真实状态
    board._session_video = None
    board._player = _FakePlayer()
    notes: list[str] = []
    board._task_bar = type("_Bar", (), {"note": lambda self, m: notes.append(m)})()
    swapped: list[tuple] = []
    board._swap_source_keeping_position = lambda r, e, p: swapped.append((r, e, p))

    board._on_stall_detected("downgrade")
    assert getattr(board, "_requested_quality", None) is None, "不该降档"
    assert swapped, "应当改走换源"
    assert swapped[0][2] == 12_000, "换源必须保留播放位置"
    assert any("更换片源" in note for note in notes)


def test_download_hint_does_not_switch_source(board) -> None:  # noqa: ANN001
    """`reason=download` 只提示"先下后播"，不应触发换源（否则会来回折腾）。"""
    remote, episode = _episode_with_qualities()
    board._session_remote = remote
    board._session_episode = episode
    board._player = _FakePlayer()
    notes: list[str] = []
    board._task_bar = type("_Bar", (), {"note": lambda self, m: notes.append(m)})()
    swapped: list[tuple] = []
    board._swap_source_keeping_position = lambda r, e, p: swapped.append((r, e, p))
    board._on_stall_detected("download")
    assert not swapped
    assert any("下载到本地" in note for note in notes)


def test_single_variant_goes_straight_to_switch_source(board) -> None:  # noqa: ANN001
    """只有一个清晰度时没有"更低档"可降 → 直接换源。"""
    episode = Episode(name="第01集", url="https://cdn/master.m3u8", index=0)
    episode.qualities = [Quality(label="1080P", url="https://cdn/1080/index.m3u8")]
    remote = RemoteVideo(source="cms", remote_id="1", title="片", episodes=[episode])
    board._session_remote = remote
    board._session_episode = episode
    board._session_quality_label = "1080P"
    board._player = _FakePlayer()
    board._task_bar = type("_Bar", (), {"note": lambda self, m: None})()
    swapped: list[tuple] = []
    board._swap_source_keeping_position = lambda r, e, p: swapped.append((r, e, p))
    board._on_stall_detected("downgrade")
    assert swapped
