"""墨软乐库的应用级单例（板块内共享；其他板块不直接引用）。"""
from __future__ import annotations

from modu_workbench.core.music import (
    MusicLibrary,
    MusicPlayer,
    MusicRegistry,
    MusicStorage,
    registry as music_source_registry,
)
from modu_workbench.core.platform.paths import app_db_path, music_dir

_music_storage: MusicStorage | None = None
_music_library: MusicLibrary | None = None
_music_player: MusicPlayer | None = None
_music_registry: MusicRegistry | None = None


def music_storage() -> MusicStorage:
    global _music_storage
    if _music_storage is None:
        _music_storage = MusicStorage(app_db_path())
    return _music_storage


def music_library() -> MusicLibrary:
    global _music_library
    if _music_library is None:
        _music_library = MusicLibrary(music_storage(), music_dir())
    return _music_library


def music_registry() -> MusicRegistry:
    """应用级音源注册表（读取用户启用/优先级配置）。"""
    global _music_registry
    if _music_registry is None:
        _music_registry = music_source_registry()
    return _music_registry


def music_player() -> MusicPlayer:
    """应用级单例播放器：切换板块/页面时音乐不中断。"""
    global _music_player
    if _music_player is None:
        _music_player = MusicPlayer()
    return _music_player
