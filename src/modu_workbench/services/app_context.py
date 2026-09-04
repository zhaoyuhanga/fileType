"""应用级共享上下文：唯一书库实例（跨板块复用）。

初始化只发生在主线程（板块构造期）；Python GIL 保证单次赋值安全，
无需额外锁（曾观察到 Qt offscreen 下 threading.Lock 被外部因素阻塞的现象）。
"""
from __future__ import annotations

from modu_workbench.core.reader import Library, Storage

from .config import ensure_legacy_migration

_storage: Storage | None = None
_library: Library | None = None


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
