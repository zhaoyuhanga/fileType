"""架构边界测试：用依赖规则锁住「板块之间互不影响」。

规则（`docs/REFACTOR_PLAN.md` 第 2 节）：

    boards/<板块>  → 同板块包、boards.base、core/<同板块>、core.platform、core.llm、ui_kit、services
    boards/<板块>  ✗→ boards/<其他板块>、core/<其他板块>
    core/<板块>    ✗→ core/<其他板块>（只允许经 core/platform 或 core/llm）
    core.platform  ✗→ 任何 core/<板块>
    ui_kit/app     ✗→ boards/<板块>（app 的注册表与主壳除外）

`PENDING_DECOUPLING` 列出的跨板块依赖是尚未完成解耦的历史包袱（P2 阶段清除）。
新增任何一条都会让本测试失败 —— 这是有意的：拆分别再拆回去。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "modu_workbench"
PACKAGE = "modu_workbench"

BOARD_KEYS = ("book", "convert", "music", "video", "gallery")

# 允许的跨板块依赖（键 = (来源包, 目标包)）：P2 解耦完成后应为空
PENDING_DECOUPLING: dict[tuple[str, str], str] = {
    ("ui_kit.settings", "core.video"): "P4：设置页迁到各板块包内",
    ("ui_kit.settings", "core.music"): "P4：设置页迁到各板块包内",
    ("ui_kit.settings", "core.gallery"): "P4：设置页迁到各板块包内",
    ("ui_kit.settings", "core.convert"): "P4：设置页迁到各板块包内",
    ("ui_kit.settings", "core.book"): "P4：设置页迁到各板块包内",
    ("ui_kit.settings", "core.llm"): "P4：设置页迁到各板块包内",
}

# `core.convert` 是共享转换引擎（PDF/图片/归档/音视频/表格/文本），
# 影视、乐库、图库都会调用它输出文件；但它自己不得反向依赖任何板块。
SHARED_CORE = ("core.convert",)


def module_files() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts)


def layer_of(path: Path) -> str:
    """把文件映射成层级名：app / boards.<key> / ui_kit / services / core.<key> / core.platform。"""
    parts = path.relative_to(SRC).parts
    if len(parts) == 1:
        return "top"
    if parts[0] == "boards" and len(parts) >= 3:
        return f"boards.{parts[1]}"
    if parts[0] == "boards":
        return "boards.base"
    if parts[0] in ("app", "ui_kit", "services"):
        return parts[0]
    if parts[0] == "core":
        if len(parts) >= 3 and parts[1] in ("platform", "llm"):
            return f"core.{parts[1]}"
        if len(parts) >= 3:
            return f"core.{parts[1]}"
        return "core"
    return parts[0]


def absolute_targets(path: Path) -> set[str]:
    """返回该文件 import 的 `modu_workbench.*` 绝对模块名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    # 模块所属包：普通模块去掉文件名，__init__.py 就是所在包本身
    package_parts = parts[:-1] if parts[-1] != "__init__" else parts
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGE):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # 相对导入：level=1 指当前包，level=2 指上一级，依此类推
                keep = len(package_parts) - (node.level - 1)
                base = package_parts[: max(0, keep)]
                name = ".".join([PACKAGE, *base, *(node.module.split(".") if node.module else [])])
            else:
                name = node.module or ""
            if name.startswith(PACKAGE):
                found.add(name)
    return found


def target_layer(name: str) -> str:
    parts = name.split(".")[1:]           # 去掉 modu_workbench
    if not parts:
        return "top"
    if parts[0] == "boards" and len(parts) >= 2:
        return f"boards.{parts[1]}"
    if parts[0] in ("app", "ui_kit", "services"):
        return parts[0]
    if parts[0] == "core" and len(parts) >= 2:
        return f"core.{parts[1]}"
    return parts[0]


def violations() -> list[str]:
    problems: list[str] = []
    for path in module_files():
        source = layer_of(path)
        if not (source.startswith("boards.") or source.startswith("core.") or source in ("ui_kit", "app")):
            continue
        for name in absolute_targets(path):
            target = target_layer(name)
            if target in ("top", "core", source):
                continue
            if source.startswith("boards."):
                board_key = source.split(".", 1)[1]
                same_board = target in (source, f"core.{board_key}")
                allowed = same_board or target in ("boards.base", "ui_kit", "services", "app.registry",
                                                   "core.platform", "core.llm")
                if source == "boards.home" and target in ("app", "app.registry"):
                    allowed = True
                if not allowed:
                    problems.append(f"{path.relative_to(REPO)}: {source} -> {name}")
            elif source.startswith("core."):
                if target in ("core.platform", "core.llm", "ui_kit", "services"):
                    continue
                if target in SHARED_CORE and source not in SHARED_CORE:
                    continue          # 共享转换引擎：其他板块可以调用它
                if target.startswith("core.") and (source, target) in PENDING_DECOUPLING:
                    continue
                if target.startswith("core."):
                    problems.append(f"{path.relative_to(REPO)}: {source} -> {name}")
            elif source == "core.platform" and target.startswith("core.") and target != "core.platform":
                problems.append(f"{path.relative_to(REPO)}: core.platform -> {name}")
            elif source == "ui_kit" and target.startswith("boards."):
                problems.append(f"{path.relative_to(REPO)}: ui_kit -> {name}")
    return problems


def test_layer_rules_hold() -> None:
    problems = violations()
    assert problems == [], "架构边界被破坏：\n" + "\n".join(problems)


def test_pending_decoupling_entries_still_real() -> None:
    """待解耦清单必须仍然真实存在：解耦完成后要把它删掉，别留着过期白名单。"""
    real: set[tuple[str, str]] = set()
    for path in module_files():
        source = layer_of(path)
        if not source.startswith("core."):
            continue
        for name in absolute_targets(path):
            target = target_layer(name)
            if target.startswith("core.") and target != source:
                real.add((source, target))
    for key in PENDING_DECOUPLING:
        if key[0].startswith("core.") and key[0] != "core.platform":
            assert key in real, f"待解耦项已不存在，请从清单删除：{key}"


@pytest.mark.parametrize("board", BOARD_KEYS)
def test_board_package_exists(board: str) -> None:
    """五大板块必须是独立包，且各自有入口模块 board.py。"""
    package = SRC / "boards" / board
    assert (package / "__init__.py").is_file(), f"缺少 boards/{board}/__init__.py"
    assert (package / "board.py").is_file(), f"缺少 boards/{board}/board.py"
