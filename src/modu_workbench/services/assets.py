"""随包资源定位（应用图标等）。

开发时位于 src/modu_workbench/assets；打包后位于 <bundle>/modu_workbench/assets
（对应 spec datas: (src/modu_workbench/assets, modu_workbench/assets)）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ICON_NAME = "app.ico"


def assets_dir() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        frozen = Path(meipass) / "modu_workbench" / "assets"
        if frozen.is_dir():
            return frozen
    dev = Path(__file__).resolve().parent.parent / "assets"
    return dev if dev.is_dir() else None


def asset_path(name: str) -> Path | None:
    folder = assets_dir()
    if folder is None:
        return None
    candidate = folder / name
    return candidate if candidate.is_file() else None


def app_icon_path() -> Path | None:
    return asset_path(ICON_NAME)
