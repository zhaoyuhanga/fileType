"""板块通用快捷键：Ctrl+F 找、F5 刷新。

## 为什么需要

在此之前全仓只有阅读器、文档、图库注册了零散快捷键，**列表页一个都没有**：

- 用户想搜索，得先用鼠标点进搜索框（列表越长，这个动作越频繁）；
- 数据变了想刷新，得去找「刷新」按钮（有的板块压根没放按钮）。

这两件事的键位是全平台通行的（`Ctrl+F` 找、`F5` 刷新），做成统一的小工具
比在每个板块里各写一遍更省事，也更不容易写漏。

## 约定

快捷键挂在**板块根控件**上（`QShortcut` 以它为 parent），板块销毁时一起消失。
`search` 传 `QLineEdit`；`refresh` 传无参可调用对象。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QLineEdit, QWidget

from modu_workbench.core.platform.logging import swallow

#: 搜索快捷键（`Ctrl+F` 之外再给一个常用替代）
SEARCH_KEYS = ("Ctrl+F", "Ctrl+K")
#: 刷新快捷键（F5 之外给 `Ctrl+R`，与浏览器一致）
REFRESH_KEYS = ("F5", "Ctrl+R")


def install_board_shortcuts(
    widget: QWidget,
    *,
    search: QLineEdit | None = None,
    refresh: Callable[[], None] | None = None,
) -> list[QShortcut]:
    """给板块装上 `Ctrl+F`（聚焦搜索）与 `F5`（刷新）。

    返回创建的快捷键列表（父对象是 `widget`，通常不用管它）。
    """
    shortcuts: list[QShortcut] = []

    if search is not None:
        for keys in SEARCH_KEYS:
            shortcut = QShortcut(QKeySequence(keys), widget)
            shortcut.activated.connect(lambda: focus_search(search))
            shortcuts.append(shortcut)

    if refresh is not None:
        # 与 FileDropController 一致：绑定方法存成 (弱引用对象, 方法名)，
        # 调用时再 getattr 解析 —— 断掉 "控件 → 快捷键 → 绑定方法 → 控件" 的环，
        # 同时保持晚绑定（子类覆写、测试替换实例属性都能生效）。
        owner_ref = None
        method_name = None
        bound = getattr(refresh, "__self__", None)
        if bound is not None and hasattr(refresh, "__name__"):
            try:
                import weakref

                owner_ref = weakref.ref(bound)
                method_name = refresh.__name__
                refresh = None  # type: ignore[assignment]
            except TypeError:
                pass

        def _refresh() -> None:
            target = refresh
            if method_name is not None:
                owner = owner_ref() if owner_ref is not None else None
                if owner is None:
                    return
                target = getattr(owner, method_name, None)
            if target is None:
                return
            try:
                target()
            except Exception as error:  # noqa: BLE001  刷新失败不该让界面崩
                swallow("快捷键刷新", error)

        for keys in REFRESH_KEYS:
            shortcut = QShortcut(QKeySequence(keys), widget)
            shortcut.activated.connect(_refresh)
            shortcuts.append(shortcut)

    return shortcuts


def focus_search(search: QLineEdit) -> None:
    """把焦点移到搜索框并全选（`Ctrl+F` 再按一次就相当于"改搜索词"）。"""
    try:
        # PySide6 的 setFocus 只接受 0 个参数或 Qt.FocusReason，
        # 传 bool 会 TypeError（曾经的写法就是 setFocus(True)，一按 Ctrl+F 就报错）
        from PySide6.QtCore import Qt

        search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        search.selectAll()
    except RuntimeError as error:  # noqa: BLE001  控件已销毁
        swallow("聚焦搜索框", error)


__all__ = ["REFRESH_KEYS", "SEARCH_KEYS", "focus_search", "install_board_shortcuts"]
