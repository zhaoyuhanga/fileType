"""打包配置完整性：spec 的 datas 路径与自研模块 hiddenimports 必须真实存在。

背景：v1.0.0 移除内嵌前端后，`workbench_mac.spec` 仍指向已删除的
`src/modu_workbench/webfront`（macOS 打包会直接失败），`boards.convert.web`
也留在隐藏导入里。spec 只在打包时被执行，漂移不会被普通单测发现，因此在这里静态校验。

只校验仓库内的东西（`datas` 路径 + `modu_workbench.*` 隐藏导入），
第三方包是否安装交由打包环境负责，避免在纯净环境里误报。
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SPECS = ("workbench.spec", "workbench_mac.spec")
PACKAGE_PREFIX = "modu_workbench"


def _analysis_keywords(spec_path: pathlib.Path) -> dict:
    """取出 spec 里 `Analysis(...)` 的关键字参数（spec 是 Python 文件，按 AST 读最稳）。"""
    tree = ast.parse(spec_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Analysis":
            return {kw.arg: kw.value for kw in node.keywords}
    return {}


def _string_list(node) -> list[str]:
    if not isinstance(node, ast.List):
        return []
    return [item.value for item in node.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)]


def _resolvable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


@pytest.mark.parametrize("spec_name", SPECS)
def test_spec_datas_paths_exist(spec_name: str) -> None:
    keywords = _analysis_keywords(REPO_ROOT / spec_name)
    missing = [src for src in _string_list(keywords.get("datas")) if not (REPO_ROOT / src).exists()]
    assert not missing, f"{spec_name} 的 datas 指向不存在的路径：{missing}"


@pytest.mark.parametrize("spec_name", SPECS)
def test_spec_hiddenimports_resolve(spec_name: str) -> None:
    keywords = _analysis_keywords(REPO_ROOT / spec_name)
    names = [name for name in _string_list(keywords.get("hiddenimports"))
             if name.startswith(PACKAGE_PREFIX)]
    assert names, f"{spec_name} 未声明任何 {PACKAGE_PREFIX} 隐藏导入"
    missing = [name for name in names if not _resolvable(name)]
    assert not missing, f"{spec_name} 的 hiddenimports 指向不存在的模块：{missing}"


def test_specs_share_the_same_project_hiddenimports() -> None:
    """Windows 与 macOS 两个 spec 的自研模块清单必须一致，避免只改一边。"""
    collected = {}
    for spec_name in SPECS:
        keywords = _analysis_keywords(REPO_ROOT / spec_name)
        collected[spec_name] = {
            name for name in _string_list(keywords.get("hiddenimports"))
            if name.startswith(PACKAGE_PREFIX)
        }
    windows, mac = collected[SPECS[0]], collected[SPECS[1]]
    assert windows == mac, (
        f"{SPECS[0]} 独有：{sorted(windows - mac)}；{SPECS[1]} 独有：{sorted(mac - windows)}"
    )
