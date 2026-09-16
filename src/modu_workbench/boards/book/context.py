"""墨软书库的应用级单例（板块内共享；其他板块不直接引用）。"""
from __future__ import annotations

from modu_workbench.core.book import Library, Storage
from modu_workbench.core.platform.paths import app_db_path

_storage: Storage | None = None
_library: Library | None = None


def storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage(app_db_path())
    return _storage


def library() -> Library:
    global _library
    if _library is None:
        _library = Library(storage())
    return _library
