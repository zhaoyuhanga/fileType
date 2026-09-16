"""数据库结构测试：单库 schema 快照 + 迁移幂等 + 设置命名空间。

这些测试是"结构变更必须被审阅"的闸门：
任何 DDL 改动都会让 `EXPECTED_TABLES` / 索引清单对不上，从而必须显式更新本文件
（并在 `docs/DATABASE.md` 同步），避免悄悄改库。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from modu_workbench.core.platform.db import SqliteStore, schema_version
from modu_workbench.core.platform.migrations import LATEST_VERSION, MIGRATIONS, apply_migrations

EXPECTED_TABLES = {
    "app_settings",
    "book_books", "book_history",
    "music_tracks", "music_playlists", "music_playlist_items", "music_history",
    "music_playlist_remotes",
    "video_videos", "video_playlists", "video_playlist_items", "video_history",
    "video_play_records",
    "gallery_images", "gallery_albums", "gallery_album_items", "gallery_tags",
    "gallery_image_tags", "gallery_edits", "gallery_ai_tasks",
    "llm_profiles", "llm_calls",
    "schema_migrations",
}

# 板块前缀：所有业务表都必须带前缀，防止再次混进通用名
BOARD_PREFIXES = ("book_", "music_", "video_", "gallery_", "llm_")
PLATFORM_TABLES = {"app_settings", "schema_migrations"}


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "modu.db")


def tables(store: SqliteStore) -> set[str]:
    rows = store.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row["name"] for row in rows if not row["name"].startswith("sqlite_")}


def indexes(store: SqliteStore) -> set[str]:
    rows = store.query("SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'")
    return {row["name"] for row in rows}


def test_schema_has_expected_tables(store: SqliteStore) -> None:
    assert tables(store) == EXPECTED_TABLES


def test_business_tables_are_board_prefixed(store: SqliteStore) -> None:
    for name in tables(store) - PLATFORM_TABLES:
        assert name.startswith(BOARD_PREFIXES), f"{name} 缺少板块前缀（板块隔离靠前缀区分）"


def test_index_names_do_not_collide(store: SqliteStore) -> None:
    """索引名在单库里是全局的：必须唯一且带板块前缀（否则后建的会静默失败）。"""
    names = [name for name in indexes(store) if name.startswith("idx_")]
    assert len(names) == len(set(names))
    for name in names:
        assert name.startswith("idx_" + BOARD_PREFIXES[0]) or any(
            name.startswith("idx_" + prefix) for prefix in BOARD_PREFIXES
        ), f"{name} 缺少板块前缀"


def test_foreign_keys_use_new_table_names(store: SqliteStore) -> None:
    """外键必须指向带前缀的表（改名时最容易漏的地方）。"""
    rows = store.query("SELECT sql FROM sqlite_master WHERE type = 'table'")
    for row in rows:
        sql = row["sql"] or ""
        for match in __import__("re").finditer(r"REFERENCES\s+(\w+)", sql):
            assert match.group(1) in EXPECTED_TABLES, f"外键指向了未知表：{match.group(1)}"


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "modu.db"
    first = SqliteStore(path)
    assert schema_version(first._conn) == LATEST_VERSION          # noqa: SLF001
    before = tables(first)
    first.close()

    # 再开一次：不应重复执行迁移，也不应报错
    second = SqliteStore(path)
    assert tables(second) == before
    rows = second.query("SELECT version FROM schema_migrations ORDER BY version")
    assert [row["version"] for row in rows] == [module.VERSION for module in MIGRATIONS]
    second.close()

    # 直接再跑一遍迁移函数也应幂等
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    assert apply_migrations(conn) == LATEST_VERSION
    conn.close()


def test_settings_are_namespaced(store: SqliteStore) -> None:
    """板块设置落到 app_settings，键带板块命名空间，互不覆盖。"""
    from modu_workbench.core.music import MusicStorage
    from modu_workbench.core.video import VideoStorage

    music = MusicStorage(store.db_path)
    video = VideoStorage(store.db_path)
    music.set_setting("sources/enabled", "kuwo,itunes")
    video.set_setting("sources/enabled", "cms_360")
    assert music.get_setting("sources/enabled") == "kuwo,itunes"
    assert video.get_setting("sources/enabled") == "cms_360"

    raw = {row["key"] for row in store.query("SELECT key FROM app_settings")}
    assert "music/sources/enabled" in raw          # 落库键带命名空间
    assert "video/sources/enabled" in raw


def test_store_query_helpers_and_transaction(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "modu.db")
    store.execute("INSERT INTO app_settings (key, value) VALUES (?,?)", ("a", "1"))
    assert store.query_one("SELECT value FROM app_settings WHERE key = ?", ("a",))["value"] == "1"
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?,?)", ("b", "2"))
            raise RuntimeError("回滚")
    # 事务失败必须整体回滚
    assert store.query_one("SELECT value FROM app_settings WHERE key = ?", ("b",)) is None
    store.close()
