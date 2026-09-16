"""静态自检：所有 Python 文件可编译，且包内导入的模块都存在。

本机环境无法启动子进程，因此这里改为在测试进程内编译并导入；
每个文件独立编译可以精确定位语法错误位置。
"""
from __future__ import annotations

import compileall
import importlib
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

# 影视板块涉及的全部模块（新增代码的主要风险面）
VIDEO_MODULES = [
    "modu_workbench.core.video",
    "modu_workbench.core.video.models",
    "modu_workbench.core.video.storage",
    "modu_workbench.core.video.library",
    "modu_workbench.core.video.hls",
    "modu_workbench.core.video.downloader",
    "modu_workbench.core.video.sources",
    "modu_workbench.core.video.sources.http",
    "modu_workbench.core.video.sources.base",
    "modu_workbench.core.video.sources.matcher",
    "modu_workbench.core.video.sources.registry",
    "modu_workbench.core.video.sources.providers",
    "modu_workbench.core.video.sources.providers.cms_vod",
    "modu_workbench.core.video.sources.providers.public",
    "modu_workbench.boards.video_board",
    "modu_workbench.boards.video_search",
    "modu_workbench.boards.video_detail",
    "modu_workbench.boards.video_library_page",
    "modu_workbench.boards.video_history",
    "modu_workbench.boards.video_sources",
    "modu_workbench.boards.video_player",
    "modu_workbench.boards.video_widgets",
    "modu_workbench.boards.registry",
    "modu_workbench.services.app_context",
    "modu_workbench.services.config",
    "modu_workbench.services.media_server",
]


def test_all_python_files_compile() -> None:
    """整个 src 目录必须能编译（语法错误会在此暴露）。"""
    assert compileall.compile_dir(str(SRC), quiet=1, force=True, legacy=False)


@pytest.mark.parametrize("module", VIDEO_MODULES)
def test_video_modules_import(module: str) -> None:
    assert importlib.import_module(module) is not None


def test_app_entry_imports_cleanly() -> None:
    """入口与主壳导入无环、无笔误。"""
    import modu_workbench.main as main_module

    assert callable(main_module.main)
    from modu_workbench import app_shell

    assert app_shell.AppShell is not None


def test_video_storage_paths_configured() -> None:
    from modu_workbench.services.config import video_db_path, video_dir, video_download_dir

    assert video_db_path().endswith("video.db")
    assert Path(video_dir()).is_dir()
    assert video_download_dir() is not None


def test_app_context_returns_video_singletons() -> None:
    from modu_workbench.services import app_context

    library = app_context.video_library()
    assert library is app_context.video_library()
    assert library.storage is app_context.video_storage()
    assert library.registry is app_context.video_registry()
    assert app_context.video_registry() is app_context.video_registry()


def test_video_registry_settings_persist_through_app_context() -> None:
    """源设置写入后应能从存储读回（验证配置持久化链路）。"""
    from modu_workbench.services import app_context

    registry = app_context.video_registry()
    storage = app_context.video_storage()
    original = registry.is_enabled("archive")
    try:
        registry.set_enabled("archive", not original)
        registry.save_settings(storage)
        assert app_context.video_storage().get_setting("sources/enabled")
        registry.load_settings(storage)
        assert registry.is_enabled("archive") is (not original)
    finally:
        registry.set_enabled("archive", original)
        registry.save_settings(storage)


def test_new_board_is_listed_on_home_page() -> None:
    """首页卡片来自 ACTIVE_BOARDS，因此第四板块会自动出现。"""
    from modu_workbench.boards.registry import ACTIVE_BOARDS

    titles = [board.title for board in ACTIVE_BOARDS]
    assert titles == ["墨软书库", "墨软转换", "墨软乐库", "墨软影视"]
