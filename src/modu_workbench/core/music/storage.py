"""墨软乐库 SQLite 存储：曲目 / 歌单 / 收藏 / 分类 / 播放历史。

表设计：
- tracks:         本地曲目（path 唯一）
- playlists:      歌单（kind: playlist / favorite / category）
- playlist_items: 歌单内曲目（顺序 + 去重）
- history:        播放与下载历史
- settings:       音乐模块配置（下载目录、播放模式、上次歌单等）
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Iterable, List, Optional

from .models import HistoryEntry, Playlist, RemoteTrack, Track

FAVORITE_NAME = "我的收藏"


class MusicStorage:
    """线程安全的音乐库存储。"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS tracks (
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

    CREATE TABLE IF NOT EXISTS playlists (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT    NOT NULL UNIQUE,
        kind       TEXT    NOT NULL DEFAULT 'playlist',
        created_at INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS playlist_items (
        playlist_id INTEGER NOT NULL,
        track_id    INTEGER NOT NULL,
        position    INTEGER NOT NULL DEFAULT 0,
        added_at    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (playlist_id, track_id),
        FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
        FOREIGN KEY (track_id)    REFERENCES tracks(id)    ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS history (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id  INTEGER NOT NULL,
        action    TEXT    NOT NULL DEFAULT 'play',
        played_at INTEGER NOT NULL,
        FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
    );

    -- 歌单中的「待下载」在线曲目（搜索结果可直接加入歌单，稍后再下载）
    CREATE TABLE IF NOT EXISTS playlist_remotes (
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
        FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    );

    CREATE INDEX IF NOT EXISTS idx_history_time ON history(played_at DESC);
    CREATE INDEX IF NOT EXISTS idx_track_artist ON tracks(artist);
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
            # 迁移：旧版本可能缺少 cover_path 列
            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(tracks)")}
            if "cover_path" not in columns:
                self._conn.execute("ALTER TABLE tracks ADD COLUMN cover_path TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
            self._ensure_favorite()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- 曲目 ----------

    def upsert_track(self, track: Track) -> int:
        """按 path 落库（存在则更新元数据），返回 track_id。"""
        now = int(time.time())
        with self._lock:
            row = self._conn.execute("SELECT id FROM tracks WHERE path = ?", (track.path,)).fetchone()
            if row:
                track_id = int(row["id"])
                self._conn.execute(
                    """UPDATE tracks SET title=?, artist=?, album=?, duration_ms=?, size_bytes=?,
                              format=?, source=?, remote_id=?, cover_path=?, category=?
                       WHERE id=?""",
                    (
                        track.title, track.artist, track.album, track.duration_ms, track.size_bytes,
                        track.format, track.source, track.remote_id, track.cover_path, track.category,
                        track_id,
                    ),
                )
            else:
                cur = self._conn.execute(
                    """INSERT INTO tracks (path, title, artist, album, duration_ms, size_bytes,
                              format, source, remote_id, cover_path, category, favorited, play_count, added_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        track.path, track.title, track.artist, track.album, track.duration_ms,
                        track.size_bytes, track.format, track.source, track.remote_id,
                        track.cover_path, track.category, int(track.favorited), track.play_count, now,
                    ),
                )
                track_id = int(cur.lastrowid)
            self._conn.commit()
        return track_id

    def get_track(self, track_id: int) -> Optional[Track]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return self._row_to_track(row) if row else None

    def get_track_by_path(self, path: str) -> Optional[Track]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()
        return self._row_to_track(row) if row else None

    def list_tracks(
        self,
        keyword: str = "",
        artist: str = "",
        category: str = "",
        favorite_only: bool = False,
        order: str = "added",
        limit: int = 5000,
    ) -> List[Track]:
        sql = "SELECT * FROM tracks WHERE 1=1"
        args: list = []
        if keyword:
            sql += " AND (title LIKE ? OR artist LIKE ? OR album LIKE ?)"
            like = f"%{keyword}%"
            args += [like, like, like]
        if artist:
            sql += " AND artist = ?"
            args.append(artist)
        if category:
            sql += " AND category = ?"
            args.append(category)
        if favorite_only:
            sql += " AND favorited = 1"
        sql += {
            "added": " ORDER BY added_at DESC, id DESC",
            "title": " ORDER BY title COLLATE NOCASE",
            "artist": " ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE",
            "played": " ORDER BY COALESCE(last_played_at, 0) DESC",
        }.get(order, " ORDER BY added_at DESC")
        sql += " LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_track(r) for r in rows]

    def list_artists(self) -> List[tuple[str, int]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT artist, COUNT(*) AS n FROM tracks
                   WHERE artist <> '' GROUP BY artist ORDER BY n DESC, artist COLLATE NOCASE"""
            ).fetchall()
        return [(r["artist"], r["n"]) for r in rows]

    def list_categories(self) -> List[tuple[str, int]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT category, COUNT(*) AS n FROM tracks
                   WHERE category <> '' GROUP BY category ORDER BY n DESC, category COLLATE NOCASE"""
            ).fetchall()
        return [(r["category"], r["n"]) for r in rows]

    def set_category(self, track_ids: Iterable[int], category: str) -> int:
        ids = list(track_ids)
        if not ids:
            return 0
        with self._lock:
            self._conn.executemany(
                "UPDATE tracks SET category = ? WHERE id = ?", [(category, i) for i in ids]
            )
            self._conn.commit()
        return len(ids)

    def set_favorite(self, track_ids: Iterable[int], favorited: bool = True) -> int:
        ids = list(track_ids)
        if not ids:
            return 0
        with self._lock:
            self._conn.executemany(
                "UPDATE tracks SET favorited = ? WHERE id = ?", [(int(favorited), i) for i in ids]
            )
            if favorited:
                fav_id = self._ensure_favorite()
                now = int(time.time())
                self._conn.executemany(
                    """INSERT OR IGNORE INTO playlist_items (playlist_id, track_id, position, added_at)
                       VALUES (?,?,?,?)""",
                    [(fav_id, i, 0, now) for i in ids],
                )
            self._conn.commit()
        return len(ids)

    def toggle_favorite(self, track_id: int) -> bool:
        track = self.get_track(track_id)
        if track is None:
            return False
        target = not track.favorited
        self.set_favorite([track_id], target)
        if not target:
            fav_id = self._ensure_favorite()
            with self._lock:
                self._conn.execute(
                    "DELETE FROM playlist_items WHERE playlist_id = ? AND track_id = ?",
                    (fav_id, track_id),
                )
                self._conn.commit()
        return target

    def delete_tracks(self, track_ids: Iterable[int]) -> int:
        ids = list(track_ids)
        if not ids:
            return 0
        with self._lock:
            self._conn.executemany("DELETE FROM tracks WHERE id = ?", [(i,) for i in ids])
            self._conn.commit()
        return len(ids)

    def mark_played(self, track_id: int) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "UPDATE tracks SET play_count = play_count + 1, last_played_at = ? WHERE id = ?",
                (now, track_id),
            )
            self._conn.commit()

    # ---------- 歌单 ----------

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

    def create_playlist(self, name: str, kind: str = "playlist") -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("歌单名不能为空")
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
                          (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id = p.id) AS n,
                          (SELECT COUNT(*) FROM playlist_remotes r WHERE r.playlist_id = p.id) AS m
                   FROM playlists p ORDER BY p.kind = 'favorite' DESC, p.created_at ASC"""
            ).fetchall()
        items = [
            Playlist(id=int(r["id"]), name=r["name"], kind=r["kind"],
                     created_at=int(r["created_at"]), track_count=int(r["n"]), pending_count=int(r["m"]))
            for r in rows
        ]
        if not include_favorite:
            items = [p for p in items if not p.is_favorite]
        return items

    def get_playlist(self, playlist_id: int) -> Optional[Playlist]:
        with self._lock:
            row = self._conn.execute(
                """SELECT p.id, p.name, p.kind, p.created_at,
                          (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id = p.id) AS n,
                          (SELECT COUNT(*) FROM playlist_remotes r WHERE r.playlist_id = p.id) AS m
                   FROM playlists p WHERE p.id = ?""",
                (playlist_id,),
            ).fetchone()
        if not row:
            return None
        return Playlist(id=int(row["id"]), name=row["name"], kind=row["kind"],
                        created_at=int(row["created_at"]), track_count=int(row["n"]),
                        pending_count=int(row["m"]))

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

    def add_to_playlist(self, playlist_id: int, track_ids: Iterable[int]) -> int:
        ids = list(track_ids)
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
            for track_id in ids:
                cur = self._conn.execute(
                    """INSERT OR IGNORE INTO playlist_items (playlist_id, track_id, position, added_at)
                       VALUES (?,?,?,?)""",
                    (playlist_id, track_id, position, now),
                )
                if cur.rowcount:
                    position += 1
                    added += 1
            self._conn.commit()
        return added

    def remove_from_playlist(self, playlist_id: int, track_ids: Iterable[int]) -> int:
        ids = list(track_ids)
        if not ids:
            return 0
        playlist = self.get_playlist(playlist_id)
        with self._lock:
            self._conn.executemany(
                "DELETE FROM playlist_items WHERE playlist_id = ? AND track_id = ?",
                [(playlist_id, i) for i in ids],
            )
            if playlist is not None and playlist.is_favorite:
                # 从「我的收藏」移除即取消收藏标记
                self._conn.executemany(
                    "UPDATE tracks SET favorited = 0 WHERE id = ?", [(i,) for i in ids]
                )
            self._conn.commit()
        return len(ids)

    def list_playlist_tracks(self, playlist_id: int) -> List[Track]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT t.* FROM playlist_items i
                   JOIN tracks t ON t.id = i.track_id
                   WHERE i.playlist_id = ?
                   ORDER BY i.position ASC, i.added_at ASC""",
                (playlist_id,),
            ).fetchall()
        return [self._row_to_track(r) for r in rows]

    def playlist_ids(self, playlist_id: int) -> List[int]:
        return [t.id for t in self.list_playlist_tracks(playlist_id)]

    # ---------- 歌单内的「待下载」在线曲目 ----------

    def add_remotes_to_playlist(self, playlist_id: int, remotes: Iterable[RemoteTrack]) -> int:
        now = int(time.time())
        added = 0
        with self._lock:
            for remote in remotes:
                key = f"{remote.source}:{remote.remote_id or remote.title}"
                cur = self._conn.execute(
                    """INSERT OR IGNORE INTO playlist_remotes
                       (playlist_id, remote_key, source, remote_id, title, artist, album,
                        duration_ms, url, cover_url, category, added_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (playlist_id, key, remote.source, remote.remote_id, remote.title, remote.artist,
                     remote.album, int(remote.duration_ms), remote.url, remote.cover_url,
                     remote.category, now),
                )
                added += cur.rowcount or 0
            self._conn.commit()
        return added

    def list_playlist_remotes(self, playlist_id: int) -> List[RemoteTrack]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM playlist_remotes WHERE playlist_id = ?
                   ORDER BY added_at ASC, remote_key ASC""",
                (playlist_id,),
            ).fetchall()
        return [
            RemoteTrack(
                source=r["source"], remote_id=r["remote_id"], title=r["title"],
                artist=r["artist"], album=r["album"], duration_ms=int(r["duration_ms"]),
                url=r["url"], cover_url=r["cover_url"], category=r["category"],
                extra={"remote_key": r["remote_key"], "playlist_id": playlist_id},
            )
            for r in rows
        ]

    def remove_playlist_remotes(self, playlist_id: int, remote_keys: Iterable[str]) -> int:
        keys = [k for k in remote_keys if k]
        if not keys:
            return 0
        with self._lock:
            self._conn.executemany(
                "DELETE FROM playlist_remotes WHERE playlist_id = ? AND remote_key = ?",
                [(playlist_id, key) for key in keys],
            )
            self._conn.commit()
        return len(keys)

    # ---------- 历史 ----------

    def add_history(self, track_id: int, action: str = "play") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO history (track_id, action, played_at) VALUES (?,?,?)",
                (track_id, action, int(time.time())),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_history(self, limit: int = 300) -> List[HistoryEntry]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT h.id, h.track_id, h.action, h.played_at,
                          t.title, t.artist, t.path
                   FROM history h LEFT JOIN tracks t ON t.id = h.track_id
                   ORDER BY h.played_at DESC, h.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            HistoryEntry(
                id=int(r["id"]), track_id=int(r["track_id"]),
                title=r["title"] or "(已删除)", artist=r["artist"] or "",
                action=r["action"], played_at=int(r["played_at"]), path=r["path"] or "",
            )
            for r in rows
        ]

    def clear_history(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM history")
            self._conn.commit()

    # ---------- 配置 ----------

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            self._conn.commit()

    # ---------- helpers ----------

    @staticmethod
    def _row_to_track(row) -> Track:
        return Track(
            id=int(row["id"]), path=row["path"], title=row["title"], artist=row["artist"],
            album=row["album"], duration_ms=int(row["duration_ms"]), size_bytes=int(row["size_bytes"]),
            format=row["format"], source=row["source"], remote_id=row["remote_id"],
            cover_path=row["cover_path"], category=row["category"],
            favorited=bool(row["favorited"]), play_count=int(row["play_count"]),
            added_at=int(row["added_at"]), last_played_at=row["last_played_at"],
        )
