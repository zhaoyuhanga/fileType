"""墨软动漫 SQLite 存储（`anime_*` 表 CRUD）。

分层与 `core.video.storage.VideoStorage` 完全一致：本模块只管 SQLite，
不碰网络、不碰 Qt、不做业务决策（业务编排在 `library.py`）。

表与语义（DDL 见 `core/platform/migrations/v3_anime.py`）：

- `anime_rules`：规则**索引**。规则 JSON 文件才是唯一真相，这里只存启用状态、
  展示字段与最近一次试跑结果 —— 所以 `prune_missing()` 只把消失的规则**标记为不可用**
  而不是删行：用户把规则文件临时挪走再放回来时，启用状态与试跑记录不该丢。
- `anime_series`：番剧库。同一部作品在不同规则下是**两行**（UNIQUE(rule_key, remote_id)），
  这正是"换规则/换线路"能保留各自进度与元数据的前提。
- `anime_favorites` / `anime_progress`：追番与按集进度（都靠 UNIQUE 约束去重，
  "重复追番/重复上报进度"必须天然幂等，UI 每 5 秒上报一次进度，靠代码判重迟早会漏）。
- `anime_history`：**快照式**历史（存 title/episode_name），番剧行被删后历史仍可读。
- `anime_danmaku`：弹幕**索引**（磁盘文件是弹幕本体，这里只记 episodeId/条数/路径）。
- `anime_download_tasks`：下载任务（与影视同构但独立成表，板块数据互不干扰）。

时间字段一律 epoch 秒（与全库一致）；所有方法都能在空库上安全调用。
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable, Optional

from modu_workbench.core.platform.db import SqliteStore

from .models import AnimeRule, AnimeSeries, WatchProgress, normalize_title

__all__ = [
    "ANIME_DOWNLOAD_STATES",
    "AnimeStorage",
    "load_subscriptions",
    "save_subscriptions",
]

#: 下载任务状态机（`anime_download_tasks.state`）。
#: paused 是"保留 .parts 目录、可继续"的暂停，与 failed 区分开：
#: 前者不该被"清理失败任务"按钮删掉，否则断点续传就白做了。
ANIME_DOWNLOAD_STATES = ("pending", "running", "paused", "failed", "done")

#: 订阅列表所在的设置键（`app_settings` 里实际是 `anime/rule_subscriptions`）。
SUBSCRIPTIONS_SETTING = "rule_subscriptions"

#: 允许写入 `anime_series` 的列（白名单：避免把 `id`/`created_at` 这类列覆写成 0）。
_SERIES_META_FIELDS = (
    "rule_key", "remote_id", "title", "title_norm", "cover", "year", "summary",
    "bangumi_id", "total_episodes", "latest_episode", "air_weekday",
)

#: 允许更新的下载任务列（`state/message` 之外都是进度位置类字段）。
_DOWNLOAD_TASK_FIELDS = (
    "episode_name", "url", "dest_path", "parts_dir", "total_parts",
    "done_parts", "state", "message",
)


def _now() -> int:
    return int(time.time())


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def _limit(value: Any, default: int = 200, maximum: int = 5000) -> int:
    """把 limit 夹到合法范围。

    负数或 0 会让 SQLite 的 `LIMIT` 语义变成"不限制"，与调用方意图相反
    （`list_history(limit=-1)` 会一次性拉全表），所以这里统一兜住。
    """
    number = _as_int(value, default)
    if number <= 0:
        return default
    return min(number, maximum)


class AnimeStorage(SqliteStore):
    """墨软动漫存储（与 MusicStorage/VideoStorage 同一套锁与事务惯例）。"""

    settings_namespace = "anime"

    # ------------------------------------------------------------------ 行映射

    @staticmethod
    def _row_to_series(row) -> AnimeSeries:  # noqa: ANN001  sqlite3.Row
        """行 → `AnimeSeries`。

        注意：表里**没有** `score` 列（方案里的 DDL 未包含），因此这里恒为 0.0。
        评分来自 Bangumi 元数据（`bangumi.py` 自己缓存），不进 `anime_series`。
        """
        return AnimeSeries(
            id=_as_int(row["id"]),
            rule_key=_as_str(row["rule_key"]),
            remote_id=_as_str(row["remote_id"]),
            title=_as_str(row["title"]),
            cover=_as_str(row["cover"]),
            year=_as_str(row["year"]),
            summary=_as_str(row["summary"]),
            bangumi_id=_as_int(row["bangumi_id"]),
            total_episodes=_as_int(row["total_episodes"]),
            latest_episode=_as_str(row["latest_episode"]),
            air_weekday=_as_int(row["air_weekday"], -1),
            created_at=_as_int(row["created_at"]),
            updated_at=_as_int(row["updated_at"]),
        )

    @staticmethod
    def _row_to_progress(row) -> WatchProgress:  # noqa: ANN001
        return WatchProgress(
            series_id=_as_int(row["series_id"]),
            episode_key=_as_str(row["episode_key"]),
            episode_name=_as_str(row["episode_name"]),
            episode_index=_as_int(row["episode_index"]),
            position_ms=_as_int(row["position_ms"]),
            duration_ms=_as_int(row["duration_ms"]),
            watched=bool(row["watched"]),
            updated_at=_as_int(row["updated_at"]),
        )

    # ------------------------------------------------------------------ 规则索引

    def upsert_rule(self, rule: AnimeRule, *, enabled: Optional[bool] = None) -> int:
        """落库一条规则索引并返回行 id。

        `enabled` 的语义是"用户的选择"：
        - 第一次见到这条规则（新导入）默认 **启用**；
        - 重新扫描到同一条规则（文件没变）时**保留用户当前选择**，不因为
          "每启动一次就重新 upsert" 把用户手动关掉的规则又打开；
        - 只有显式传入 `enabled`（规则页的开关、`set_rule_enabled`）才覆盖。
        """
        now = _now()
        with self._lock:
            row = self._conn.execute(
                "SELECT id, enabled, test_state, test_note FROM anime_rules WHERE key = ?",
                (rule.key,),
            ).fetchone()
            if row is not None:
                rule_id = _as_int(row["id"])
                final_enabled = _as_int(row["enabled"], 1) if enabled is None else int(bool(enabled))
                # 文件回来了：清掉 prune_missing 留下的 missing 标记
                # （否则规则页会一直显示"文件已不存在"，用户只能删库重来）
                was_missing = _as_str(row["test_state"]) == "missing"
                test_state = "" if was_missing else _as_str(row["test_state"])
                test_note = "" if was_missing else _as_str(row["test_note"])
                self._conn.execute(
                    """UPDATE anime_rules SET name = ?, version = ?, kind = ?, path = ?,
                              builtin = ?, enabled = ?, source_url = ?, test_state = ?,
                              test_note = ?, updated_at = ?
                       WHERE id = ?""",
                    (rule.label, _as_str(rule.version), rule.kind, _as_str(rule.path),
                     int(bool(rule.builtin)), final_enabled, _as_str(rule.source_url),
                     test_state, test_note, now, rule_id),
                )
            else:
                final_enabled = 1 if enabled is None else int(bool(enabled))
                cursor = self._conn.execute(
                    """INSERT INTO anime_rules (key, name, version, kind, path, builtin,
                              enabled, source_url, test_state, test_note, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,'','',?)""",
                    (rule.key, rule.label, _as_str(rule.version), rule.kind, _as_str(rule.path),
                     int(bool(rule.builtin)), final_enabled, _as_str(rule.source_url), now),
                )
                rule_id = int(cursor.lastrowid or 0)
            self._conn.commit()
            return rule_id

    def set_rule_enabled(self, key: str, enabled: bool) -> bool:
        """开关一条规则；返回是否命中（规则文件被删后再点开关应该是 False 而不是静默成功）。"""
        cursor = self.execute(
            "UPDATE anime_rules SET enabled = ?, updated_at = ? WHERE key = ?",
            (int(bool(enabled)), _now(), _as_str(key)),
        )
        return bool(cursor.rowcount)

    def get_rule(self, key: str) -> Optional[dict]:
        row = self.query_one("SELECT * FROM anime_rules WHERE key = ?", (_as_str(key),))
        return dict(row) if row is not None else None

    def list_rules(self) -> list[dict]:
        """全部规则索引：内置在后、按名称排序（与规则页展示顺序一致）。"""
        rows = self.query(
            "SELECT * FROM anime_rules ORDER BY builtin ASC, name COLLATE NOCASE ASC, key ASC"
        )
        return [dict(row) for row in rows]

    def set_rule_test(self, key: str, state: str, note: str = "") -> bool:
        """写最近一次试跑结果（`state`：'' / ok / fail；`note` 给用户看的可读原因）。"""
        cursor = self.execute(
            "UPDATE anime_rules SET test_state = ?, test_note = ?, updated_at = ? WHERE key = ?",
            (_as_str(state), _as_str(note), _now(), _as_str(key)),
        )
        return bool(cursor.rowcount)

    def delete_rule(self, key: str) -> bool:
        """删除规则索引行。

        只删索引：**不碰规则文件**（文件是唯一真相，删除文件由 `RuleManager` 负责），
        也不删番剧/进度（用户追的番不该因为换了一条规则就消失）。
        """
        cursor = self.execute("DELETE FROM anime_rules WHERE key = ?", (_as_str(key),))
        return bool(cursor.rowcount)

    def prune_missing(self, keys: Iterable[str]) -> int:
        """把"文件已消失"的规则标记为不可用，返回**本次新标记**的行数。

        为什么是标记而不是删行：规则文件可能只是被临时挪走（同步盘、手工整理），
        删行会连带丢掉用户的启用状态与试跑记录。这里把 `path` 清空作为"失效"标记，
        并用 `test_state='missing'` 说明原因；文件回来后 `upsert_rule` 会重新写入 path
        并清掉标记（下面显式复位 test_state）。

        注意：**不按 `path` 是否为空做前置判断**。规则对象来自文件时 path 一定有值，
        但"导入后写入失败"的索引行同样属于"文件不在"，用 path 当条件会让 prune
        静默失效；幂等性改由 `test_state='missing'` 判定（已标记的不重复计数）。
        """
        alive = {_as_str(key) for key in keys}
        rows = self.query("SELECT key, path, test_state FROM anime_rules")
        marked = 0
        for row in rows:
            key = _as_str(row["key"])
            if key in alive:
                continue
            if _as_str(row["test_state"]) == "missing" and not _as_str(row["path"]):
                continue
            self.execute(
                """UPDATE anime_rules SET path = '', test_state = 'missing',
                          test_note = '规则文件已不存在（可能被移动或删除）', updated_at = ?
                   WHERE key = ?""",
                (_now(), key),
            )
            marked += 1
        return marked

    def enabled_keys(self) -> list[str]:
        rows = self.query("SELECT key FROM anime_rules WHERE enabled = 1 ORDER BY key ASC")
        return [_as_str(row["key"]) for row in rows]

    # ------------------------------------------------------------------ 番剧

    def upsert_series(self, series: AnimeSeries) -> int:
        """按 (rule_key, remote_id) upsert 番剧并返回 id。

        - `title_norm` 一律**由标题重算**（不信任调用方传入），否则跨规则合并、
          弹幕匹配会用到过期归一化值；
        - `created_at` 保留首次入库时间（追番时间在 UI 上要稳定，不能每次刷新都变）；
        - 系列里没填的字段（如 `summary`）**不清空**已有值：详情页某次抓取失败
          返回空摘要时，不该把库里的简介抹掉。
        """
        now = _now()
        title_norm = normalize_title(series.title)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM anime_series WHERE rule_key = ? AND remote_id = ?",
                (_as_str(series.rule_key), _as_str(series.remote_id)),
            ).fetchone()
            if row is not None:
                series_id = _as_int(row["id"])
                self._conn.execute(
                    """UPDATE anime_series SET title = ?, title_norm = ?, cover = ?, year = ?,
                              summary = ?, bangumi_id = ?, total_episodes = ?,
                              latest_episode = ?, air_weekday = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        _as_str(series.title) or _as_str(row["title"]),
                        title_norm or _as_str(row["title_norm"]),
                        _as_str(series.cover) or _as_str(row["cover"]),
                        _as_str(series.year) or _as_str(row["year"]),
                        _as_str(series.summary) or _as_str(row["summary"]),
                        _as_int(series.bangumi_id) or _as_int(row["bangumi_id"]),
                        _as_int(series.total_episodes) or _as_int(row["total_episodes"]),
                        _as_str(series.latest_episode) or _as_str(row["latest_episode"]),
                        _as_int(series.air_weekday, -1) if series.air_weekday != -1
                        else _as_int(row["air_weekday"], -1),
                        now, series_id,
                    ),
                )
            else:
                cursor = self._conn.execute(
                    """INSERT INTO anime_series (rule_key, remote_id, title, title_norm, cover,
                              year, summary, bangumi_id, total_episodes, latest_episode,
                              air_weekday, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        _as_str(series.rule_key), _as_str(series.remote_id),
                        _as_str(series.title), title_norm, _as_str(series.cover),
                        _as_str(series.year), _as_str(series.summary),
                        _as_int(series.bangumi_id), _as_int(series.total_episodes),
                        _as_str(series.latest_episode),
                        _as_int(series.air_weekday, -1), now, now,
                    ),
                )
                series_id = int(cursor.lastrowid or 0)
            self._conn.commit()
        series.id = series_id
        series.created_at = _as_int(row["created_at"]) if row is not None else now
        series.updated_at = now
        return series_id

    def get_series(self, series_id: int) -> Optional[AnimeSeries]:
        row = self.query_one("SELECT * FROM anime_series WHERE id = ?", (_as_int(series_id),))
        return self._row_to_series(row) if row is not None else None

    def find_series(self, rule_key: str, remote_id: str) -> Optional[AnimeSeries]:
        row = self.query_one(
            "SELECT * FROM anime_series WHERE rule_key = ? AND remote_id = ?",
            (_as_str(rule_key), _as_str(remote_id)),
        )
        return self._row_to_series(row) if row is not None else None

    def list_series(self, *, favorite: Optional[bool] = None, keyword: str = "",
                    limit: int = 200) -> list[AnimeSeries]:
        """番剧列表。

        - `favorite=True` 只看追番（`False` 只看没追的）；
        - `keyword` 同时匹配标题与归一化标题（用户输"进击的巨人"能命中带标点的原标题）；
        - 默认按 `updated_at DESC`：番剧库的"最近更新"分组直接用它。
        """
        sql = "SELECT s.* FROM anime_series s"
        args: list = []
        if favorite is not None:
            sql += " WHERE s.id %s (SELECT series_id FROM anime_favorites)" % (
                "IN" if favorite else "NOT IN")
        if keyword:
            like = f"%{keyword}%"
            norm = normalize_title(keyword)
            sql += " AND " if favorite is not None else " WHERE "
            sql += "(s.title LIKE ? OR s.title_norm LIKE ?)"
            args += [like, f"%{norm}%"]
        sql += " ORDER BY s.updated_at DESC, s.id DESC LIMIT ?"
        args.append(_limit(limit))
        return [self._row_to_series(row) for row in self.query(sql, args)]

    def set_series_meta(self, series_id: int, **fields: Any) -> bool:
        """更新番剧元数据字段（白名单外的键直接忽略，避免写坏结构）。

        典型用途：更新检查写回 `total_episodes` / `latest_episode`，
        Bangumi 元数据回填 `bangumi_id` / `air_weekday` / `summary`。
        """
        updates = {key: value for key, value in fields.items() if key in _SERIES_META_FIELDS}
        if not updates:
            return False
        columns: list[str] = []
        args: list = []
        for key, value in updates.items():
            if key == "title":
                value = _as_str(value)
                updates["title_norm"] = normalize_title(value)
            columns.append(f"{key} = ?")
            args.append(value)
        if "title_norm" in updates and "title_norm = ?" not in columns:
            columns.append("title_norm = ?")
            args.append(updates["title_norm"])
        columns.append("updated_at = ?")
        args.append(_now())
        args.append(_as_int(series_id))
        cursor = self.execute(
            f"UPDATE anime_series SET {', '.join(columns)} WHERE id = ?", args
        )
        return bool(cursor.rowcount)

    def delete_series(self, series_id: int) -> int:
        """删除番剧及其追番/进度/弹幕索引，返回删掉的番剧行数（0 或 1）。

        **历史不删**：历史是快照（自带 title/episode_name），删番剧后用户仍能
        从历史里看到"看过什么"，也能一键重新搜索（与影视的语义一致）。
        下载任务同样保留：文件可能已经下好，任务记录是它的索引。
        """
        target = _as_int(series_id)
        with self.transaction() as conn:
            conn.execute("DELETE FROM anime_favorites WHERE series_id = ?", (target,))
            conn.execute("DELETE FROM anime_progress WHERE series_id = ?", (target,))
            conn.execute("DELETE FROM anime_danmaku WHERE series_id = ?", (target,))
            cursor = conn.execute("DELETE FROM anime_series WHERE id = ?", (target,))
            return int(cursor.rowcount)

    def favorite_series(self) -> list[AnimeSeries]:
        """追番的番剧列表（按追番时间倒序）。"""
        rows = self.query(
            """SELECT s.* FROM anime_favorites f JOIN anime_series s ON s.id = f.series_id
               ORDER BY f.created_at DESC, f.id DESC"""
        )
        return [self._row_to_series(row) for row in rows]

    # ------------------------------------------------------------------ 追番

    def add_favorite(self, series_id: int, note: str = "") -> bool:
        """加追番（已追则只更新备注，返回是否新建）。

        `ON CONFLICT ... DO UPDATE` 而不是 `INSERT OR IGNORE`：用户点了"追番"并写了备注，
        再点一次不应该把备注丢掉。`created_at` 保持首次时间（列表顺序不跳）。
        """
        target = _as_int(series_id)
        if not target:
            return False
        now = _now()
        with self._lock:
            existed = self._conn.execute(
                "SELECT id FROM anime_favorites WHERE series_id = ?", (target,)
            ).fetchone() is not None
            self._conn.execute(
                """INSERT INTO anime_favorites (series_id, note, created_at) VALUES (?,?,?)
                   ON CONFLICT(series_id) DO UPDATE SET note = excluded.note""",
                (target, _as_str(note), now),
            )
            self._conn.commit()
        return not existed

    def remove_favorite(self, series_id: int) -> bool:
        cursor = self.execute("DELETE FROM anime_favorites WHERE series_id = ?",
                              (_as_int(series_id),))
        return bool(cursor.rowcount)

    def is_favorite(self, series_id: int) -> bool:
        return self.query_one("SELECT 1 AS ok FROM anime_favorites WHERE series_id = ?",
                              (_as_int(series_id),)) is not None

    def list_favorites(self) -> list[tuple[AnimeSeries, dict]]:
        """追番列表：`(番剧, 追番行信息)`，按追番时间倒序。"""
        rows = self.query(
            """SELECT s.*, f.id AS fav_id, f.note AS fav_note, f.created_at AS fav_created_at
               FROM anime_favorites f JOIN anime_series s ON s.id = f.series_id
               ORDER BY f.created_at DESC, f.id DESC"""
        )
        return [
            (self._row_to_series(row),
             {"id": _as_int(row["fav_id"]), "series_id": _as_int(row["id"]),
              "note": _as_str(row["fav_note"]), "created_at": _as_int(row["fav_created_at"])})
            for row in rows
        ]

    # ------------------------------------------------------------------ 进度

    def save_progress(self, series_id: int, episode_key: str, episode_name: str = "",
                      episode_index: int = 0, position_ms: int = 0,
                      duration_ms: int = 0, watched: bool = False) -> bool:
        """按 (series_id, episode_key) upsert 进度；返回是否是"看完"状态。

        播放器每 5 秒上报一次，因此这里必须**幂等且便宜**（一条 UPSERT）。
        `episode_index` 为 0 时保留库里的旧值：有些调用方（比如"标记已看"按钮）
        手里只有 episode_key，不该把集序号冲成 0。
        """
        target = _as_int(series_id)
        key = _as_str(episode_key)
        if not target or not key:
            return False
        self.execute(
            """INSERT INTO anime_progress (series_id, episode_key, episode_name, episode_index,
                      position_ms, duration_ms, watched, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(series_id, episode_key) DO UPDATE SET
                   episode_name = CASE WHEN excluded.episode_name <> ''
                                       THEN excluded.episode_name ELSE episode_name END,
                   episode_index = CASE WHEN excluded.episode_index <> 0
                                        THEN excluded.episode_index ELSE episode_index END,
                   position_ms = excluded.position_ms,
                   duration_ms = CASE WHEN excluded.duration_ms <> 0
                                      THEN excluded.duration_ms ELSE duration_ms END,
                   watched = excluded.watched,
                   updated_at = excluded.updated_at""",
            (target, key, _as_str(episode_name), _as_int(episode_index),
             max(0, _as_int(position_ms)), max(0, _as_int(duration_ms)),
             int(bool(watched)), _now()),
        )
        return bool(watched)

    def get_progress(self, series_id: int, episode_key: str) -> Optional[WatchProgress]:
        row = self.query_one(
            "SELECT * FROM anime_progress WHERE series_id = ? AND episode_key = ?",
            (_as_int(series_id), _as_str(episode_key)),
        )
        return self._row_to_progress(row) if row is not None else None

    def list_progress(self, series_id: int) -> list[WatchProgress]:
        rows = self.query(
            """SELECT * FROM anime_progress WHERE series_id = ?
               ORDER BY episode_index ASC, updated_at ASC""",
            (_as_int(series_id),),
        )
        return [self._row_to_progress(row) for row in rows]

    def continue_watching(self, limit: int = 20) -> list[tuple[AnimeSeries, WatchProgress]]:
        """继续观看：最近看过、**还没看完**的进度（按更新时间倒序）。

        "看完"的判定与 `WatchProgress.finished` 一致（watched 标记或 >=95%）。
        同时过滤 `watched=1` 与"position 达到 95%"，两者取并集：
        前者是用户手动标记，后者是播放器播到结尾没来得及写 watched。
        """
        rows = self.query(
            """SELECT p.* FROM anime_progress p JOIN anime_series s ON s.id = p.series_id
               WHERE p.watched = 0
                 AND NOT (p.duration_ms > 0 AND p.position_ms >= p.duration_ms * 0.95)
               ORDER BY p.updated_at DESC, p.id DESC"""
        )
        items: list[tuple[AnimeSeries, WatchProgress]] = []
        for row in rows:
            if len(items) >= _limit(limit):
                break
            progress = self._row_to_progress(row)
            series = self.get_series(progress.series_id)
            if series is None:
                continue
            items.append((series, progress))
        return items

    def mark_watched(self, series_id: int, episode_key: str, watched: bool = True) -> bool:
        """标记某集已看/未看（不存在则创建一行，便于"手动打勾"）。"""
        target = _as_int(series_id)
        key = _as_str(episode_key)
        if not target or not key:
            return False
        self.execute(
            """INSERT INTO anime_progress (series_id, episode_key, watched, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(series_id, episode_key) DO UPDATE SET
                   watched = excluded.watched, updated_at = excluded.updated_at""",
            (target, key, int(bool(watched)), _now()),
        )
        return bool(watched)

    # ------------------------------------------------------------------ 历史

    def add_history(self, series_id: int, rule_key: str, title: str,
                    episode_name: str = "", source: str = "") -> int:
        """写一条播放历史（title 作为快照，删番剧后仍可读）。"""
        cursor = self.execute(
            """INSERT INTO anime_history (series_id, rule_key, title, episode_name, source, played_at)
               VALUES (?,?,?,?,?,?)""",
            (_as_int(series_id), _as_str(rule_key), _as_str(title),
             _as_str(episode_name), _as_str(source), _now()),
        )
        return int(cursor.lastrowid or 0)

    def list_history(self, limit: int = 200) -> list[dict]:
        rows = self.query(
            "SELECT * FROM anime_history ORDER BY played_at DESC, id DESC LIMIT ?",
            (_limit(limit),),
        )
        return [dict(row) for row in rows]

    def clear_history(self) -> None:
        self.execute("DELETE FROM anime_history")

    # ------------------------------------------------------------------ 弹幕索引

    def save_danmaku_index(self, series_id: int, episode_key: str, provider: str,
                           episode_id: int, count: int, path: str) -> bool:
        """记录弹幕缓存索引（按 (series_id, episode_key, provider) 唯一）。"""
        self.execute(
            """INSERT INTO anime_danmaku (series_id, episode_key, provider, episode_id,
                      count, path, fetched_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(series_id, episode_key, provider) DO UPDATE SET
                   episode_id = excluded.episode_id, count = excluded.count,
                   path = excluded.path, fetched_at = excluded.fetched_at""",
            (_as_int(series_id), _as_str(episode_key), _as_str(provider) or "dandanplay",
             _as_int(episode_id), _as_int(count), _as_str(path), _now()),
        )
        return True

    def get_danmaku_index(self, series_id: int, episode_key: str,
                          provider: str = "dandanplay") -> Optional[dict]:
        row = self.query_one(
            """SELECT * FROM anime_danmaku
               WHERE series_id = ? AND episode_key = ? AND provider = ?""",
            (_as_int(series_id), _as_str(episode_key), _as_str(provider) or "dandanplay"),
        )
        return dict(row) if row is not None else None

    def list_danmaku_index(self, series_id: int) -> list[dict]:
        rows = self.query(
            "SELECT * FROM anime_danmaku WHERE series_id = ? ORDER BY episode_key ASC",
            (_as_int(series_id),),
        )
        return [dict(row) for row in rows]

    def purge_danmaku(self, series_id: int = 0) -> int:
        """删除弹幕索引；`series_id=0` 表示**全清**（"清空弹幕缓存"按钮）。

        只删索引行：磁盘 xml/json 由调用方按 `path` 删除（清缓存属于文件系统操作，
        放在这一层会让存储层出现"删文件"的副作用，测试也更难写）。
        """
        target = _as_int(series_id)
        if target:
            cursor = self.execute("DELETE FROM anime_danmaku WHERE series_id = ?", (target,))
        else:
            cursor = self.execute("DELETE FROM anime_danmaku")
        return int(cursor.rowcount or 0)

    # ------------------------------------------------------------------ 下载任务

    def upsert_download_task(self, series_id: int = 0, episode_key: str = "",
                             episode_name: str = "", rule_key: str = "", url: str = "",
                             dest_path: str = "", parts_dir: str = "",
                             total_parts: int = 0, done_parts: int = 0,
                             state: str = "pending", message: str = "") -> int:
        """新建或更新一条下载任务（按 (series_id, episode_key) 去重），返回行 id。

        为什么按 (series_id, episode_key) 去重而不是每次新建：同一集反复点击
        "下载"（换线路重试、暂停后继续）应该更新同一行，否则任务列表会堆出一串
        同名条目，用户根本分不清哪个是真的在跑。
        """
        target = _as_int(series_id)
        key = _as_str(episode_key)
        now = _now()
        with self._lock:
            row = None
            if target or key:
                row = self._conn.execute(
                    """SELECT id FROM anime_download_tasks
                       WHERE series_id = ? AND episode_key = ? ORDER BY id DESC LIMIT 1""",
                    (target, key),
                ).fetchone()
            if row is not None:
                task_id = _as_int(row["id"])
                self._conn.execute(
                    """UPDATE anime_download_tasks SET episode_name = ?, rule_key = ?, url = ?,
                              dest_path = ?, parts_dir = ?, total_parts = ?, done_parts = ?,
                              state = ?, message = ?, updated_at = ?
                       WHERE id = ?""",
                    (_as_str(episode_name), _as_str(rule_key), _as_str(url),
                     _as_str(dest_path), _as_str(parts_dir), _as_int(total_parts),
                     _as_int(done_parts), _as_str(state) or "pending", _as_str(message),
                     now, task_id),
                )
            else:
                cursor = self._conn.execute(
                    """INSERT INTO anime_download_tasks (series_id, episode_key, episode_name,
                              rule_key, url, dest_path, parts_dir, total_parts, done_parts,
                              state, message, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (target, key, _as_str(episode_name), _as_str(rule_key), _as_str(url),
                     _as_str(dest_path), _as_str(parts_dir), _as_int(total_parts),
                     _as_int(done_parts), _as_str(state) or "pending", _as_str(message), now),
                )
                task_id = int(cursor.lastrowid or 0)
            self._conn.commit()
            return task_id

    def update_download_task(self, task_id: int, **fields: Any) -> bool:
        """更新下载任务的可变字段（白名单，返回是否命中）。"""
        updates = {key: value for key, value in fields.items() if key in _DOWNLOAD_TASK_FIELDS}
        if not updates:
            return False
        columns: list[str] = []
        args: list = []
        for key, value in updates.items():
            if key == "state":
                value = _as_str(value) or "pending"
            elif key in ("total_parts", "done_parts"):
                value = max(0, _as_int(value))
            columns.append(f"{key} = ?")
            args.append(value)
        columns.append("updated_at = ?")
        args.append(_now())
        args.append(_as_int(task_id))
        cursor = self.execute(
            f"UPDATE anime_download_tasks SET {', '.join(columns)} WHERE id = ?", args
        )
        return bool(cursor.rowcount)

    def list_download_tasks(self, state: str = "") -> list[dict]:
        sql = "SELECT * FROM anime_download_tasks"
        args: list = []
        if state:
            sql += " WHERE state = ?"
            args.append(_as_str(state))
        sql += " ORDER BY updated_at DESC, id DESC"
        return [dict(row) for row in self.query(sql, args)]

    def get_download_task(self, task_id: int) -> Optional[dict]:
        row = self.query_one("SELECT * FROM anime_download_tasks WHERE id = ?",
                             (_as_int(task_id),))
        return dict(row) if row is not None else None

    def delete_download_task(self, task_id: int) -> bool:
        cursor = self.execute("DELETE FROM anime_download_tasks WHERE id = ?",
                              (_as_int(task_id),))
        return bool(cursor.rowcount)

    # ------------------------------------------------------------------ 导出 / 导入

    def export_payload(self) -> dict:
        """导出追番进度（D 阶段"换台机器还原"用）。

        刻意只导出**用户资产**：番剧元数据 + 追番（含备注）+ 进度 + 启用规则键 + 订阅。
        **不导出**播放历史与下载任务：前者是本地行为日志，后者绑定了本机文件路径，
        导到另一台机器只会变成一堆"失败任务"（诚实边界，写在这里免得以后有人"顺手补上"）。
        """
        series_rows = self.query(
            """SELECT s.*, f.note AS fav_note, f.created_at AS fav_created_at
               FROM anime_series s LEFT JOIN anime_favorites f ON f.series_id = s.id
               ORDER BY s.id ASC"""
        )
        series: list[dict] = []
        favorites: list[dict] = []
        for row in series_rows:
            target = self._row_to_series(row)
            series.append({
                "id": target.id, "rule_key": target.rule_key, "remote_id": target.remote_id,
                "title": target.title, "title_norm": target.title_norm, "cover": target.cover,
                "year": target.year, "summary": target.summary,
                "bangumi_id": target.bangumi_id, "total_episodes": target.total_episodes,
                "latest_episode": target.latest_episode, "air_weekday": target.air_weekday,
                "created_at": target.created_at, "updated_at": target.updated_at,
            })
            if row["fav_note"] is not None or row["fav_created_at"] is not None:
                favorites.append({
                    "series_id": target.id,
                    "note": _as_str(row["fav_note"]),
                    "created_at": _as_int(row["fav_created_at"]),
                })
        progress = [
            {"series_id": _as_int(row["series_id"]), "episode_key": _as_str(row["episode_key"]),
             "episode_name": _as_str(row["episode_name"]),
             "episode_index": _as_int(row["episode_index"]),
             "position_ms": _as_int(row["position_ms"]),
             "duration_ms": _as_int(row["duration_ms"]),
             "watched": int(bool(row["watched"])), "updated_at": _as_int(row["updated_at"])}
            for row in self.query("SELECT * FROM anime_progress ORDER BY id ASC")
        ]
        return {
            "version": 1,
            "kind": "anime-follow",
            "exported_at": _now(),
            "series": series,
            "favorites": favorites,
            "progress": progress,
            "enabled_rules": self.enabled_keys(),
            "subscriptions": load_subscriptions(),
        }

    def import_payload(self, payload: dict, *, merge: bool = True) -> dict:
        """导入 `export_payload()` 的产物，返回 `{"series": n, "favorites": n, "progress": n}`。

        - 对**缺失键完全容忍**：`payload={}`、只有 `series`、键名写错都不会抛异常，
          因为导出文件是用户手里传来传去的文本，格式漂移是常态（宁可导入 0 条也不要崩）；
        - `merge=True`（默认）时按 (rule_key, remote_id) 覆盖元数据、按唯一键覆盖进度，
          绝不删除本机已有数据 —— "导入"不该变成"替换全部"；
        - 计数口径 = **实际写入的行数**（覆盖也算写入），导入后 UI 要能说清"导入了多少"。
        """
        if not isinstance(payload, dict):
            return {"series": 0, "favorites": 0, "progress": 0}
        series_items = payload.get("series") or []
        favorite_items = payload.get("favorites") or []
        progress_items = payload.get("progress") or []
        if not isinstance(series_items, list):
            series_items = []
        if not isinstance(favorite_items, list):
            favorite_items = []
        if not isinstance(progress_items, list):
            progress_items = []

        id_map: dict[int, int] = {}
        series_count = 0
        for item in series_items:
            if not isinstance(item, dict):
                continue
            rule_key = _as_str(item.get("rule_key"))
            remote_id = _as_str(item.get("remote_id"))
            if not rule_key and not remote_id:
                continue          # 连标识都没有的行导入进去只会变成垃圾数据
            series = AnimeSeries(
                rule_key=rule_key, remote_id=remote_id,
                title=_as_str(item.get("title")), cover=_as_str(item.get("cover")),
                year=_as_str(item.get("year")), summary=_as_str(item.get("summary")),
                bangumi_id=_as_int(item.get("bangumi_id")),
                total_episodes=_as_int(item.get("total_episodes")),
                latest_episode=_as_str(item.get("latest_episode")),
                air_weekday=_as_int(item.get("air_weekday"), -1),
            )
            try:
                new_id = self.upsert_series(series)
            except Exception:  # noqa: BLE001  单行坏数据不该毁掉整次导入
                continue
            id_map[_as_int(item.get("id"))] = new_id
            if merge and _as_int(item.get("created_at")):
                # 保留原导出机的"追番时间"，否则跨机器还原后列表顺序会全乱
                self.execute("UPDATE anime_series SET created_at = ? WHERE id = ?",
                             (_as_int(item.get("created_at")), new_id))
            series_count += 1

        favorite_count = 0
        for item in favorite_items:
            if not isinstance(item, dict):
                continue
            old_id = _as_int(item.get("series_id"))
            target = id_map.get(old_id, old_id)
            if not target or self.get_series(target) is None:
                continue          # 依赖的番剧没导进来（缺键/坏行）时跳过，不报错
            self.add_favorite(target, _as_str(item.get("note")))
            favorite_count += 1

        progress_count = 0
        for item in progress_items:
            if not isinstance(item, dict):
                continue
            old_id = _as_int(item.get("series_id"))
            target = id_map.get(old_id, old_id)
            key = _as_str(item.get("episode_key"))
            if not target or not key or self.get_series(target) is None:
                continue
            self.save_progress(
                target, key,
                episode_name=_as_str(item.get("episode_name")),
                episode_index=_as_int(item.get("episode_index")),
                position_ms=_as_int(item.get("position_ms")),
                duration_ms=_as_int(item.get("duration_ms")),
                watched=bool(item.get("watched")),
            )
            progress_count += 1

        # 启用规则键：只在导入方**已有**这条规则时才应用（否则会把"没装规则"全打开）
        enabled = payload.get("enabled_rules") or []
        if isinstance(enabled, list):
            wanted = {_as_str(key) for key in enabled}
            for row in self.query("SELECT key FROM anime_rules"):
                key = _as_str(row["key"])
                if key in wanted:
                    self.set_rule_enabled(key, True)

        subscriptions = payload.get("subscriptions")
        if isinstance(subscriptions, list) and subscriptions:
            save_subscriptions(subscriptions)

        return {"series": series_count, "favorites": favorite_count, "progress": progress_count}


# --------------------------------------------------------------------------- 订阅清单


def load_subscriptions() -> list[dict]:
    """读取规则订阅列表（`app_settings` 的 `anime/rule_subscriptions`，JSON 数组）。

    任何异常都退化成 `[]`：订阅列表是**附属信息**，DB 损坏或格式漂移时
    规则页仍要能打开（`rules.py` 的 `RuleManager.discover()` 会在每次扫描时调它）。

    两个容易踩的坑，都在这里一次性解决：

    1. 用 `AnimeStorage`（而不是裸 `SqliteStore`）—— 裸基类的 `settings_namespace`
       是 `app`，读写会落到 `app/rule_subscriptions` 这个**谁也不会再看一眼**的键上，
       于是"保存成功但读回来是空的"（这个 bug 真实发生过，测试与界面都看不出来）；
    2. 不传 `migrate=False` —— 首次运行（或库刚被删）时 `app_settings` 还不存在，
       跳过迁移会让读取静默拿到空值、写入静默失败；迁移本身是幂等且廉价的。
    """
    store = None
    try:
        from modu_workbench.core.platform.paths import app_db_path

        store = AnimeStorage(app_db_path())
        raw = store.get_setting(SUBSCRIPTIONS_SETTING, "")
    except Exception:  # noqa: BLE001  读不到就当没有订阅
        return []
    finally:
        if store is not None:
            store.close()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def save_subscriptions(entries: Iterable[dict]) -> None:
    """写入规则订阅列表（**静默失败**：写不进去不该让"添加订阅"整个流程报错）。

    与 `load_subscriptions` 同理：命名空间必须是 `anime`（见上面的坑 1），
    并且保留迁移（幂等）。
    """
    try:
        from modu_workbench.core.platform.paths import app_db_path

        items = [dict(item) for item in (entries or []) if isinstance(item, dict)]
        store = AnimeStorage(app_db_path())
        try:
            store.set_setting(SUBSCRIPTIONS_SETTING, json.dumps(items, ensure_ascii=False))
        finally:
            store.close()
    except Exception:  # noqa: BLE001
        return
