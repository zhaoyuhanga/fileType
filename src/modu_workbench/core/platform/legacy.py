"""旧版分库数据导入：把 5 个独立 SQLite 库并进单库 `modu.db`。

v1.0.0 之前每个板块一个库（library.db / music.db / video.db / gallery.db / llm.db），
本模块在应用首次打开单库时把它们的数据搬过来，然后**把旧文件改名为 `*.imported.bak`**
（不删除，用户可自行回退），并在 `app_settings` 里记下 `platform/legacy_imported`。

约定：
- 只导入「旧表存在且新表存在」的表，按列名对齐（`INSERT OR IGNORE ... SELECT`）；
- 板块 settings 表并入 `app_settings`（键加板块命名空间前缀）；
- 任何一步失败都只记录不抛出：迁移数据不该拦住应用启动。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# 旧库文件名 → 板块命名空间
LEGACY_FILES: dict[str, str] = {
    "library.db": "book",
    "music.db": "music",
    "video.db": "video",
    "gallery.db": "gallery",
    "llm.db": "llm",
}

# 旧表名 → 新表名（settings 单独处理）
LEGACY_TABLES: dict[str, dict[str, str]] = {
    "library.db": {"books": "book_books", "history": "book_history"},
    "music.db": {
        "tracks": "music_tracks",
        "playlists": "music_playlists",
        "playlist_items": "music_playlist_items",
        "history": "music_history",
        "playlist_remotes": "music_playlist_remotes",
    },
    "video.db": {
        "videos": "video_videos",
        "playlists": "video_playlists",
        "playlist_items": "video_playlist_items",
        "history": "video_history",
        "play_records": "video_play_records",
    },
    "gallery.db": {
        "images": "gallery_images",
        "albums": "gallery_albums",
        "album_items": "gallery_album_items",
        "tags": "gallery_tags",
        "image_tags": "gallery_image_tags",
        "edits": "gallery_edits",
        "ai_tasks": "gallery_ai_tasks",
    },
    "llm.db": {"model_profiles": "llm_profiles", "llm_calls": "llm_calls"},
}

# 旧库里名为 llm_settings 的设置表（llm.db 用）
LEGACY_SETTING_TABLES: dict[str, str] = {"llm.db": "llm_settings"}

FLAG_KEY = "platform/legacy_imported"


def stage_legacy_files(data_dir: Path) -> list[Path]:
    """把更早版本（Electron 时代 WinEBook）的书库复制成待导入的 library.db。

    旧 Electron 版数据目录是 `%APPDATA%\\WinEBook`，这里只做**复制**，
    真正的合并交给后面的统一导入流程。
    """
    staged: list[Path] = []
    try:
        from .paths import LEGACY_APP_DIR_NAME, legacy_db_path

        target = data_dir / "library.db"
        source = legacy_db_path()
        if source is not None and not target.exists():
            import shutil

            shutil.copy2(source, target)
            staged.append(target)
        _ = LEGACY_APP_DIR_NAME
    except Exception:  # noqa: BLE001  旧库迁移失败不阻断启动
        return staged
    return staged


def _columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()
    except sqlite3.DatabaseError:
        return []
    return [row["name"] for row in rows]


def _shared_columns(old: list[str], new: list[str]) -> list[str]:
    return [name for name in old if name in new]


def _required_defaults(conn: sqlite3.Connection, table: str, shared: list[str]) -> list[tuple[str, str]]:
    """新表里「NOT NULL 且无默认值」但旧表没有的列 → 给出兜底常量。

    老版本的旧库往往缺列（例如 music_tracks.added_at 是后来加的），
    直接 `INSERT ... SELECT` 会因 NOT NULL 约束整库失败，所以这里补 0 / ''。
    """
    filled: list[tuple[str, str]] = []
    for row in conn.execute(f"PRAGMA table_info({table})"):
        name = str(row["name"])
        if name in shared or name == "id":
            continue
        if row["notnull"] and row["dflt_value"] is None:
            column_type = str(row["type"] or "").upper()
            filled.append((name, "0" if any(t in column_type for t in ("INT", "REAL", "NUM", "DOUBLE")) else "''"))
    return filled


def _import_file(conn: sqlite3.Connection, path: Path, namespace: str) -> tuple[int, list[str]]:
    """导入单个旧库，返回（导入行数, 说明列表）。"""
    notes: list[str] = []
    tables = LEGACY_TABLES.get(path.name, {})
    conn.execute("ATTACH DATABASE ? AS legacy", (str(path),))
    imported = 0
    try:
        for old_table, new_table in tables.items():
            old_cols = _columns(conn, "legacy", old_table)
            if not old_cols:
                continue
            new_cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({new_table})")]
            if not new_cols:
                notes.append(f"{new_table} 不存在，跳过")
                continue
            shared = _shared_columns(old_cols, new_cols)
            if not shared:
                notes.append(f"{old_table} → {new_table} 无同名列，跳过")
                continue
            filled = _required_defaults(conn, new_table, shared)
            columns = ", ".join(shared + [name for name, _ in filled])
            select = ", ".join(shared + [f"{value} AS {name}" for name, value in filled])
            cursor = conn.execute(
                f"INSERT OR IGNORE INTO {new_table} ({columns}) "
                f"SELECT {select} FROM legacy.{old_table}"
            )
            imported += max(0, cursor.rowcount)
            if filled:
                notes.append(f"{old_table} 缺列已补默认值：{', '.join(name for name, _ in filled)}")
        # 板块设置 → app_settings（键加命名空间）
        setting_table = "settings" if _columns(conn, "legacy", "settings") else LEGACY_SETTING_TABLES.get(path.name, "")
        if setting_table and _columns(conn, "legacy", setting_table):
            conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value, updated_at) "
                f"SELECT ? || key, value, strftime('%s','now') FROM legacy.{setting_table}",
                (f"{namespace}/",),
            )
            notes.append(f"{setting_table} → app_settings")
        conn.commit()
    finally:
        # DETACH 要求没有未结事务，否则报 "database legacy is locked"
        try:
            conn.commit()
        except sqlite3.DatabaseError:
            conn.rollback()
        try:
            conn.execute("DETACH DATABASE legacy")
        except sqlite3.DatabaseError:
            pass
    return imported, notes


def import_legacy_databases(store, data_dir: Path) -> list[str]:  # noqa: ANN001  SqliteStore
    """首次打开单库时导入旧分库；已导入过或没有旧库时什么都不做。"""
    if store.get_setting(FLAG_KEY, ""):
        return []
    stage_legacy_files(data_dir)
    notes: list[str] = []
    for filename, namespace in LEGACY_FILES.items():
        path = data_dir / filename
        if not path.is_file():
            continue
        try:
            rows, file_notes = _import_file(store._conn, path, namespace)  # noqa: SLF001
            backup = path.with_suffix(path.suffix + ".imported.bak")
            try:
                path.replace(backup)
                moved = f"，旧文件已改名 {backup.name}"
            except OSError as error:  # 文件被占用时不强求改名
                moved = f"，旧文件改名失败（{error}）"
            notes.append(f"{filename}：导入 {rows} 行{moved}" + (f"；{'、'.join(file_notes)}" if file_notes else ""))
        except Exception as error:  # noqa: BLE001  单个旧库失败不影响启动
            notes.append(f"{filename}：导入失败（{error}）")
            print(f"[legacy] {filename} 导入失败：{error}", file=sys.stderr)
    store.set_setting(FLAG_KEY, "1")
    if notes:
        print("[legacy] " + "；".join(notes), file=sys.stderr)
    return notes
