"""应用级共享上下文：唯一书库实例（跨板块复用）。

初始化只发生在主线程（板块构造期）；Python GIL 保证单次赋值安全，
无需额外锁（曾观察到 Qt offscreen 下 threading.Lock 被外部因素阻塞的现象）。
"""
from __future__ import annotations

from modu_workbench.core.music import MusicLibrary, MusicStorage, MusicPlayer
from modu_workbench.core.reader import Library, Storage

from .config import ensure_legacy_migration, music_db_path, music_dir

_storage: Storage | None = None
_library: Library | None = None
_music_storage: MusicStorage | None = None
_music_library: MusicLibrary | None = None
_music_player: MusicPlayer | None = None


def storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage(ensure_legacy_migration())
    return _storage


def library() -> Library:
    global _library
    if _library is None:
        _library = Library(storage())
    return _library


def music_storage() -> MusicStorage:
    global _music_storage
    if _music_storage is None:
        _music_storage = MusicStorage(music_db_path())
    return _music_storage


def music_library() -> MusicLibrary:
    global _music_library
    if _music_library is None:
        _music_library = MusicLibrary(music_storage(), music_dir())
    return _music_library


def music_player() -> MusicPlayer:
    """应用级单例播放器：切换板块/页面时音乐不中断。"""
    global _music_player
    if _music_player is None:
        _music_player = MusicPlayer()
    return _music_player
