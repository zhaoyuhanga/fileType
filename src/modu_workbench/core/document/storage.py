"""墨软文档：持久化（单库 `modu.db` 的 `doc_*` 表）。

存四类东西：
- `doc_documents`：文档库（打开过的文件 + 体检统计 + 收藏/标签）；
- `doc_versions`：**版本快照**（IR 的 JSON）—— 美化/合并/循环美化每轮都能回滚；
- `doc_merge_reports`：合并差异报告与摘要；
- `doc_ai_calls` / `doc_audit`：AI 调用日志与审计日志（含耗时与成本估算）。

设置走 `app_settings` 的 `document/` 命名空间（`SqliteStore` 自动加前缀），
因此不会与其它板块互相覆盖。
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from modu_workbench.core.platform.db import SqliteStore

from .models import AiCallRecord, DocumentIR, MergeReport

#: 每个文档最多保留的版本快照数（超出时删最旧的，避免库无限增长）
MAX_VERSIONS_PER_DOCUMENT = 40


@dataclass
class DocumentRecord:
    """`doc_documents` 的一行。"""

    id: int = 0
    path: str = ""
    title: str = ""
    format: str = ""
    category: str = ""
    size_bytes: int = 0
    blocks: int = 0
    words: int = 0
    pages: int = 0
    favorited: bool = False
    tags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    added_at: int = 0
    opened_at: int = 0
    edited_at: int = 0

    @property
    def name(self) -> str:
        return Path(self.path).name or self.title

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DocumentRecord":
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except ValueError:
            meta = {}
        return cls(
            id=int(row["id"]),
            path=str(row["path"] or ""),
            title=str(row["title"] or ""),
            format=str(row["format"] or ""),
            category=str(row["category"] or ""),
            size_bytes=int(row["size_bytes"] or 0),
            blocks=int(row["blocks"] or 0),
            words=int(row["words"] or 0),
            pages=int(row["pages"] or 0),
            favorited=bool(row["favorited"]),
            tags=[item for item in str(row["tags"] or "").split(",") if item],
            meta=meta if isinstance(meta, dict) else {},
            added_at=int(row["added_at"] or 0),
            opened_at=int(row["opened_at"] or 0),
            edited_at=int(row["edited_at"] or 0),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "path": self.path, "title": self.title, "format": self.format,
            "category": self.category, "size_bytes": self.size_bytes, "blocks": self.blocks,
            "words": self.words, "pages": self.pages, "favorited": self.favorited,
            "tags": list(self.tags), "meta": dict(self.meta), "added_at": self.added_at,
            "opened_at": self.opened_at, "edited_at": self.edited_at,
        }


class DocumentStorage(SqliteStore):
    """文档库 / 版本快照 / 合并报告 / AI 与审计日志。"""

    settings_namespace = "document"

    #: 版本保留上限的读取器：板块层注入（读设置页的「版本保留」），
    #: 不注入时用常量 `MAX_VERSIONS_PER_DOCUMENT`。
    #: 设计成"每次裁剪都取一次"而不是构造时取一次，设置改完立即生效。
    version_keep_provider: Optional[Callable[[], int]] = None

    # ---------- 文档库 ----------

    def upsert_document(self, path: str, *, title: str = "", format_key: str = "",
                        category: str = "", size_bytes: int = 0, blocks: int = 0,
                        words: int = 0, pages: int = 0, meta: Optional[dict] = None) -> int:
        """按路径登记文档（已存在则更新统计并刷新 opened_at）。"""
        now = int(time.time())
        payload = json.dumps(meta or {}, ensure_ascii=False)
        existing = self.query_one("SELECT id FROM doc_documents WHERE path = ?", (str(path),))
        if existing:
            document_id = int(existing["id"])
            self.execute(
                """UPDATE doc_documents SET title=?, format=?, category=?, size_bytes=?,
                       blocks=?, words=?, pages=?, meta_json=?, opened_at=?
                   WHERE id=?""",
                (title, format_key, category, int(size_bytes), int(blocks), int(words),
                 int(pages), payload, now, document_id),
            )
            return document_id
        cursor = self.execute(
            """INSERT INTO doc_documents
                   (path, title, format, category, size_bytes, blocks, words, pages,
                    meta_json, added_at, opened_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (str(path), title, format_key, category, int(size_bytes), int(blocks),
             int(words), int(pages), payload, now, now),
        )
        return int(cursor.lastrowid or 0)

    def get_document(self, document_id: int) -> Optional[DocumentRecord]:
        row = self.query_one("SELECT * FROM doc_documents WHERE id = ?", (int(document_id),))
        return DocumentRecord.from_row(row) if row else None

    def get_document_by_path(self, path: str) -> Optional[DocumentRecord]:
        row = self.query_one("SELECT * FROM doc_documents WHERE path = ?", (str(path),))
        return DocumentRecord.from_row(row) if row else None

    def list_documents(self, *, limit: int = 500, keyword: str = "",
                       category: str = "", format_key: str = "",
                       favorited_only: bool = False) -> list[DocumentRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if keyword:
            clauses.append("(title LIKE ? OR path LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        if category:
            clauses.append("category = ?")
            params.append(category)
        if format_key:
            clauses.append("format = ?")
            params.append(format_key)
        if favorited_only:
            clauses.append("favorited = 1")
        sql = "SELECT * FROM doc_documents"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY opened_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        return [DocumentRecord.from_row(row) for row in self.query(sql, params)]

    def touch_document(self, document_id: int, *, opened: bool = False,
                       edited: bool = False) -> None:
        now = int(time.time())
        if opened:
            self.execute("UPDATE doc_documents SET opened_at=? WHERE id=?", (now, int(document_id)))
        if edited:
            self.execute("UPDATE doc_documents SET edited_at=? WHERE id=?", (now, int(document_id)))

    def toggle_favorite(self, document_id: int) -> bool:
        row = self.query_one("SELECT favorited FROM doc_documents WHERE id = ?",
                             (int(document_id),))
        if row is None:
            return False
        value = 0 if row["favorited"] else 1
        self.execute("UPDATE doc_documents SET favorited=? WHERE id=?", (value, int(document_id)))
        return bool(value)

    def delete_document(self, document_id: int) -> None:
        self.execute("DELETE FROM doc_versions WHERE document_id = ?", (int(document_id),))
        self.execute("DELETE FROM doc_documents WHERE id = ?", (int(document_id),))

    def stats(self) -> dict:
        row = self.query_one(
            "SELECT COUNT(*) AS total, COALESCE(SUM(words),0) AS words, "
            "COALESCE(SUM(size_bytes),0) AS size, COALESCE(SUM(favorited),0) AS favorites "
            "FROM doc_documents")
        versions = self.query_one("SELECT COUNT(*) AS total FROM doc_versions")
        return {
            "documents": int(row["total"] if row else 0),
            "words": int(row["words"] if row else 0),
            "size_bytes": int(row["size"] if row else 0),
            "favorites": int(row["favorites"] if row else 0),
            "versions": int(versions["total"] if versions else 0),
        }

    # ---------- 版本快照 ----------

    def save_version(self, ir: DocumentIR, *, document_id: int = 0, label: str = "",
                     kind: str = "manual", round_index: int = 0, note: str = "") -> int:
        """保存一份 IR 快照（返回版本 id）。"""
        payload = json.dumps(ir.to_dict(), ensure_ascii=False)
        cursor = self.execute(
            """INSERT INTO doc_versions
                   (document_id, path, label, kind, round_index, note, ir_json, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (int(document_id), ir.path, label or (ir.title or Path(ir.path).name),
             kind, int(round_index), note, payload, int(time.time())),
        )
        version_id = int(cursor.lastrowid or 0)
        if document_id:
            self.prune_versions(int(document_id))
        return version_id

    def list_versions(self, document_id: int = 0, *, path: str = "",
                      limit: int = 60) -> list[dict]:
        if document_id:
            rows = self.query(
                "SELECT id, document_id, path, label, kind, round_index, note, created_at, "
                "LENGTH(ir_json) AS size FROM doc_versions WHERE document_id = ? "
                "ORDER BY id DESC LIMIT ?", (int(document_id), int(limit)))
        elif path:
            rows = self.query(
                "SELECT id, document_id, path, label, kind, round_index, note, created_at, "
                "LENGTH(ir_json) AS size FROM doc_versions WHERE path = ? "
                "ORDER BY id DESC LIMIT ?", (str(path), int(limit)))
        else:
            rows = self.query(
                "SELECT id, document_id, path, label, kind, round_index, note, created_at, "
                "LENGTH(ir_json) AS size FROM doc_versions ORDER BY id DESC LIMIT ?",
                (int(limit),))
        return [dict(row) for row in rows]

    def get_version(self, version_id: int) -> Optional[dict]:
        row = self.query_one("SELECT * FROM doc_versions WHERE id = ?", (int(version_id),))
        return dict(row) if row else None

    def load_version_ir(self, version_id: int) -> Optional[DocumentIR]:
        record = self.get_version(version_id)
        if record is None:
            return None
        try:
            payload = json.loads(record.get("ir_json") or "{}")
        except ValueError:
            return None
        return DocumentIR.from_dict(payload)

    def version_keep(self) -> int:
        """当前生效的版本保留上限（设置里的「版本保留」，缺失/非法时用常量）。"""
        provider = self.version_keep_provider
        if provider is None:
            return MAX_VERSIONS_PER_DOCUMENT
        try:
            value = int(provider() or 0)
        except (TypeError, ValueError):
            return MAX_VERSIONS_PER_DOCUMENT
        return value if value > 0 else MAX_VERSIONS_PER_DOCUMENT

    def prune_versions(self, document_id: int, keep: int = 0) -> int:
        """只保留最近的 `keep` 个快照（`keep<=0` 时按 `version_keep()` 取设置值）。"""
        limit = int(keep) if int(keep) > 0 else self.version_keep()
        rows = self.query(
            "SELECT id FROM doc_versions WHERE document_id = ? ORDER BY id DESC", (int(document_id),))
        stale = [int(row["id"]) for row in rows[limit:]]
        for version_id in stale:
            self.execute("DELETE FROM doc_versions WHERE id = ?", (version_id,))
        return len(stale)

    # ---------- 合并报告 ----------

    def save_merge_report(self, report: MergeReport) -> int:
        cursor = self.execute(
            """INSERT INTO doc_merge_reports
                   (main_path, other_path, mode, output_path, summary, report_json,
                    ai_used, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (report.main_path, report.other_path, report.mode, report.output_path,
             report.summary, json.dumps(report.to_dict(), ensure_ascii=False),
             1 if report.ai_used else 0, int(report.created_at or time.time())),
        )
        return int(cursor.lastrowid or 0)

    def list_merge_reports(self, *, limit: int = 50) -> list[dict]:
        rows = self.query(
            "SELECT id, main_path, other_path, mode, output_path, summary, ai_used, created_at "
            "FROM doc_merge_reports ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in rows]

    def load_merge_report(self, report_id: int) -> Optional[MergeReport]:
        row = self.query_one("SELECT * FROM doc_merge_reports WHERE id = ?", (int(report_id),))
        if row is None:
            return None
        try:
            payload = json.loads(row["report_json"] or "{}")
        except ValueError:
            return None
        report = MergeReport.from_dict(payload)
        report.output_path = str(row["output_path"] or report.output_path)
        return report

    # ---------- AI 调用日志 ----------

    def record_ai_call(self, record: AiCallRecord, *, document_id: int = 0) -> int:
        cursor = self.execute(
            """INSERT INTO doc_ai_calls
                   (document_id, kind, role, model, prompt_preview, output_preview, tools,
                    ok, detail, duration_ms, est_cost, masked, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (int(document_id), record.kind, record.role, record.model,
             record.prompt_preview[:500], record.output_preview[:500],
             ",".join(record.tools), 1 if record.ok else 0, record.detail[:500],
             int(record.duration_ms), float(record.est_cost), int(record.masked),
             int(record.created_at or time.time())),
        )
        return int(cursor.lastrowid or 0)

    def list_ai_calls(self, *, limit: int = 100, document_id: int = 0) -> list[dict]:
        if document_id:
            rows = self.query(
                "SELECT * FROM doc_ai_calls WHERE document_id = ? ORDER BY id DESC LIMIT ?",
                (int(document_id), int(limit)))
        else:
            rows = self.query("SELECT * FROM doc_ai_calls ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in rows]

    def ai_usage(self) -> dict:
        row = self.query_one(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(est_cost),0) AS cost, "
            "COALESCE(SUM(duration_ms),0) AS duration, COALESCE(SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END),0) AS failed "
            "FROM doc_ai_calls")
        return {
            "calls": int(row["calls"] if row else 0),
            "cost": round(float(row["cost"] if row else 0.0), 6),
            "duration_ms": int(row["duration"] if row else 0),
            "failed": int(row["failed"] if row else 0),
        }

    def clear_ai_calls(self) -> None:
        self.execute("DELETE FROM doc_ai_calls")

    # ---------- 审计 ----------

    def add_audit(self, action: str, target: str, detail: str = "", actor: str = "local") -> int:
        cursor = self.execute(
            "INSERT INTO doc_audit (action, target, detail, actor, created_at) VALUES (?,?,?,?,?)",
            (action, target[:300], detail[:500], actor, int(time.time())),
        )
        return int(cursor.lastrowid or 0)

    def list_audit(self, *, limit: int = 200, action: str = "") -> list[dict]:
        if action:
            rows = self.query(
                "SELECT * FROM doc_audit WHERE action = ? ORDER BY id DESC LIMIT ?",
                (action, int(limit)))
        else:
            rows = self.query("SELECT * FROM doc_audit ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in rows]

    def clear_audit(self) -> None:
        self.execute("DELETE FROM doc_audit")


__all__ = ["MAX_VERSIONS_PER_DOCUMENT", "DocumentRecord", "DocumentStorage"]
