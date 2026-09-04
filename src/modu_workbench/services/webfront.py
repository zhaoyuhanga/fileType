"""内嵌 main 分支前端（墨读转换 + 文档预览）的资源定位。

资源目录 webfront/ 由构建脚本生成（Vite 产物 + qtwebchannel/bridge 注入）。
开发时位于 src/modu_workbench/webfront；打包后位于 <bundle>/modu_workbench/webfront
（对应 spec datas: (src/modu_workbench/webfront, modu_workbench/webfront)）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def webfront_dir() -> Path | None:
    override = os.environ.get("MODU_WEBFRONT")
    if override:
        candidate = Path(override)
        if (candidate / "index.html").is_file():
            return candidate
    # 打包环境
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        frozen = Path(meipass) / "modu_workbench" / "webfront"
        if (frozen / "index.html").is_file():
            return frozen
    # 开发环境：src/modu_workbench/webfront
    dev = Path(__file__).resolve().parent.parent / "webfront"
    if (dev / "index.html").is_file():
        return dev
    return None
