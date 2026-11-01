"""把"文件拖进板块就导入"做成一个可复用的小控制器。

## 为什么需要

在此之前全仓 `dragEnterEvent`/`dropEvent` 是 **0 处** —— 一个以文件为核心的
桌面应用，却不能把文件拖进来，只能一层层点「选择文件」。这是最容易被用户
第一眼发现的功能缺口。

## 为什么用事件过滤器，而不是给每个板块写 dragEnterEvent

板块的根控件是各自的 `QWidget` 子类，形状差异很大（书库/图库/影视/转换
各有自己的导入入口）。用 `installEventFilter` 挂在**根控件**上：

- 不动任何板块的类继承关系，也不侵入它们的布局代码；
- 想撤掉只需 `deleteLater()`，不会留下"类里有个永远不用的方法"；
- 顺带覆盖了子控件的拖放（Qt 会把拖放事件发给 `acceptDrops()` 为真的
  **最内层**控件；这里把根控件设为接收，子控件不接收，于是事件冒泡到根）。

## 约定

`handler(paths)` 收到的是**字符串路径列表**：文件是文件本身，目录是目录本身
（由各板块自己决定要不要递归 —— 书库/转换本来就会递归扫描目录）。
"""
from __future__ import annotations

import weakref
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget

from modu_workbench.core.platform.logging import swallow

#: 一次拖拽最多处理多少个条目（防手滑拖进一个巨型目录的根）
MAX_DROPPED_ITEMS = 500


def _iter_urls(mime) -> Iterable[str]:  # noqa: ANN001
    """从 MIME 里取出本地路径（忽略网络 URL 与文本）。"""
    if not mime.hasUrls():
        return []
    paths: list[str] = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        local = url.toLocalFile()
        if not local:
            continue
        # `QUrl.toLocalFile()` 在 Windows 上给的是正斜杠（C:/a/b），
        # 而程序内部与数据库里都是反斜杠。不统一的话，同一个文件的两种写法
        # 会被当成两条（去重、查重、同一路径判断全部失效）。
        try:
            paths.append(str(Path(local)))
        except (OSError, ValueError):
            paths.append(local)
    return paths


class FileDropController(QObject):
    """让一个控件接受"把文件/文件夹拖进来"。

    参数
    ----
    widget:
        接收拖放的根控件（板块主部件）。
    handler:
        `handler(paths: list[str]) -> None`，在 **UI 线程**被调用，
        所以里面若有耗时操作应自行放后台线程（书库/图库/影视的导入函数
        本来就是这样做的）。
    suffixes:
        允许的扩展名（含点，大小写不敏感）。为 `None` 时接受任意文件；
        **目录永远接受**（用户常常是拖一个文件夹进来）。
    hint:
        可选的提示语，拖入被接受时会写进控件的 toolTip，给一点可见反馈。
    """

    def __init__(
        self,
        widget: QWidget,
        handler: Callable[[list[str]], None],
        *,
        suffixes: Sequence[str] | None = None,
        hint: str = "",
    ) -> None:
        super().__init__(widget)
        self._widget = widget
        # **用弱引用持有回调**：板块把 `self._import_paths`（绑定方法）传进来，
        # 强引用会形成 "父控件 → 控制器 → 绑定方法 → 父控件" 的环；
        # PySide 下这种环在解释器退出阶段的回收顺序很不确定，
        # 实测表现为"用例全绿、进程却以 0xC0000409 退出"。
        #
        # 具体做法：绑定方法存成 (弱引用对象, 方法名)，调用时再 `getattr` 解析。
        # 用方法名而不是 `WeakMethod`，是为了保持**晚绑定**语义 ——
        # 子类覆写、或在测试里替换实例属性，都能生效。
        bound = getattr(handler, "__self__", None)
        if bound is not None and hasattr(handler, "__name__"):
            try:
                self._owner_ref: "weakref.ref | None" = weakref.ref(bound)  # noqa: UP037
                self._method_name: str | None = handler.__name__
                self._handler = None
            except TypeError:
                self._owner_ref, self._method_name, self._handler = None, None, handler
        else:
            self._owner_ref, self._method_name, self._handler = None, None, handler

        self._suffixes = {s.lower() for s in suffixes} if suffixes else None
        self._hint = hint
        widget.setAcceptDrops(True)
        widget.installEventFilter(self)

    def _resolve_handler(self) -> Callable[[list[str]], None] | None:
        if self._method_name is not None:
            owner = self._owner_ref() if self._owner_ref is not None else None
            if owner is None:
                return None            # 宿主已销毁：静默跳过
            return getattr(owner, self._method_name, None)
        return self._handler

    # ---------------------------------------------------------------- 判定

    def accepts_path(self, path: str) -> bool:
        """单个路径是否可接收（目录一律可接收）。"""
        try:
            candidate = Path(path)
            if candidate.is_dir():
                return True
            if self._suffixes is None:
                return True
            return candidate.suffix.lower() in self._suffixes
        except OSError as error:  # noqa: BLE001  路径异常就当不接受
            swallow("判断拖入路径", error)
            return False

    def collect(self, mime) -> list[str]:  # noqa: ANN001
        """从 MIME 里挑出可接收的路径（去重、截断到上限）。"""
        picked: list[str] = []
        seen: set[str] = set()
        for path in _iter_urls(mime):
            if path in seen or not self.accepts_path(path):
                continue
            seen.add(path)
            picked.append(path)
            if len(picked) >= MAX_DROPPED_ITEMS:
                break
        return picked

    # ---------------------------------------------------------------- 事件

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001, ANN201
        event_type = event.type()
        if event_type in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            paths = self.collect(event.mimeData())
            if paths:
                event.acceptProposedAction()
                if self._hint:
                    self._widget.setToolTip(self._hint)
            else:
                event.ignore()
            return True

        if event_type == QEvent.Type.DragLeave:
            if self._hint:
                self._widget.setToolTip("")
            return False

        if event_type == QEvent.Type.Drop:
            paths = self.collect(event.mimeData())
            if not paths:
                event.ignore()
                return True
            event.acceptProposedAction()
            if self._hint:
                self._widget.setToolTip("")
            handler = self._resolve_handler()
            if handler is None:
                return True
            try:
                handler(paths)
            except Exception as error:  # noqa: BLE001  导入失败不该让界面崩
                swallow(f"拖入导入（{len(paths)} 项）", error)
            return True

        return super().eventFilter(obj, event)


def accepts_drag(widget: QWidget) -> bool:
    """该控件是否已启用接收拖放（测试与自检用）。"""
    return bool(widget.acceptDrops())


__all__ = ["MAX_DROPPED_ITEMS", "FileDropController", "accepts_drag"]
