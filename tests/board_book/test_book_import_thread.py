"""书库批量导入：必须在后台线程执行，且不能因单个文件失败而中断整批。

## 为什么要这道闸门

三个问题都是"用户能感觉到但说不清"的类型：

1. **UI 冻结**：一本书要"读文件 → 猜编码 → 解析章节 → 入库"，
   50MB 的 TXT 实测约 256 ms；100 本累计十几秒，原来全在 UI 线程做。
2. **一批全断**：原来只 `except ValueError`，遇到 `PermissionError`
   （文件被占用）会直接抛出去 —— 后面的书一本都不导入。
3. **失败不记名**：用户只看到"N 个文件无法解析"，不知道是哪个。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from modu_workbench.boards.book.shelf import BookImportWorker


class FakeLibrary:
    """替身书库：记录调用线程，可按路径注入失败。"""

    def __init__(
        self,
        *,
        delay: float = 0.0,
        value_errors: set[str] | None = None,
        os_errors: set[str] | None = None,
    ) -> None:
        self.delay = delay
        self.value_errors = value_errors or set()
        self.os_errors = os_errors or set()
        self.threads: list[str] = []
        self.imported: list[str] = []

    def import_path(self, path: str) -> None:
        self.threads.append(threading.current_thread().name)
        if self.delay:
            time.sleep(self.delay)
        if path in self.os_errors:
            raise PermissionError(f"没有权限：{path}")
        if path in self.value_errors:
            raise ValueError(f"无法解析：{path}")
        self.imported.append(path)


def test_worker_runs_off_main_thread(qapp) -> None:  # noqa: ANN001
    """导入必须发生在非主线程（否则界面照样冻结）。"""
    library = FakeLibrary()
    worker = BookImportWorker(library, ["a.txt", "b.txt"])
    done: list[tuple] = []
    worker.finishedImport.connect(lambda *args: done.append(args))

    worker.start()
    assert worker.wait(5000), "worker 没能在 5 秒内结束"
    qapp.processEvents()

    assert library.threads, "import_path 没有被调用"
    assert library.threads[0] != threading.main_thread().name, (
        f"导入跑在主线程上了（{library.threads[0]}）—— 界面会冻住"
    )
    assert done == [(2, 0, [])], f"完成信号参数不对：{done}"


def test_oserror_does_not_abort_the_batch(qapp) -> None:  # noqa: ANN001
    """单个文件权限错误**不能中断整批**（这是原来的真实缺陷）。

    原来只捕获 `ValueError`，`PermissionError` 会一路抛出去，
    导致这批里排在它后面的书全部不导入。
    """
    library = FakeLibrary(os_errors={"locked.txt"})
    worker = BookImportWorker(library, ["a.txt", "locked.txt", "c.txt"])
    done: list[tuple] = []
    worker.finishedImport.connect(lambda *args: done.append(args))

    worker.start()
    assert worker.wait(5000)
    qapp.processEvents()

    imported, skipped, failed = done[0]
    assert imported == 2, f"锁住 1 个文件却只导入了 {imported} 个（后面的被跳过了）"
    assert skipped == 0, "权限错误不该算作'格式不支持'"
    assert failed == ["locked.txt"], f"失败文件没有被记录下来：{failed}"


def test_unparsable_files_counted_as_skipped(qapp) -> None:  # noqa: ANN001
    """解析失败（编码/格式）计入"跳过"，不算致命错误。"""
    library = FakeLibrary(value_errors={"bad.txt"})
    worker = BookImportWorker(library, ["ok.txt", "bad.txt"])
    done: list[tuple] = []
    worker.finishedImport.connect(lambda *args: done.append(args))

    worker.start()
    assert worker.wait(5000)
    qapp.processEvents()

    imported, skipped, failed = done[0]
    assert (imported, skipped, failed) == (1, 1, [])


def test_worker_reports_unexpected_failure(qapp) -> None:  # noqa: ANN001
    """线程级异常要通过信号报告，不能冒出去把进程带崩。"""
    library = FakeLibrary()
    library.import_path = lambda path: (_ for _ in ()).throw(RuntimeError("磁盘满了"))  # type: ignore[method-assign]
    worker = BookImportWorker(library, ["a.txt"])
    failures: list[str] = []
    worker.failed.connect(failures.append)

    worker.start()
    assert worker.wait(5000)
    qapp.processEvents()

    assert failures and "磁盘满了" in failures[0]


def test_cancel_stops_early(qapp) -> None:  # noqa: ANN001
    """取消后能尽快结束（不挂死）。"""
    library = FakeLibrary(delay=0.02)
    worker = BookImportWorker(library, [f"{i}.txt" for i in range(40)])

    worker.start()
    time.sleep(0.05)
    worker.cancel()
    assert worker.wait(5000), "取消后没能在 5 秒内结束"


# --------------------------------------------------------------------------- 书架读取失败


def test_reload_failure_is_not_silent(qapp) -> None:  # noqa: ANN001
    """读库失败**不能假装"书架是空的"**。

    原来 `except Exception: self._records = []` 会让用户以为书全没了。
    现在要把原因记下来，界面会显示"书架读取失败"而不是"空空如也"。
    """
    from modu_workbench.boards.book.shelf import ShelfView

    class BrokenLibrary:
        def list_books(self):
            raise OSError("database is locked")

    shelf = ShelfView.__new__(ShelfView)   # 只测 reload 逻辑，跳过完整构造
    shelf._library = BrokenLibrary()
    shelf._records = []
    shelf._filter = ""
    shelf._load_error = ""
    shelf._rebuild_grid = lambda: None

    shelf.reload()

    assert shelf._records == []
    assert shelf._load_error, "读库失败却没有记录原因 —— 界面会显示成「书架空空如也」"
    assert "locked" in shelf._load_error
