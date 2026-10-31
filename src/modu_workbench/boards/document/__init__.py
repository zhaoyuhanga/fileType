"""墨软文档板块（`boards/document/`）：一站式文档工作台。

入口是 `board.DocumentBoardPage`（由 `app/registry.py` 注册）；
子页：viewer / beautify_page / sheet_page / merge_page / loop_page / security_page。
"""
from __future__ import annotations

from . import board  # noqa: F401  `__all__` 声明了它，就必须真的导入（否则 import * 会缺名字）

__all__ = ["board"]
