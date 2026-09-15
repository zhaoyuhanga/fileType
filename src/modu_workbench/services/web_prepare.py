"""把 main 分支 Vite 构建产物整理为可内嵌的 webfront。

- 清空并重建 webfront（assets/index.html/bridge_shim.js/qtwebchannel.js），
  避免旧哈希 bundle 残留导致体积膨胀或加载到过期代码
- 复制 dist/renderer → webfront
- 写入 qtwebchannel.js（取自 Qt 资源 :/qtwebchannel/qwebchannel.js）
- 写入 bridge_shim.js（Python⇄React 桥）
- 在 index.html 的 React 模块脚本之前注入上述两个脚本
- 归一化 <title> 与注入 window.__MODU_VERSION__（版本单一来源）
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from modu_workbench import __version__

SHIM_SOURCE = Path(__file__).resolve().parent / "bridge_shim.js"
APP_TITLE = "墨读·工作台 · 墨读转换"
STALE_ENTRIES = ("assets", "index.html", "bridge_shim.js", "qtwebchannel.js")


def _qwebchannel_js() -> bytes:
    import PySide6.QtWebChannel  # noqa: F401  确保资源被链接
    from PySide6.QtCore import QFile

    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QFile.OpenModeFlag.ReadOnly):
        raise RuntimeError("无法读取 Qt 内置 qwebchannel.js")
    data = bytes(f.readAll())
    f.close()
    return data


def _clean(target: Path) -> None:
    """删除上一轮构建残留（只删已知产物，保留其他手写文件）。"""
    for name in STALE_ENTRIES:
        victim = target / name
        if victim.is_dir():
            shutil.rmtree(victim, ignore_errors=True)
        elif victim.exists():
            victim.unlink()


def _normalize_html(html: str) -> str:
    """统一标题并注入版本号（幂等）。"""
    import re

    if re.search(r"<title>.*?</title>", html, flags=re.S):
        html = re.sub(r"<title>.*?</title>", f"<title>{APP_TITLE}</title>", html, count=1, flags=re.S)
    marker = '<script type="module"'
    inject = (
        '<script src="./qtwebchannel.js"></script>\n'
        '<script src="./bridge_shim.js"></script>\n'
        f'<script>window.__MODU_VERSION__ = "{__version__}";</script>\n'
    )
    if marker in html and "bridge_shim.js" not in html:
        html = html.replace(marker, inject + marker, 1)
    return html


def prepare(vite_dist: Path, target: Path) -> None:
    if not vite_dist.is_dir():
        raise FileNotFoundError(f"Vite 构建产物不存在：{vite_dist}（先执行 npm run build）")
    if not (vite_dist / "index.html").is_file():
        raise FileNotFoundError(f"Vite 构建产物缺少 index.html：{vite_dist}")

    target.mkdir(parents=True, exist_ok=True)
    _clean(target)

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
    index.write_text(_normalize_html(html), encoding="utf-8")
    print(f"webfront ready -> {target} (v{__version__})")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python -m modu_workbench.services.web_prepare <vite_dist> <webfront_target>")
        raise SystemExit(2)
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
