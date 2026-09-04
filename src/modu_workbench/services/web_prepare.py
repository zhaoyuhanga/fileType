"""把 main 分支 Vite 构建产物整理为可内嵌的 webfront。

- 复制 dist/renderer → webfront
- 写入 qtwebchannel.js（取自 Qt 资源 :/qtwebchannel/qwebchannel.js）
- 写入 bridge_shim.js（Python⇄React 桥）
- 在 index.html 的 React 模块脚本之前注入上述两个脚本
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

SHIM_SOURCE = Path(__file__).resolve().parent / "bridge_shim.js"


def _qwebchannel_js() -> bytes:
    import PySide6.QtWebChannel  # noqa: F401  确保资源被链接
    from PySide6.QtCore import QFile

    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QFile.OpenModeFlag.ReadOnly):
        raise RuntimeError("无法读取 Qt 内置 qwebchannel.js")
    data = bytes(f.readAll())
    f.close()
    return data


def prepare(vite_dist: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in vite_dist.iterdir():
        dest = target / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)

    (target / "qtwebchannel.js").write_bytes(_qwebchannel_js())
    shutil.copy2(SHIM_SOURCE, target / "bridge_shim.js")

    index = target / "index.html"
    html = index.read_text(encoding="utf-8")
    marker = '<script type="module"'
    inject = (
        '<script src="./qtwebchannel.js"></script>\n'
        '<script src="./bridge_shim.js"></script>\n'
    )
    if marker in html and "bridge_shim.js" not in html:
        html = html.replace(marker, inject + marker, 1)
        index.write_text(html, encoding="utf-8")
    print(f"webfront ready -> {target}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python -m modu_workbench.services.web_prepare <vite_dist> <webfront_target>")
        raise SystemExit(2)
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
