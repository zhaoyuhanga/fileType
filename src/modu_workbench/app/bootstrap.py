"""应用启动时的数据初始化：建库、迁移、导入旧版分库。

单独成模块的原因：
- 让"数据准备"与"界面启动"解耦，测试可直接调用 `bootstrap_data()`；
- 任何一步失败都不该拦住应用启动（用户宁可看到空库，也不能看到打不开的窗口）。
"""
from __future__ import annotations

import sys

from modu_workbench.core.platform.db import app_db


def bootstrap_data() -> bool:
    """确保单库可用（迁移到最新版本 + 首次导入旧分库）。返回是否成功。"""
    try:
        app_db()
        return True
    except Exception as error:  # noqa: BLE001
        print(f"[data] 初始化失败：{error}", file=sys.stderr)
        return False
