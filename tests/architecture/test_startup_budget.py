"""启动预算闸门：主壳起来时**不允许**把六个板块都导入进来。

## 为什么需要（v2.1.1 的隐藏缺陷）

v2.0.1 把板块页改成"按需构造"，`AppShell.__init__` 里写了：

    self._factories = {spec.key: spec.page for spec in ACTIVE_BOARDS}   # ← 这里就全导入了

`BoardSpec.page` 是**懒解析属性**，一取就 `importlib.import_module(板块模块)`。
于是"按需构造"只做到了"按需实例化"，**导入**在构造主壳时就全发生了：

- 启动加载模块 **181 / 205**；
- 白付 `requests` 的 95ms（urllib3 63ms + ssl 5ms）；
- `bs4`、`lxml`、`ebooklib` 等重依赖一并被拖进来 —— 而用户此刻只想看到首页。

改成保存 `BoardSpec` 本身、真正 `go_page()` 时才取 `.page` 之后：
启动 24 个模块，`requests` 不再出现，首次进板块付 230ms（一次性，且是用户主动点的）。

## 这条闸门守什么

主壳构造完成时：
1. 已加载的本项目模块数必须低于预算（默认 60，当前实测 24）；
2. `requests` 不得已被导入；
3. 任何 `boards.<板块>.board` / `core.<板块>` 模块都不得已被导入。

如果哪天有人为了省事在 `registry.py` 或 `shell.py` 顶层又写静态 import，这条会先失败。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

#: 主壳起来时允许加载的本项目模块数上限（实测 24，留一倍余量给合理的未来增长）
STARTUP_MODULE_BUDGET = 60

PROBE = r"""
import sys
from PySide6.QtWidgets import QApplication
app = QApplication([])
from modu_workbench.app.shell import AppShell
shell = AppShell()
mw = [m for m in sys.modules if m.startswith("modu_workbench")]
boards = [m for m in mw if ".boards." in m and m.endswith(".board")]
cores = [m for m in mw if m.startswith("modu_workbench.core.")
         and not m.startswith(("modu_workbench.core.platform", "modu_workbench.core.llm"))]
print("COUNT", len(mw))
print("REQUESTS", "requests" in sys.modules)
print("BOARDS", ",".join(sorted(boards)))
print("CORES", ",".join(sorted(cores)))
"""


@pytest.fixture(scope="module")
def startup_report() -> dict[str, object]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["MODU_DATA_DIR"] = env.get("TEMP", "/tmp") + "/modu-startup-probe"
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True, text=True, timeout=300, env=env,
    )
    assert result.returncode == 0, f"启动探针失败：\n{result.stderr[-3000:]}"
    report: dict[str, object] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        report[key] = value
    return report


def test_startup_does_not_import_all_boards(startup_report: dict[str, object]) -> None:
    """构造主壳不得把六个板块都导入进来。"""
    boards = str(startup_report.get("BOARDS", ""))
    assert boards == "", (
        "构造主壳时导入了板块模块：" + boards + "\n"
        "常见原因：`_factories = {spec.key: spec.page ...}` 这类写法会立刻解析懒属性；"
        "应该保存 BoardSpec 本身，等 go_page() 时再取 .page。"
    )


def test_startup_does_not_import_board_cores(startup_report: dict[str, object]) -> None:
    """板块内核（core/<板块>）也不该在启动时被导入。"""
    cores = str(startup_report.get("CORES", ""))
    assert cores == "", f"构造主壳时导入了板块内核：{cores}"


def test_startup_does_not_import_requests(startup_report: dict[str, object]) -> None:
    """`requests` 只在真正联网时才该被导入（它自己就要 95ms）。"""
    assert startup_report.get("REQUESTS") == "False", (
        "启动时导入了 requests —— 检查是否有模块级 `from .http import ...` 或 "
        "`from modu_workbench.core.platform import HttpClient` 之类的提前解析。"
    )


def test_startup_module_count_within_budget(startup_report: dict[str, object]) -> None:
    """启动加载的模块数必须低于预算。"""
    count = int(str(startup_report.get("COUNT", "9999")))
    assert count <= STARTUP_MODULE_BUDGET, (
        f"启动加载了 {count} 个本项目模块，超过预算 {STARTUP_MODULE_BUDGET}。"
        "说明又有模块在启动路径上被提前导入。"
    )
