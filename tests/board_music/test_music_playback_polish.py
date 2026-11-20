"""乐库播放打磨：随机播放真随机、歌单从选中行起播、音量/模式记忆、失败上报。"""
from __future__ import annotations

import types
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from modu_workbench.boards.music import board as music_board
from modu_workbench.boards.music.board import MUSIC_MODE_KEY, MUSIC_VOLUME_KEY, MusicPlayerBar
from modu_workbench.boards.music.playlist import MusicPlaylistPage
from modu_workbench.core.music import MusicLibrary, MusicPlayer, MusicStorage, Track
from modu_workbench.core.platform.paths import SETTINGS_APP, SETTINGS_ORG
from modu_workbench.ui_kit.components import TaskBar
from modu_workbench.ui_kit.toast import Toaster


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture(autouse=True)
def _clean_prefs():
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    for key in (MUSIC_VOLUME_KEY, MUSIC_MODE_KEY, "music/download_dir"):
        settings.remove(key)
    settings.sync()
    yield
    for key in (MUSIC_VOLUME_KEY, MUSIC_MODE_KEY, "music/download_dir"):
        settings.remove(key)
    settings.sync()


def _toaster() -> Toaster:
    host = QWidget()
    host.hide()
    return Toaster(host)


@pytest.fixture()
def library(tmp_path: Path) -> MusicLibrary:
    storage = MusicStorage(str(tmp_path / "music.db"))
    return MusicLibrary(storage, tmp_path / "library")


def test_volume_and_mode_are_restored_and_saved(
    qapp: QApplication, library: MusicLibrary
) -> None:
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    settings.setValue(MUSIC_VOLUME_KEY, 30)
    settings.setValue(MUSIC_MODE_KEY, "shuffle")
    settings.sync()

    player = MusicPlayer(silent=True)
    bar = MusicPlayerBar(player)
    try:
        assert player.volume == 30
        assert player.mode == "shuffle"
        assert bar._volume.value() == 30  # noqa: SLF001
        assert "随机" in bar._mode.text(), "恢复的播放模式要显示在按钮上"  # noqa: SLF001

        bar._volume.setValue(45)  # noqa: SLF001
        assert player.volume == 45
        assert int(settings.value(MUSIC_VOLUME_KEY)) == 45

        player.cycle_mode()
        assert str(settings.value(MUSIC_MODE_KEY)) == player.mode
    finally:
        bar.close()


def test_random_play_sets_shuffle_mode_and_random_start(
    qapp: QApplication, library: MusicLibrary, tmp_path: Path
) -> None:
    player = MusicPlayer(silent=True)
    tracks = []
    for index in range(6):
        path = tmp_path / f"t{index}.mp3"
        path.write_bytes(b"\x00")
        tracks.append(Track(id=index + 1, path=str(path), title=f"曲{index}", format="mp3"))

    fake = types.SimpleNamespace(_player=player, _toaster=_toaster())
    music_board.MusicBoardPage._start_playback(fake, tracks, -1, None)  # noqa: SLF001

    assert player.mode == "shuffle", "「随机播放」必须切到随机模式"
    assert 0 <= player.current_index < len(tracks)
    assert player.queue


def test_playlist_play_starts_from_selected_row(
    qapp: QApplication, library: MusicLibrary
) -> None:
    storage = library.storage
    playlist_id = storage.create_playlist("测试歌单")
    tracks = []
    for index in range(4):
        path = library.music_dir / f"t{index}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        imported = library.import_paths([str(path)])
        tracks.extend(imported)
    storage.add_to_playlist(playlist_id, [track.id for track in tracks])

    player = MusicPlayer(silent=True)
    page = MusicPlaylistPage(library, storage, player, _toaster(), task_bar=TaskBar())
    try:
        page.reload()
        # 「我的收藏」固定排第一，这里要切到刚建的歌单
        from PySide6.QtCore import Qt

        target_row = next(
            row for row in range(page._list.count())  # noqa: SLF001
            if page._list.item(row).data(Qt.ItemDataRole.UserRole) == playlist_id  # noqa: SLF001
        )
        page._list.setCurrentRow(target_row)  # noqa: SLF001
        assert len(page._tracks) == 4  # noqa: SLF001
        page._table.selectRow(2)  # noqa: SLF001
        received: list[tuple[list, int, str]] = []
        page.playRequested.connect(lambda tracks_, index, mode: received.append((tracks_, index, mode)))

        play_button = next(
            button for button in page.findChildren(QPushButton) if button.text() == "播放"
        )
        play_button.click()
        assert received, "点「播放」没有发出播放请求"
        assert received[-1][1] == 2, "歌单应从选中的第 3 行开始播，而不是永远第 1 首"
    finally:
        page.close()
        library.storage.close()


def test_playlist_download_reports_failures(
    qapp: QApplication, library: MusicLibrary
) -> None:
    storage = library.storage
    playlist_id = storage.create_playlist("失败歌单")
    player = MusicPlayer(silent=True)
    task = TaskBar()
    page = MusicPlaylistPage(library, storage, player, _toaster(), task_bar=task)
    try:
        page._playlist_id = playlist_id  # noqa: SLF001
        successes: list[str] = []
        errors: list[str] = []
        page._toaster.success = successes.append  # type: ignore[method-assign]  # noqa: SLF001
        page._toaster.error = errors.append  # type: ignore[method-assign]  # noqa: SLF001

        failed = [types.SimpleNamespace(ok=False, message="版权受限", track=None)]
        page._on_downloaded(failed)  # noqa: SLF001

        assert not successes, "全部失败时不能弹「已下载」成功提示"
        assert errors and "下载失败" in errors[-1] and "版权受限" in errors[-1]
        assert "失败 1" in task.text
    finally:
        page.close()
        library.storage.close()


def test_library_import_can_be_cancelled(library: MusicLibrary, tmp_path: Path) -> None:
    """导入几百首时点「取消」应停下，而不是等整批跑完（已入库的保留）。"""
    paths: list[str] = []
    for index in range(8):
        path = tmp_path / f"批量{index}.mp3"
        path.write_bytes(b"\x00" * 64)
        paths.append(str(path))

    state = {"cancelled": False}

    def on_progress(done: int, total: int, message: str) -> None:
        if done >= 1:
            state["cancelled"] = True

    tracks = library.import_paths(
        paths, on_progress=on_progress, cancel=lambda: state["cancelled"]
    )
    assert 1 <= len(tracks) < 8, "取消后仍在继续导入"
    assert len(library.storage.list_tracks()) == len(tracks)


def test_cancel_current_reaches_import_worker(qapp: QApplication, library: MusicLibrary) -> None:
    from modu_workbench.boards.music.library_page import MusicLibraryPage

    page = MusicLibraryPage(
        library, library.storage, MusicPlayer(silent=True), _toaster(), task_bar=TaskBar()
    )
    try:
        calls: list[bool] = []

        class DummyWorker:
            def cancel(self) -> None:
                calls.append(True)

        page._import_worker = DummyWorker()  # type: ignore[assignment]  # noqa: SLF001
        page._cancel_current()  # noqa: SLF001
        assert calls == [True], "任务条「取消」没有转给导入线程"
    finally:
        page.close()


def test_download_dir_pref_round_trip(tmp_path: Path) -> None:
    """搜索页选的下载目录要写回设置，歌单页「下载待下载项」才用同一个目录。"""
    from modu_workbench.boards.music.widgets import (
        music_download_dir_pref,
        set_music_download_dir_pref,
    )

    target = tmp_path / "音乐下载"
    target.mkdir()
    set_music_download_dir_pref(str(target))
    assert Path(music_download_dir_pref()) == target
