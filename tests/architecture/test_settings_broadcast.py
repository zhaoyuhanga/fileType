"""设置变更广播（`notify_settings_changed`）的正确性测试。

## 背景

`SettingsPage.save()` 原来只是"把值写进 QSettings 就结束"，**没有任何通知**。
用户在设置里改了东西，已经打开的板块仍拿着旧值 —— 感受是"改了没用"。

现在设置对话框保存完会按页 key 广播 `notify_settings_changed(page_key)`，
板块可订阅并在自己的控件上立即生效。这组用例守住的是**广播本身的正确性**：

- 只广播"真的变了"的页（`save()` 返回 True 才算）；
- 订阅者抛异常不能影响保存流程（否则一个板块的 bug 会让整个设置存不进去）；
- `owner` 销毁后自动退订（否则会回调到已删除的控件 → 崩溃）。
"""
from __future__ import annotations

import pytest

from modu_workbench.ui_kit.settings import base as settings_base


@pytest.fixture(autouse=True)
def clean_subscribers():  # noqa: ANN201
    """每个用例前后清空订阅表，避免相互污染。"""
    settings_base._CHANGE_SUBSCRIBERS.clear()
    yield
    settings_base._CHANGE_SUBSCRIBERS.clear()


def test_notify_reaches_subscriber() -> None:
    """订阅者能收到被保存页面的 key。"""
    received: list[str] = []
    settings_base.subscribe_settings_changed(received.append)

    settings_base.notify_settings_changed("video")
    settings_base.notify_settings_changed("book")

    assert received == ["video", "book"]


def test_unsubscribe_stops_callbacks() -> None:
    """手动退订后不再收到通知。"""
    received: list[str] = []
    settings_base.subscribe_settings_changed(received.append)
    settings_base.unsubscribe_settings_changed(received.append)

    settings_base.notify_settings_changed("video")
    assert received == []


def test_subscriber_exception_does_not_break_save(qapp) -> None:  # noqa: ANN001
    """一个订阅者抛异常，不能影响其它订阅者、也不能冒泡到保存流程。"""
    good: list[str] = []

    def bad_callback(_key: str) -> None:
        raise RuntimeError("订阅方自己的问题")

    settings_base.subscribe_settings_changed(bad_callback)
    settings_base.subscribe_settings_changed(good.append)

    # 关键：不抛异常
    settings_base.notify_settings_changed("video")
    assert good == ["video"], "前面的订阅者炸了，后面的也必须照常收到"


def test_owner_destroyed_auto_unsubscribes(qapp) -> None:  # noqa: ANN001
    """控件销毁后自动退订（否则会回调到已删除的控件 → 崩溃）。"""
    from PySide6.QtWidgets import QWidget

    received: list[str] = []
    owner = QWidget()
    settings_base.subscribe_settings_changed(received.append, owner=owner)

    settings_base.notify_settings_changed("video")
    assert received == ["video"]

    # `destroyed` 由 deleteLater 在事件循环里发出；用 sendPostedEvents
    # 确保它已经被分发（只 processEvents 有时还没轮到 DeferredDelete）。
    from PySide6.QtCore import QCoreApplication, QEvent

    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    qapp.processEvents()

    settings_base.notify_settings_changed("music")
    assert received == ["video"], "owner 销毁后不应再收到通知"


def test_dialog_broadcasts_only_changed_pages(qapp) -> None:  # noqa: ANN001
    """设置对话框只在 `save()` 返回 True 时广播该页。

    注意：对话框的页面是**按需创建**的（只建当前页 + 访问过的页），
    所以这里显式访问两页，模拟"用户切过去看过 video 页"。
    """
    from modu_workbench.ui_kit.settings import SettingsDialog

    received: list[str] = []
    settings_base.subscribe_settings_changed(received.append)

    dialog = SettingsDialog(None, initial="general")
    try:
        # 对话框按需建页：显式把 video 页建出来（模拟用户切过去看过）
        dialog._ensure_page("video")        # noqa: SLF001
        assert "video" in dialog._pages, "video 页应当可以被创建"  # noqa: SLF001

        # 只让 "video" 报告"有改动"，其余页报告"没改"
        for key, page in dialog._pages.items():  # noqa: SLF001
            changed = key == "video"

            def fake_save(_changed: bool = changed) -> bool:
                return _changed

            page.save = fake_save          # type: ignore[method-assign]
        dialog._save()                     # noqa: SLF001
    finally:
        dialog.deleteLater()

    assert received == ["video"], f"只应广播真的改了的页，实际：{received}"
