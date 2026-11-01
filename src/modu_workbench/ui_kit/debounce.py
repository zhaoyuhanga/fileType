"""输入防抖：把"每敲一个键就查一次库"变成"停下来才查一次"。

## 为什么需要

乐库、影视的搜索框都是这么接的：

```python
self._keyword.textChanged.connect(lambda _text: self.reload())
```

而 `reload()` 会**查数据库 + 重建整张表**（还顺带刷新合集下拉）。用户打
"周杰伦"三个字就是三次查询与三次重建；库里有几千条时，输入会明显一顿一顿的。

## 做法

`Debouncer` 用一个单次 `QTimer`：连续触发时不断把定时器往后推，只有安静下来
`delay_ms` 毫秒才真正执行一次。回车（`returnPressed`）可以 `flush()` 立即执行，
不让人等。

不引入任何依赖，纯 Qt。
"""
from __future__ import annotations

import weakref
from typing import Callable

from PySide6.QtCore import QObject, QTimer

#: 默认延迟：250 ms 是"打字停顿"与"响应及时"之间的常用折中
DEFAULT_DELAY_MS = 250


class Debouncer(QObject):
    """把高频触发合并成一次执行。

    参数
    ----
    callback:
        真正要执行的函数（无参或可接收触发时传入的参数）。
    delay_ms:
        安静多久后执行，默认 250 ms。
    """

    def __init__(
        self,
        parent: QObject | None,
        callback: Callable[..., None],
        *,
        delay_ms: int = DEFAULT_DELAY_MS,
    ) -> None:
        super().__init__(parent)
        # 与 FileDropController 同样理由：绑定方法用弱引用，避免
        # "父对象 → 定时器 → 绑定方法 → 父对象" 的强引用环。
        bound = getattr(callback, "__self__", None)
        if bound is not None and hasattr(callback, "__name__"):
            try:
                self._owner_ref: "weakref.ref | None" = weakref.ref(bound)  # noqa: UP037
                self._method_name: str | None = callback.__name__
                self._callback = None
            except TypeError:
                self._owner_ref, self._method_name, self._callback = None, None, callback
        else:
            self._owner_ref, self._method_name, self._callback = None, None, callback
        self._pending_args: tuple = ()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, int(delay_ms)))
        self._timer.timeout.connect(self._fire)

    # ---------------------------------------------------------------- 接口

    @property
    def delay_ms(self) -> int:
        return self._timer.interval()

    @property
    def pending(self) -> bool:
        """是否有等待中的触发（测试与"有未保存输入"提示可用）。"""
        return self._timer.isActive()

    def trigger(self, *args) -> None:  # noqa: ANN002
        """记录一次触发并重新计时（高频调用只会执行最后一次的参数）。"""
        self._pending_args = args
        self._timer.start()

    def flush(self) -> None:
        """立即执行（回车、或离开页面时用），不再等计时。"""
        if not self._timer.isActive():
            return
        self._timer.stop()
        self._fire()

    def cancel(self) -> None:
        """取消等待中的触发（例如页面被销毁）。"""
        self._timer.stop()
        self._pending_args = ()

    # ---------------------------------------------------------------- 内部

    def _fire(self) -> None:
        args, self._pending_args = self._pending_args, ()
        if self._method_name is not None:
            owner = self._owner_ref() if self._owner_ref is not None else None
            if owner is None:
                return                 # 宿主已销毁：静默跳过
            callback = getattr(owner, self._method_name, None)
        else:
            callback = self._callback
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as error:  # noqa: BLE001  防抖回调失败不该让界面崩
            from modu_workbench.core.platform.logging import swallow

            swallow("防抖回调", error)


__all__ = ["DEFAULT_DELAY_MS", "Debouncer"]
