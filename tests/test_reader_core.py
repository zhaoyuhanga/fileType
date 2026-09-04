"""墨读书库核心流程测试：storage 持久化 + library 导入/进度/历史。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from modu_workbench.core.reader import Library, Storage


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "library.db")


def _write_txt(path: Path, title: str = "测试书") -> str:
    content = (
        f"正文 第一章 起点\n\n这是第一章的内容。\n\n"
        f"正文 第二章 发展\n\n这是第二章的内容。\n\n"
        f"正文 第三章 结局\n\n这是第三章的内容。"
    )
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_storage_upsert_dedupe_and_get(db_path: str, tmp_path: Path) -> None:
    storage = Storage(db_path)
    try:
        p = _write_txt(tmp_path / "book.txt")
        first = storage.upsert_book(path=p, title="测试书", author="作者", fmt="txt")
        again = storage.upsert_book(path=p, title="测试书改", author="作者", fmt="txt")

        assert first == again  # 同一路径只入库一次
        rec = storage.get_book(first)
        assert rec is not None
        assert rec.title == "测试书改"
        assert rec.path == p
    finally:
        storage.close()


def test_library_import_list_read_and_history(db_path: str, tmp_path: Path) -> None:
    storage = Storage(db_path)
    library = Library(storage)
    try:
        p = _write_txt(tmp_path / "book.txt")
        rec = library.import_path(p)
        assert rec.format == "txt"

        # 重复导入去重：仍只有一本书
        library.import_path(p)
        books = library.list_books()
        assert len(books) == 1
        assert books[0].id == rec.id

        # 解析用于阅读，并写入进度与历史
        record, parsed = library.load_for_reading(rec.id)
        assert record is not None
        assert len(parsed.chapters) >= 3
        library.save_position(rec.id, 1, 0, 0.5, chapter_title=parsed.chapters[1].title, action="chapter")

        after = storage.get_book(rec.id)
        assert after is not None
        assert after.last_chapter_index == 1
        assert after.last_progress == 0.5

        history = library.history(book_id=rec.id)
        assert len(history) >= 2  # open + chapter
        actions = {entry.action for entry in history}
        assert {"open", "chapter"} <= actions
    finally:
        storage.close()


def test_remove_book_cascades_history(db_path: str, tmp_path: Path) -> None:
    storage = Storage(db_path)
    library = Library(storage)
    try:
        p = _write_txt(tmp_path / "book.txt")
        rec = library.import_path(p)
        library.load_for_reading(rec.id)
        assert len(library.history(book_id=rec.id)) >= 1

        library.remove(rec.id)
        assert library.get_book(rec.id) is None
        assert library.history(book_id=rec.id) == []
    finally:
        storage.close()
