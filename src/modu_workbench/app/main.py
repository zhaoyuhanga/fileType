"""入口：创建 QApplication、套用全局主题并启动 AppShell。"""

from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    # 统一数据入口：建库/迁移/导入旧版分库（失败不阻断启动）
    try:
        from .bootstrap import bootstrap_data

        bootstrap_data()
    except Exception as error:  # noqa: BLE001
        print(f"[data] 初始化失败：{error}", file=sys.stderr)

    # 解码后端/硬解环境变量：**必须早于任何 QtMultimedia 使用**
    # （Qt 在首次创建播放器时读取这些变量，之后再改无效）
    try:
        from ..ui_kit.media.decode import apply_decoding_env

        apply_decoding_env()
    except Exception as error:  # noqa: BLE001
        print(f"[media] 解码环境变量注入失败：{error}", file=sys.stderr)

    # 依赖自检（打包验证用）：MODU_CHECK_DEPS=输出路径 时只做检查并退出，不启动界面。
    check_out = os.environ.get("MODU_CHECK_DEPS")
    if check_out:
        return _run_dependency_check(check_out)

    from PySide6.QtWidgets import QApplication

    from .. import APP_NAME, APP_SLOGAN, __version__
    from .shell import AppShell
    from ..ui_kit.theme import apply_theme

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("ModuWorkbench")
    app.setApplicationVersion(__version__)
    apply_theme(app)

    shell = AppShell()
    shell.setWindowTitle(f"{APP_NAME} · {APP_SLOGAN} v{__version__}")
    shell.resize(1180, 760)
    shell.show()
    return app.exec()


def _run_dependency_check(output_path: str) -> int:
    """转发到 `app/selfcheck.py`（保持既有调用点不变）。"""
    from .selfcheck import run_checks

    return run_checks(output_path)


if __name__ == "__main__":
    raise SystemExit(main())
