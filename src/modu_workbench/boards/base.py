"""板块元数据：声明式定义，便于后续扩展新板块。

新增板块步骤：
1. 在 boards/ 下新建页面模块（继承 QWidget）；
2. 在 app/registry.py 追加一个 BoardSpec；
3. 首页与顶栏自动出现入口。

## 为什么是「字符串 + 懒导入」而不是直接存页面类

`BoardSpec.page` 以前是一个 `Type[QWidget]` 字段，于是 `app/registry.py` 必须在模块顶层
`import` 六个板块的 `board` 模块 —— 每个 board 又拖进它的 core 层（sqlite / requests /
PySide6 各子模块）。实测：**import registry 就要 364ms**，而用户此刻只想看到首页。

改成 `page_target="modu_workbench.boards.book.board:BookBoardPage"` 之后：
- import registry 只剩元数据，几乎零成本；
- 真正的模块只在"第一次点进该板块"时导入（`page` 属性内 `importlib`），
  与 `AppShell` 的"按需构造页面"配套；
- `spec.page` 仍是**页面类**，`isinstance(page, spec.page)` 这类既有用法不受影响
  （访问即解析，解析结果缓存）。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Type

from PySide6.QtWidgets import QWidget


def _import_symbol(target: str) -> type:
    """`"a.b.c:Name"` → 该对象（导入失败抛出真实异常，便于定位）。"""
    module_name, _, attribute = target.partition(":")
    if not module_name or not attribute:
        raise ValueError(f"page_target 必须写成 '模块:属性'，收到 {target!r}")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attribute)
    except AttributeError as error:
        raise ImportError(f"{module_name} 里没有 {attribute}") from error


@dataclass(frozen=True)
class BoardSpec:
    key: str
    title: str
    tagline: str
    description: str
    icon: str
    phase: str          # 卡片角标文案，如 "建设中"
    #: 页面类的导入路径：`"包.模块:类名"`（**不要**在 registry 顶层 import 板块模块）
    page_target: str
    #: 解析缓存（frozen 数据类里用可变容器承载缓存，避免每次访问都 import）
    _page_cache: list = field(default_factory=list, repr=False, compare=False)

    @property
    def page(self) -> Type[QWidget]:
        """板块根页面类型（首次访问时导入模块，之后走缓存）。"""
        if not self._page_cache:
            self._page_cache.append(_import_symbol(self.page_target))
        return self._page_cache[0]

    @property
    def nav_label(self) -> str:
        return self.title
