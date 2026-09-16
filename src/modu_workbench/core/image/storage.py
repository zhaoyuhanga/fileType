"""墨软图库 SQLite 存储：图片 / 相册 / 标签 / 编辑历史 / AI 任务 / 配置。

表设计：
- images:      图片元数据（path 唯一；url 用于「在线收集」尚未落地的图）
- albums:      相册（kind: album / favorite）
- album_items: 相册内图片（顺序 + 去重）
- tags:        标签（source: user / auto，自动标签可手动修正）
- image_tags:  图片 ↔ 标签
- edits:       非破坏性编辑历史（每张图一条，存 JSON 步骤栈）
- ai_tasks:    AI 优化任务（排队/进度/结果，可回退）
- settings:    图库配置（缩略图尺寸、默认视图、DeepSeek 凭据等）
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Iterable, List, Optional

from .models import DEFAULT_SORT, AiTask, Album, EditStep, ImageItem, Tag

FAVORITE_ALBUM_NAME = "我的收藏"


class ImageStorage:
    """线程安全的图库存储。"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS images (
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

    CREATE TABLE IF NOT EXISTS albums (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT    NOT NULL UNIQUE,
        cover_path TEXT    NOT NULL DEFAULT '',
        kind       TEXT    NOT NULL DEFAULT 'album',
        created_at INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS album_items (
        album_id INTEGER NOT NULL,
        image_id INTEGER NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        added_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (album_id, image_id),
        FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE,
        FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS tags (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT    NOT NULL UNIQUE,
        color      TEXT    NOT NULL DEFAULT '#4f5bd5',
        source     TEXT    NOT NULL DEFAULT 'user',
        use_count  INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS image_tags (
        image_id INTEGER NOT NULL,
        tag_id   INTEGER NOT NULL,
        PRIMARY KEY (image_id, tag_id),
        FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
        FOREIGN KEY (tag_id)   REFERENCES tags(id)   ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS edits (
        image_id   INTEGER PRIMARY KEY,
        steps_json TEXT    NOT NULL DEFAULT '[]',
        updated_at INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ai_tasks (
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

    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_images_path ON images(path) WHERE path <> '';
    CREATE INDEX IF NOT EXISTS idx_images_sha ON images(sha256);
    CREATE INDEX IF NOT EXISTS idx_images_dhash ON images(dhash);
    CREATE INDEX IF NOT EXISTS idx_images_taken ON images(taken_at DESC);
    CREATE INDEX IF NOT EXISTS idx_images_added ON images(added_at DESC);
    CREATE INDEX IF NOT EXISTS idx_images_camera ON images(camera);
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

    # ---------- 图片 ----------

    def upsert_image(self, item: ImageItem) -> int:
        """落库并返回 image_id。

        去重键：path 非空按 path；在线条目按 url。
        """
        now = int(time.time())
        with self._lock:
            row = None
            if item.path:
                row = self._conn.execute(
                    "SELECT id FROM images WHERE path = ?", (item.path,)
                ).fetchone()
            if row is None and item.url:
                row = self._conn.execute(
                    "SELECT id FROM images WHERE url = ? AND path = ''", (item.url,)
                ).fetchone()

            if row:
                image_id = int(row["id"])
                self._conn.execute(
                    """UPDATE images SET path=?, url=?, thumb_path=?, title=?, ext=?, mime=?,
                              size_bytes=?, width=?, height=?, taken_at=?, exif_json=?,
                              gps_lat=?, gps_lon=?, camera=?, source=?, sha256=?, dhash=?,
                              ai_tags=?, ai_caption=?
                       WHERE id=?""",
                    (
                        item.path, item.url, item.thumb_path, item.title, item.ext, item.mime,
                        int(item.size_bytes), int(item.width), int(item.height), int(item.taken_at),
                        item.exif_json, item.gps_lat, item.gps_lon, item.camera, item.source,
                        item.sha256, item.dhash, item.ai_tags, item.ai_caption, image_id,
                    ),
                )
            else:
                cur = self._conn.execute(
                    """INSERT INTO images (path, url, thumb_path, title, ext, mime, size_bytes,
                              width, height, taken_at, added_at, exif_json, gps_lat, gps_lon,
                              camera, source, sha256, dhash, favorited, rating, ai_tags,
                              ai_caption, last_opened_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item.path, item.url, item.thumb_path, item.title, item.ext, item.mime,
                        int(item.size_bytes), int(item.width), int(item.height),
                        int(item.taken_at), now, item.exif_json, item.gps_lat, item.gps_lon,
                        item.camera, item.source, item.sha256, item.dhash,
                        int(item.favorited), int(item.rating), item.ai_tags, item.ai_caption,
                        int(item.last_opened_at),
                    ),
                )
                image_id = int(cur.lastrowid)
            self._conn.commit()
        return image_id

    def get_image(self, image_id: int) -> Optional[ImageItem]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
        return self._row_to_image(row) if row else None

    def get_by_path(self, path: str) -> Optional[ImageItem]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM images WHERE path = ?", (path,)).fetchone()
        return self._row_to_image(row) if row else None

    def find_by_sha256(self, sha256: str) -> Optional[ImageItem]:
        if not sha256:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM images WHERE sha256 = ? LIMIT 1", (sha256,)
            ).fetchone()
        return self._row_to_image(row) if row else None

    def find_similar(self, dhash: str, max_distance: int = 6, limit: int = 8) -> List[tuple[ImageItem, int]]:
        """按感知哈希找视觉相近的图片（汉明距离 ≤ max_distance）。"""
        if not dhash:
            return []
        from .hashing import hamming_distance

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM images WHERE dhash <> ''"
            ).fetchall()
        scored: list[tuple[ImageItem, int]] = []
        for row in rows:
            item = self._row_to_image(row)
            distance = hamming_distance(dhash, item.dhash)
            if distance is not None and distance <= max_distance:
                scored.append((item, distance))
        scored.sort(key=lambda pair: pair[1])
        return scored[:limit]

    def list_images(
        self,
        *,
        keyword: str = "",
        album_id: int | None = None,
        tag: str = "",
        favorite_only: bool = False,
        min_rating: int = 0,
        source: str = "",
        camera: str = "",
        date_from: int = 0,
        date_to: int = 0,
        order: str = DEFAULT_SORT,
        descending: bool = True,
        limit: int = 20000,
        offset: int = 0,
    ) -> List[ImageItem]:
        sql = "SELECT * FROM images WHERE 1=1"
        args: list = []
        if keyword:
            like = f"%{keyword}%"
            sql += (" AND (title LIKE ? OR path LIKE ? OR camera LIKE ? OR ai_tags LIKE ?"
                    " OR ai_caption LIKE ?)")
            args += [like, like, like, like, like]
        if album_id:
            sql += " AND id IN (SELECT image_id FROM album_items WHERE album_id = ?)"
            args.append(int(album_id))
        if tag:
            sql += (" AND (id IN (SELECT it.image_id FROM image_tags it JOIN tags t ON t.id = it.tag_id"
                    " WHERE t.name = ?) OR ai_tags LIKE ?)")
            args += [tag, f"%{tag}%"]
        if favorite_only:
            sql += " AND favorited = 1"
        if min_rating:
            sql += " AND rating >= ?"
            args.append(int(min_rating))
        if source:
            sql += " AND source = ?"
            args.append(source)
        if camera:
            sql += " AND camera = ?"
            args.append(camera)
        if date_from:
            sql += " AND COALESCE(NULLIF(taken_at, 0), added_at) >= ?"
            args.append(int(date_from))
        if date_to:
            sql += " AND COALESCE(NULLIF(taken_at, 0), added_at) <= ?"
            args.append(int(date_to))

        direction = "DESC" if descending else "ASC"
        column = {
            "taken": "COALESCE(NULLIF(taken_at, 0), added_at)",
            "added": "added_at",
            "name": "title COLLATE NOCASE",
            "size": "size_bytes",
            "rating": "rating",
        }.get(order, "added_at")
        sql += f" ORDER BY {column} {direction}, id {direction} LIMIT ? OFFSET ?"
        args += [int(limit), int(offset)]

        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_image(r) for r in rows]

    def count_images(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM images").fetchone()
        return int(row["n"]) if row else 0

    def list_cameras(self) -> List[tuple[str, int]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT camera, COUNT(*) AS n FROM images WHERE camera <> ''
                   GROUP BY camera ORDER BY n DESC, camera COLLATE NOCASE"""
            ).fetchall()
        return [(r["camera"], int(r["n"])) for r in rows]

    def timeline(self, order: str = "taken") -> List[tuple[str, int]]:
        """按「年-月」聚合的计数，用于时间轴分组。"""
        column = "COALESCE(NULLIF(taken_at, 0), added_at)" if order == "taken" else "added_at"
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT strftime('%Y-%m', {column}, 'unixepoch', 'localtime') AS bucket,
                           COUNT(*) AS n
                    FROM images GROUP BY bucket ORDER BY bucket DESC"""
            ).fetchall()
        return [(r["bucket"] or "未知", int(r["n"])) for r in rows]

    def set_favorite(self, image_ids: Iterable[int], favorited: bool = True) -> int:
        ids = list(image_ids)
        if not ids:
            return 0
        with self._lock:
            self._conn.executemany(
                "UPDATE images SET favorited = ? WHERE id = ?",
                [(int(favorited), i) for i in ids],
            )
            if favorited:
                fav_id = self._ensure_favorite()
                now = int(time.time())
                self._conn.executemany(
                    """INSERT OR IGNORE INTO album_items (album_id, image_id, position, added_at)
                       VALUES (?,?,?,?)""",
                    [(fav_id, i, 0, now) for i in ids],
                )
            else:
                fav_id = self._ensure_favorite()
                self._conn.executemany(
                    "DELETE FROM album_items WHERE album_id = ? AND image_id = ?",
                    [(fav_id, i) for i in ids],
                )
            self._conn.commit()
        return len(ids)

    def toggle_favorite(self, image_id: int) -> bool:
        item = self.get_image(image_id)
        if item is None:
            return False
        target = not item.favorited
        self.set_favorite([image_id], target)
        return target

    def set_rating(self, image_ids: Iterable[int], rating: int) -> int:
        ids = list(image_ids)
        if not ids:
            return 0
        rating = max(0, min(5, int(rating)))
        with self._lock:
            self._conn.executemany(
                "UPDATE images SET rating = ? WHERE id = ?", [(rating, i) for i in ids]
            )
            self._conn.commit()
        return len(ids)

    def update_ai_fields(self, image_id: int, *, tags: str = "", caption: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE images SET ai_tags = ?, ai_caption = ? WHERE id = ?",
                (tags, caption, int(image_id)),
            )
            self._conn.commit()

    def mark_opened(self, image_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE images SET last_opened_at = ? WHERE id = ?", (int(time.time()), image_id)
            )
            self._conn.commit()

    def delete_images(self, image_ids: Iterable[int]) -> int:
        ids = list(image_ids)
        if not ids:
            return 0
        with self._lock:
            self._conn.executemany("DELETE FROM images WHERE id = ?", [(i,) for i in ids])
            self._conn.commit()
        return len(ids)

    # ---------- 相册 ----------

    def _ensure_favorite(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM albums WHERE kind = 'favorite' ORDER BY id LIMIT 1"
            ).fetchone()
            if row:
                return int(row["id"])
            cur = self._conn.execute(
                "INSERT INTO albums (name, kind, created_at) VALUES (?,?,?)",
                (FAVORITE_ALBUM_NAME, "favorite", int(time.time())),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    @property
    def favorite_album_id(self) -> int:
        return self._ensure_favorite()

    def create_album(self, name: str, kind: str = "album") -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("相册名不能为空")
        with self._lock:
            row = self._conn.execute("SELECT id FROM albums WHERE name = ?", (name,)).fetchone()
            if row:
                return int(row["id"])
            cur = self._conn.execute(
                "INSERT INTO albums (name, kind, created_at) VALUES (?,?,?)",
                (name, kind, int(time.time())),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_albums(self, include_favorite: bool = True) -> List[Album]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT a.id, a.name, a.cover_path, a.kind, a.created_at,
                          (SELECT COUNT(*) FROM album_items i WHERE i.album_id = a.id) AS n
                   FROM albums a
                   ORDER BY a.kind = 'favorite' DESC, a.created_at ASC"""
            ).fetchall()
        items = [
            Album(id=int(r["id"]), name=r["name"], cover_path=r["cover_path"], kind=r["kind"],
                  created_at=int(r["created_at"]), image_count=int(r["n"]))
            for r in rows
        ]
        if not include_favorite:
            items = [a for a in items if not a.is_favorite]
        return items

    def get_album(self, album_id: int) -> Optional[Album]:
        with self._lock:
            row = self._conn.execute(
                """SELECT a.id, a.name, a.cover_path, a.kind, a.created_at,
                          (SELECT COUNT(*) FROM album_items i WHERE i.album_id = a.id) AS n
                   FROM albums a WHERE a.id = ?""",
                (album_id,),
            ).fetchone()
        if not row:
            return None
        return Album(id=int(row["id"]), name=row["name"], cover_path=row["cover_path"],
                     kind=row["kind"], created_at=int(row["created_at"]),
                     image_count=int(row["n"]))

    def rename_album(self, album_id: int, new_name: str) -> bool:
        new_name = (new_name or "").strip()
        if not new_name:
            return False
        album = self.get_album(album_id)
        if album is None or album.is_favorite:
            return False
        with self._lock:
            try:
                self._conn.execute("UPDATE albums SET name = ? WHERE id = ?", (new_name, album_id))
                self._conn.commit()
            except sqlite3.IntegrityError:
                return False
        return True

    def delete_album(self, album_id: int) -> bool:
        """删除相册（不删除原图）。"""
        album = self.get_album(album_id)
        if album is None or album.is_favorite:
            return False
        with self._lock:
            self._conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
            self._conn.commit()
        return True

    def add_to_album(self, album_id: int, image_ids: Iterable[int]) -> int:
        ids = list(image_ids)
        if not ids:
            return 0
        now = int(time.time())
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(position), 0) AS pos FROM album_items WHERE album_id = ?",
                (album_id,),
            ).fetchone()
            position = int(row["pos"]) if row else 0
            added = 0
            for image_id in ids:
                cur = self._conn.execute(
                    """INSERT OR IGNORE INTO album_items (album_id, image_id, position, added_at)
                       VALUES (?,?,?,?)""",
                    (album_id, image_id, position, now),
                )
                if cur.rowcount:
                    position += 1
                    added += 1
            self._conn.commit()
        return added

    def remove_from_album(self, album_id: int, image_ids: Iterable[int]) -> int:
        ids = list(image_ids)
        if not ids:
            return 0
        album = self.get_album(album_id)
        with self._lock:
            self._conn.executemany(
                "DELETE FROM album_items WHERE album_id = ? AND image_id = ?",
                [(album_id, i) for i in ids],
            )
            if album is not None and album.is_favorite:
                self._conn.executemany(
                    "UPDATE images SET favorited = 0 WHERE id = ?", [(i,) for i in ids]
                )
            self._conn.commit()
        return len(ids)

    def list_album_images(self, album_id: int) -> List[ImageItem]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT i.* FROM album_items a JOIN images i ON i.id = a.image_id
                   WHERE a.album_id = ? ORDER BY a.position ASC, a.added_at ASC""",
                (album_id,),
            ).fetchall()
        return [self._row_to_image(r) for r in rows]

    def set_album_cover(self, album_id: int, path: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE albums SET cover_path = ? WHERE id = ?", (path, album_id))
            self._conn.commit()

    # ---------- 标签 ----------

    def ensure_tag(self, name: str, color: str = "#4f5bd5", source: str = "user") -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("标签名不能为空")
        with self._lock:
            row = self._conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
            if row:
                return int(row["id"])
            cur = self._conn.execute(
                "INSERT INTO tags (name, color, source, use_count) VALUES (?,?,?,0)",
                (name, color, source),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_tags(self, source: str = "") -> List[Tag]:
        sql = "SELECT * FROM tags"
        args: list = []
        if source:
            sql += " WHERE source = ?"
            args.append(source)
        sql += " ORDER BY use_count DESC, name COLLATE NOCASE"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [
            Tag(id=int(r["id"]), name=r["name"], color=r["color"], source=r["source"],
                use_count=int(r["use_count"]))
            for r in rows
        ]

    def tag_images(self, image_ids: Iterable[int], tag_name: str, *, source: str = "user") -> int:
        ids = list(image_ids)
        if not ids:
            return 0
        tag_id = self.ensure_tag(tag_name, source=source)
        with self._lock:
            added = 0
            for image_id in ids:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO image_tags (image_id, tag_id) VALUES (?,?)",
                    (image_id, tag_id),
                )
                added += cur.rowcount or 0
            self._refresh_tag_counts()
            self._conn.commit()
        return added

    def untag_images(self, image_ids: Iterable[int], tag_name: str) -> int:
        ids = list(image_ids)
        if not ids:
            return 0
        with self._lock:
            row = self._conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
            if row is None:
                return 0
            tag_id = int(row["id"])
            self._conn.executemany(
                "DELETE FROM image_tags WHERE image_id = ? AND tag_id = ?",
                [(i, tag_id) for i in ids],
            )
            self._refresh_tag_counts()
            self._conn.commit()
        return len(ids)

    def rename_tag(self, tag_id: int, new_name: str) -> bool:
        new_name = (new_name or "").strip()
        if not new_name:
            return False
        with self._lock:
            try:
                self._conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new_name, tag_id))
                self._conn.commit()
            except sqlite3.IntegrityError:
                return False
        return True

    def merge_tags(self, source_id: int, target_id: int) -> bool:
        """把 source 标签的所有图片并入 target，然后删除 source。"""
        if source_id == target_id:
            return False
        with self._lock:
            rows = self._conn.execute(
                "SELECT image_id FROM image_tags WHERE tag_id = ?", (source_id,)
            ).fetchall()
            self._conn.executemany(
                "INSERT OR IGNORE INTO image_tags (image_id, tag_id) VALUES (?,?)",
                [(int(r["image_id"]), target_id) for r in rows],
            )
            self._conn.execute("DELETE FROM tags WHERE id = ?", (source_id,))
            self._refresh_tag_counts()
            self._conn.commit()
        return True

    def delete_tag(self, tag_id: int) -> bool:
        with self._lock:
            self._conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            self._refresh_tag_counts()
            self._conn.commit()
        return True

    def image_tags(self, image_id: int) -> List[str]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT t.name FROM image_tags it JOIN tags t ON t.id = it.tag_id
                   WHERE it.image_id = ? ORDER BY t.name""",
                (image_id,),
            ).fetchall()
        return [r["name"] for r in rows]

    def _refresh_tag_counts(self) -> None:
        self._conn.execute(
            """UPDATE tags SET use_count =
                   (SELECT COUNT(*) FROM image_tags it WHERE it.tag_id = tags.id)"""
        )

    # ---------- 编辑历史（非破坏性） ----------

    def save_edit_steps(self, image_id: int, steps: Iterable[EditStep]) -> None:
        payload = json.dumps([s.to_json() for s in steps], ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                """INSERT INTO edits (image_id, steps_json, updated_at) VALUES (?,?,?)
                   ON CONFLICT(image_id) DO UPDATE SET
                       steps_json = excluded.steps_json, updated_at = excluded.updated_at""",
                (int(image_id), payload, int(time.time())),
            )
            self._conn.commit()

    def load_edit_steps(self, image_id: int) -> List[EditStep]:
        with self._lock:
            row = self._conn.execute(
                "SELECT steps_json FROM edits WHERE image_id = ?", (int(image_id),)
            ).fetchone()
        if not row:
            return []
        try:
            data = json.loads(row["steps_json"] or "[]")
        except ValueError:
            return []
        return [EditStep.from_json(item) for item in data if isinstance(item, dict)]

    def clear_edit_steps(self, image_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM edits WHERE image_id = ?", (int(image_id),))
            self._conn.commit()

    # ---------- AI 任务 ----------

    def create_task(self, image_id: int, kind: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO ai_tasks (image_id, kind, state, progress, created_at)
                   VALUES (?,?,?,?,?)""",
                (int(image_id), kind, "pending", 0, int(time.time())),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_task(self, task_id: int, *, state: str = "", progress: int = -1,
                    result_path: str = "", message: str = "") -> None:
        sets: list[str] = []
        args: list = []
        if state:
            sets.append("state = ?")
            args.append(state)
            if state in ("done", "failed", "canceled"):
                sets.append("finished_at = ?")
                args.append(int(time.time()))
        if progress >= 0:
            sets.append("progress = ?")
            args.append(int(progress))
        if result_path:
            sets.append("result_path = ?")
            args.append(result_path)
        if message:
            sets.append("message = ?")
            args.append(message[:500])
        if not sets:
            return
        args.append(int(task_id))
        with self._lock:
            self._conn.execute(f"UPDATE ai_tasks SET {', '.join(sets)} WHERE id = ?", args)
            self._conn.commit()

    def list_tasks(self, limit: int = 100) -> List[AiTask]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM ai_tasks ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            AiTask(id=int(r["id"]), image_id=int(r["image_id"]), kind=r["kind"], state=r["state"],
                   progress=int(r["progress"]), result_path=r["result_path"], message=r["message"],
                   created_at=int(r["created_at"]), finished_at=int(r["finished_at"]))
            for r in rows
        ]

    def clear_tasks(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM ai_tasks")
            self._conn.commit()

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
    def _row_to_image(row) -> ImageItem:
        return ImageItem(
            id=int(row["id"]), path=row["path"], url=row["url"], thumb_path=row["thumb_path"],
            title=row["title"], ext=row["ext"], mime=row["mime"],
            size_bytes=int(row["size_bytes"]), width=int(row["width"]), height=int(row["height"]),
            taken_at=int(row["taken_at"]), added_at=int(row["added_at"]),
            exif_json=row["exif_json"], gps_lat=row["gps_lat"], gps_lon=row["gps_lon"],
            camera=row["camera"], source=row["source"], sha256=row["sha256"],
            dhash=row["dhash"], favorited=bool(row["favorited"]), rating=int(row["rating"]),
            ai_tags=row["ai_tags"], ai_caption=row["ai_caption"],
            last_opened_at=int(row["last_opened_at"]),
        )


__all__ = ["FAVORITE_ALBUM_NAME", "ImageStorage"]
