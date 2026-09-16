"""v1：墨软·工作台单库初始结构（各板块前缀分表）。

本文件由各板块原有 DDL 归并而来（表名加板块前缀、板块 settings 并入 app_settings），
之后所有结构变更都追加新的迁移模块，**不再修改本文件**。
"""
from __future__ import annotations

import sqlite3

VERSION = 1
NOTE = "单库 modu.db 初始结构（book_/music_/video_/gallery_/llm_ 前缀分表 + app_settings）"

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL,
    note       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL DEFAULT 0
);

-- ---------- book ----------
CREATE TABLE IF NOT EXISTS book_books (
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

    CREATE TABLE IF NOT EXISTS book_history (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id         INTEGER NOT NULL,
        chapter_index   INTEGER NOT NULL,
        chapter_title   TEXT    NOT NULL DEFAULT '',
        opened_at       INTEGER NOT NULL,
        action          TEXT    NOT NULL DEFAULT 'open',
        FOREIGN KEY (book_id) REFERENCES book_books(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_book_history_book ON book_history(book_id, opened_at DESC);

-- ---------- music ----------
CREATE TABLE IF NOT EXISTS music_tracks (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        path           TEXT    UNIQUE NOT NULL,
        title          TEXT    NOT NULL DEFAULT '',
        artist         TEXT    NOT NULL DEFAULT '',
        album          TEXT    NOT NULL DEFAULT '',
        duration_ms    INTEGER NOT NULL DEFAULT 0,
        size_bytes     INTEGER NOT NULL DEFAULT 0,
        format         TEXT    NOT NULL DEFAULT '',
        source         TEXT    NOT NULL DEFAULT '',
        remote_id      TEXT    NOT NULL DEFAULT '',
        cover_path     TEXT    NOT NULL DEFAULT '',
        category       TEXT    NOT NULL DEFAULT '',
        favorited      INTEGER NOT NULL DEFAULT 0,
        play_count     INTEGER NOT NULL DEFAULT 0,
        added_at       INTEGER NOT NULL DEFAULT 0,
        last_played_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS music_playlists (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT    NOT NULL UNIQUE,
        kind       TEXT    NOT NULL DEFAULT 'playlist',
        created_at INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS music_playlist_items (
        playlist_id INTEGER NOT NULL,
        track_id    INTEGER NOT NULL,
        position    INTEGER NOT NULL DEFAULT 0,
        added_at    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (playlist_id, track_id),
        FOREIGN KEY (playlist_id) REFERENCES music_playlists(id) ON DELETE CASCADE,
        FOREIGN KEY (track_id)    REFERENCES music_tracks(id)    ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS music_history (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id  INTEGER NOT NULL,
        action    TEXT    NOT NULL DEFAULT 'play',
        played_at INTEGER NOT NULL,
        FOREIGN KEY (track_id) REFERENCES music_tracks(id) ON DELETE CASCADE
    );

    -- 歌单中的「待下载」在线曲目（搜索结果可直接加入歌单，稍后再下载）
    CREATE TABLE IF NOT EXISTS music_playlist_remotes (
        playlist_id INTEGER NOT NULL,
        remote_key  TEXT    NOT NULL,
        source      TEXT    NOT NULL DEFAULT '',
        remote_id   TEXT    NOT NULL DEFAULT '',
        title       TEXT    NOT NULL DEFAULT '',
        artist      TEXT    NOT NULL DEFAULT '',
        album       TEXT    NOT NULL DEFAULT '',
        duration_ms INTEGER NOT NULL DEFAULT 0,
        url         TEXT    NOT NULL DEFAULT '',
        cover_url   TEXT    NOT NULL DEFAULT '',
        category    TEXT    NOT NULL DEFAULT '',
        added_at    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (playlist_id, remote_key),
        FOREIGN KEY (playlist_id) REFERENCES music_playlists(id) ON DELETE CASCADE
    );

    

    CREATE INDEX IF NOT EXISTS idx_music_history_time ON music_history(played_at DESC);
    CREATE INDEX IF NOT EXISTS idx_music_track_artist ON music_tracks(artist);

-- ---------- video ----------
CREATE TABLE IF NOT EXISTS video_videos (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path      TEXT    NOT NULL DEFAULT '',
        title          TEXT    NOT NULL DEFAULT '',
        kind           TEXT    NOT NULL DEFAULT 'other',
        series_key     TEXT    NOT NULL DEFAULT '',
        season         INTEGER NOT NULL DEFAULT 0,
        episode_index  INTEGER NOT NULL DEFAULT 0,
        episode_label  TEXT    NOT NULL DEFAULT '',
        year           TEXT    NOT NULL DEFAULT '',
        region         TEXT    NOT NULL DEFAULT '',
        category       TEXT    NOT NULL DEFAULT '',
        tags           TEXT    NOT NULL DEFAULT '',
        actors         TEXT    NOT NULL DEFAULT '',
        director       TEXT    NOT NULL DEFAULT '',
        description    TEXT    NOT NULL DEFAULT '',
        cover_path     TEXT    NOT NULL DEFAULT '',
        duration_ms    INTEGER NOT NULL DEFAULT 0,
        size_bytes     INTEGER NOT NULL DEFAULT 0,
        format         TEXT    NOT NULL DEFAULT '',
        source         TEXT    NOT NULL DEFAULT '',
        remote_id      TEXT    NOT NULL DEFAULT '',
        quality        TEXT    NOT NULL DEFAULT '',
        favorited      INTEGER NOT NULL DEFAULT 0,
        play_count     INTEGER NOT NULL DEFAULT 0,
        added_at       INTEGER NOT NULL DEFAULT 0,
        last_played_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS video_playlists (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT    NOT NULL UNIQUE,
        kind       TEXT    NOT NULL DEFAULT 'category',
        created_at INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS video_playlist_items (
        playlist_id INTEGER NOT NULL,
        video_id    INTEGER NOT NULL,
        position    INTEGER NOT NULL DEFAULT 0,
        added_at    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (playlist_id, video_id),
        FOREIGN KEY (playlist_id) REFERENCES video_playlists(id) ON DELETE CASCADE,
        FOREIGN KEY (video_id)    REFERENCES video_videos(id)    ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS video_history (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id      INTEGER NOT NULL DEFAULT 0,
        action        TEXT    NOT NULL DEFAULT 'play',
        played_at     INTEGER NOT NULL,
        episode_label TEXT    NOT NULL DEFAULT '',
        quality       TEXT    NOT NULL DEFAULT '',
        source        TEXT    NOT NULL DEFAULT '',
        title_snapshot TEXT   NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS video_play_records (
        video_id      INTEGER NOT NULL,
        episode_label TEXT    NOT NULL DEFAULT '',
        quality       TEXT    NOT NULL DEFAULT '',
        url           TEXT    NOT NULL DEFAULT '',
        source        TEXT    NOT NULL DEFAULT '',
        updated_at    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (video_id, episode_label)
    );

    

    CREATE INDEX IF NOT EXISTS idx_video_series ON video_videos(series_key);
    CREATE INDEX IF NOT EXISTS idx_video_kind ON video_videos(kind);
    CREATE INDEX IF NOT EXISTS idx_video_time ON video_videos(added_at DESC);
    CREATE INDEX IF NOT EXISTS idx_video_history_time ON video_history(played_at DESC);

-- ---------- gallery ----------
CREATE TABLE IF NOT EXISTS gallery_images (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        path           TEXT    NOT NULL DEFAULT '',
        url            TEXT    NOT NULL DEFAULT '',
        thumb_path     TEXT    NOT NULL DEFAULT '',
        title          TEXT    NOT NULL DEFAULT '',
        ext            TEXT    NOT NULL DEFAULT '',
        mime           TEXT    NOT NULL DEFAULT '',
        size_bytes     INTEGER NOT NULL DEFAULT 0,
        width          INTEGER NOT NULL DEFAULT 0,
        height         INTEGER NOT NULL DEFAULT 0,
        taken_at       INTEGER NOT NULL DEFAULT 0,
        added_at       INTEGER NOT NULL DEFAULT 0,
        exif_json      TEXT    NOT NULL DEFAULT '',
        gps_lat        REAL,
        gps_lon        REAL,
        camera         TEXT    NOT NULL DEFAULT '',
        source         TEXT    NOT NULL DEFAULT 'local',
        sha256         TEXT    NOT NULL DEFAULT '',
        dhash          TEXT    NOT NULL DEFAULT '',
        favorited      INTEGER NOT NULL DEFAULT 0,
        rating         INTEGER NOT NULL DEFAULT 0,
        ai_tags        TEXT    NOT NULL DEFAULT '',
        ai_caption     TEXT    NOT NULL DEFAULT '',
        last_opened_at INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS gallery_albums (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT    NOT NULL UNIQUE,
        cover_path TEXT    NOT NULL DEFAULT '',
        kind       TEXT    NOT NULL DEFAULT 'album',
        created_at INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS gallery_album_items (
        album_id INTEGER NOT NULL,
        image_id INTEGER NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        added_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (album_id, image_id),
        FOREIGN KEY (album_id) REFERENCES gallery_albums(id) ON DELETE CASCADE,
        FOREIGN KEY (image_id) REFERENCES gallery_images(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS gallery_tags (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT    NOT NULL UNIQUE,
        color      TEXT    NOT NULL DEFAULT '#4f5bd5',
        source     TEXT    NOT NULL DEFAULT 'user',
        use_count  INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS gallery_image_tags (
        image_id INTEGER NOT NULL,
        tag_id   INTEGER NOT NULL,
        PRIMARY KEY (image_id, tag_id),
        FOREIGN KEY (image_id) REFERENCES gallery_images(id) ON DELETE CASCADE,
        FOREIGN KEY (tag_id)   REFERENCES gallery_tags(id)   ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS gallery_edits (
        image_id   INTEGER PRIMARY KEY,
        steps_json TEXT    NOT NULL DEFAULT '[]',
        updated_at INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (image_id) REFERENCES gallery_images(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS gallery_ai_tasks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        image_id    INTEGER NOT NULL DEFAULT 0,
        kind        TEXT    NOT NULL DEFAULT '',
        state       TEXT    NOT NULL DEFAULT 'pending',
        progress    INTEGER NOT NULL DEFAULT 0,
        result_path TEXT    NOT NULL DEFAULT '',
        message     TEXT    NOT NULL DEFAULT '',
        created_at  INTEGER NOT NULL DEFAULT 0,
        finished_at INTEGER NOT NULL DEFAULT 0
    );

    

    CREATE UNIQUE INDEX IF NOT EXISTS idx_gallery_images_path ON gallery_images(path) WHERE path <> '';
    CREATE INDEX IF NOT EXISTS idx_gallery_images_sha ON gallery_images(sha256);
    CREATE INDEX IF NOT EXISTS idx_gallery_images_dhash ON gallery_images(dhash);
    CREATE INDEX IF NOT EXISTS idx_gallery_images_taken ON gallery_images(taken_at DESC);
    CREATE INDEX IF NOT EXISTS idx_gallery_images_added ON gallery_images(added_at DESC);
    CREATE INDEX IF NOT EXISTS idx_gallery_images_camera ON gallery_images(camera);

-- ---------- llm ----------
CREATE TABLE IF NOT EXISTS llm_profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL DEFAULT 'text',
    name        TEXT NOT NULL DEFAULT '',
    provider    TEXT NOT NULL DEFAULT 'deepseek',
    base_url    TEXT NOT NULL DEFAULT '',
    api_key     TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    vision_model TEXT NOT NULL DEFAULT '',
    temperature REAL NOT NULL DEFAULT 0.3,
    max_tokens  INTEGER NOT NULL DEFAULT 1024,
    timeout     REAL NOT NULL DEFAULT 60.0,
    priority    INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    note        TEXT NOT NULL DEFAULT '',
    updated_at  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL DEFAULT '',
    profile_id INTEGER NOT NULL DEFAULT 0,
    profile    TEXT NOT NULL DEFAULT '',
    ok         INTEGER NOT NULL DEFAULT 0,
    detail     TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT 0
);
"""


def apply(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
