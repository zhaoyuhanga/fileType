"""v3：墨软动漫板块（第七大板块）新增表。

与 v2 同样的原则：**只新增表与索引**，不改动任何既有板块的表 —— 老库升级是纯增量、
可重复执行（`CREATE TABLE IF NOT EXISTS`），不需要数据搬迁。

表说明见 `docs/DATABASE.md` 第 3 节「墨软动漫」：

- `anime_rules`：规则索引（规则 JSON 文件是唯一真相，这里只存状态/索引/最近试跑结果）；
- `anime_series`：番剧库（元数据缓存，每 (规则, 站点作品 id) 一行）；
- `anime_favorites`：追番（`series_id` 唯一，避免重复追同一部）；
- `anime_progress`：按集观看进度（`(series_id, episode_key)` 唯一，续播与"看到第几集"）；
- `anime_history`：播放历史（**快照式**存 title/episode_name，番剧行删掉后历史仍可读）；
- `anime_danmaku`：弹幕缓存索引（磁盘文件才是弹幕本体，这里只记 episodeId/条数/路径）；
- `anime_download_tasks`：下载任务（与影视的 `video_download_tasks` 同构但**独立成表**：
  两个板块的数据互不干扰，符合"板块最小化"原则，见 `docs/VIDEO_ANIME_PLAN.md` 4.4）。

为什么不给 `anime_*` 加外键：番剧行的来源是站点（规则换源会重建 `remote_id`），
外键级联会在"删规则索引"时连带删掉用户追番与进度；清理统一走 `AnimeStorage` 的显式方法。
"""
from __future__ import annotations

import sqlite3

VERSION = 3
NOTE = ("墨软动漫板块（anime_rules / anime_series / anime_favorites / anime_progress / "
        "anime_history / anime_danmaku / anime_download_tasks）")

SCHEMA = """
-- ---------- anime：墨软动漫 ----------
CREATE TABLE IF NOT EXISTS anime_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL DEFAULT '',
    version     TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'xpath',
    path        TEXT NOT NULL DEFAULT '',
    builtin     INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    source_url  TEXT NOT NULL DEFAULT '',
    test_state  TEXT NOT NULL DEFAULT '',
    test_note   TEXT NOT NULL DEFAULT '',
    updated_at  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS anime_series (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key       TEXT NOT NULL DEFAULT '',
    remote_id      TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL DEFAULT '',
    title_norm     TEXT NOT NULL DEFAULT '',
    cover          TEXT NOT NULL DEFAULT '',
    year           TEXT NOT NULL DEFAULT '',
    summary        TEXT NOT NULL DEFAULT '',
    bangumi_id     INTEGER NOT NULL DEFAULT 0,
    total_episodes INTEGER NOT NULL DEFAULT 0,
    latest_episode TEXT NOT NULL DEFAULT '',
    air_weekday    INTEGER NOT NULL DEFAULT -1,
    created_at     INTEGER NOT NULL DEFAULT 0,
    updated_at     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(rule_key, remote_id)
);
CREATE TABLE IF NOT EXISTS anime_favorites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id  INTEGER NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT 0,
    UNIQUE(series_id)
);
CREATE TABLE IF NOT EXISTS anime_progress (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id     INTEGER NOT NULL,
    episode_key   TEXT NOT NULL DEFAULT '',
    episode_name  TEXT NOT NULL DEFAULT '',
    episode_index INTEGER NOT NULL DEFAULT 0,
    position_ms   INTEGER NOT NULL DEFAULT 0,
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    watched       INTEGER NOT NULL DEFAULT 0,
    updated_at    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(series_id, episode_key)
);
CREATE TABLE IF NOT EXISTS anime_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id    INTEGER NOT NULL DEFAULT 0,
    rule_key     TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    episode_name TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    played_at    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS anime_danmaku (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id   INTEGER NOT NULL DEFAULT 0,
    episode_key TEXT NOT NULL DEFAULT '',
    provider    TEXT NOT NULL DEFAULT 'dandanplay',
    episode_id  INTEGER NOT NULL DEFAULT 0,
    count       INTEGER NOT NULL DEFAULT 0,
    path        TEXT NOT NULL DEFAULT '',
    fetched_at  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(series_id, episode_key, provider)
);
CREATE TABLE IF NOT EXISTS anime_download_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id     INTEGER NOT NULL DEFAULT 0,
    episode_key   TEXT NOT NULL DEFAULT '',
    episode_name  TEXT NOT NULL DEFAULT '',
    rule_key      TEXT NOT NULL DEFAULT '',
    url           TEXT NOT NULL DEFAULT '',
    dest_path     TEXT NOT NULL DEFAULT '',
    parts_dir     TEXT NOT NULL DEFAULT '',
    total_parts   INTEGER NOT NULL DEFAULT 0,
    done_parts    INTEGER NOT NULL DEFAULT 0,
    state         TEXT NOT NULL DEFAULT 'pending',
    message       TEXT NOT NULL DEFAULT '',
    updated_at    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_anime_series_rule ON anime_series(rule_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_anime_history_time ON anime_history(played_at DESC);
CREATE INDEX IF NOT EXISTS idx_anime_progress_time ON anime_progress(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_anime_download_state ON anime_download_tasks(state, updated_at DESC);
"""


def apply(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
