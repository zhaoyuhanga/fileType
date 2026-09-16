"""页面约定测试：状态助手与兼容属性的静态约束（防"脚本改坏结构"式回归）。

背景：v1.0.0 把各板块的状态/进度统一到 `TaskBar` 后，页面里出现了
`self._report(...)` / `self._busy(...)` 这类调用，以及只读属性 `_status`。
一旦某个类调用了助手却没定义（或 `@property` 被插到别的方法前），
`compileall` 查不出来、只有运行时才炸 —— 这里用静态检查兜住。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BOARDS = REPO / "src" / "modu_workbench" / "boards"

HELPERS = ("_report", "_busy", "_idle", "_set_progress")


def board_files() -> list[Path]:
    return sorted(path for path in BOARDS.rglob("*.py") if "__pycache__" not in path.parts)


def classes_of(path: Path) -> list[ast.ClassDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def methods_of(node: ast.ClassDef) -> set[str]:
    return {child.name for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))}


def called_helpers(node: ast.ClassDef) -> set[str]:
    called: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) and child.value.id == "self":
            if child.attr in HELPERS:
                called.add(child.attr)
    return called


@pytest.mark.parametrize("path", board_files(), ids=lambda path: path.name)
def test_helper_calls_are_defined(path: Path) -> None:
    """调用状态助手的类必须自己定义这些方法（否则运行时 AttributeError）。"""
    for node in classes_of(path):
        missing = called_helpers(node) - methods_of(node)
        assert not missing, f"{path.name} 的 {node.name} 调用了未定义的助手：{sorted(missing)}"


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
def test_status_property_not_reassigned(path: Path) -> None:
    """定义了只读 `_status` 属性的类，不得再给 self._status 赋值。"""
    text = path.read_text(encoding="utf-8")
    for node in classes_of(path):
        methods = methods_of(node)
        if "_status" not in methods:
            continue
        # 类体内出现 self._status = ... 就会被属性拦住
        assert not re.search(r"self\._status\s*=", text), f"{path.name} 的 {node.name} 给只读属性 _status 赋值"


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
