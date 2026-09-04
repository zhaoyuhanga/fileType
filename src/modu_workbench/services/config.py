"""本地数据目录与旧版数据迁移（墨读书库）。

优先级：
1. 环境变量 MODU_DATA_DIR（测试/便携模式）
2. %APPDATA%\\ModuWorkbench（Windows）或 ~/.ModuWorkbench
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

APP_DIR_NAME = "ModuWorkbench"
LEGACY_APP_DIR_NAME = "WinEBook"  # win-e-book 旧数据目录
SUPPORTED_BOOK_EXTENSIONS = (".txt", ".epub")


def app_data_dir() -> Path:
    override = os.environ.get("MODU_DATA_DIR")
    if override:
        path = Path(override)
    elif os.environ.get("APPDATA"):
        path = Path(os.environ["APPDATA"]) / APP_DIR_NAME
    else:
        path = Path.home() / ("." + APP_DIR_NAME)
    path.mkdir(parents=True, exist_ok=True)
    return path


def library_db_path() -> str:
    return str(app_data_dir() / "library.db")


def legacy_db_path() -> Path | None:
    """win-e-book 旧库位置（%APPDATA%\\WinEBook\\library.db）。"""
    if not os.environ.get("APPDATA"):
        return None
    candidate = Path(os.environ["APPDATA"]) / LEGACY_APP_DIR_NAME / "library.db"
    return candidate if candidate.is_file() else None


def ensure_legacy_migration() -> str:
    """首次运行时若发现墨读旧库则复制到新位置（返回实际使用的 db 路径）。"""
    target = library_db_path()
    if Path(target).is_file():
        return target
    legacy = legacy_db_path()
    if legacy is not None:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(legacy), target)
    return target


def walk_book_files(paths: list[str]) -> list[str]:
    """扫描给定文件/文件夹，收集受支持的书本文件（递归、去重）。"""
    result: list[str] = []
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            continue
        if p.is_file():
            if p.suffix.lower() in SUPPORTED_BOOK_EXTENSIONS:
                result.append(str(p))
            continue
        if p.is_dir():
            for child in p.rglob("*"):
                if child.is_file() and child.suffix.lower() in SUPPORTED_BOOK_EXTENSIONS:
                    result.append(str(child))
    return list(dict.fromkeys(result))
