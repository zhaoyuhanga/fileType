"""打包闸门：**动态导入目标必须写进 PyInstaller 的 hiddenimports**。

## 为什么需要（v2.1.0 定版事故）

为了让启动更快，v2.0.1 把板块页改成了「字符串 + importlib 懒导入」：

    BoardSpec(page_target="modu_workbench.boards.book.board:BookBoardPage")
    # boards/base.py:  importlib.import_module(module_name)

`app/context.py` 的应用级单例同样按名字 importlib 解析。

**PyInstaller 的静态分析看不见字符串里的模块名** —— 漏了哪个，开发机（源码）永远正常，
只有打包产物会炸：

    Unhandled exception in script
    Failed to execute script '__main__' due to unhandled exception:
    No module named 'modu_workbench.boards.book.board'

当时的真实情况：`boards.convert/music/video/gallery/document.board` 都在 spec 里，
唯独 `boards.book.board` 与 5 个 `*.context` 不在 —— 首屏点「墨软书库」就崩。

更糟的是**两道既有检查都放过了它**：
1. `$required` 模块核验只看 exe 字节流里有没有这个**名字**（注释/字符串也算命中）；
2. 打包后的 `MODU_CHECK_DEPS` 自检在创建 `QApplication` 之前就 return 了，
   根本不会走到"点板块"这一步。

所以这里补一道**静态**闸门：把运行期真实的目标集合，与两个 spec 的
`hiddenimports` 逐项对齐。新增板块或单例时，这条会先失败。
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SPECS = ("workbench.spec", "workbench_mac.spec")


def _dynamic_targets() -> set[str]:
    """运行期的动态导入目标：板块页面 + app.context 的懒单例。"""
    from modu_workbench.app.context import _LAZY
    from modu_workbench.app.registry import ACTIVE_BOARDS

    targets = set(_LAZY.values())
    targets.update(spec.page_target.split(":", 1)[0] for spec in ACTIVE_BOARDS)
    # 同样是"只出现在字符串/间接引用里"的模块
    targets.add("modu_workbench.ui_kit.theme_icons")
    targets.add("modu_workbench.app.selfcheck")
    return targets


def _hiddenimports(spec: pathlib.Path) -> set[str]:
    text = spec.read_text(encoding="utf-8")
    return set(re.findall(r'"(modu_workbench[\w.]*)"', text))


def test_page_targets_are_importable() -> None:
    """`page_target` 指向的模块与属性必须真实存在（拼错在这里就暴露，不必等打包）。"""
    import importlib

    from modu_workbench.app.registry import ACTIVE_BOARDS

    problems: list[str] = []
    for spec in ACTIVE_BOARDS:
        module_name, _, attribute = spec.page_target.partition(":")
        try:
            module = importlib.import_module(module_name)
        except Exception as error:  # noqa: BLE001
            problems.append(f"{spec.key}: 导入 {module_name} 失败：{error}")
            continue
        if not hasattr(module, attribute):
            problems.append(f"{spec.key}: {module_name} 里没有 {attribute}")
    assert problems == [], "\n".join(problems)


def test_lazy_context_targets_are_importable() -> None:
    """`app.context` 的懒单例目标必须都能解析（属性名也要对）。"""
    import importlib

    from modu_workbench.app.context import _LAZY

    problems: list[str] = []
    for attribute, module_name in sorted(_LAZY.items()):
        try:
            module = importlib.import_module(module_name)
        except Exception as error:  # noqa: BLE001
            problems.append(f"{attribute}: 导入 {module_name} 失败：{error}")
            continue
        if not hasattr(module, attribute):
            problems.append(f"{attribute}: {module_name} 里没有该属性")
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("spec_name", SPECS)
def test_dynamic_targets_are_bundled(spec_name: str) -> None:
    """每个动态导入目标都必须出现在 spec 的 hiddenimports 里，否则冻结后 ImportError。"""
    spec = REPO_ROOT / spec_name
    if not spec.is_file():  # macOS spec 在 Windows 上也可能不参与构建，但仍应存在
        pytest.skip(f"{spec_name} 不存在")
    declared = _hiddenimports(spec)
    missing = sorted(_dynamic_targets() - declared)
    assert missing == [], (
        f"{spec_name} 的 hiddenimports 漏了这些**动态导入**目标：\n  "
        + "\n  ".join(missing)
        + "\n\nPyInstaller 静态分析看不到 importlib 字符串里的模块名，"
        "漏了会「装完启动失败：No module named ...」。"
    )


def test_specs_declare_the_same_modu_workbench_modules() -> None:
    """两个 spec 的 `modu_workbench.*` 清单必须一致（Windows/macOS 不能漂移）。"""
    windows, mac = (_hiddenimports(REPO_ROOT / name) for name in SPECS)
    only_windows = sorted(windows - mac)
    only_mac = sorted(mac - windows)
    assert not only_windows and not only_mac, (
        f"仅 workbench.spec 有：{only_windows}\n仅 workbench_mac.spec 有：{only_mac}"
    )
