"""`tests/board_anime/` 的共享夹具。

导入约定：本目录没有 `__init__.py`（与 `tests/board_video/` 等一致），因此测试
模块之间**不要用相对导入**互相引用；需要"过渡期导入兜底"的模块自行 import
`from conftest import import_anime`（pytest 会把本目录加入 sys.path）。
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

ANIME_PACKAGE = "modu_workbench.core.anime"


def real_package_dir() -> Path:
    """定位真实的 `core/anime` 目录（不依赖包能否导入）。"""
    return Path(__file__).resolve().parents[2] / "src" / "modu_workbench" / "core" / "anime"


def install_package_shell() -> None:
    """往 `sys.modules` 放一个不带 `__init__` 副作用的包壳。"""
    shell = types.ModuleType(ANIME_PACKAGE)
    shell.__path__ = [str(real_package_dir())]
    sys.modules[ANIME_PACKAGE] = shell


def import_anime(name: str):
    """导入 `modu_workbench.core.anime.<name>`。

    **为什么需要兜底**：`core/anime/__init__.py` 会一次性导入
    `bangumi/danmaku/engine/providers` 等模块，而它们由不同批次的工作并行落地；
    只要其中一个还没写完，`from modu_workbench.core.anime.storage import …` 就会在
    "导入父包"这一步直接抛 ModuleNotFoundError —— 这与被测代码本身无关。

    正常导入失败时才装包壳重试；子模块之间的相对导入（`from .models import …`）
    依然走真实文件，因此测的仍是真实代码。等 `core/anime/` 全部落地后，
    第一条路径就会成功，这个兜底不影响任何断言。
    """
    target = f"{ANIME_PACKAGE}.{name}"
    try:
        return importlib.import_module(target)
    except ImportError:
        sys.modules.pop(target, None)
        install_package_shell()
        return importlib.import_module(target)


@pytest.fixture(scope="session")
def anime_modules() -> dict:
    """一次性导入被测模块（session 级：包壳只需装一次）。"""
    return {
        "storage": import_anime("storage"),
        "library": import_anime("library"),
        "update_check": import_anime("update_check"),
        "models": importlib.import_module(f"{ANIME_PACKAGE}.models"),
        "rules": importlib.import_module(f"{ANIME_PACKAGE}.rules"),
    }
