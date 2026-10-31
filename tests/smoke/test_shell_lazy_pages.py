"""主壳：板块页**按需构造**（启动性能契约）。

改造前 `AppShell.__init__` 会把六个板块的页面全部构造完（实测 offscreen 下 154ms，
真机上还要给每个板块建表格/卡片/播放器）。用户一次只看一个板块，没进过的板块
不该先付这笔钱。这里把契约钉住：

1. 构造主壳后，除首页外**没有**任何板块页被构造；
2. 第一次 `go_page(key)` 才构造，且类型正确；
3. 重复进入复用同一实例（不能每次切页都重建，否则状态全丢）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from modu_workbench.app.registry import ACTIVE_BOARDS  # noqa: E402
from modu_workbench.app.shell import HOME_KEY, AppShell  # noqa: E402


def test_shell_builds_only_home_at_startup(qapp: QApplication) -> None:
    shell = AppShell()
    try:
        assert shell.is_page_built(HOME_KEY) is True
        not_built = [spec.key for spec in ACTIVE_BOARDS if shell.is_page_built(spec.key)]
        assert not_built == [], f"启动时不应构造这些板块页：{not_built}"
        # 栈里只有首页
        assert shell._stack.count() == 1          # noqa: SLF001
    finally:
        shell.close()


def test_first_visit_builds_page_and_reuses_it(qapp: QApplication) -> None:
    shell = AppShell()
    try:
        spec = ACTIVE_BOARDS[0]
        assert shell.is_page_built(spec.key) is False
        shell.go_page(spec.key)
        assert shell.is_page_built(spec.key) is True
        first = shell.current_page()
        assert isinstance(first, spec.page)
        # 切走再切回来：必须还是同一个实例（否则播放/筛选/勾选状态会丢）
        shell.go_page(HOME_KEY)
        shell.go_page(spec.key)
        assert shell.current_page() is first
    finally:
        shell.close()


def test_unknown_page_key_is_ignored(qapp: QApplication) -> None:
    """不存在的 key 不能改当前页，也不能抛异常。"""
    shell = AppShell()
    try:
        shell.go_page(HOME_KEY)
        shell.go_page("definitely-not-a-board")
        assert shell.current_key == HOME_KEY
    finally:
        shell.close()


def test_all_boards_build_and_shutdown(qapp: QApplication) -> None:
    """六个板块都要能按需构造；关闭时逐个 shutdown 不报错。"""
    shell = AppShell()
    for spec in ACTIVE_BOARDS:
        shell.go_page(spec.key)
        assert isinstance(shell.current_page(), spec.page)
    assert shell._stack.count() == len(ACTIVE_BOARDS) + 1   # noqa: SLF001  首页 + 六板块
    shell.close()
