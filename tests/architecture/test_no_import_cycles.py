"""架构闸门：模块级导入**不允许成环**。

## 为什么必须有这条（v2.1.0 的真实事故）

拆分 `ui_kit/theme.py` 时把图标生成挪到 `theme_icons.py`：

- `theme.py`（模块级）`from .theme_icons import _icon_urls, _image_rule`
- `theme_icons.py`（模块级）`from .theme import ThemeTokens`

源码单测里 `theme` 总是先被导入（`theme.py` 先执行到那行时 `ThemeTokens` 已绑好），
**1154 个测试全绿、`MODU_CHECK_DEPS` 打包自检也 17/17**；但 PyInstaller 冻结后
模块加载顺序变了（`theme_icons` 先被导入），于是**装完之后点图标启动就失败**：

    ImportError: cannot import name '_icon_urls' from partially initialized
    module 'modu_workbench.ui_kit.theme_icons' (most likely due to a circular import)

打包自检没抓到，是因为自检路径（`MODU_CHECK_DEPS`）在创建 `QApplication`/主壳之前
就 return 了 —— 那条路径根本不会走到主题组装。

## 规则

- **只统计模块级（顶层）导入**：函数体内的延迟导入是拆环的标准手法，不算违规；
- 自环（模块 import 自己）视为违规；
- 发现环就失败，并在报错里打印完整环路径。

## 修法（不要靠"调整导入顺序"糊过去）

1. 真的需要类型 → `if TYPE_CHECKING:` + `from __future__ import annotations`（首选）；
2. 真的需要运行时对象 → 把公共部分下沉到第三个模块（如 `tokens.py`），双方都依赖它；
3. 少数确实互相依赖的 → 其中一方改成函数内导入，并在注释里写明**为什么**。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import modu_workbench

SRC = Path(modu_workbench.__file__).resolve().parent
PACKAGE = "modu_workbench"


def _module_name(path: Path) -> str:
    relative = path.relative_to(SRC.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_of(module: str) -> str:
    """`a.b.c` → `a.b`；`a.b`（包）也要能算出自己的包名。"""
    return module.rsplit(".", 1)[0] if "." in module else module


def _resolve(module: str, node: ast.ImportFrom, is_package: bool) -> list[str]:
    """把 `from ... import X` 解析成可能被导入的绝对模块名。"""
    if node.level == 0:
        base = node.module or ""
    else:
        # 相对导入：`from .` = 当前包（模块则取其父包），`from ..` = 再上一层
        package = module if is_package else _package_of(module)
        parts = package.split(".") if package else []
        up = node.level - 1
        if up:
            parts = parts[:-up] if up <= len(parts) else []
        base = ".".join(parts + ([node.module] if node.module else []))
    if not base.startswith(PACKAGE):
        return []
    names = []
    for alias in node.names:
        if alias.name == "*":
            names.append(base)
        else:
            names.append(f"{base}.{alias.name}")
    return names


def _top_level_imports(path: Path) -> list[str]:
    """只取**模块级**导入（函数体/类体里的延迟导入不算，那是合法的拆环手法）。"""
    module = _module_name(path)
    is_package = path.name == "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:                       # 只遍历顶层，不 walk
        if isinstance(node, ast.Import):
            found.extend(name for name in (a.name for a in node.names)
                         if name.startswith(PACKAGE))
        elif isinstance(node, ast.ImportFrom):
            found.extend(_resolve(module, node, is_package))
    return found


def _graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_name(path)
        graph[module] = set(_top_level_imports(path))
    return graph


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """DFS 找环；返回去重后的环路径列表。"""
    cycles: list[list[str]] = []
    seen_signatures: set[frozenset[str]] = set()
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {name: WHITE for name in graph}
    stack: list[str] = []

    def visit(node: str) -> None:
        color[node] = GREY
        stack.append(node)
        for target in sorted(graph.get(node, ())):
            if target == node:                      # 自环
                signature = frozenset({node})
                if signature not in seen_signatures:
                    seen_signatures.add(signature)
                    cycles.append([node, node])
                continue
            if target not in color:                  # 指向非包内模块（如第三方）
                continue
            if color[target] == GREY:
                start = stack.index(target)
                cycle = stack[start:] + [target]
                signature = frozenset(cycle)
                if signature not in seen_signatures:
                    seen_signatures.add(signature)
                    cycles.append(cycle)
            elif color[target] == WHITE:
                visit(target)
        stack.pop()
        color[node] = BLACK

    for name in sorted(graph):
        if color[name] == WHITE:
            visit(name)
    return cycles


def test_module_level_imports_are_acyclic() -> None:
    """模块级导入图必须无环（否则冻结后可能按另一种顺序导入而启动失败）。"""
    cycles = _find_cycles(_graph())
    if cycles:
        report = "\n".join("  → ".join(cycle) for cycle in cycles)
        pytest.fail(
            "模块级导入成环，冻结后可能启动失败：\n" + report + "\n\n"
            "修法：类型依赖用 `if TYPE_CHECKING:`；共享部分下沉到第三个模块；"
            "确实互依赖则一方改函数内导入并注释说明。"
        )


def test_theme_icons_can_be_imported_before_theme() -> None:
    """本次事故的最小复现：`theme_icons` 先于 `theme` 被导入也必须成功。

    PyInstaller 冻结后就是这个顺序，而源码单测默认反着来 —— 所以单独钉一条。
    """
    import subprocess
    import sys

    code = (
        "import modu_workbench.ui_kit.theme_icons as icons;"
        "import modu_workbench.ui_kit.theme as theme;"
        "assert icons._icon_urls and theme.app_qss;"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=120,
        env={"PYTHONPATH": str(SRC.parent), "PATH": "", "SYSTEMROOT": "C:\\Windows"},
    )
    assert result.returncode == 0, f"导入失败：\n{result.stderr[-2000:]}"
    assert "ok" in result.stdout
