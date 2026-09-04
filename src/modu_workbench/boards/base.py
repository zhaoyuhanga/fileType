"""板块元数据：声明式定义，便于后续扩展新板块。

新增板块步骤：
1. 在 boards/ 下新建页面模块（继承 QWidget）；
2. 在 boards/registry.py 追加一个 BoardSpec；
3. 首页与顶栏自动出现入口。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Type

from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class BoardSpec:
    key: str
    title: str
    tagline: str
    description: str
    icon: str
    phase: str          # 卡片角标文案，如 "建设中"
    page: Type[QWidget]  # 板块根页面类型

    @property
    def nav_label(self) -> str:
        return self.title
