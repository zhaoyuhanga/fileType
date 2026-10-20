"""跨板块共享单例的**合法中介**（`services` 层）。

## 它为什么必须存在（不是历史包袱）

其他板块内部用 `from . import context as app_context`（板块自带单例）；
而 `boards/convert` 需要把转换产物**导入书库**（`library().import_path(...)`）。
`boards/convert` 直接 `import` `boards.book` 或 `app.context` 都会被架构闸门拦下：

    boards/<板块> → 同板块包、boards.base、core/<同板块>、core.platform、core.llm、ui_kit、services
    boards/<板块> ✗→ boards/<其他板块>、core/<其他板块>，也不允许反向依赖 app 层

`services` 是闸门允许的共享层，因此本模块就是那条**唯一合规的通道**：
按名字转发到 `app.context` 的单例工厂（`library` / `video_registry` / ...），
不做任何缓存与额外逻辑。

**别把它当死代码删掉**：v2.0.0 优化时曾经删过一次，`tests/architecture/
test_architecture.py::test_layer_rules_hold` 立刻报
`boards.convert -> modu_workbench.app.context` 违规 —— 闸门是对的，
删掉它只会把人逼去违反分层。
"""
from __future__ import annotations

from modu_workbench.app import context as _context
from modu_workbench.app.context import __all__  # noqa: F401  对外暴露同样的名字集合


def __getattr__(name: str):  # noqa: ANN201  PEP 562 懒转发
    return getattr(_context, name)
