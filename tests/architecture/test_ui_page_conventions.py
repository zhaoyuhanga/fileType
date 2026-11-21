"""页面约定测试：状态助手与兼容属性的静态约束（防"脚本改坏结构"式回归）。

背景：v1.0.0 把各板块的状态/进度统一到 `TaskBar` 后，页面里出现了
`self._report(...)` / `self._busy(...)` 这类调用，以及只读属性 `_status`。
一旦某个类调用了助手却没定义（或 `@property` 被插到别的方法前），
`compileall` 查不出来、只有运行时才炸 —— 这里用静态检查兜住。

## v2.3.0：助手可以来自共享混入

这五个助手原本在乐库 5 个页面里逐字复制了 5 遍（影视还有 3 份 `_note_fallback`），
v2.3.0 收敛成 `ui_kit.components.task_status.TaskStatusMixin`。因此本闸门放行
**显式声明了该混入的类**：它们不再各自定义助手是设计意图，而不是漏定义。

放行的边界故意写得很窄（只认 `TaskStatusMixin` 这一个名字）：
换别的基类、拼错名字、忘了 `import`，都仍然按"未定义"判失败。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BOARDS = REPO / "src" / "modu_workbench" / "boards"

HELPERS = ("_report", "_busy", "_idle", "_set_progress", "_note_fallback")

#: 由共享混入统一提供的助手（页面侧不再重复定义）。键是基类名，值是它提供的方法。
#: 只登记"实现与语义都由一处决定"的混入；新增条目要求同时在
#: `tests/architecture/test_ui_task_status.py` 里有用例钉住其行为。
MIXIN_HELPERS: dict[str, set[str]] = {
    "TaskStatusMixin": set(HELPERS),
}


def board_files() -> list[Path]:
    return sorted(path for path in BOARDS.rglob("*.py") if "__pycache__" not in path.parts)


def classes_of(path: Path) -> list[ast.ClassDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def methods_of(node: ast.ClassDef) -> set[str]:
    return {child.name for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))}


def provided_helpers(node: ast.ClassDef) -> set[str]:
    """该类可用的状态助手 = 自己定义的 + 显式声明的共享混入提供的。"""
    provided = methods_of(node)
    for base in node.bases:
        provided |= MIXIN_HELPERS.get(ast.unparse(base), set())
    return provided


def called_helpers(node: ast.ClassDef) -> set[str]:
    called: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) and child.value.id == "self":
            if child.attr in HELPERS:
                called.add(child.attr)
    return called


@pytest.mark.parametrize("path", board_files(), ids=lambda path: path.name)
def test_helper_calls_are_defined(path: Path) -> None:
    """调用状态助手的类必须自己定义、或显式继承提供它们的共享混入。"""
    for node in classes_of(path):
        missing = called_helpers(node) - provided_helpers(node)
        assert not missing, (
            f"{path.name} 的 {node.name} 调用了未定义的助手：{sorted(missing)}"
            "（要么在本类里定义，要么继承 ui_kit.components.task_status.TaskStatusMixin）"
        )


def test_registered_mixins_really_provide_helpers() -> None:
    """登记进 `MIXIN_HELPERS` 的混入必须**真的有**这些方法（防止闸门被空登记绕过）。"""
    from modu_workbench.ui_kit.components import task_status

    for mixin_name, helpers in MIXIN_HELPERS.items():
        mixin = getattr(task_status, mixin_name, None)
        assert mixin is not None, f"{mixin_name} 不在 ui_kit.components.task_status 里"
        missing = sorted(helpers - {name for name in dir(mixin)})
        assert not missing, f"{mixin_name} 缺少这些助手实现：{missing}"


@pytest.mark.parametrize("path", board_files(), ids=lambda path: path.name)
def test_property_decorator_is_attached(path: Path) -> None:
    """每个 @property 后面必须紧跟 def（脚本插错位置会让 _status 变成 property 对象）。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "@property":
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
            assert following.startswith("def "), (
                f"{path.name}:{index + 1} @property 后面不是方法定义（实际：{following[:40]!r}）"
            )


@pytest.mark.parametrize("path", board_files(), ids=lambda path: path.name)
def test_readonly_properties_not_reassigned(path: Path) -> None:
    """定义了只读 `_status` / `_progress` 属性的类，不得再给它们赋值。"""
    text = path.read_text(encoding="utf-8")
    for node in classes_of(path):
        methods = methods_of(node)
        for name in ("_status", "_progress"):
            if name not in methods:
                continue
            assert not re.search(rf"self\.{name}\s*=", text), (
                f"{path.name} 的 {node.name} 给只读属性 {name} 赋值（会抛 property has no setter）"
            )


def test_pages_expose_task_bar_parameter() -> None:
    """接入统一任务条的页面构造函数要接受 task_bar（否则板块注入会 TypeError）。"""
    checked = 0
    for path in board_files():
        text = path.read_text(encoding="utf-8")
        if "self._task = task_bar" not in text:
            continue
        checked += 1
        assert re.search(r"task_bar(?::[^,)=]+)?\s*=", text), (
            f"{path.name} 使用了 task_bar 但构造签名未声明该参数"
        )
    assert checked >= 6, f"应当已有多个页面接入任务条，实际 {checked}"
