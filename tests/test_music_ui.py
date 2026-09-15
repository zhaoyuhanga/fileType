"""墨读音乐界面测试（offscreen）：板块导航 / 播放条 / 曲库 / 歌单 / 历史 / 搜索。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QTableWidget, QWidget

from modu_workbench.boards.music_board import MusicBoardPage, MusicPlayerBar
from modu_workbench.boards.music_history import MusicHistoryPage
from modu_workbench.boards.music_library_page import MusicConvertPage, MusicLibraryPage
from modu_workbench.boards.music_playlist import MusicPlaylistPage
from modu_workbench.boards.music_search import MusicSearchPage
from modu_workbench.boards.music_widgets import checked_remotes, select_all
from modu_workbench.core.music import (
    MusicLibrary,
    MusicPlayer,
    MusicStorage,
    RemoteTrack,
    Track,
)
from modu_workbench.ui_kit.toast import Toaster


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


def toaster() -> Toaster:
    """测试用提示器：需要一个隐藏宿主控件（运行时为主窗口）。"""
    host = QWidget()
    host.hide()
    return Toaster(host)


@pytest.fixture()
def storage(tmp_path: Path) -> MusicStorage:
    return MusicStorage(str(tmp_path / "music.db"))


@pytest.fixture()
def library(storage: MusicStorage, tmp_path: Path) -> MusicLibrary:
    return MusicLibrary(storage, tmp_path / "library")


@pytest.fixture()
def player() -> MusicPlayer:
    return MusicPlayer(silent=True)


def add_tracks(storage: MusicStorage, library: MusicLibrary, count: int = 3) -> list[Track]:
    tracks: list[Track] = []
    for index in range(count):
        path = library.music_dir / f"歌手{index} - 歌曲{index}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 128)
        tracks.extend(library.import_paths([str(path)]))
    return tracks


# --------------------------------------------------------------- 播放条

def test_player_bar_updates_on_track_change(qapp: QApplication, player: MusicPlayer, tmp_path: Path) -> None:
    bar = MusicPlayerBar(player)
    assert bar._title.text() == "未在播放"

    track = Track(id=1, path=str(tmp_path / "a.mp3"), title="晴天", artist="周杰伦",
                  album="叶惠美", format="mp3", duration_ms=269_000)
    player.trackChanged.emit(track)
    assert bar._title.text() == "晴天"
    assert "周杰伦" in bar._meta.text()

    player.positionChanged.emit(65_000, 269_000)
    assert bar._time.text().startswith("01:05")

    player.stateChanged.emit("playing")
    assert bar._play.text() == "⏸"
    player.stateChanged.emit("paused")
    assert bar._play.text() == "▶"

    assert player.cycle_mode() == "loop-all"
    assert bar._mode.text() == "列表循环"

    bar.refresh_favorite(True)
    assert "已收藏" in bar._favorite.text()


# --------------------------------------------------------------- 板块导航

def test_board_navigation(qapp: QApplication) -> None:
    board = MusicBoardPage()
    for key in ("search", "library", "playlist", "convert", "history"):
        board.show_page(key)
        assert board._stack.currentIndex() == board._indexes[key]
        assert board._nav_buttons[key].objectName() == "navButtonActive"
    board.on_shown()
    board.shutdown()


# --------------------------------------------------------------- 我的音乐

def test_library_page_lists_and_plays(qapp: QApplication, storage: MusicStorage, library: MusicLibrary,
                                      player: MusicPlayer) -> None:
    add_tracks(storage, library, 2)
    page = MusicLibraryPage(library, storage, player, toaster())
    page.reload()
    assert page._table.rowCount() == 2
    assert "共 2 首" in page._status.text()

    emitted: list = []
    page.playRequested.connect(lambda tracks, index: emitted.append((tracks, index)))
    page._table.selectRow(0)
    page.play_selected()
    assert emitted and emitted[0][0][0].title.startswith("歌曲")

    storage.set_favorite([page.tracks()[0].id], True)
    page.reload()
    page._favorite_only.setCurrentIndex(1)
    assert page._table.rowCount() == 1
    page._favorite_only.setCurrentIndex(0)

    page._keyword.setText("歌曲1")
    assert page._table.rowCount() == 1
    page._keyword.setText("")

    page._table.selectRow(0)
    page._remove_tracks()
    assert page._table.rowCount() == 1


def test_library_page_shuffle_all(qapp: QApplication, storage: MusicStorage, library: MusicLibrary,
                                  player: MusicPlayer) -> None:
    add_tracks(storage, library, 2)
    page = MusicLibraryPage(library, storage, player, toaster())
    page.reload()
    emitted: list = []
    page.playRequested.connect(lambda tracks, index: emitted.append((tracks, index)))
    page.play_shuffle()
    assert emitted and emitted[0][1] == -1 and len(emitted[0][0]) == 2


# --------------------------------------------------------------- 歌单

def test_playlist_page_render_and_play(qapp: QApplication, storage: MusicStorage, library: MusicLibrary,
                                       player: MusicPlayer) -> None:
    tracks = add_tracks(storage, library, 2)
    playlist_id = storage.create_playlist("通勤")
    storage.add_to_playlist(playlist_id, [t.id for t in tracks])
    storage.add_remotes_to_playlist(playlist_id, [
        RemoteTrack(source="itunes", remote_id="9", title="待下载曲", artist="X", url="http://x/9.m4a"),
    ])

    page = MusicPlaylistPage(library, storage, player, toaster())
    page.reload()
    page._playlist_id = playlist_id
    page._render()
    assert page._table.rowCount() == 2
    assert page._pending.rowCount() == 1
    assert "待下载（1）" in page._pending_label.text()

    emitted: list = []
    page.playRequested.connect(lambda tracks_, index, mode: emitted.append((tracks_, index, mode)))
    page._play(0, "shuffle")
    assert emitted and emitted[0][2] == "shuffle"
    page._play(0, "loop-all")
    assert emitted[-1][2] == "loop-all"

    page._pending.selectRow(0)
    page._remove_pending()
    assert page._pending.rowCount() == 0

    page.shutdown()


# --------------------------------------------------------------- 历史

def test_history_page_play(qapp: QApplication, storage: MusicStorage, library: MusicLibrary,
                           player: MusicPlayer) -> None:
    tracks = add_tracks(storage, library, 1)
    library.record_play(tracks[0].id)
    page = MusicHistoryPage(library, storage, player, toaster())
    page.reload()
    assert page._table.rowCount() == 1
    assert page._table.item(0, 2).text() == "播放"

    emitted: list = []
    page.playRequested.connect(lambda tracks_, index: emitted.append((tracks_, index)))
    page._table.selectRow(0)
    page.play_selected()
    assert emitted and emitted[0][0][0].id == tracks[0].id

    page._clear()
    assert page._table.rowCount() == 0


# --------------------------------------------------------------- 搜索页

def test_search_page_results_and_playlist(qapp: QApplication, storage: MusicStorage, library: MusicLibrary,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    page = MusicSearchPage(library, storage, toaster())
    remotes = [
        RemoteTrack(source="itunes", remote_id="1", title="晴天", artist="周杰伦",
                    album="叶惠美", duration_ms=269_000, url="http://x/1.m4a"),
        RemoteTrack(source="itunes", remote_id="2", title="稻香", artist="周杰伦",
                    album="魔杰座", duration_ms=223_000, url="http://x/2.m4a"),
    ]
    page._on_results(remotes, ["某音源：超时"])
    assert page._table.rowCount() == 2
    assert "共 2 条结果" in page._status.text()
    assert "超时" in page._status.text()

    select_all(page._table, True)
    assert len(checked_remotes(page._table)) == 2

    playlist_id = storage.create_playlist("我喜欢的")

    class FakePicker:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def exec(self):  # noqa: ANN201
            return QDialog.DialogCode.Accepted

        def selected_playlist_id(self) -> int:
            return playlist_id

    monkeypatch.setattr("modu_workbench.boards.music_search.PlaylistPicker", FakePicker)
    page._add_to_playlist()
    assert [r.title for r in storage.list_playlist_remotes(playlist_id)] == ["晴天", "稻香"]

    page.shutdown()


def test_search_page_empty_selection_warns(qapp: QApplication, storage: MusicStorage,
                                           library: MusicLibrary) -> None:
    page = MusicSearchPage(library, storage, toaster())
    page._on_results([], [])
    assert page._table.rowCount() == 0
    page._start_download()   # 未勾选时应提示而非抛异常
    page._add_to_playlist()
    page.shutdown()


# --------------------------------------------------------------- 格式转换页

def test_convert_page_lists_and_runs(qapp: QApplication, storage: MusicStorage, library: MusicLibrary,
                                     monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracks = add_tracks(storage, library, 2)
    page = MusicConvertPage(library, storage, toaster())
    assert page._table.rowCount() == 2
    assert page._target.count() >= 8

    calls: list = []

    def fake_convert(track_id, target_format, output_dir=None, cancel=None, on_progress=None):  # noqa: ANN001
        calls.append((track_id, target_format))
        output = Path(output_dir) / f"out{track_id}.{target_format}"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"ok")
        return output

    monkeypatch.setattr(library, "convert", fake_convert)
    page._table.selectRow(0)
    page._target.setCurrentIndex(0)
    page._start_convert()
    assert page._worker is not None
    page._worker.wait(5000)
    qapp.processEvents()
    assert calls == [(tracks[0].id, page._target.currentData())]
    assert "转换完成" in page._status.text()
    page.shutdown()


def test_convert_page_cancel_without_selection(qapp: QApplication, storage: MusicStorage,
                                               library: MusicLibrary) -> None:
    page = MusicConvertPage(library, storage, toaster())
    page._cancel_convert()   # 无任务时不应异常
    assert page._worker is None
    page.shutdown()


def test_search_and_library_tables_are_tables(qapp: QApplication, storage: MusicStorage,
                                              library: MusicLibrary) -> None:
    page = MusicSearchPage(library, storage, toaster())
    assert isinstance(page._table, QTableWidget)
    library_page = MusicLibraryPage(library, storage, MusicPlayer(silent=True), toaster())
    assert isinstance(library_page._table, QTableWidget)
    page.shutdown()
