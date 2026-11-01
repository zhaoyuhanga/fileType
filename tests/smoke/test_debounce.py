"""输入防抖测试。

## 为什么需要

乐库/影视的搜索框原来直接 `textChanged → reload()`，而 `reload()` 会**查库 +
重建整张表**。打"周杰伦"三个字就是三次查询三次重建 —— 库里几千条时输入会一顿一顿。

`Debouncer` 把连续触发合并成一次：安静 250 ms 才执行；回车可立即执行。
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from modu_workbench.ui_kit.debounce import DEFAULT_DELAY_MS, Debouncer


def test_rapid_triggers_collapse_into_one_call(qapp: QApplication) -> None:
    """连续触发只执行一次，且用的是最后一次的参数。"""
    calls: list[tuple] = []
    debouncer = Debouncer(None, lambda *args: calls.append(args), delay_ms=50)

    for text in ("周", "周杰", "周杰伦"):
        debouncer.trigger(text)
    assert calls == [], "计时未到就执行了，防抖没生效"

    qapp.processEvents()
    _wait(120)
    qapp.processEvents()
    assert calls == [("周杰伦",)], f"应只执行最后一次：{calls}"


def test_flush_runs_immediately(qapp: QApplication) -> None:
    """回车要立即执行，不能让人等 250ms。"""
    calls: list[str] = []
    debouncer = Debouncer(None, lambda text="": calls.append(text), delay_ms=5000)

    debouncer.trigger("周杰伦")
    assert calls == []
    debouncer.flush()
    assert calls == ["周杰伦"], "flush 没有立即执行"
    assert debouncer.pending is False

    _wait(60)
    qapp.processEvents()
    assert calls == ["周杰伦"], "flush 之后定时器又触发了一次"


def test_cancel_drops_pending_trigger(qapp: QApplication) -> None:
    """取消后不应再执行（页面销毁时用）。"""
    calls: list[str] = []
    debouncer = Debouncer(None, lambda text="": calls.append(text), delay_ms=40)

    debouncer.trigger("x")
    debouncer.cancel()
    _wait(100)
    qapp.processEvents()
    assert calls == [], "取消后仍然执行了"


def test_callback_error_does_not_escape(qapp: QApplication) -> None:
    """回调抛异常要被吞掉（防抖不该把界面搞崩）。"""

    def boom() -> None:
        raise RuntimeError("故意失败")

    debouncer = Debouncer(None, boom, delay_ms=30)
    debouncer.trigger()
    _wait(90)
    qapp.processEvents()          # 不抛即通过
    assert debouncer.pending is False


def test_default_delay_is_sane() -> None:
    """默认延迟在"打字停顿"与"响应及时"之间（150~400ms）。"""
    assert 150 <= DEFAULT_DELAY_MS <= 400


def test_boards_wire_debounced_search(qapp: QApplication, tmp_path) -> None:  # noqa: ANN001
    """乐库/影视的搜索框确实接到了防抖器上（而不是直连 reload）。"""
    from modu_workbench.boards.video.library_page import VideoLibraryPage
    from modu_workbench.core.video import VideoLibrary, VideoStorage
    from modu_workbench.ui_kit.toast import Toaster
    from PySide6.QtWidgets import QWidget

    host = QWidget()
    host.resize(200, 100)
    _HOSTS.append(host)               # 宿主必须活到用例结束
    storage = VideoStorage(str(tmp_path / "v.db"))
    page = None
    try:
        library = VideoLibrary(storage, tmp_path / "files")
        page = VideoLibraryPage(library, storage, Toaster(host))
        debouncer = page._search_debounce          # noqa: SLF001
        assert isinstance(debouncer, Debouncer), "影视搜索没有接防抖"

        # 敲三下：只有一次 reload 真正发生（用计数器验证）
        page.reload = lambda: calls.append(1)      # noqa: SLF001
        calls: list[int] = []
        for text in ("a", "ab", "abc"):
            page._keyword.setText(text)            # noqa: SLF001
        assert calls == [], "每敲一键就 reload 了，防抖没接上"
    finally:
        if page is not None:
            page._search_debounce.cancel()      # noqa: SLF001  别留悬挂定时器
            page.close()
            page.deleteLater()
        storage.close()


def _wait(ms: int) -> None:
    """等一小段，但**不自建嵌套事件循环**。

    用 `QEventLoop.exec()` 会在测试进程里层层嵌套事件循环；在 offscreen 下
    多次嵌套之后，解释器退出阶段偶发 0xC0000409（用例全绿、进程却非零退出）。
    改为自旋 `processEvents()`：效果等价（定时器照常到点），但不嵌套循环。
    """
    from PySide6.QtCore import QCoreApplication, QDeadlineTimer

    deadline = QDeadlineTimer(ms)
    while not deadline.hasExpired():
        QCoreApplication.processEvents()


#: Toaster 需要一个活着的宿主控件；模块级持有，避免被回收
_HOSTS: list = []


@pytest.fixture(autouse=True)
def _debounce_cleanup(qapp: QApplication):
    """用例后关掉自己造的宿主控件。

    刻意不调用 `sendPostedEvents(DeferredDelete)`：它是全局的，会把其它测试
    排队中的删除也提前执行（那些测试可能仍持有引用），反而制造悬空对象。
    """
    yield
    for host in list(_HOSTS):
        host.close()
        host.deleteLater()
    _HOSTS.clear()
    qapp.processEvents()
