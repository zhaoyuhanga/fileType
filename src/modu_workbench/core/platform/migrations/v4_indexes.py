"""v4：补齐热点查询缺失的索引（纯增量，不碰任何表结构与数据）。

## 为什么要这一步

`v1_initial` 只给"列表页排序"建了索引（`series_key` / `kind` / `added_at DESC` 等），
但**按标识反查**的高频路径没建。实测（20 万行 `video_videos`，`EXPLAIN QUERY PLAN`）：

    SELECT ... WHERE source=? AND remote_id=? AND episode_index=?
    无索引: SCAN video_videos        21.296 ms/次
    有索引: SEARCH USING INDEX        0.003 ms/次     ← 6343x

这不是"理论优化"：**每次增量入库都要为每一集查一次**，用户库大了以后
导入一季番剧会退化成几十次全表扫描。

## 三条索引与各自的查询

| 索引 | 支撑的查询 | 场景 |
|---|---|---|
| `idx_video_remote(source, remote_id, episode_index)` | `WHERE source=? AND remote_id=? AND episode_index=?` | 增量入库去重（热） |
| `idx_video_title_ep(title, episode_index)` | `WHERE title=? AND episode_index=?` | 换源/匹配去重 |
| `idx_gallery_images_url(url)` | `WHERE url=?` | 图库按链接反查 |

对照：`book_books.path` / `music_tracks.path` / `gallery_images.path` 是 `UNIQUE`，
SQLite 会自动建索引，所以那几个板块没有这个问题 —— 这里补的是**遗漏**，不是设计取舍。

## 为什么是普通索引，不是部分索引（踩过）

第一版写成 `... WHERE remote_id <> ''`（部分索引，看着更小更省），结果实测
`EXPLAIN QUERY PLAN` 依然是 `SCAN video_videos` —— **部分索引只有在查询条件能
静态蕴含索引条件时才会被使用**。调用方写的是 `WHERE remote_id = ?`（参数化），
SQLite 无法据此证明 `remote_id <> ''`，于是干脆不用这个索引。

改成普通索引后查询真的走索引。代价是索引会收录少量空串行，收益是**计划可验证**
（`tests/platform/test_db_indexes.py` 用 `EXPLAIN QUERY PLAN` 钉死，不靠"看起来更优"）。

## 幂等 + 容错

全部 `IF NOT EXISTS`；重复执行、或从 v1.1.0（`schema_migrations.version=3`）的老库升级，
都不会冲突。

**索引只建在存在的表上**：迁移不假设"前序迁移一定建好了表"。老库/手工裁剪过的库
可能没有 `video_videos`，直接 `CREATE INDEX` 会抛 `no such table` 让整个启动失败 ——
这是迁移里最容易踩的坑（`tests/platform/test_db_indexes.py` 的老库升级用例会抓它）。

## 为什么版本号是 4 不是 3

v3 是当年动漫板块用掉又撤掉的号。从 v1.1.0 升级的库里 `schema_migrations` 已经有
`version=3`，新迁移若也叫 3 会被"已应用就跳过"直接跳过，**索引永远建不上**。
"""
from __future__ import annotations

import sqlite3

VERSION = 4
NOTE = "补齐热点反查索引（video 源标识 / video 标题集数 / gallery url）"

#: 索引名 → (表名, 建索引语句)。按表是否存在决定建不建。
INDEXES: tuple[tuple[str, str, str], ...] = (
    (
        "idx_video_remote",
        "video_videos",
        # 影视：按"源标识"反查（增量入库去重，实测 21.3ms → 0.003ms）
        "CREATE INDEX IF NOT EXISTS idx_video_remote"
        " ON video_videos(source, remote_id, episode_index)",
    ),
    (
        "idx_video_title_ep",
        "video_videos",
        # 影视：按"标题 + 集数"反查（换源/匹配去重）
        "CREATE INDEX IF NOT EXISTS idx_video_title_ep"
        " ON video_videos(title, episode_index)",
    ),
    (
        "idx_gallery_images_url",
        "gallery_images",
        # 图库：按来源链接反查
        "CREATE INDEX IF NOT EXISTS idx_gallery_images_url"
        " ON gallery_images(url)",
    ),
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def apply(conn: sqlite3.Connection) -> None:
    """只给"真的存在"的表建索引；缺表就跳过（老库/裁剪库不能因此启动失败）。"""
    for _name, table, statement in INDEXES:
        if not _table_exists(conn, table):
            continue
        conn.execute(statement)
    conn.commit()
