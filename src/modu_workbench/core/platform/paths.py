"""数据目录与文件布局（板块无关）。

优先级：环境变量 `MODU_DATA_DIR`（测试/便携模式）→ `%APPDATA%\\ModuWorkbench` → `~/.ModuWorkbench`。
各板块只从这里取路径，不自己拼目录，保证"数据目录可整体搬移"。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

APP_DIR_NAME = "ModuWorkbench"
LEGACY_APP_DIR_NAME = "WinEBook"  # win-e-book 旧数据目录


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


def app_db_path() -> str:
    """**唯一**应用数据库（v1.0.0 起板块共用一个库，表名带板块前缀）。"""
    return str(app_data_dir() / "modu.db")


# ---------- 墨软书库 ----------

def library_db_path() -> str:
    """兼容旧引用：书库不再是独立库，统一指向 modu.db。"""
    return app_db_path()


# ---------- 墨软乐库 ----------

def music_db_path() -> str:
    """兼容旧引用：乐库不再是独立库，统一指向 modu.db。"""
    return app_db_path()


def music_dir() -> Path:
    """墨软乐库默认存放目录（下载/转换输出/复制入库）。"""
    path = app_data_dir() / "music"
    path.mkdir(parents=True, exist_ok=True)
    return path


def music_download_dir() -> Path:
    """墨软乐库默认下载目录（用户可见的音乐文件夹）。"""
    override = os.environ.get("MODU_MUSIC_DIR")
    path = Path(override) if override else Path.home() / "Music" / "墨软乐库"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------- 墨软影视 ----------

def video_db_path() -> str:
    """兼容旧引用：影视不再是独立库，统一指向 modu.db。"""
    return app_db_path()


def video_dir() -> Path:
    """墨软影视默认存放目录（下载/转换输出/复制入库）。"""
    path = app_data_dir() / "video"
    path.mkdir(parents=True, exist_ok=True)
    return path


def video_download_dir() -> Path:
    """墨软影视默认下载目录（用户可见的视频文件夹）。"""
    override = os.environ.get("MODU_VIDEO_DIR")
    path = Path(override) if override else Path.home() / "Videos" / "墨软影视"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------- 墨软动漫 ----------

def anime_dir() -> Path:
    """墨软动漫的数据根目录（规则/弹幕缓存/导入产出的兜底位置）。

    注意：下载产物**不**默认放这里 —— 与影视一致，用户可见的下载目录由
    设置页决定（默认 `~/Videos/墨软动漫`），数据目录只放"程序自己管"的东西。
    """
    path = app_data_dir() / "anime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def anime_rules_dir() -> Path:
    """用户规则目录（`anime/rules`）。

    内置规则随包只读（`core/anime/rules_builtin/`），这里只放用户导入与订阅的规则；
    `core/anime/rules.py` 的 `user_rules_dir()` 直接指向本函数，因此目录必须自动创建
    （首次运行就打开规则页时不能因为目录不存在而报错）。
    """
    path = anime_dir() / "rules"
    path.mkdir(parents=True, exist_ok=True)
    return path


def anime_danmaku_dir() -> Path:
    """弹幕缓存目录（`anime/danmaku`）。

    弹幕以 `xml/json` 文件落盘、DB 只记索引：官方建议按小时~天级缓存，
    用文件的好处是"清缓存"就是删目录，且不会把几十万条弹幕塞进 SQLite。
    """
    path = anime_dir() / "danmaku"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------- 墨软图库 ----------

def gallery_db_path() -> str:
    """兼容旧引用：图库不再是独立库，统一指向 modu.db。"""
    return app_db_path()


def gallery_dir() -> Path:
    """墨软图库默认存放目录（收集/编辑/AI 产出的图片）。"""
    path = app_data_dir() / "gallery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def gallery_cache_dir() -> Path:
    """墨软图库缩略图缓存目录（可安全清理，会按需重建）。"""
    path = app_data_dir() / "gallery" / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def gallery_import_dir() -> Path:
    """墨软图库默认导入目录（用户可见的图片文件夹）。"""
    override = os.environ.get("MODU_GALLERY_DIR")
    path = Path(override) if override else Path.home() / "Pictures" / "墨软图库"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------- 墨软文档 ----------

def document_db_path() -> str:
    """兼容引用：文档板块不再是独立库，统一指向 modu.db。"""
    return app_db_path()


def document_dir() -> Path:
    """墨软文档默认存放目录（自动快照、拆分产物、导出兜底）。"""
    path = app_data_dir() / "document"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------- 大模型 ----------

def llm_db_path() -> str:
    """兼容旧引用：大模型配置也落在同一个库（llm_* 表）。"""
    return app_db_path()


# ---------- 旧数据 ----------

def legacy_db_path() -> Path | None:
    """win-e-book 旧库位置（%APPDATA%\\WinEBook\\library.db）。"""
    if not os.environ.get("APPDATA"):
        return None
    candidate = Path(os.environ["APPDATA"]) / LEGACY_APP_DIR_NAME / "library.db"
    return candidate if candidate.is_file() else None


def ensure_legacy_migration() -> str:
    """首次运行时若发现墨软书库旧库则复制到新位置（返回实际使用的 db 路径）。"""
    target = library_db_path()
    if Path(target).is_file():
        return target
    legacy = legacy_db_path()
    if legacy is not None:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(legacy), target)
    return target
