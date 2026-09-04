"""墨读·工作台入口。

兼容三种运行方式：
- python -m modu_workbench          （以包模块运行）
- python src/modu_workbench/__main__.py （以脚本直接运行）
- PyInstaller 单入口打包（workbench.spec）

因此这里一律使用绝对导入，并在以脚本方式执行时把包根目录加入 sys.path。
"""
import os
import sys

if __package__ in (None, ""):
    here = os.path.dirname(os.path.abspath(__file__))
    for root in (os.path.dirname(here), here):
        if root not in sys.path:
            sys.path.insert(0, root)

from modu_workbench.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
