"""墨软影视界面测试（offscreen）：板块导航 / 搜索页 / 我的视频 / 历史 / 源设置 / 播放器。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidget, QWidget

from modu_workbench.boards.video.board import VideoBoardPage
from modu_workbench.boards.video.detail import VideoDetailDialog
from modu_workbench.boards.video.history import VideoHistoryPage
from modu_workbench.boards.video.library_page import VideoLibraryPage
from modu_workbench.boards.video.player import VideoPlayerDialog, format_time, player_available
from modu_workbench.boards.video.search import VideoSearchPage
from modu_workbench.boards.video.sources import VideoSourceDialog
from modu_workbench.boards.video.widgets import (
    action_remotes,
    action_video_ids,
    attach_context_menu,
    configure_table,
    fill_remote_row,
    fill_video_row,
    make_kind_combo,
    select_all,
)
from modu_workbench.core.video import (
    Episode,
    Quality,
    RemoteVideo,
    Video,
    VideoLibrary,
    VideoStorage,
)
from modu_workbench.ui_kit.toast import Toaster


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


_HOSTS: list[QWidget] = []


def _wait_for(worker, timeout_ms: int = 5000) -> None:  # noqa: ANN001
    """等待后台工作线程结束，并确保排队的跨线程信号已送达。

    用「轮询 + 处理事件」而不是单次 wait：即使线程结束得比信号投递还快，
    也能把 `finishedResolve` 之类的排队调用跑到，避免测试假失败。
    """
    if worker is None:
        return
    import time

    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    deadline = time.monotonic() + timeout_ms / 1000
    while worker.isRunning() and time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        time.sleep(0.01)
    if app is not None:
        # 再跑一小段时间，确保线程发出的排队信号都处理完
        settle = time.monotonic() + 0.2
        while time.monotonic() < settle:
            app.processEvents()
            time.sleep(0.005)


def toaster() -> Toaster:
    """测试用提示器：需要一个隐藏宿主控件（运行时为主窗口）。

    宿主挂在模块级列表上，避免被垃圾回收后 Toaster 访问到已销毁的控件。
    """
    host = QWidget()
    host.hide()
    _HOSTS.append(host)
    return Toaster(host)


@pytest.fixture()
def storage(tmp_path: Path) -> VideoStorage:
    store = VideoStorage(str(tmp_path / "video.db"))
    yield store
    store.close()


@pytest.fixture()
def library(storage: VideoStorage, tmp_path: Path) -> VideoLibrary:
    return VideoLibrary(storage, tmp_path / "files")


def make_video_file(path: Path, size: int = 4096) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x47" + b"\x00" * (size - 1))
    return path


def make_remote(source: str = "cms_360", remote_id: str = "1", title: str = "流浪地球",
                episodes: int = 3) -> RemoteVideo:
    remote = RemoteVideo(source=source, remote_id=remote_id, title=title,
                         kind="movie", year="2019")
    remote.episodes = [
        Episode(name=f"第{index + 1:02d}集", url=f"https://cdn/{index}.m3u8", index=index,
                qualities=[Quality(label="1080P", url=f"https://cdn/{index}_1080.m3u8"),
                           Quality(label="720P", url=f"https://cdn/{index}_720.m3u8")])
        for index in range(episodes)
    ]
    return remote


# --------------------------------------------------------------- 表格组件


def test_configure_and_fill_remote_table(qapp: QApplication) -> None:
    table = configure_table(QTableWidget(), ["片名", "类型", "年份", "地区", "集数", "备注", "源"])
    remote = make_remote()
    table.setRowCount(1)
    fill_remote_row(table, 0, remote)
    assert table.item(0, 0).text() == "流浪地球"
    assert table.item(0, 0).checkState() == Qt.CheckState.Unchecked

    select_all(table, True)
    remotes = action_remotes(table)
    assert len(remotes) == 1 and remotes[0].title == "流浪地球"
    select_all(table, False)
    assert action_remotes(table) == []


def test_fill_video_row_and_ids(qapp: QApplication) -> None:
    table = configure_table(QTableWidget(), ["片名", "类型", "集", "画质", "时长", "大小", "来源", "本地"])
    video = Video(id=7, title="剧", kind="tv", episode_label="第01集", quality="1080P",
                  duration_ms=65_000, size_bytes=2 * 1048576, source="cms", remote_id="9")
    table.setRowCount(1)
    fill_video_row(table, 0, video)
    assert table.item(0, 0).text() == "剧"
    assert table.item(0, 2).text() == "第01集"
    assert table.item(0, 3).text() == "1080P"
    assert table.item(0, 4).text() == "01:05"
    select_all(table, True)
    assert action_video_ids(table) == [7]


def test_make_kind_combo_lists_all_kinds(qapp: QApplication) -> None:
    combo = make_kind_combo(include_all=True)
    labels = [combo.itemText(index) for index in range(combo.count())]
    assert labels[0] == "全部类型"
    assert "电影" in labels and "电视剧" in labels and "动漫" in labels
    combo_only = make_kind_combo(include_all=False)
    assert combo_only.itemText(0) == "电影"


def test_context_menu_provider(qapp: QApplication) -> None:
    table = configure_table(QTableWidget(), ["片名"])
    table.setRowCount(1)
    fill_remote_row(table, 0, make_remote())
    attach_context_menu(table, lambda row: [("动作", lambda: None)])
    assert table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu


# --------------------------------------------------------------- 子页


def test_search_page_constructs_and_lists_sources(qapp: QApplication, library: VideoLibrary,
                                                  storage: VideoStorage) -> None:
    page = VideoSearchPage(library, storage, toaster())
    try:
        assert page._source.count() >= 1     # noqa: SLF001
        assert page._source.itemData(0) == "all"   # noqa: SLF001
        page.reload_sources()
        page.on_shown()
        # 没有结果时操作按钮不可用
        assert page._detail_button.isEnabled() is False
    finally:
        page.shutdown()


def test_search_page_renders_results(qapp: QApplication, library: VideoLibrary,
                                     storage: VideoStorage) -> None:
    page = VideoSearchPage(library, storage, toaster())
    try:
        page._on_results([make_remote(), make_remote(remote_id="2", title="流浪地球2")], [])  # noqa: SLF001
        assert page._table.rowCount() == 2   # noqa: SLF001
        assert page._detail_button.isEnabled() is True
        assert "2 条结果" in page._status.text()
        page._on_results([], ["360资源：接口 404"])   # noqa: SLF001
        assert "部分源失败" in page._status.text()
    finally:
        page.shutdown()


def test_library_page_lists_and_filters(qapp: QApplication, library: VideoLibrary,
                                        storage: VideoStorage) -> None:
    path = make_video_file(library.video_dir / "流浪地球 (2019).mp4")
    library.import_paths([str(path)])
    library.ensure_video(make_remote())

    page = VideoLibraryPage(library, storage, toaster())
    try:
        page.reload()
        assert page._table.rowCount() == 2      # noqa: SLF001
        assert page._play_button.isEnabled() is True

        # 按类型过滤：只留电影（本地导入的默认是 other）
        page._kind.setCurrentIndex(1)          # noqa: SLF001
        page.reload()
        assert page._table.rowCount() == 1      # noqa: SLF001

        page._kind.setCurrentIndex(0)          # noqa: SLF001
        page._keyword.setText("流浪")           # noqa: SLF001
        page.reload()
        assert page._table.rowCount() == 2      # noqa: SLF001
    finally:
        page.shutdown()


def test_library_page_category_and_favorite(qapp: QApplication, library: VideoLibrary,
                                            storage: VideoStorage) -> None:
    video = library.ensure_video(make_remote())
    page = VideoLibraryPage(library, storage, toaster())
    try:
        page.reload()
        select_all(page._table, True)                    # noqa: SLF001
        page._toggle_favorite([video.id])                # noqa: SLF001
        assert storage.get_video(video.id).favorited is True

        library.set_category([video.id], "科幻")
        assert storage.get_video(video.id).category == "科幻"

        # 收藏分类出现在下拉里
        page.reload()
        labels = [page._collection.itemText(i) for i in range(page._collection.count())]  # noqa: SLF001
        assert any("我的收藏" in label for label in labels)
    finally:
        page.shutdown()


def test_history_page_lists_entries(qapp: QApplication, library: VideoLibrary,
                                    storage: VideoStorage) -> None:
    video = library.ensure_video(make_remote(episodes=1))
    library.record_play(video, quality="1080P", source="360资源")
    page = VideoHistoryPage(library, storage, toaster())
    try:
        page.reload()
        assert page._table.rowCount() == 1     # noqa: SLF001
        assert page._table.item(0, 0).text() == "流浪地球"
        assert page._table.item(0, 3).text() == "播放"
        assert page._play_button.isEnabled() is True

        page._action.setCurrentIndex(2)        # noqa: SLF001  仅下载
        page.reload()
        assert page._table.rowCount() == 0     # noqa: SLF001
    finally:
        page.shutdown()


def test_history_refresh_on_shown(qapp: QApplication, library: VideoLibrary,
                                  storage: VideoStorage) -> None:
    page = VideoHistoryPage(library, storage, toaster())
    try:
        video = library.ensure_video(make_remote(episodes=1))
        library.record_play(video)
        page.on_shown()
        assert page._table.rowCount() == 1     # noqa: SLF001
    finally:
        page.shutdown()


# --------------------------------------------------------------- 详情对话框


def test_detail_dialog_lists_episodes_and_qualities(qapp: QApplication, library: VideoLibrary) -> None:
    dialog = VideoDetailDialog(make_remote(episodes=3), library)
    try:
        assert dialog._episode_list.count() == 3      # noqa: SLF001
        assert dialog._episode_list.currentRow() == 0  # noqa: SLF001
        assert dialog.current_episode() is not None
        # 清晰度下拉包含源提供的两路画质
        labels = [dialog._quality_box.itemText(i) for i in range(dialog._quality_box.count())]  # noqa: SLF001
        assert any("1080P" in label for label in labels)
        assert any("720P" in label for label in labels)
        assert dialog._play_button.isEnabled() is True   # noqa: SLF001

        # 只选中第 2、3 集（先清空默认选中）
        dialog._episode_list.clearSelection()             # noqa: SLF001
        dialog._episode_list.item(1).setSelected(True)    # noqa: SLF001
        dialog._episode_list.item(2).setSelected(True)    # noqa: SLF001
        selected = dialog.selected_episodes()
        assert [episode.index for episode in selected] == [1, 2]
    finally:
        dialog.close()


def test_detail_dialog_quality_choice_maps_to_url(qapp: QApplication, library: VideoLibrary) -> None:
    dialog = VideoDetailDialog(make_remote(episodes=1), library)
    try:
        # 选 720P → 应映射到 720 的地址
        index = dialog._quality_box.findData("720P")     # noqa: SLF001
        assert index >= 0
        dialog._quality_box.setCurrentIndex(index)       # noqa: SLF001
        chosen = dialog.current_quality()
        assert chosen is not None and chosen.label == "720P"
        assert chosen.url.endswith("_720.m3u8")
    finally:
        dialog.close()


def test_detail_dialog_without_episodes_shows_hint(qapp: QApplication, library: VideoLibrary) -> None:
    remote = RemoteVideo(source="cms", remote_id="1", title="待解析")
    dialog = VideoDetailDialog(remote, library)
    try:
        # 没有剧集时按钮不可用（详情线程在 offscreen 下可能立刻失败）
        assert dialog._episode_list.count() == 0      # noqa: SLF001
    finally:
        dialog.close()


# --------------------------------------------------------------- 源设置


def test_source_dialog_lists_all_sources(qapp: QApplication) -> None:
    dialog = VideoSourceDialog()
    try:
        assert dialog._table.rowCount() >= 5          # noqa: SLF001
        keys = [dialog._key_at(row) for row in range(dialog._table.rowCount())]  # noqa: SLF001
        assert "archive" in keys and "wikimedia" in keys and "url" in keys
        # 采集源地址可编辑（便于站点换域名）
        editable_rows = [
            row for row in range(dialog._table.rowCount())     # noqa: SLF001
            if dialog._table.item(row, 4).flags() & Qt.ItemFlag.ItemIsEditable   # noqa: SLF001
        ]
        assert editable_rows
    finally:
        dialog.reject()


def test_source_dialog_toggle_enabled(qapp: QApplication) -> None:
    from modu_workbench.app import context as app_context

    dialog = VideoSourceDialog()
    try:
        row = dialog._table.rowCount() - 1            # noqa: SLF001
        item = dialog._table.item(row, 0)             # noqa: SLF001
        key = dialog._key_at(row)                     # noqa: SLF001
        was_enabled = app_context.video_registry().is_enabled(key)
        item.setCheckState(Qt.CheckState.Unchecked if was_enabled else Qt.CheckState.Checked)
        assert app_context.video_registry().is_enabled(key) is (not was_enabled)
        # 复原，避免影响其他用例
        item.setCheckState(Qt.CheckState.Checked if was_enabled else Qt.CheckState.Unchecked)
        assert app_context.video_registry().is_enabled(key) is was_enabled
    finally:
        dialog.reject()


# --------------------------------------------------------------- 播放器


def test_player_helpers(qapp: QApplication) -> None:
    assert format_time(0) == "00:00"
    assert format_time(65_000) == "01:05"
    assert format_time(3_725_000) == "01:02:05"
    assert isinstance(player_available(), bool)


def test_player_dialog_controls(qapp: QApplication) -> None:
    dialog = VideoPlayerDialog(
        title="测试片", url="http://127.0.0.1:9/media/does-not-exist.mp4",
        is_hls=False, qualities=["1080P", "720P"], current_quality="1080P",
        episode_label="正片", has_prev=False, has_next=True, start_ms=30_000,
    )
    try:
        assert dialog.duration_ms() >= 0
        assert dialog.current_position_ms() >= 0
        assert dialog._prev_button.isEnabled() is False   # noqa: SLF001
        assert dialog._next_button.isEnabled() is True    # noqa: SLF001
        # 清晰度下拉含两路
        labels = [dialog._quality_box.itemText(i) for i in range(dialog._quality_box.count())]  # noqa: SLF001
        assert "1080P" in labels and "720P" in labels

        dialog.set_qualities(["4K", "720P"], "4K")
        labels = [dialog._quality_box.itemText(i) for i in range(dialog._quality_box.count())]  # noqa: SLF001
        assert labels[0] == "4K" and "720P" in labels

        # 换集导航状态可更新
        dialog.set_episode_nav(has_prev=True, has_next=False)
        assert dialog._prev_button.isEnabled() is True    # noqa: SLF001
        assert dialog._next_button.isEnabled() is False   # noqa: SLF001

        # 换源（换清晰度/换集）就地切换地址并更新标题
        dialog.switch_source("http://127.0.0.1:9/media/other.mp4", is_hls=False,
                             title="测试片 · 第02集", start_ms=5_000)
        assert "第02集" in dialog.windowTitle()
        dialog.set_status("测试状态")
    finally:
        dialog.close()


def test_player_quality_signal_emitted(qapp: QApplication) -> None:
    dialog = VideoPlayerDialog(title="片", url="http://127.0.0.1:9/m.mp4", is_hls=False,
                               qualities=["1080P", "720P"], current_quality="1080P")
    received: list[str] = []
    dialog.qualityRequested.connect(received.append)
    try:
        dialog._quality_box.setCurrentText("720P")   # noqa: SLF001
        assert received == ["720P"]
    finally:
        dialog.close()


def test_player_episode_signal_emitted(qapp: QApplication) -> None:
    dialog = VideoPlayerDialog(title="片", url="http://127.0.0.1:9/m.mp4", is_hls=False,
                               has_prev=True, has_next=True)
    steps: list[int] = []
    dialog.episodeRequested.connect(steps.append)
    try:
        dialog._next_button.click()      # noqa: SLF001
        dialog._prev_button.click()      # noqa: SLF001
        assert steps == [1, -1]
    finally:
        dialog.close()


# --------------------------------------------------------------- 板块装配


def _stub_registry(*, key: str, title: str, error: str = "", episodes: int = 3):  # noqa: ANN201
    """构造一个只走本地逻辑的桩源注册表。"""
    from modu_workbench.core.video import VideoRegistry
    from modu_workbench.core.video.sources import SourceError, SourceInfo, VideoSource

    class Stub(VideoSource):
        info = SourceInfo(key=key, label="桩源", note="test")

        def search(self, keyword, kind="all", limit=30, page=1):  # noqa: ANN001, ANN003
            if error:
                raise SourceError(error)
            return [make_remote(key, "7", title, episodes=episodes)]

        def detail(self, video):  # noqa: ANN001
            if error:
                raise SourceError(error)
            return video

        def play_url(self, video, episode, quality=None):  # noqa: ANN001, ANN003
            return episode.url

    return VideoRegistry([Stub()])


def offline_registry():  # noqa: ANN201
    """离线桩注册表：避免界面测试触碰真实网络。"""
    return _stub_registry(key="offline", title="离线", error="离线")


def stub_library(library: VideoLibrary, **kwargs) -> VideoLibrary:  # noqa: ANN003
    """把桩注册表装进传入的库 —— 板块以「库」为唯一事实来源（含其 registry）。

    直接改库上的 registry，保证界面展示的源与解析用的源一致；
    board 构造函数本身就取 self._library.registry。
    """
    library.registry = _stub_registry(**kwargs)
    return library


def test_board_page_navigation(qapp: QApplication, library: VideoLibrary,
                               storage: VideoStorage) -> None:
    from modu_workbench.core.video import VideoRegistry

    page = VideoBoardPage(library=stub_library(library, key="offline", title="离线", error="离线"),
                          storage=storage)
    try:
        for key in ("search", "library", "history"):
            page.show_page(key)
            assert page._stack.currentIndex() == page._indexes[key]   # noqa: SLF001
        page.on_shown()
        assert "已启用" in page._source_hint.text()                    # noqa: SLF001
        assert isinstance(page._registry, VideoRegistry)                # noqa: SLF001
    finally:
        page.shutdown()


def test_board_play_local_file(qapp: QApplication, library: VideoLibrary,
                               storage: VideoStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    """本地条目播放：走本机流服务，不发起联网解析，并记入历史。"""
    # 不依赖本机流服务能否起来：固定返回一个本机地址（真实链路由 media_server 测试覆盖）
    monkeypatch.setattr(
        "modu_workbench.boards.video.board.local_file_url",
        lambda path: f"http://127.0.0.1:9/media/{Path(path).name}",
    )
    path = make_video_file(library.video_dir / "本地测试片.mp4")
    video = library.import_paths([str(path)])[0]

    page = VideoBoardPage(library=stub_library(library, key="offline", title="离线", error="离线"),
                          storage=storage)
    try:
        page._play_local(video)                       # noqa: SLF001
        assert page._player is not None               # noqa: SLF001
        assert page._session_video is not None        # noqa: SLF001
        assert page._session_source == "local"        # noqa: SLF001
        assert "本地文件" in page._status.text()
        assert storage.get_video(video.id).play_count == 1
        entries = storage.list_history(action="play")
        assert any(entry.title == video.title for entry in entries)
    finally:
        page.shutdown()


def test_board_online_play_resolves_and_records(qapp: QApplication, library: VideoLibrary,
                                                storage: VideoStorage) -> None:
    """在线播放全链路（用本地桩源，不走网络）：解析 → 打开播放器 → 入库 → 记历史。"""
    from PySide6.QtCore import QEventLoop, QTimer

    page = VideoBoardPage(library=stub_library(library, key="stub", title="流浪地球", episodes=2),
                          storage=storage)
    try:
        remote = make_remote("stub", "7", "流浪地球", episodes=2)
        page._play_online(remote, remote.episodes[0], None)   # noqa: SLF001
        _wait_for(page._resolve_worker)                       # noqa: SLF001

        assert page._session_video is not None                # noqa: SLF001
        assert page._session_video.title == "流浪地球"
        assert page._session_source == "stub"                 # noqa: SLF001
        assert page._player is not None                       # noqa: SLF001
        # 在线条目已入库（file_path 为空，可播放），并记入播放历史
        assert storage.get_video(page._session_video.id).title == "流浪地球"   # noqa: SLF001
        assert storage.list_history(action="play")
        # 解析出的直链被记入播放记录（便于后续续播）
        assert storage.get_play_record(page._session_video.id, "第01集") is not None   # noqa: SLF001
    finally:
        page.shutdown()


def test_board_episode_step_switches_episode(qapp: QApplication, library: VideoLibrary,
                                             storage: VideoStorage) -> None:
    """播放器里点「下一集」应切到同一部的下一集。"""
    page = VideoBoardPage(library=stub_library(library, key="stub", title="流浪地球", episodes=3),
                          storage=storage)
    try:
        remote = make_remote("stub", "7", "流浪地球", episodes=3)
        page._session_remote = remote                          # noqa: SLF001
        page._session_episode = remote.episodes[0]             # noqa: SLF001
        page._on_episode_step(1)                               # noqa: SLF001
        _wait_for(page._resolve_worker)                        # noqa: SLF001

        assert page._session_episode is not None               # noqa: SLF001
        assert page._session_episode.index == 1                # noqa: SLF001
        assert page._session_episode.name == "第02集"           # noqa: SLF001
    finally:
        page.shutdown()


def test_board_download_request_without_episodes_is_safe(qapp: QApplication, library: VideoLibrary,
                                                         storage: VideoStorage) -> None:
    """在线条目没有剧集信息时，应当先去补详情而不是崩溃（这里用离线桩源）。"""
    page = VideoBoardPage(library=stub_library(library, key="offline", title="离线", error="离线"),
                          storage=storage)
    try:
        remote = RemoteVideo(source="offline", remote_id="1", title="流浪地球")
        page._handle_download_request(remote, None, None)   # noqa: SLF001
        assert page._download_worker is None                # noqa: SLF001
    finally:
        page.shutdown()
