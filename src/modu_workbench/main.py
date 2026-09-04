"""入口：创建 QApplication、套用全局主题并启动 AppShell。"""
from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    # 依赖自检（打包验证用）：MODU_CHECK_DEPS=输出路径 时只做检查并退出，不启动界面。
    check_out = os.environ.get("MODU_CHECK_DEPS")
    if check_out:
        return _run_dependency_check(check_out)

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


def _run_dependency_check(output_path: str) -> int:
    import json

    results: dict[str, object] = {}

    def record(name: str, fn) -> None:  # noqa: ANN001
        try:
            results[name] = fn()
        except Exception as error:  # noqa: BLE001
            results[name] = f"ERROR: {error}"

    def check_markdown_extra() -> bool:
        import markdown

        return "<h1>" in markdown.markdown("# 标题", extensions=["extra", "sane_lists"])

    def check_markdown_codeblock() -> bool:
        from modu_workbench.boards.doc_viewer import _render_markdown

        return "<pre" in _render_markdown("# 标题\n\n```json\n{\"a\": 1}\n```")

    def check_json_highlight() -> bool:
        from modu_workbench.boards.doc_viewer import _render_json

        return "color:" in _render_json('{"a": 1}')

    def check_lexer_by_name() -> bool:
        from pygments.lexers import get_lexer_by_name

        return get_lexer_by_name("json") is not None

    def check_webengine_import() -> bool:
        try:
            import PySide6.QtWebEngineWidgets  # noqa: F401
            import PySide6.QtWebEngineCore  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    record("markdown_extra", check_markdown_extra)
    record("markdown_codeblock", check_markdown_codeblock)
    record("json_highlight", check_json_highlight)
    record("lexer_by_name", check_lexer_by_name)
    record("webengine_import", check_webengine_import)

    try:
        with open(output_path, "w", encoding="utf-8") as fp:
            json.dump(results, fp, ensure_ascii=False, indent=2)
    except Exception as error:  # noqa: BLE001
        return 2 if f"write_error={error}" else 2
    return 0 if all(value is True for value in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
