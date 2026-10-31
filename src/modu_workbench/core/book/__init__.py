"""核心模块:解析、存储、书库管理。"""
# 包内 __init__.py 用相对 import(永远正确,不会自引用)
from .parser import Chapter, Book, parse_book
from .storage import Storage, BookRecord, HistoryEntry
from .library import Library

__all__ = ["Chapter", "Book", "parse_book", "Storage", "Library", "BookRecord", "HistoryEntry"]
