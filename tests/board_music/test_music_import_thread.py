"""乐库批量导入：必须在后台线程执行，不能冻住界面。

## 为什么要这道闸门

导入 1000 首歌要 5~15 秒（每首都要读音频元数据、算时长、写库）。
原来 `_import()` 在 **UI 线程**同步调用 `library.import_paths()`，
这段时间整个窗口不响应 —— 用户以为程序卡死，往往再点一次，于是导入两遍。

图库早就有 `ImportWorker(QThread)`，乐库一直漏了。本文件守住三件事：

1. `_import()` **立即返回**（不阻塞调用线程）；
2. 真正的导入工作发生在**非主线程**；
3. 完成后在主线程刷新界面（跨线程只能通过信号回传）。
"""
from __future__ import annotations

import threading
import time

import pytest

from modu_workbench.boards.music.widgets import ImportWorker


class FakeLibrary:
    """替身曲库：记录调用线程，并按需拖慢以模拟真实耗时。"""

    def __init__(self, delay: float = 0.0, raise_error: Exception | None = None) -> None:
        self.delay = delay
        self.raise_error = raise_error
        self.calls: list[str] = []
        self.threads: list[str] = []
        self.progress: list[tuple[int, int, str]] = []

    def import_paths(self, paths, *, category: str = "", on_progress=None, **_kwargs):  # noqa: ANN001, ANN003
        self.threads.append(threading.current_thread().name)
        total = len(list(paths))
        for index, path in enumerate(paths, start=1):
            if self.delay:
                time.sleep(self.delay)
            self.calls.append(path)
            if on_progress:
                on_progress(index, total, f"导入中 {index}/{total}")
        if self.raise_error is not None:
            raise self.raise_error
        return [f"track-{index}" for index in range(total)]


def test_worker_runs_off_the_main_thread(qapp) -> None:  # noqa: ANN001
    """导入工作必须发生在**非主线程**（否则界面照样冻住）。"""
    library = FakeLibrary()
    worker = ImportWorker(library, ["a.mp3", "b.mp3"])
    finished: list[object] = []
    worker.finishedImport.connect(finished.append)

    worker.start()
    assert worker.wait(5000), "worker 没能在 5 秒内结束"
    qapp.processEvents()

    assert library.threads, "import_paths 没有被调用"
    assert library.threads[0] != threading.main_thread().name, (
        f"导入跑在主线程上了（{library.threads[0]}）—— 界面会冻住"
    )
    assert finished and len(finished[0]) == 2


def test_worker_emits_progress(qapp) -> None:  # noqa: ANN001
    """进度要逐条回传（用户需要看到"还在动"）。"""
    library = FakeLibrary()
    worker = ImportWorker(library, [f"{i}.mp3" for i in range(5)])
    seen: list[tuple[int, int, str]] = []
    worker.progressed.connect(lambda d, t, m: seen.append((d, t, m)))

    worker.start()
    assert worker.wait(5000)
    qapp.processEvents()

    assert seen, "没有收到任何进度"
    assert seen[-1][0] == 5 and seen[-1][1] == 5


def test_worker_reports_failure_without_crashing(qapp) -> None:  # noqa: ANN001
    """导入出错要通过信号报告，不能让异常在线程里冒出去。"""
    library = FakeLibrary(raise_error=RuntimeError("磁盘满了"))
    worker = ImportWorker(library, ["a.mp3"])
    failures: list[str] = []
    worker.failed.connect(failures.append)

    worker.start()
    assert worker.wait(5000)
    qapp.processEvents()

    assert failures and "磁盘满了" in failures[0]


def test_worker_can_be_cancelled(qapp) -> None:  # noqa: ANN001
    """`cancel()` 之后能正常结束（不挂死）。"""
    library = FakeLibrary(delay=0.01)
    worker = ImportWorker(library, [f"{i}.mp3" for i in range(50)])

    worker.start()
    time.sleep(0.02)
    worker.cancel()
    assert worker.wait(5000), "取消后 worker 没能在 5 秒内结束"


def test_import_returns_immediately(qapp, monkeypatch) -> None:  # noqa: ANN001
    """`_import()` 必须**立即返回**，不等导入完成。

    这是用户最直接的感受：点"导入"后界面还能动。
    """
    from pathlib import Path as _Path
    from tempfile import mkdtemp

    from modu_workbench.boards.music.library_page import MusicLibraryPage
    from modu_workbench.core.music.storage import MusicStorage

    # 假路径不会被 `scan_audio_files` 认出来，这里直接绕开文件系统扫描
    monkeypatch.setattr(
        "modu_workbench.boards.music.library_page.scan_audio_files",
        lambda paths: list(paths),
    )
    storage = MusicStorage(str(_Path(mkdtemp()) / "music.db"))
    library = FakeLibrary(delay=0.05)
    page = MusicLibraryPage(library, storage, _NullPlayer(), _NullToaster())
    try:
        started = time.perf_counter()
        page._import([f"{i}.mp3" for i in range(40)])
        elapsed = time.perf_counter() - started

        assert elapsed < 0.5, f"_import 阻塞了 {elapsed:.2f} 秒 —— 说明还在 UI 线程同步执行"
        assert page._import_worker is not None, "没有保存 worker 引用（会被 GC 回收导致崩溃）"
        assert page._import_worker.wait(10000), "后台导入没有结束"
    finally:
        page.deleteLater()
        storage.close()


class _NullToaster:
    def info(self, *_a, **_k) -> None:
        return None

    def success(self, *_a, **_k) -> None:
        return None

    def error(self, *_a, **_k) -> None:
        return None


class _NullSignal:
    def emit(self, *_a, **_k) -> None:
        return None


class _NullPlayer:
    """最简播放器替身（页面构造时会握手，但导入用不到）。"""

    def __getattr__(self, name: str):  # noqa: ANN401
        return lambda *args, **kwargs: None

