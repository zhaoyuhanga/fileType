"""v2：墨软文档板块（第六大板块）新增表。

只新增表与索引，**不改动任何既有板块的表** —— 老用户升级时这一步是纯增量、
可重复执行（`CREATE TABLE IF NOT EXISTS`），不需要数据搬迁。

表说明见 `docs/DATABASE.md` 第 3.6 节：
- `doc_documents`：文档库（打开过的文件与体检统计）
- `doc_versions`：版本快照（IR 的 JSON，支持回滚）
- `doc_merge_reports`：两个文档合并的差异报告与摘要
- `doc_ai_calls`：AI 调用日志（模型/提示词/工具/耗时/成本）
- `doc_audit`：审计日志（AI 调用、工具调用、导出、合并等）
"""
from __future__ import annotations

import sqlite3

VERSION = 2
NOTE = "墨软文档板块（doc_documents / doc_versions / doc_merge_reports / doc_ai_calls / doc_audit）"

SCHEMA = """
-- ---------- doc：墨软文档 ----------
CREATE TABLE IF NOT EXISTS doc_documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT    UNIQUE NOT NULL,
    title       TEXT    NOT NULL DEFAULT '',
    format      TEXT    NOT NULL DEFAULT '',
    category    TEXT    NOT NULL DEFAULT '',
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    blocks      INTEGER NOT NULL DEFAULT 0,
    words       INTEGER NOT NULL DEFAULT 0,
    pages       INTEGER NOT NULL DEFAULT 0,
    favorited   INTEGER NOT NULL DEFAULT 0,
    tags        TEXT    NOT NULL DEFAULT '',
    meta_json   TEXT    NOT NULL DEFAULT '',
    added_at    INTEGER NOT NULL DEFAULT 0,
    opened_at   INTEGER NOT NULL DEFAULT 0,
    edited_at   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS doc_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL DEFAULT 0,
    path        TEXT    NOT NULL DEFAULT '',
    label       TEXT    NOT NULL DEFAULT '',
    kind        TEXT    NOT NULL DEFAULT 'manual',
    round_index INTEGER NOT NULL DEFAULT 0,
    note        TEXT    NOT NULL DEFAULT '',
    ir_json     TEXT    NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS doc_merge_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    main_path   TEXT    NOT NULL DEFAULT '',
    other_path  TEXT    NOT NULL DEFAULT '',
    mode        TEXT    NOT NULL DEFAULT '',
    output_path TEXT    NOT NULL DEFAULT '',
    summary     TEXT    NOT NULL DEFAULT '',
    report_json TEXT    NOT NULL DEFAULT '',
    ai_used     INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS doc_ai_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id    INTEGER NOT NULL DEFAULT 0,
    kind           TEXT    NOT NULL DEFAULT '',
    role           TEXT    NOT NULL DEFAULT '',
    model          TEXT    NOT NULL DEFAULT '',
    prompt_preview TEXT    NOT NULL DEFAULT '',
    output_preview TEXT    NOT NULL DEFAULT '',
    tools          TEXT    NOT NULL DEFAULT '',
    ok             INTEGER NOT NULL DEFAULT 1,
    detail         TEXT    NOT NULL DEFAULT '',
    duration_ms    INTEGER NOT NULL DEFAULT 0,
    est_cost       REAL    NOT NULL DEFAULT 0,
    masked         INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS doc_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    action     TEXT    NOT NULL DEFAULT '',
    target     TEXT    NOT NULL DEFAULT '',
    detail     TEXT    NOT NULL DEFAULT '',
    actor      TEXT    NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_doc_documents_opened ON doc_documents(opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_versions_doc ON doc_versions(document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_merge_time ON doc_merge_reports(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_ai_time ON doc_ai_calls(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_audit_time ON doc_audit(created_at DESC);
"""


def apply(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
