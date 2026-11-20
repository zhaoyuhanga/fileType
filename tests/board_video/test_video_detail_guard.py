"""影视：详情取不到剧集时必须停下并报原因（不能无限重试）。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from modu_workbench.boards.video.board import VideoBoardPage
from modu_workbench.core.video import (
    RemoteVideo,
    VideoLibrary,
    VideoRegistry,
    VideoStorage,
)
from modu_workbench.core.video.sources import SourceInfo, VideoSource


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def storage(tmp_path: Path) -> VideoStorage:
    store = VideoStorage(str(tmp_path / "video.db"))
    yield store
    store.close()


class _EmptySource(VideoSource):
    """搜索/详情都成功，但永远没有分集（Internet Archive 条目、残缺采集页）。"""

    info = SourceInfo(key="empty", label="空源", note="test")

    def search(self, keyword, kind="all", limit=30, page=1):  # noqa: ANN001, ANN003
        return [RemoteVideo(source="empty", remote_id="1", title="没有分集的片子")]

    def detail(self, video):  # noqa: ANN001
        return video

    def play_url(self, video, episode, quality=None):  # noqa: ANN001, ANN003
        raise AssertionError("没有分集时不该走到取流")


def _wait_worker(qapp: QApplication, worker, timeout: float = 5.0) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while worker.isRunning() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    settle = time.monotonic() + 0.2
    while time.monotonic() < settle:
        qapp.processEvents()
        time.sleep(0.005)


def test_play_without_episodes_stops_after_one_detail_attempt(
    qapp: QApplication, storage: VideoStorage, tmp_path: Path
) -> None:
    library = VideoLibrary(storage, tmp_path / "files")
    library.registry = VideoRegistry([_EmptySource()])
    page = VideoBoardPage(library=library, storage=storage)
    errors: list[str] = []
    page._toaster.error = errors.append  # type: ignore[method-assign]  # noqa: SLF001
    remote = RemoteVideo(source="empty", remote_id="1", title="没有分集的片子")
    try:
        page._play_online(remote, None, None)  # noqa: SLF001
        assert len(page._detail_workers) == 1  # noqa: SLF001
        _wait_worker(qapp, page._detail_workers[0])  # noqa: SLF001

        # 关键：补详情仍是 0 集时必须停手，而不是再发一个同样的请求
        assert len(page._detail_workers) == 0, "取不到剧集后还在继续重试"  # noqa: SLF001
        assert "没有取到可播放的剧集" in page._task_bar.text  # noqa: SLF001
        assert errors and "没有取到可播放的剧集" in errors[-1]

        page._handle_download_request(remote, None, None)  # noqa: SLF001
        _wait_worker(qapp, page._detail_workers[0])  # noqa: SLF001
        assert "没有取到可下载的剧集" in page._task_bar.text  # noqa: SLF001
    finally:
        page.shutdown()


def test_busy_download_is_queued_not_dropped(
    qapp: QApplication, storage: VideoStorage, tmp_path: Path
) -> None:
    """勾 3 部一起下：忙时后续批次进队列，当前批次结束后自动接上。"""
    from modu_workbench.core.video import Episode

    library = VideoLibrary(storage, tmp_path / "files")
    library.registry = VideoRegistry([_EmptySource()])
    page = VideoBoardPage(library=library, storage=storage)
    remote = RemoteVideo(source="empty", remote_id="1", title="排队片")
    episode = Episode(name="第01集", url="http://127.0.0.1:9/1.mp4")

    class BusyWorker:
        def isRunning(self) -> bool:
            return True

    try:
        page._download_worker = BusyWorker()  # type: ignore[assignment]  # noqa: SLF001
        page._start_download(remote, [episode], None)  # noqa: SLF001
        assert len(page._download_queue) == 1  # noqa: SLF001

        started: list[tuple] = []
        page._start_download = lambda r, e, q: started.append((r, e, q))  # type: ignore[method-assign]
        page._download_worker = None  # noqa: SLF001
        page._on_download_thread_finished()  # noqa: SLF001
        for _ in range(50):
            qapp.processEvents()
            time.sleep(0.01)
            if started:
                break
        assert started == [(remote, [episode], None)], "队列里的批次没有被接上"

        page._download_queue.append((remote, [episode], None))  # noqa: SLF001
        page._cancel_download()  # noqa: SLF001
        assert not page._download_queue, "取消当前下载时应同时清空排队批次"  # noqa: SLF001
    finally:
        page.shutdown()
