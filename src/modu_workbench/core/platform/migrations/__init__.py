"""结构迁移：按版本顺序执行，已应用的版本记录在 `schema_migrations`。

新增结构变更的步骤：
1. 在 `core/platform/migrations/` 下加 `v2_xxx.py`（`VERSION`、`NOTE`、`apply(conn)`）；
2. 在下面 `MIGRATIONS` 元组里注册；
3. 补一个迁移测试（`tests/test_db_schema.py`），并在 `docs/DATABASE.md` 更新表结构。
**已发布的迁移模块不得再修改**（老用户已经跑过了）。
"""
from __future__ import annotations

import sqlite3
import time

from . import v1_initial, v2_document

MIGRATIONS: tuple = (v1_initial, v2_document)

LATEST_VERSION = max(module.VERSION for module in MIGRATIONS)


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " applied_at INTEGER NOT NULL,"
        " note TEXT NOT NULL DEFAULT '')"
    )


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    _ensure_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row["version"]) for row in rows}


def apply_migrations(conn: sqlite3.Connection, migrations=MIGRATIONS) -> int:
    """顺序执行未应用的迁移，返回最终版本。幂等：重复调用不会重复执行。"""
    done = applied_versions(conn)
    for module in sorted(migrations, key=lambda item: item.VERSION):
        if module.VERSION in done:
            continue
        module.apply(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_migrations (version, applied_at, note) VALUES (?,?,?)",
            (module.VERSION, int(time.time()), module.NOTE),
        )
        conn.commit()
    return max(done | {module.VERSION for module in migrations})


__all__ = ["LATEST_VERSION", "MIGRATIONS", "applied_versions", "apply_migrations"]
