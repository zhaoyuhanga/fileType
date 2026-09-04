"""书库管理:导入、解析、缓存。"""
from __future__ import annotations

import os
from typing import List, Optional

from modu_workbench.core.reader.parser import parse_book, Book
from modu_workbench.core.reader.storage import Storage, BookRecord


class Library:
    """书库:协调解析器与存储。"""

    def __init__(self, storage: Storage):
        self.storage = storage

    def import_path(self, path: str) -> BookRecord:
        """导入一本书(解析 + 入库)。返回 BookRecord。"""
        parsed = parse_book(path)
        if not parsed.ok:
            raise ValueError(parsed.error or "解析失败")
        book_id = self.storage.upsert_book(
            path=parsed.path, title=parsed.title,
            author=parsed.author, fmt=parsed.format,
        )
        rec = self.storage.get_book(book_id)
        assert rec is not None
        return rec

    def import_paths(self, paths: List[str]) -> List[BookRecord]:
        return [self.import_path(p) for p in paths]

    def remove(self, book_id: int) -> None:
        self.storage.delete_book(book_id)

    def list_books(self) -> List[BookRecord]:
        return self.storage.list_books()

    def get_book(self, book_id: int) -> Optional[BookRecord]:
        return self.storage.get_book(book_id)

    def load_for_reading(self, book_id: int) -> tuple[BookRecord, Book]:
        """按需解析一本书用于阅读(并写一条 open 历史)。"""
        rec = self.storage.get_book(book_id)
        if rec is None:
            raise KeyError(f"book_id {book_id} 不存在")
        parsed = parse_book(rec.path)
        if not parsed.ok:
            raise ValueError(parsed.error or "重新解析失败")
        # 记录"打开"行为
        title = parsed.chapters[rec.last_chapter_index].title \
            if 0 <= rec.last_chapter_index < len(parsed.chapters) else ""
        self.storage.add_history(rec.id, rec.last_chapter_index, title, action="open")
        return rec, parsed

    def save_position(self, book_id: int, chapter_index: int,
                      offset_in_chapter: int, progress: float,
                      chapter_title: str = "", action: str = "progress") -> None:
        self.storage.save_position(book_id, chapter_index, offset_in_chapter, progress)
        # 切章时再写一条历史
        if action:
            self.storage.add_history(book_id, chapter_index, chapter_title, action=action)

    def history(self, book_id: Optional[int] = None, limit: int = 200):
        return self.storage.list_history(book_id=book_id, limit=limit)

    @staticmethod
    def supported_extensions() -> tuple[str, ...]:
        return (".txt", ".epub")
