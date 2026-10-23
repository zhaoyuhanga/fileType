"""结构迁移：按版本顺序执行，已应用的版本记录在 `schema_migrations`。

新增结构变更的步骤：
1. 在 `core/platform/migrations/` 下加 `v2_xxx.py`（`VERSION`、`NOTE`、`apply(conn)`）；
2. 在下面 `MIGRATIONS` 元组里注册；
3. 补一个迁移测试（`tests/test_db_schema.py`），并在 `docs/DATABASE.md` 更新表结构。
**已发布的迁移模块不得再修改**（老用户已经跑过了）。

## v2.0.0 移除了 v3_anime

第七板块「墨软动漫」在 v1.1.0 里对应 `v3_anime`（7 张 `anime_*` 表）。
v2.0.0 移除板块时**没有删掉这张"已发布迁移"的痕迹，也没有写"删表"迁移**：

- 从 v1.1.0 升级上来的库，`schema_migrations` 里仍有 `version=3` 记录，
  `anime_*` 表连同用户当年追的番/进度/弹幕索引**原样保留**（只是不再有任何代码读它）；
  这样做不静默销毁用户数据，也让"降级回 v1.1.0"依然可用；
- 全新安装只跑到 v2，**不会创建**这些表。
- 想回收空间的用户可执行 `docs/DATABASE.md` 末尾给出的 `DROP TABLE anime_*` 片段。

因为 `apply_migrations` 按"已应用就跳过"工作，`schema_version()` 高于
`LATEST_VERSION` 是**合法状态**（老库），不会被当成损坏。

## 版本号有空洞：v3 从未存在

`MIGRATIONS` 现在是 v1 / v2 / **v4**。v3 是当年动漫板块用掉又撤掉的号，
这里**故意不复用** —— 从 v1.1.0 升级上来的库 `schema_migrations` 里已经有 `version=3`
（对应那 7 张 `anime_*` 表），如果新迁移也叫 v3，会被"已应用就跳过"逻辑直接跳过，
索引就永远建不上。所以新加索引用 **v4**，不要为了"好看"把号改小。
"""
from __future__ import annotations

import sqlite3
import time

from . import v1_initial, v2_document, v4_indexes

MIGRATIONS: tuple = (v1_initial, v2_document, v4_indexes)

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
