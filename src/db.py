"""SQLite schema + helpers. Rich objects are stored as JSON text columns."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Iterable

from src.config import path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    site TEXT NOT NULL,
    condition TEXT NOT NULL,          -- baseline | with_kb | with_hindsight_kb | transfer_baseline | transfer
    model TEXT NOT NULL,
    seed INTEGER NOT NULL,
    terminal_status TEXT NOT NULL,    -- done | max_steps | error | blocked_captcha | blocked_login | guard_stopped
    agent_claimed_success INTEGER NOT NULL,
    final_answer TEXT,
    final_url TEXT,
    num_steps INTEGER,
    wall_time_s REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    est_cost_usd REAL,
    kb_entry_ids_used TEXT,           -- JSON list
    trace_path TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS judgments (
    run_id TEXT PRIMARY KEY,
    success INTEGER NOT NULL,
    confidence TEXT,
    agent_claimed_success_but_failed INTEGER,
    reason TEXT,
    judge_model TEXT
);
CREATE TABLE IF NOT EXISTS failures (
    run_id TEXT PRIMARY KEY,
    failure_step_index INTEGER,
    failure_category TEXT,
    ui_pattern TEXT,
    cause TEXT,
    page_context TEXT,
    fixable TEXT,                     -- yes | no | handoff
    draft_note TEXT,
    fixable_rationale TEXT,
    cluster_a TEXT,
    cluster_b TEXT
);
CREATE TABLE IF NOT EXISTS clusters (
    cluster_id TEXT PRIMARY KEY,
    method TEXT NOT NULL,             -- A | B
    site TEXT NOT NULL,
    name TEXT,
    size INTEGER,
    n_tasks INTEGER,
    member_run_ids TEXT               -- JSON list
);
CREATE TABLE IF NOT EXISTS kb_entries (
    id TEXT PRIMARY KEY,
    entry TEXT NOT NULL,              -- JSON KBEntry
    source TEXT NOT NULL,             -- failure | hindsight
    frozen INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kb_rejections (
    cluster_key TEXT,
    attempt INTEGER,
    note TEXT,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP,
    purpose TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    est_cost_usd REAL,
    cached INTEGER
);
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(path("db"), check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
    return _conn


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    with _lock:
        return conn().execute(sql, tuple(params))


def query(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, tuple(params)).fetchall()]


def upsert(table: str, row: dict[str, Any]) -> None:
    row = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in row.items()}
    cols = ",".join(row)
    qs = ",".join("?" for _ in row)
    execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({qs})", row.values())


def run_exists(run_id: str) -> bool:
    return bool(query("SELECT 1 FROM runs WHERE run_id=?", [run_id]))


def total_spend_usd() -> float:
    a = query("SELECT COALESCE(SUM(est_cost_usd),0) s FROM runs")[0]["s"]
    b = query("SELECT COALESCE(SUM(est_cost_usd),0) s FROM llm_calls WHERE cached=0")[0]["s"]
    return float(a) + float(b)


def runs_with_judgments(condition: str | None = None) -> list[dict[str, Any]]:
    sql = """SELECT r.*, j.success, j.confidence, j.agent_claimed_success_but_failed, j.reason AS judge_reason
             FROM runs r LEFT JOIN judgments j USING(run_id)"""
    params: list[Any] = []
    if condition:
        sql += " WHERE r.condition=?"
        params.append(condition)
    rows = query(sql + " ORDER BY r.task_id, r.seed", params)
    for r in rows:
        r["kb_entry_ids_used"] = json.loads(r["kb_entry_ids_used"] or "[]")
    return rows
