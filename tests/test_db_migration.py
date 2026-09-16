"""旧版分库 → 单库迁移测试（用真实的旧结构样本演练一次）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from modu_workbench.core.platform.db import SqliteStore
from modu_workbench.core.platform.legacy import import_legacy_databases

# 旧版（v0.3.x）分库的最小结构：只保留与导入相关的列
LEGACY_SCHEMAS = {
    "library.db": """
        CREATE TABLE books (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', format TEXT NOT NULL DEFAULT '',
            added_at INTEGER NOT NULL);
        CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, book_id INTEGER NOT NULL,
            chapter_index INTEGER NOT NULL, chapter_title TEXT NOT NULL DEFAULT '',
            opened_at INTEGER NOT NULL, action TEXT NOT NULL DEFAULT 'open');
    """,
    "music.db": """
        CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL DEFAULT '', artist TEXT NOT NULL DEFAULT '');
        CREATE TABLE playlists (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'playlist', created_at INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, track_id INTEGER NOT NULL,
            played_at INTEGER NOT NULL);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
    """,
    "video.db": """
        CREATE TABLE videos (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '', remote_id TEXT NOT NULL DEFAULT '',
            episode_index INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE play_records (id INTEGER PRIMARY KEY AUTOINCREMENT, video_id INTEGER NOT NULL,
            episode_label TEXT NOT NULL DEFAULT '', quality TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
    """,
    "gallery.db": """
        CREATE TABLE images (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL,
            sha256 TEXT NOT NULL DEFAULT '', added_at INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'album', created_at INTEGER NOT NULL DEFAULT 0);
    """,
    "llm.db": """
        CREATE TABLE model_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL DEFAULT 'text',
            name TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT 'deepseek');
        CREATE TABLE llm_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
    """,
}


def make_legacy_dir(root: Path) -> None:
    for name, schema in LEGACY_SCHEMAS.items():
        conn = sqlite3.connect(root / name)
        conn.executescript(schema)
        conn.commit()
        conn.close()
    # 各库塞一点数据
    conn = sqlite3.connect(root / "library.db")
    conn.execute("INSERT INTO books (path, title, author, format, added_at) VALUES ('a.txt','书','作者','txt',1)")
    conn.execute("INSERT INTO history (book_id, chapter_index, opened_at) VALUES (1, 0, 1)")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(root / "music.db")
    conn.execute("INSERT INTO tracks (path, title, artist) VALUES ('a.mp3','歌','歌手')")
    conn.execute("INSERT INTO playlists (name, kind, created_at) VALUES ('收藏','favorite',1)")
    conn.execute("INSERT INTO settings (key, value) VALUES ('sources/enabled','kuwo')")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(root / "video.db")
    conn.execute("INSERT INTO videos (title, source, remote_id, episode_index) VALUES ('片','cms_360','1',0)")
    conn.execute("INSERT INTO play_records (video_id, episode_label, quality, url, source, updated_at)"
                 " VALUES (1,'正片','1080P','https://x/1.m3u8','cms_360',1)")
    conn.execute("INSERT INTO settings (key, value) VALUES ('sources/enabled','cms_360,cms_ffzy')")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(root / "gallery.db")
    conn.execute("INSERT INTO images (path, sha256, added_at) VALUES ('p.jpg','abc',1)")
    conn.execute("INSERT INTO albums (name, kind, created_at) VALUES ('旅行','album',1)")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(root / "llm.db")
    conn.execute("INSERT INTO model_profiles (kind, name, provider) VALUES ('text','默认','deepseek')")
    conn.execute("INSERT INTO llm_settings (key, value) VALUES ('active/text','1')")
    conn.commit()
    conn.close()


@pytest.fixture()
def migrated(tmp_path: Path) -> tuple[SqliteStore, Path]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    make_legacy_dir(data_dir)
    store = SqliteStore(data_dir / "modu.db")
    import_legacy_databases(store, data_dir)
    return store, data_dir


def test_all_boards_are_imported(migrated) -> None:  # noqa: ANN001
    store, _ = migrated
    assert store.query_one("SELECT COUNT(*) AS n FROM book_books")["n"] == 1
    assert store.query_one("SELECT COUNT(*) AS n FROM book_history")["n"] == 1
    assert store.query_one("SELECT COUNT(*) AS n FROM music_tracks")["n"] == 1
    assert store.query_one("SELECT COUNT(*) AS n FROM music_playlists")["n"] == 1
    assert store.query_one("SELECT COUNT(*) AS n FROM video_videos")["n"] == 1
    assert store.query_one("SELECT COUNT(*) AS n FROM video_play_records")["n"] == 1
    assert store.query_one("SELECT COUNT(*) AS n FROM gallery_images")["n"] == 1
    assert store.query_one("SELECT COUNT(*) AS n FROM gallery_albums")["n"] == 1
    assert store.query_one("SELECT COUNT(*) AS n FROM llm_profiles")["n"] == 1


def test_board_settings_keep_namespace(migrated) -> None:  # noqa: ANN001
    store, _ = migrated
    keys = {row["key"]: row["value"] for row in store.query("SELECT key, value FROM app_settings")}
    assert keys["music/sources/enabled"] == "kuwo"
    assert keys["video/sources/enabled"] == "cms_360,cms_ffzy"
    assert keys["llm/active/text"] == "1"


def test_legacy_files_are_backed_up(migrated) -> None:  # noqa: ANN001
    _, data_dir = migrated
    backups = {path.name for path in data_dir.glob("*.imported.bak")}
    assert backups == {f"{name}.imported.bak" for name in LEGACY_SCHEMAS}
    # 原文件已改名，再次启动不会重复导入
    assert not (data_dir / "music.db").exists()


def test_import_runs_only_once(migrated) -> None:  # noqa: ANN001
    store, data_dir = migrated
    assert import_legacy_databases(store, data_dir) == []      # 已有标记 → 直接返回
    assert store.query_one("SELECT COUNT(*) AS n FROM music_tracks")["n"] == 1


def test_import_survives_missing_tables(tmp_path: Path) -> None:
    """旧库结构缺表/缺列时必须跳过而不是崩溃（用户可能装了很旧的版本）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = sqlite3.connect(data_dir / "video.db")
    conn.execute("CREATE TABLE videos (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO videos (title) VALUES ('老片')")
    conn.commit()
    conn.close()

    store = SqliteStore(data_dir / "modu.db")
    notes = import_legacy_databases(store, data_dir)
    assert notes, "应给出导入说明"
    row = store.query_one("SELECT title FROM video_videos")
    assert row is not None and row["title"] == "老片"
