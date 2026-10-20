"""模块契约闸门：`__all__` 必须真的解析得到，且公开导入不能是死代码。

为什么需要这两条（都是本轮优化踩出来的真实故障）：

1. **`__all__` 与属性不一致**：`boards/document/__init__.py` 曾声明 `__all__ = ["board"]`
   却没有导入 `board`，`from ... import *` 会缺名字；
2. **清理"未使用导入"时误删再导出**：用正则批量删导入时，`from PySide6.QtCore import
   Qt, Signal, QThread` 被整行删掉（只想删 `Qt`），于是 `class X(QThread)` 在导入期
   直接 `NameError` —— 如果只跑单测里"恰好没覆盖到那个板块"的用例，这种破坏会漏网；
   而 `ui_kit/theme.py` 这类**门面模块**（再导出令牌）如果被删名字，调用方会
   `ImportError`。

对应两道闸门：
- `test_dunder_all_resolves`：所有 `modu_workbench.*` 模块的 `__all__` 每一项都必须存在；
- `test_public_facades_still_export_their_names`：几个**已知门面**必须仍能取到它们的名字。
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

import modu_workbench

SRC = Path(modu_workbench.__file__).resolve().parent


def _iter_modules() -> list[str]:
    """遍历包内全部模块名（不 import，只按文件发现）。"""
    names: list[str] = []
    for info in pkgutil.walk_packages([str(SRC)], prefix="modu_workbench."):
        names.append(info.name)
    return sorted(names)


#: 门面模块 → 必须仍能取到的名字（再导出，本文件里可能"看起来没用到"）
FACADES: dict[str, tuple[str, ...]] = {
    "modu_workbench.ui_kit.theme": ("DARK", "LIGHT", "TOKENS", "ThemeTokens", "app_qss"),
    "modu_workbench.core.video": ("HlsPlaylist", "VideoLibrary", "parse_m3u8"),
    "modu_workbench.ui_kit.media": ("VideoPlayerDialog", "DanmakuItem", "SubtitleTrack"),
    "modu_workbench.services.dandanplay": ("DandanplayClient", "DanmakuMatch"),
    "modu_workbench.core.platform.textutil": ("normalize_title", "parse_episode_number"),
}


def test_dunder_all_resolves() -> None:
    """每个模块的 `__all__` 里列出的名字都必须真的存在。"""
    problems: list[str] = []
    for name in _iter_modules():
        try:
            module = importlib.import_module(name)
        except Exception as error:  # noqa: BLE001  导入失败本身也要报出来
            problems.append(f"{name}: 导入失败 {type(error).__name__}: {error}")
            continue
        declared = getattr(module, "__all__", None)
        if not declared:
            continue
        missing = [item for item in declared if not hasattr(module, item)]
        if missing:
            problems.append(f"{name}: __all__ 里有不存在的名字 {missing}")
    assert problems == [], "模块契约不完整：\n" + "\n".join(problems)


@pytest.mark.parametrize(("module_name", "names"), FACADES.items())
def test_public_facades_still_export_their_names(module_name: str,
                                                names: tuple[str, ...]) -> None:
    """门面模块必须仍能取到对外名字（防止"清理未使用导入"把再导出删掉）。"""
    module = importlib.import_module(module_name)
    missing = [name for name in names if not hasattr(module, name)]
    assert missing == [], f"{module_name} 缺少对外名字：{missing}"
