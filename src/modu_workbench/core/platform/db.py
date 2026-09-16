"""共享 SQLite 连接：迁移、事务、设置读写（各板块存储的统一基类）。

设计（v1.0.0）：
- **单库**：全部板块落在同一个 `modu.db`，用板块前缀分表（book_/music_/video_/gallery_/llm_）；
- **迁移**：结构由 `core/platform/migrations/` 的版本化模块管理，
  连接建立时自动把库升到最新版本（`schema_migrations` 记录已应用版本）；
- **设置**：统一 `app_settings(key, value, updated_at)`，键带板块命名空间
  （如 `music/sources/enabled`），子类只需声明 `settings_namespace`；
- **测试隔离**：任何存储类都可以指向任意 sqlite 文件（`Storage(tmp_path/"x.db")`），
  迁移会在该文件上自动建好完整结构，因此单测无需共享全局库。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Sequence

from .migrations import MIGRATIONS, apply_migrations


def run_migrations(conn: sqlite3.Connection) -> int:
    """把连接上的库升到最新版本，返回最终版本号。"""
    return apply_migrations(conn, MIGRATIONS)


class SqliteStore:
    """板块存储的公共基类：连接、迁移、锁、事务与设置。"""

    #: 设置键的命名空间（子类覆盖，如 "music" → 实际键为 music/sources/enabled）
    settings_namespace: str = "app"

    def __init__(self, db_path: str | Path, *, migrate: bool = True):
        self.db_path = str(db_path)
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON;")
            try:
                # WAL：多板块各自持连接读写同一文件时更稳（失败不影响功能，如网络盘）
                self._conn.execute("PRAGMA journal_mode = WAL;")
            except sqlite3.DatabaseError:
                pass
            if migrate:
                run_migrations(self._conn)
            self._conn.commit()

    # ---------- 基础访问 ----------

    def execute(self, sql: str, params: Sequence = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def executemany(self, sql: str, seq: Iterable[Sequence]) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.executemany(sql, seq)
            self._conn.commit()
            return cursor

    def query(self, sql: str, params: Sequence = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def table_columns(self, table: str) -> set[str]:
        """表字段名集合（迁移自检 / 兼容旧库时用）。"""
        return {row["name"] for row in self.query(f"PRAGMA table_info({table})")}

    @contextmanager
    def transaction(self):
        """显式事务（默认每个 execute 自带提交，多语句原子写时用这个）。"""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001  关闭失败不影响退出
                pass

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        self.close()

    # ---------- 设置（app_settings，键带命名空间） ----------

    def _setting_key(self, key: str) -> str:
        return f"{self.settings_namespace}/{key}" if self.settings_namespace else key

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.query_one("SELECT value FROM app_settings WHERE key = ?", (self._setting_key(key),))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (self._setting_key(key), str(value), int(time.time())),
        )

    def delete_setting(self, key: str) -> None:
        self.execute("DELETE FROM app_settings WHERE key = ?", (self._setting_key(key),))

    def all_settings(self) -> dict[str, str]:
        prefix = f"{self.settings_namespace}/" if self.settings_namespace else ""
        rows = self.query("SELECT key, value FROM app_settings ORDER BY key")
        if not prefix:
            return {row["key"]: row["value"] for row in rows}
        return {row["key"][len(prefix):]: row["value"] for row in rows if row["key"].startswith(prefix)}


def schema_version(conn: sqlite3.Connection) -> int:
    """当前库的结构版本（无表返回 0）。"""
    try:
        row = conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
    except sqlite3.DatabaseError:
        return 0
    return int(row["version"] or 0) if row else 0


# ---------- 应用级单例（全部板块共用同一条 modu.db） ----------

_app_store: SqliteStore | None = None


def app_db() -> SqliteStore:
    """应用级数据库（首次调用时迁移 + 导入旧版分库数据）。"""
    global _app_store
    if _app_store is None:
        from .legacy import import_legacy_databases
        from .paths import app_data_dir, app_db_path

        store = SqliteStore(app_db_path())
        import_legacy_databases(store, app_data_dir())
        _app_store = store
    return _app_store


def reset_app_db() -> None:
    """仅供测试：丢弃应用级单例。"""
    global _app_store
    _app_store = None
