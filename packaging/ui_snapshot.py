"""UI 截图工具：把各板块页面离屏渲染成 PNG，用于排版验收与回归对比。

用法：
    .venv\\Scripts\\python.exe packaging\\ui_snapshot.py            # 输出到 docs/ui/
    .venv\\Scripts\\python.exe packaging\\ui_snapshot.py --width 1440 --height 900

产物：`docs/ui/board-<key>.png`（首页/五大板块各一张）。

为什么需要它：v1.0.0 的界面规范（无直角、统一留白、空状态）光靠单测锁不住，
把页面渲染成图后可以人眼过一遍，也能在改动前后对比。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

# 离屏渲染必须先设置，否则 Qt 会尝试连接显示服务
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 用系统临时目录：截图是"看一眼就扔"的工具，不该在仓库里留下数据库
os.environ.setdefault("MODU_DATA_DIR", tempfile.mkdtemp(prefix="modu-ui-snapshot-"))
# 离屏环境默认找不到字体，截图会全是方框：显式指向系统字体目录
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtWidgets import QApplication  # noqa: E402

from modu_workbench.app.registry import ACTIVE_BOARDS  # noqa: E402
from modu_workbench.app.shell import AppShell  # noqa: E402
from modu_workbench.ui_kit.theme import apply_theme  # noqa: E402


def snapshot(app: QApplication, shell: AppShell, key: str, out_dir: Path,
             width: int, height: int) -> Path:
    shell.go_page(key)
    shell.resize(width, height)
    app.processEvents()
    page = shell.current_page()
    page.repaint()
    app.processEvents()
    target = out_dir / f"board-{key}.png"
    page.grab().save(str(target))
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染板块页面截图")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=820)
    parser.add_argument("--out", default=str(REPO / "docs" / "ui"))
    parser.add_argument("--only", default="", help="只渲染某个板块 key（home/book/...）")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv[:1])
    apply_theme(app)

    shell = AppShell()
    shell.resize(args.width, args.height)
    shell.show()
    app.processEvents()

    keys = ["home", *[spec.key for spec in ACTIVE_BOARDS]]
    if args.only:
        keys = [key for key in keys if key == args.only]

    for key in keys:
        target = snapshot(app, shell, key, out_dir, args.width, args.height)
        print(f"已生成 {target.relative_to(REPO)}")

    shell.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
