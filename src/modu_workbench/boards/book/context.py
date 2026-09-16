"""墨软书库的应用级单例（板块内共享；其他板块不直接引用）。"""
from __future__ import annotations

from modu_workbench.core.book import Library, Storage
from modu_workbench.core.platform.paths import ensure_legacy_migration

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
