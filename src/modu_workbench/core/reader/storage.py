"""SQLite 存储:书库 + 上次阅读位置 + 历史记录。

表设计:
- books:        主表(每本书一条)
- reading_pos:  上次阅读位置(每本书一条,UPSERT)
- history:      历史记录(每次"打开"或"读完一章"追加一条)
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class BookRecord:
    id: int
    path: str
    title: str
    author: str
    format: str
    added_at: int
    last_opened_at: Optional[int] = None
    last_chapter_index: int = 0
    last_offset_in_chapter: int = 0
    last_progress: float = 0.0  # 0~1


@dataclass
class HistoryEntry:
    id: int
    book_id: int
    book_title: str
    chapter_index: int
    chapter_title: str
    opened_at: int
    action: str  # 'open' / 'chapter' / 'progress'


class Storage:
    """线程安全的 SQLite 封装。"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS books (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        path            TEXT    UNIQUE NOT NULL,
        title           TEXT    NOT NULL,
        author          TEXT    NOT NULL DEFAULT '',
        format          TEXT    NOT NULL DEFAULT '',
        added_at        INTEGER NOT NULL,
        last_opened_at  INTEGER,
        last_chapter_index     INTEGER NOT NULL DEFAULT 0,
        last_offset_in_chapter INTEGER NOT NULL DEFAULT 0,
        last_progress          REAL    NOT NULL DEFAULT 0.0
    );

    CREATE TABLE IF NOT EXISTS history (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id         INTEGER NOT NULL,
        chapter_index   INTEGER NOT NULL,
        chapter_title   TEXT    NOT NULL DEFAULT '',
        opened_at       INTEGER NOT NULL,
        action          TEXT    NOT NULL DEFAULT 'open',
        FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_history_book ON history(book_id, opened_at DESC);
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        with self._lock:
            self._conn.executescript(self.SCHEMA)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # ---------- books ----------

    def upsert_book(self, path: str, title: str, author: str, fmt: str) -> int:
        """注册一本书;若已存在则更新元数据,返回 book_id。"""
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute("SELECT id FROM books WHERE path = ?", (path,))
            row = cur.fetchone()
            if row:
                book_id = row["id"]
                self._conn.execute(
                    "UPDATE books SET title=?, author=?, format=? WHERE id=?",
                    (title, author, fmt, book_id),
                )
            else:
                cur = self._conn.execute(
                    "INSERT INTO books (path, title, author, format, added_at) VALUES (?,?,?,?,?)",
                    (path, title, author, fmt, now),
                )
                book_id = cur.lastrowid
            self._conn.commit()
            return book_id

    def list_books(self) -> List[BookRecord]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, path, title, author, format, added_at,
                          last_opened_at, last_chapter_index,
                          last_offset_in_chapter, last_progress
                   FROM books
                   ORDER BY COALESCE(last_opened_at, added_at) DESC"""
            ).fetchall()
        return [self._row_to_book(r) for r in rows]

    def get_book(self, book_id: int) -> Optional[BookRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM books WHERE id = ?", (book_id,)
            ).fetchone()
        return self._row_to_book(row) if row else None

    def get_book_by_path(self, path: str) -> Optional[BookRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM books WHERE path = ?", (path,)
            ).fetchone()
        return self._row_to_book(row) if row else None

    def delete_book(self, book_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
            self._conn.commit()

    # ---------- reading position ----------

    def save_position(self, book_id: int, chapter_index: int,
                      offset_in_chapter: int, progress: float) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """UPDATE books
                   SET last_opened_at = ?,
                       last_chapter_index = ?,
                       last_offset_in_chapter = ?,
                       last_progress = ?
                   WHERE id = ?""",
                (now, chapter_index, offset_in_chapter, progress, book_id),
            )
            self._conn.commit()

    # ---------- history ----------

    def add_history(self, book_id: int, chapter_index: int, chapter_title: str,
                    action: str = "open") -> int:
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO history (book_id, chapter_index, chapter_title, opened_at, action)
                   VALUES (?,?,?,?,?)""",
                (book_id, chapter_index, chapter_title, now, action),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_history(self, book_id: Optional[int] = None, limit: int = 200) -> List[HistoryEntry]:
        with self._lock:
            if book_id is None:
                rows = self._conn.execute(
                    """SELECT h.id, h.book_id, b.title AS book_title,
                              h.chapter_index, h.chapter_title, h.opened_at, h.action
                       FROM history h JOIN books b ON b.id = h.book_id
                       ORDER BY h.opened_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT h.id, h.book_id, b.title AS book_title,
                              h.chapter_index, h.chapter_title, h.opened_at, h.action
                       FROM history h JOIN books b ON b.id = h.book_id
                       WHERE h.book_id = ?
                       ORDER BY h.opened_at DESC LIMIT ?""",
                    (book_id, limit),
                ).fetchall()
        return [
            HistoryEntry(
                id=r["id"], book_id=r["book_id"], book_title=r["book_title"],
                chapter_index=r["chapter_index"], chapter_title=r["chapter_title"],
                opened_at=r["opened_at"], action=r["action"],
            )
            for r in rows
        ]

    def clear_history(self, book_id: Optional[int] = None) -> None:
        with self._lock:
            if book_id is None:
                self._conn.execute("DELETE FROM history")
            else:
                self._conn.execute("DELETE FROM history WHERE book_id = ?", (book_id,))
            self._conn.commit()

    # ---------- helpers ----------

    @staticmethod
    def _row_to_book(row) -> BookRecord:
        return BookRecord(
            id=row["id"], path=row["path"], title=row["title"], author=row["author"],
            format=row["format"], added_at=row["added_at"],
            last_opened_at=row["last_opened_at"],
            last_chapter_index=row["last_chapter_index"],
            last_offset_in_chapter=row["last_offset_in_chapter"],
            last_progress=row["last_progress"],
        )


def default_db_path(app_dir: str) -> str:
    """默认 SQLite 文件路径。"""
    return os.path.join(app_dir, "library.db")
