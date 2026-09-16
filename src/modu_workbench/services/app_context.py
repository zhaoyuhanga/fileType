"""兼容入口：应用级上下文已拆分到 `app.context` 与各板块自己的 `context.py`。

v1.0.0 重构后：
- 板块内部使用 `from . import context as app_context`（板块自带单例，互不牵动）；
- 设置页 / 测试等跨板块调用用 `from modu_workbench.app import context`；
- 本模块仅为既有引用保留，逐名字懒转发，不做任何缓存与额外逻辑。
"""
from __future__ import annotations

from modu_workbench.app import context as _context
from modu_workbench.app.context import __all__  # noqa: F401  对外暴露同样的名字集合


def __getattr__(name: str):  # noqa: ANN201  PEP 562 懒转发
    return getattr(_context, name)
