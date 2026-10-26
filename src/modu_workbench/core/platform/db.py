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
from modu_workbench.core.platform.logging import swallow


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
        #: 是否处于 `transaction()` 块内。块内不逐条提交，退出时统一提交一次。
        self._in_transaction = False
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
            if not self._in_transaction:
                self._conn.commit()
            return cursor

    def _commit(self) -> None:
        """提交一次写操作；**在 `transaction()` 块内跳过**（由块统一提交）。

        为什么要这个包装：各板块 storage 原来是直接 `self._conn.commit()`，
        绕过了 `_in_transaction` 判断，导致 `with store.transaction():`
        对批量写入完全无效（实测 3000 行：逐条 4247ms / 事务 4240ms，几乎没差别）。
        统一走这里之后，块内不再逐条 fsync。
        """
        if not self._in_transaction:
            self._conn.commit()

    def executemany(self, sql: str, seq: Iterable[Sequence]) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.executemany(sql, seq)
            if not self._in_transaction:
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
        """显式事务：块内**延迟提交**，退出时统一提交一次（异常则回滚）。

        ## 为什么原来的实现等于没用

        以前 `execute()` 里是**无条件** `self._conn.commit()`，所以即使写在
        `with store.transaction():` 块里，每调用一次 execute 仍然各自提交一次 ——
        事务只是包了层壳，没有任何批量效果。

        实测 2000 行写入（SQLite WAL，每行一次 fsync）：

            逐条提交 : 2753 ms
            单事务   :   58 ms      ← 47 倍
            executemany : 54 ms    ← 51 倍

        这才是"导入 5000 张照片要 7 秒"的真正原因，不是 SQL 慢。

        现在块内 `execute`/`executemany` 不再提交，由这里统一收尾。
        **块外行为完全不变**（仍逐条提交），所以既有调用方不受影响。

        用法：

            with store.transaction():
                for item in items:
                    store.upsert_image(item)      # 不再逐条 fsync
        """
        with self._lock:
            previous = self._in_transaction
            self._in_transaction = True
            try:
                yield self._conn
                self._in_transaction = previous
                self._conn.commit()
            except Exception:
                self._in_transaction = previous
                self._conn.rollback()
                raise
            finally:
                self._in_transaction = previous

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error as error:  # 关闭失败不影响退出
                swallow("关闭数据库连接", error)

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        self.close()

    # ---------- 设置（app_settings，键带命名空间） ----------

    def _setting_key(self, key: str) -> str:
        return f"{self.settings_namespace}/{key}" if self.settings_namespace else key

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.query_one(
            "SELECT value FROM app_settings WHERE key = ?", (self._setting_key(key),)
        )
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
        return {
            row["key"][len(prefix) :]: row["value"] for row in rows if row["key"].startswith(prefix)
        }


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
