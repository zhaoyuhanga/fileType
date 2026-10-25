"""设置页基类：各板块的设置页实现同一套接口，由设置对话框统一装配。

约定：
- `title` / `icon` 用于左侧列表；
- `load()` 从存储读取当前值填入控件；
- `save()` 把控件值写回存储（QSettings 或板块数据库），返回是否有改动；
- 设置页只负责"本板块"的配置，通用项放 `general.py`。

## 保存之后怎么让板块知道（v2.2.0 之后补的机制）

原来 `save()` 只是"把值写进 QSettings 就结束"，**没有任何通知**：
用户在设置里换了下载目录 / 字体 / 开关，已经打开的板块仍拿着旧值，
必须重启才生效 —— 用户的感受是"改了没用"。

现在 `SettingsPage.save()` 之外增加了一个**模块级广播** `settings_changed(page_key)`：

- 设置对话框保存完，统一发一次（按页 key，例如 `"video"`）；
- 板块想响应就订阅：`subscribe_settings_changed(self._on_settings_changed)`；
- 订阅者用 `subscribe_settings_changed` 注册、控件销毁时自动退订
  （传 `owner=`，用 `QObject.destroyed` 连接解绑，避免悬空回调）。

**没有订阅者的板块不会受影响**（向后兼容）。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

SETTINGS_ORG = "ModuWorkbench"
SETTINGS_APP = "modu-workbench"


def app_settings():  # noqa: ANN201
    """统一的应用级 QSettings 工厂（各板块设置页共用）。"""
    from PySide6.QtCore import QSettings

    return QSettings(SETTINGS_ORG, SETTINGS_APP)


#: 设置变更订阅者：(持有人对象, 回调)。
#:
#: **为什么用 `(holder, callback)` 而不是只存 callback**：
#: `obj.method` 每次访问都会新建一个 bound method 对象，
#: `a.method is a.method` 为 `False` —— 只存 callback 的话"退订"永远匹配不上
#: （这个 bug 是写测试时当场发现的：`unsubscribe(list.append)` 之后回调还在）。
#: 用绑定前的**持有人**（`obj.__self__` 或函数本身）做身份，才能可靠退订。
_CHANGE_SUBSCRIBERS: list[tuple[object, Callable[[str], None]]] = []

#: 与 `_CHANGE_SUBSCRIBERS` 平行的 owner 列表（用于 `owner` 销毁时批量退订）
_SUBSCRIBER_OWNERS: list[QObject | None] = []


def _holder_of(callback: Callable[[str], None]) -> object:
    """取回"是谁的方法"：绑定方法返回 `__self__`，普通函数/内建返回自身。"""
    return getattr(callback, "__self__", callback)


def subscribe_settings_changed(
    callback: Callable[[str], None], *, owner: QObject | None = None
) -> None:
    """订阅"某个板块的设置已保存"。

    参数：
    - `callback(page_key)`：收到**被保存页面的 key**（`"video"` / `"book"` / `"general"`…）；
    - `owner`：传入控件/板块对象，**它销毁时自动退订**，避免悬空回调导致崩溃。
    """
    _CHANGE_SUBSCRIBERS.append((_holder_of(callback), callback))
    _SUBSCRIBER_OWNERS.append(owner)
    if owner is not None:
        def _detach(*_args: object, _cb: Callable[[str], None] = callback) -> None:
            unsubscribe_settings_changed(_cb)

        owner.destroyed.connect(_detach)


def unsubscribe_settings_changed(callback: Callable[[str], None]) -> None:
    """退订（一般不需要：传了 `owner` 就会自动解绑）。"""
    holder = _holder_of(callback)
    keep: list[tuple[object, Callable[[str], None]]] = []
    owners: list[QObject | None] = []
    for index, (item_holder, item_callback) in enumerate(_CHANGE_SUBSCRIBERS):
        if item_holder is holder:
            continue
        keep.append((item_holder, item_callback))
        owners.append(_SUBSCRIBER_OWNERS[index])
    _CHANGE_SUBSCRIBERS[:] = keep
    _SUBSCRIBER_OWNERS[:] = owners


def notify_settings_changed(page_key: str) -> None:
    """广播"某个设置页已保存"。**订阅者抛异常不能影响保存流程**。"""
    from modu_workbench.core.platform.logging import swallow

    for _holder, callback in list(_CHANGE_SUBSCRIBERS):
        try:
            callback(page_key)
        except Exception as error:  # noqa: BLE001  订阅方自己的问题，不能回滚保存
            swallow(f"设置变更通知（{page_key}）", error)


class SettingsPage(QWidget):
    """板块设置页基类。"""

    title = "设置"
    icon = "⚙"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._dirty = False

    # ---------- 子类实现 ----------

    def load(self) -> None:
        """从存储载入当前配置到控件。"""
        return None

    def save(self) -> bool:
        """保存控件值；返回 True 表示发生了改动。"""
        return False

    # ---------- 辅助 ----------

    def mark_dirty(self) -> None:
        self._dirty = True

    @property
    def dirty(self) -> bool:
        return self._dirty

    def hint(self) -> str:
        """页脚提示（可选覆盖）。"""
        return ""


__all__ = [
    "SETTINGS_APP",
    "SETTINGS_ORG",
    "SettingsPage",
    "app_settings",
    "notify_settings_changed",
    "subscribe_settings_changed",
    "unsubscribe_settings_changed",
]
