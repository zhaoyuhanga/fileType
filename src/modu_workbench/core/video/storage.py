"""墨软影视 SQLite 存储：本地视频 / 分类收藏 / 播放历史 / 在线条目 / 播放记录 / 配置。

表设计：
- videos:         本地与已入库的条目（file_path 为空表示「只入库未下载」）
- playlists:      分类与收藏（kind: category / favorite）
- playlist_items: 集合内视频（顺序 + 去重）
- history:        播放与下载历史
- play_records:   在线播放的最近可用直链与画质（按 视频+集 去重）
- settings:       影视模块配置（下载目录、启用源、源顺序等）
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Iterable, List, Optional

from .models import FAVORITE_NAME, Episode, HistoryEntry, Playlist, PlayRecord, RemoteVideo, Video

__all__ = ["FAVORITE_NAME", "VideoStorage", "remote_to_video"]


class VideoStorage:
    """线程安全的影视库存储（与 MusicStorage 同一套锁/事务惯例）。"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS videos (
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

    CREATE TABLE IF NOT EXISTS playlists (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT    NOT NULL UNIQUE,
        kind       TEXT    NOT NULL DEFAULT 'category',
        created_at INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS playlist_items (
        playlist_id INTEGER NOT NULL,
        video_id    INTEGER NOT NULL,
        position    INTEGER NOT NULL DEFAULT 0,
        added_at    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (playlist_id, video_id),
        FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
        FOREIGN KEY (video_id)    REFERENCES videos(id)    ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS history (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id      INTEGER NOT NULL DEFAULT 0,
        action        TEXT    NOT NULL DEFAULT 'play',
        played_at     INTEGER NOT NULL,
        episode_label TEXT    NOT NULL DEFAULT '',
        quality       TEXT    NOT NULL DEFAULT '',
        source        TEXT    NOT NULL DEFAULT '',
        title_snapshot TEXT   NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS play_records (
        video_id      INTEGER NOT NULL,
        episode_label TEXT    NOT NULL DEFAULT '',
        quality       TEXT    NOT NULL DEFAULT '',
        url           TEXT    NOT NULL DEFAULT '',
        source        TEXT    NOT NULL DEFAULT '',
        updated_at    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (video_id, episode_label)
    );

    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    );

    CREATE INDEX IF NOT EXISTS idx_video_series ON videos(series_key);
    CREATE INDEX IF NOT EXISTS idx_video_kind ON videos(kind);
    CREATE INDEX IF NOT EXISTS idx_video_time ON videos(added_at DESC);
    CREATE INDEX IF NOT EXISTS idx_history_time ON history(played_at DESC);
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        with self._lock:
            self._conn.executescript(self.SCHEMA)
            self._ensure_favorite()
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- 视频条目 ----------

    def upsert_video(self, video: Video) -> int:
        """落库并返回 video_id。

        唯一键：
        - 本地文件（file_path 非空）按 file_path 去重；
        - 在线条目按 (source, remote_id, episode_index) 去重；
        - 纯条目标题（无 remote_id，例如手动创建的条目）按 (title, episode_index) 去重。
        """
        now = int(time.time())
        with self._lock:
            row = None
            if video.file_path:
                row = self._conn.execute(
                    "SELECT id FROM videos WHERE file_path = ?", (video.file_path,)
                ).fetchone()
            if row is None and video.remote_id:
                row = self._conn.execute(
                    "SELECT id FROM videos WHERE source = ? AND remote_id = ? AND episode_index = ?",
                    (video.source, video.remote_id, int(video.episode_index)),
                ).fetchone()
            if row is None and not video.file_path and not video.remote_id:
                row = self._conn.execute(
                    "SELECT id FROM videos WHERE title = ? AND episode_index = ? AND remote_id = ''",
                    (video.title, int(video.episode_index)),
                ).fetchone()

            if row:
                video_id = int(row["id"])
                self._conn.execute(
                    """UPDATE videos SET file_path=?, title=?, kind=?, series_key=?, season=?,
                              episode_index=?, episode_label=?, year=?, region=?, category=?,
                              tags=?, actors=?, director=?, description=?, cover_path=?,
                              duration_ms=?, size_bytes=?, format=?, source=?, remote_id=?, quality=?
                       WHERE id=?""",
                    (
                        video.file_path, video.title, video.kind, video.series_key, int(video.season),
                        int(video.episode_index), video.episode_label, video.year, video.region,
                        video.category, video.tags, video.actors, video.director, video.description,
                        video.cover_path, int(video.duration_ms), int(video.size_bytes), video.format,
                        video.source, video.remote_id, video.quality, video_id,
                    ),
                )
            else:
                cur = self._conn.execute(
                    """INSERT INTO videos (file_path, title, kind, series_key, season, episode_index,
                              episode_label, year, region, category, tags, actors, director, description,
                              cover_path, duration_ms, size_bytes, format, source, remote_id, quality,
                              favorited, play_count, added_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        video.file_path, video.title, video.kind, video.series_key, int(video.season),
                        int(video.episode_index), video.episode_label, video.year, video.region,
                        video.category, video.tags, video.actors, video.director, video.description,
                        video.cover_path, int(video.duration_ms), int(video.size_bytes), video.format,
                        video.source, video.remote_id, video.quality,
                        int(video.favorited), int(video.play_count), now,
                    ),
                )
                video_id = int(cur.lastrowid)
            self._conn.commit()
        return video_id

    def get_video(self, video_id: int) -> Optional[Video]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return self._row_to_video(row) if row else None

    def get_video_by_path(self, path: str) -> Optional[Video]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM videos WHERE file_path = ?", (path,)).fetchone()
        return self._row_to_video(row) if row else None

    def find_remote_video(self, source: str, remote_id: str, episode_index: int = 0) -> Optional[Video]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM videos WHERE source = ? AND remote_id = ? AND episode_index = ?",
                (source, remote_id, int(episode_index)),
            ).fetchone()
        return self._row_to_video(row) if row else None

    def list_videos(
        self,
        keyword: str = "",
        kind: str = "",
        category: str = "",
        favorite_only: bool = False,
        local_only: bool = False,
        order: str = "added",
        limit: int = 5000,
    ) -> List[Video]:
        sql = "SELECT * FROM videos WHERE 1=1"
        args: list = []
        if keyword:
            sql += " AND (title LIKE ? OR actors LIKE ? OR director LIKE ? OR tags LIKE ?)"
            like = f"%{keyword}%"
            args += [like, like, like, like]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        if category:
            sql += " AND category = ?"
            args.append(category)
        if favorite_only:
            sql += " AND favorited = 1"
        if local_only:
            sql += " AND file_path <> ''"
        sql += {
            "added": " ORDER BY added_at DESC, id DESC",
            "title": " ORDER BY title COLLATE NOCASE, episode_index ASC",
            "played": " ORDER BY COALESCE(last_played_at, 0) DESC",
            "kind": " ORDER BY kind COLLATE NOCASE, title COLLATE NOCASE",
        }.get(order, " ORDER BY added_at DESC")
        sql += " LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_video(r) for r in rows]

    def list_series_episodes(self, series_key: str) -> List[Video]:
        """同一部剧的全部已入库剧集（按季/集排序）。"""
        if not series_key:
            return []
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM videos WHERE series_key = ?
                   ORDER BY season ASC, episode_index ASC, id ASC""",
                (series_key,),
            ).fetchall()
        return [self._row_to_video(r) for r in rows]

    def list_kinds(self) -> List[tuple[str, int]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT kind, COUNT(*) AS n FROM videos
                   WHERE kind <> '' GROUP BY kind ORDER BY n DESC"""
            ).fetchall()
        return [(r["kind"], int(r["n"])) for r in rows]

    def list_categories(self) -> List[tuple[str, int]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT category, COUNT(*) AS n FROM videos
                   WHERE category <> '' GROUP BY category ORDER BY n DESC, category COLLATE NOCASE"""
            ).fetchall()
        return [(r["category"], int(r["n"])) for r in rows]

    def set_category(self, video_ids: Iterable[int], category: str) -> int:
        ids = list(video_ids)
        if not ids:
            return 0
        with self._lock:
            self._conn.executemany(
                "UPDATE videos SET category = ? WHERE id = ?", [(category, i) for i in ids]
            )
            self._conn.commit()
        return len(ids)

    def set_favorite(self, video_ids: Iterable[int], favorited: bool = True) -> int:
        ids = list(video_ids)
        if not ids:
            return 0
        with self._lock:
            self._conn.executemany(
                "UPDATE videos SET favorited = ? WHERE id = ?", [(int(favorited), i) for i in ids]
            )
            if favorited:
                fav_id = self._ensure_favorite()
                now = int(time.time())
                self._conn.executemany(
                    """INSERT OR IGNORE INTO playlist_items (playlist_id, video_id, position, added_at)
                       VALUES (?,?,?,?)""",
                    [(fav_id, i, 0, now) for i in ids],
                )
            self._conn.commit()
        return len(ids)

    def toggle_favorite(self, video_id: int) -> bool:
        video = self.get_video(video_id)
        if video is None:
            return False
        target = not video.favorited
        self.set_favorite([video_id], target)
        if not target:
            fav_id = self._ensure_favorite()
            with self._lock:
                self._conn.execute(
                    "DELETE FROM playlist_items WHERE playlist_id = ? AND video_id = ?",
                    (fav_id, video_id),
                )
                self._conn.commit()
        return target

    def delete_videos(self, video_ids: Iterable[int]) -> int:
        ids = list(video_ids)
        if not ids:
            return 0
        with self._lock:
            self._conn.executemany("DELETE FROM videos WHERE id = ?", [(i,) for i in ids])
            self._conn.executemany("DELETE FROM play_records WHERE video_id = ?", [(i,) for i in ids])
            self._conn.commit()
        return len(ids)

    def mark_played(self, video_id: int) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "UPDATE videos SET play_count = play_count + 1, last_played_at = ? WHERE id = ?",
                (now, video_id),
            )
            self._conn.commit()

    def update_duration(self, video_id: int, duration_ms: int) -> bool:
        if duration_ms <= 0:
            return False
        video = self.get_video(video_id)
        if video is None or video.duration_ms == duration_ms:
            return False
        video.duration_ms = int(duration_ms)
        self.upsert_video(video)
        return True

    # ---------- 集合（分类 / 收藏） ----------

    def _ensure_favorite(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM playlists WHERE kind = 'favorite' ORDER BY id LIMIT 1"
            ).fetchone()
            if row:
                return int(row["id"])
            cur = self._conn.execute(
                "INSERT INTO playlists (name, kind, created_at) VALUES (?,?,?)",
                (FAVORITE_NAME, "favorite", int(time.time())),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    @property
    def favorite_playlist_id(self) -> int:
        return self._ensure_favorite()

    def create_playlist(self, name: str, kind: str = "category") -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("名称不能为空")
        with self._lock:
            row = self._conn.execute("SELECT id FROM playlists WHERE name = ?", (name,)).fetchone()
            if row:
                return int(row["id"])
            cur = self._conn.execute(
                "INSERT INTO playlists (name, kind, created_at) VALUES (?,?,?)",
                (name, kind, int(time.time())),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_playlists(self, include_favorite: bool = True) -> List[Playlist]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT p.id, p.name, p.kind, p.created_at,
                          (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id = p.id) AS n
                   FROM playlists p ORDER BY p.kind = 'favorite' DESC, p.created_at ASC"""
            ).fetchall()
        items = [
            Playlist(id=int(r["id"]), name=r["name"], kind=r["kind"],
                     created_at=int(r["created_at"]), video_count=int(r["n"]))
            for r in rows
        ]
        if not include_favorite:
            items = [p for p in items if not p.is_favorite]
        return items

    def get_playlist(self, playlist_id: int) -> Optional[Playlist]:
        with self._lock:
            row = self._conn.execute(
                """SELECT p.id, p.name, p.kind, p.created_at,
                          (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id = p.id) AS n
                   FROM playlists p WHERE p.id = ?""",
                (playlist_id,),
            ).fetchone()
        if not row:
            return None
        return Playlist(id=int(row["id"]), name=row["name"], kind=row["kind"],
                        created_at=int(row["created_at"]), video_count=int(row["n"]))

    def rename_playlist(self, playlist_id: int, new_name: str) -> bool:
        new_name = (new_name or "").strip()
        if not new_name:
            return False
        playlist = self.get_playlist(playlist_id)
        if playlist is None or playlist.is_favorite:
            return False
        with self._lock:
            try:
                self._conn.execute("UPDATE playlists SET name = ? WHERE id = ?", (new_name, playlist_id))
                self._conn.commit()
            except sqlite3.IntegrityError:
                return False
        return True

    def delete_playlist(self, playlist_id: int) -> bool:
        playlist = self.get_playlist(playlist_id)
        if playlist is None or playlist.is_favorite:
            return False
        with self._lock:
            self._conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
            self._conn.commit()
        return True

    def add_to_playlist(self, playlist_id: int, video_ids: Iterable[int]) -> int:
        ids = list(video_ids)
        if not ids:
            return 0
        now = int(time.time())
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(position), 0) AS pos FROM playlist_items WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchone()
            position = int(row["pos"]) if row else 0
            added = 0
            for video_id in ids:
                cur = self._conn.execute(
                    """INSERT OR IGNORE INTO playlist_items (playlist_id, video_id, position, added_at)
                       VALUES (?,?,?,?)""",
                    (playlist_id, video_id, position, now),
                )
                if cur.rowcount:
                    position += 1
                    added += 1
            self._conn.commit()
        return added

    def remove_from_playlist(self, playlist_id: int, video_ids: Iterable[int]) -> int:
        ids = list(video_ids)
        if not ids:
            return 0
        playlist = self.get_playlist(playlist_id)
        with self._lock:
            self._conn.executemany(
                "DELETE FROM playlist_items WHERE playlist_id = ? AND video_id = ?",
                [(playlist_id, i) for i in ids],
            )
            if playlist is not None and playlist.is_favorite:
                self._conn.executemany(
                    "UPDATE videos SET favorited = 0 WHERE id = ?", [(i,) for i in ids]
                )
            self._conn.commit()
        return len(ids)

    def list_playlist_videos(self, playlist_id: int) -> List[Video]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT v.* FROM playlist_items i
                   JOIN videos v ON v.id = i.video_id
                   WHERE i.playlist_id = ?
                   ORDER BY i.position ASC, i.added_at ASC""",
                (playlist_id,),
            ).fetchall()
        return [self._row_to_video(r) for r in rows]

    def playlist_ids(self, playlist_id: int) -> List[int]:
        return [v.id for v in self.list_playlist_videos(playlist_id)]

    # ---------- 历史 ----------

    def add_history(self, video_id: int, action: str = "play", *, episode_label: str = "",
                    quality: str = "", source: str = "", title: str = "") -> int:
        """写一条历史。

        title 作为快照保存：条目被删除后历史仍可读（与乐库 LEFT JOIN 的语义等价）。
        """
        if not title and video_id:
            video = self.get_video(video_id)
            if video is not None:
                title = video.title
                episode_label = episode_label or video.episode_label
                source = source or video.source
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO history (video_id, action, played_at, episode_label, quality, source, title_snapshot)
                   VALUES (?,?,?,?,?,?,?)""",
                (int(video_id), action, int(time.time()), episode_label,
                 quality, source, title),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_history(self, limit: int = 300, action: str = "") -> List[HistoryEntry]:
        sql = """SELECT h.id, h.video_id, h.action, h.played_at, h.episode_label, h.quality,
                        h.source, h.title_snapshot, v.title AS vtitle, v.file_path, v.kind
                 FROM history h LEFT JOIN videos v ON v.id = h.video_id"""
        args: list = []
        if action:
            sql += " WHERE h.action = ?"
            args.append(action)
        sql += " ORDER BY h.played_at DESC, h.id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [
            HistoryEntry(
                id=int(r["id"]), video_id=int(r["video_id"]),
                title=r["vtitle"] or r["title_snapshot"] or "(已删除)",
                kind=r["kind"] or "",
                action=r["action"], played_at=int(r["played_at"]),
                episode_label=r["episode_label"] or "", quality=r["quality"] or "",
                source=r["source"] or "", path=r["file_path"] or "",
            )
            for r in rows
        ]

    def clear_history(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM history")
            self._conn.commit()

    # ---------- 在线播放记录 ----------

    def save_play_record(self, video_id: int, episode_label: str, quality: str,
                         url: str, source: str) -> None:
        if not video_id or not url:
            return
        with self._lock:
            self._conn.execute(
                """INSERT INTO play_records (video_id, episode_label, quality, url, source, updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(video_id, episode_label) DO UPDATE SET
                       quality = excluded.quality, url = excluded.url,
                       source = excluded.source, updated_at = excluded.updated_at""",
                (int(video_id), episode_label or "", quality or "", url, source or "", int(time.time())),
            )
            self._conn.commit()

    def get_play_record(self, video_id: int, episode_label: str = "") -> Optional[PlayRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM play_records WHERE video_id = ? AND episode_label = ?",
                (int(video_id), episode_label or ""),
            ).fetchone()
        if not row:
            return None
        return PlayRecord(
            video_id=int(row["video_id"]), episode_label=row["episode_label"],
            quality=row["quality"], url=row["url"], source=row["source"],
            updated_at=int(row["updated_at"]),
        )

    # ---------- 配置 ----------

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            self._conn.commit()

    # ---------- helpers ----------

    @staticmethod
    def _row_to_video(row) -> Video:
        return Video(
            id=int(row["id"]), file_path=row["file_path"], title=row["title"], kind=row["kind"],
            series_key=row["series_key"], season=int(row["season"]),
            episode_index=int(row["episode_index"]), episode_label=row["episode_label"],
            year=row["year"], region=row["region"], category=row["category"], tags=row["tags"],
            actors=row["actors"], director=row["director"], description=row["description"],
            cover_path=row["cover_path"], duration_ms=int(row["duration_ms"]),
            size_bytes=int(row["size_bytes"]), format=row["format"], source=row["source"],
            remote_id=row["remote_id"], quality=row["quality"],
            favorited=bool(row["favorited"]), play_count=int(row["play_count"]),
            added_at=int(row["added_at"]), last_played_at=row["last_played_at"],
        )


def remote_to_video(remote: RemoteVideo, episode: Episode | None = None, *,
                    file_path: str = "", size_bytes: int = 0, duration_ms: int = 0,
                    quality: str = "") -> Video:
    """把在线条目（+可选某一集）转换成可落库的 Video。"""
    episode = episode or (remote.episodes[0] if remote.episodes else None)
    label = episode.name if episode else ""
    index = episode.index if episode else 0
    return Video(
        file_path=file_path,
        title=remote.title,
        kind=remote.kind,
        series_key=remote.series_key or f"{remote.source}:{remote.remote_id}",
        episode_index=index,
        episode_label=label,
        year=remote.year,
        region=remote.region,
        category=remote.category,
        tags=remote.tags,
        actors=remote.actors,
        director=remote.director,
        description=remote.description,
        duration_ms=duration_ms or remote.duration_ms,
        size_bytes=size_bytes,
        format=(file_path.rsplit(".", 1)[-1].lower() if file_path and "." in file_path else ""),
        source=remote.source,
        remote_id=remote.remote_id,
        quality=quality,
        remote_url=(episode.url if episode else ""),
    )
