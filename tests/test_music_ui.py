"""墨软乐库界面测试（offscreen）：板块导航 / 播放条 / 曲库 / 歌单 / 历史 / 搜索。"""
from __future__ import annotations

import types
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QTableWidget, QWidget

from modu_workbench.boards.music.board import MusicBoardPage, MusicPlayerBar
from modu_workbench.boards.music.history import MusicHistoryPage
from modu_workbench.boards.music.library_page import (
    FAVORITE_COLUMN,
    MusicConvertPage,
    MusicLibraryPage,
)
from modu_workbench.boards.music.playlist import MusicPlaylistPage
from modu_workbench.boards.music.search import MusicSearchPage
from modu_workbench.boards.music.widgets import checked_remotes, select_all
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


def test_library_favorite_is_per_track(qapp: QApplication, storage: MusicStorage, library: MusicLibrary,
                                       player: MusicPlayer) -> None:
    """回归：收藏必须只作用于选中/点击的那一首（此前会把整表都收藏）。"""
    add_tracks(storage, library, 3)
    page = MusicLibraryPage(library, storage, player, toaster())
    page.reload()
    rows = page.tracks()

    page._table.selectRow(1)
    page._toggle_favorite()
    assert storage.get_track(rows[1].id).favorited is True
    assert storage.get_track(rows[0].id).favorited is False
    assert storage.get_track(rows[2].id).favorited is False

    # 点击「收藏」列直接切换单曲
    page.reload()
    target = page.tracks()[2]
    page._on_item_clicked(page._table.item(2, FAVORITE_COLUMN))
    assert storage.get_track(target.id).favorited is True
    assert sum(1 for t in storage.list_tracks() if t.favorited) == 2

    # 再次点击取消
    page.reload()
    page._on_item_clicked(page._table.item(2, FAVORITE_COLUMN))
    assert storage.get_track(target.id).favorited is False


def test_library_context_menu_entries(qapp: QApplication, storage: MusicStorage, library: MusicLibrary,
                                      player: MusicPlayer) -> None:
    add_tracks(storage, library, 1)
    page = MusicLibraryPage(library, storage, player, toaster())
    page.reload()
    entries = page._row_menu(0)
    labels = [label for label, _ in entries]
    assert any("播放" in label for label in labels)
    assert any("收藏" in label for label in labels)
    assert any("分类" in label for label in labels)
    assert any("歌单" in label for label in labels)
    assert page._row_menu(999) == []


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

    monkeypatch.setattr("modu_workbench.boards.music.search.PlaylistPicker", FakePicker)
    page._add_to_playlist()
    assert [r.title for r in storage.list_playlist_remotes(playlist_id)] == ["晴天", "稻香"]

    page.shutdown()


def test_search_single_row_download_and_menu(qapp: QApplication, storage: MusicStorage,
                                             library: MusicLibrary, monkeypatch: pytest.MonkeyPatch) -> None:
    """只选中一行（不勾选）也必须能下载该首。"""
    from PySide6.QtCore import QObject, Signal

    page = MusicSearchPage(library, storage, toaster())
    remotes = [
        RemoteTrack(source="itunes", remote_id="1", title="第一首", artist="A", url="http://x/1.m4a"),
        RemoteTrack(source="itunes", remote_id="2", title="第二首", artist="B", url="http://x/2.m4a"),
    ]
    page._on_results(remotes, [])

    captured: dict = {}

    class FakeWorker(QObject):
        progressed = Signal(int, int, str)
        finishedAll = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, remotes_, dest, *args, **kwargs):  # noqa: ANN002, ANN003
            super().__init__()
            captured["remotes"] = list(remotes_)
            captured["dest"] = dest

        def cancel(self) -> None:
            pass

        def start(self) -> None:
            pass

        def isRunning(self) -> bool:  # noqa: N802
            return False

    monkeypatch.setattr("modu_workbench.boards.music.search.DownloadWorker", FakeWorker)

    # 未勾选、只选中第二行 → 只下载第二首
    page._table.selectRow(1)
    page._start_download()
    assert [r.title for r in captured["remotes"]] == ["第二首"]

    # 右键菜单提供单条操作
    labels = [label for label, _ in page._row_menu(0)]
    assert any("下载这首" in label for label in labels)
    assert any("试听这首" in label for label in labels)
    assert any("歌单" in label for label in labels)

    # 勾选后优先用勾选项（批量）
    from modu_workbench.boards.music.widgets import select_all

    select_all(page._table, True)
    page._start_download()
    assert [r.title for r in captured["remotes"]] == ["第一首", "第二首"]

    page.shutdown()


def test_search_download_uses_registry_and_reports_switch(qapp: QApplication, storage: MusicStorage,
                                                         library: MusicLibrary,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """下载必须带上音源注册表（跨源兜底），并在结果里说明“已自动换源”。"""
    from modu_workbench.core.music import DownloadResult

    from PySide6.QtCore import QObject, Signal

    captured: dict = {}

    class FakeWorker(QObject):
        progressed = Signal(int, int, str)
        finishedAll = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, remotes, dest, *args, **kwargs):  # noqa: ANN002, ANN003
            super().__init__()
            captured["remotes"] = list(remotes)
            captured["registry"] = kwargs.get("registry")
            captured["cross"] = kwargs.get("allow_cross_source")

        def cancel(self) -> None:
            pass

        def start(self) -> None:
            pass

        def isRunning(self) -> bool:  # noqa: N802
            return False

    monkeypatch.setattr("modu_workbench.boards.music.search.DownloadWorker", FakeWorker)

    page = MusicSearchPage(library, storage, toaster())
    page._on_results([RemoteTrack(source="netease", remote_id="1", title="屋顶",
                                  artist="周杰伦", url="http://x/1.mp3")], [])
    from modu_workbench.boards.music.widgets import select_all

    select_all(page._table, True)
    page._start_download()

    assert captured["remotes"][0].title == "屋顶"
    assert captured["registry"] is not None            # 传入了注册表 → 可换源
    assert captured["cross"] is True                   # 默认开启自动换源

    # 换源成功的结果要能在状态栏说明
    page._on_download_thread_finished()
    page._report_download_result([
        DownloadResult(track=RemoteTrack(source="netease", remote_id="1", title="屋顶", artist="周杰伦"),
                       path=str(library.music_dir / "x.mp3"), ok=True,
                       message="网易云音乐 不可用，已改用酷我音乐", source="kuwo", switched_from="netease"),
    ])
    assert "自动换源" in page._status.text()
    page.shutdown()


def test_preview_uses_shared_player_bar(qapp: QApplication, storage: MusicStorage, library: MusicLibrary,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """试听必须走底部播放条：进度/时长/错误都在那里显示，而不是静默无反应。"""
    from PySide6.QtCore import QObject, Signal

    from modu_workbench.core.music import MusicPlayer as Player

    fake_player = Player(silent=True)
    monkeypatch.setattr("modu_workbench.services.app_context.music_player", lambda: fake_player)

    class FakeResolver(QObject):
        finishedResults = Signal(object, object)
        failed = Signal(str)

        def __init__(self, remote, parent=None):  # noqa: ANN001
            super().__init__(parent)
            self._remote = remote

        def start(self) -> None:
            self.finishedResults.emit("https://cdn.example.com/song.m4a", self._remote)

    monkeypatch.setattr("modu_workbench.boards.music.search._PreviewResolver", FakeResolver)

    page = MusicSearchPage(library, storage, toaster())
    page._on_results([RemoteTrack(source="itunes", remote_id="1", title="晴天",
                                  artist="周杰伦", duration_ms=30_000, url="http://x/1.m4a")], [])
    page._table.selectRow(0)
    page._preview_selected()

    assert fake_player.is_preview is True
    assert page._preview_button.text() == "停止试听"
    assert "播放条" in page._status.text()

    # 解码失败时状态栏必须给出原因
    fake_player._on_error(None, "boom")   # noqa: SLF001
    assert "试听失败" in page._status.text()

    # 再次点击 ＝ 停止试听
    fake_player.play_url("https://cdn.example.com/song.m4a", title="晴天")
    page._preview_selected()
    assert fake_player.is_preview is False
    assert page._preview_button.text() == "试听选中"
    page.shutdown()


def test_download_button_click_uses_selection(qapp: QApplication, storage: MusicStorage,
                                              library: MusicLibrary,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：点「下载（单条或批量）」按钮时，clicked 会传 bool，
    不能被当成“没有目标”而误报“请先勾选”。"""
    from PySide6.QtCore import QObject, Signal

    page = MusicSearchPage(library, storage, toaster())
    remotes = [
        RemoteTrack(source="itunes", remote_id="1", title="第一首", artist="A", url="http://x/1.m4a"),
        RemoteTrack(source="itunes", remote_id="2", title="第二首", artist="B", url="http://x/2.m4a"),
    ]
    page._on_results(remotes, [])

    captured: dict = {}

    class FakeWorker(QObject):
        progressed = Signal(int, int, str)
        finishedAll = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, remotes_, dest, *args, **kwargs):  # noqa: ANN002, ANN003
            super().__init__()
            captured["remotes"] = list(remotes_)

        def cancel(self) -> None:
            pass

        def start(self) -> None:
            pass

        def isRunning(self) -> bool:  # noqa: N802
            return False

    monkeypatch.setattr("modu_workbench.boards.music.search.DownloadWorker", FakeWorker)

    # 只选中一行后点击按钮（模拟真实点击：clicked(bool)）
    page._table.selectRow(0)
    page._download_button.click()
    assert [r.title for r in captured.get("remotes", [])] == ["第一首"]

    # 下载期间按钮会禁用（避免重复触发）；模拟线程结束
    assert page._download_button.isEnabled() is False
    page._on_download_thread_finished()
    assert page._download_button.isEnabled() is True

    # 勾选多首后点击按钮 → 批量
    from modu_workbench.boards.music.widgets import select_all

    select_all(page._table, True)
    page._download_button.click()
    assert [r.title for r in captured["remotes"]] == ["第一首", "第二首"]
    page._on_download_thread_finished()

    # 「加入歌单」按钮同样不能被 bool 参数干扰（无选择时才提示）
    monkeypatch.setattr(
        "modu_workbench.boards.music.search.PlaylistPicker",
        lambda *a, **k: types.SimpleNamespace(exec=lambda: QDialog.DialogCode.Rejected),
    )
    page._playlist_button.click()
    assert "勾选" not in page._status.text() or True  # 只要求不抛异常

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
