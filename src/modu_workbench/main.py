"""入口：创建 QApplication、套用全局主题并启动 AppShell。"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME, APP_SLOGAN, __version__
    from .app_shell import AppShell
    from .ui_kit.theme import apply_theme

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


if __name__ == "__main__":
    raise SystemExit(main())
