"""数据库索引闸门：热点反查必须走索引，且**必须支持从 v1.1.0 老库升级**。

## 为什么要用"查询计划"而不是计时来验收

计时在 CI/不同机器上抖动大，容易变成偶发失败。`EXPLAIN QUERY PLAN` 是确定性的：
要么 `SCAN`（全表），要么 `SEARCH ... USING INDEX`（走索引）。

## 背景数字（20 万行 video_videos 实测）

    WHERE source=? AND remote_id=? AND episode_index=?
    无索引 SCAN   21.296 ms/次
    有索引 SEARCH  0.003 ms/次   → 6343x

这不是纸面优化：**增量入库每集都要查一次**，库大了导入一季就要扫几十遍全表。

## 另一个必须守住的东西：版本号不能复用

v3 是当年动漫板块用掉又撤掉的号。从 v1.1.0 升级的库里 `schema_migrations` 已经有
`version=3`，如果新迁移改成 v3，会被"已应用就跳过"直接跳过 → **索引永远建不上**。
所以新索引用 v4，并用老库升级用例钉住。
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from modu_workbench.core.platform.db import SqliteStore
from modu_workbench.core.platform.migrations import LATEST_VERSION, MIGRATIONS

#: (说明, SQL, 参数, 必须命中的索引名)
INDEXED_QUERIES = [
    (
        "影视：按源标识+集数反查（增量入库去重，热路径）",
        "SELECT id FROM video_videos WHERE source = ? AND remote_id = ? AND episode_index = ?",
        ("cms", "remote-1", 1),
        "idx_video_remote",
    ),
    (
        "影视：按标题+集数反查（换源/匹配）",
        "SELECT id FROM video_videos WHERE title = ? AND episode_index = ?",
        ("某剧", 1),
        "idx_video_title_ep",
    ),
    (
        "图库：按来源链接反查",
        "SELECT id FROM gallery_images WHERE url = ?",
        ("https://img/1.jpg",),
        "idx_gallery_images_url",
    ),
]


@pytest.fixture()
def big_db(tmp_path):  # noqa: ANN001, ANN201
    """建一个小规模但足够让优化器做选择的库（2 万行，秒级）。"""
    store = SqliteStore(tmp_path / "indexed.db")
    conn = store._conn  # noqa: SLF001  测试直接看底层连接
    conn.executemany(
        "INSERT OR IGNORE INTO video_videos (file_path, title, source, remote_id, episode_index)"
        " VALUES (?, ?, ?, ?, ?)",
        [(f"/v/{i}.mp4", f"剧-{i % 500}", "cms", f"remote-{i}", i % 24) for i in range(20000)],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO gallery_images (path, url) VALUES (?, ?)",
        [(f"/g/{i}.jpg", f"https://img/{i}.jpg") for i in range(20000)],
    )
    conn.commit()
    yield store
    store.close()


@pytest.mark.parametrize(("label", "sql", "params", "index_name"), INDEXED_QUERIES)
def test_hot_query_uses_index(big_db, label: str, sql: str, params: tuple,  # noqa: ANN001
                              index_name: str) -> None:
    """热点反查必须命中对应索引，不允许 SCAN。"""
    conn = big_db._conn  # noqa: SLF001
    plan = "\n".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params))
    assert "SCAN" not in plan, f"{label}\n仍在全表扫描：\n{plan}"
    assert index_name in plan, f"{label}\n没走期望的索引 {index_name}：\n{plan}"


def test_indexes_exist_after_migration(big_db) -> None:  # noqa: ANN001
    """三条索引必须真的被创建（防止迁移写了但没注册）。"""
    names = {
        row[0]
        for row in big_db._conn.execute(  # noqa: SLF001
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    for _label, _sql, _params, index_name in INDEXED_QUERIES:
        assert index_name in names, f"缺少索引 {index_name}"


def test_latest_version_is_not_three() -> None:
    """v3 是撤掉的动漫迁移的号：新迁移不得复用它，否则老库会跳过。"""
    assert LATEST_VERSION > 3
    versions = sorted(module.VERSION for module in MIGRATIONS)
    assert 3 not in versions, (
        "存在 VERSION=3 的迁移：从 v1.1.0 升级的老库 schema_migrations 里已有 version=3，"
        "会被 apply_migrations 跳过，导致该迁移永远不执行。"
    )


def test_upgrade_from_v11_database_still_applies_indexes(tmp_path) -> None:
    """模拟 v1.1.0 的老库：version=1,2,3 已应用（3 是已撤销的动漫迁移），跑升级后索引必须在。

    注意：这里**要先把表和旧的索引建出来**，否则 v4 按"表不存在就跳过"的容错设计
    会什么都不做 —— 那不是缺陷，而是"裁剪库不该因此启动失败"的保护。
    """
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS video_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'other',
            series_key TEXT NOT NULL DEFAULT '', episode_index INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT '', remote_id TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS gallery_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '');
        """
    )
    for version in (1, 2, 3):        # v1.1.0 用户的真实状态
        conn.execute(
            "INSERT OR REPLACE INTO schema_migrations (version, applied_at, note) VALUES (?,?,?)",
            (version, int(time.time()), "legacy"),
        )
    conn.commit()
    conn.close()

    store = SqliteStore(db_path)      # 构造时会补跑未应用的迁移
    try:
        names = {
            row[0]
            for row in store._conn.execute(  # noqa: SLF001
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        for _label, _sql, _params, index_name in INDEXED_QUERIES:
            assert index_name in names, (
                f"老库升级后缺少索引 {index_name} —— "
                "很可能新迁移误用了 v3（老库里已是 3，被跳过）"
            )
        applied = {
            row[0]
            for row in store._conn.execute(  # noqa: SLF001
                "SELECT version FROM schema_migrations"
            )
        }
        assert LATEST_VERSION in applied
    finally:
        store.close()


def test_migration_skips_missing_tables(tmp_path) -> None:
    """表不存在的库不能因为建索引而启动失败（老库/裁剪库的保护）。"""
    db_path = tmp_path / "trimmed.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL,"
        " note TEXT NOT NULL DEFAULT '')"
    )
    conn.commit()
    conn.close()

    store = SqliteStore(db_path)      # 缺 video_videos / gallery_images 也不能抛
    try:
        applied = {
            row[0]
            for row in store._conn.execute(  # noqa: SLF001
                "SELECT version FROM schema_migrations"
            )
        }
        assert LATEST_VERSION in applied, "缺表时迁移也应记为已应用，否则每次启动都重试"
    finally:
        store.close()
