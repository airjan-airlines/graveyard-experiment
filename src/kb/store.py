"""KB storage: the experiment's DB-backed store, the frozen snapshot used by with_kb runs, and the
product-facing lookup(site, task) / contribute(entry) interface."""

from __future__ import annotations

import json
from pathlib import Path

from src import db
from src.config import Task, path
from src.kb.schema import KBEntry


def next_id() -> str:
    n = db.query("SELECT COUNT(*) n FROM kb_entries")[0]["n"]
    return f"kb_{n + 1:06d}"


def contribute(entry: KBEntry) -> KBEntry:
    if not entry.id:
        entry.id = next_id()
    db.upsert("kb_entries", {"id": entry.id, "entry": json.dumps(entry.to_dict()), "source": entry.source})
    return entry


def all_entries(source: str | None = None) -> list[KBEntry]:
    sql, params = "SELECT entry FROM kb_entries", []
    if source:
        sql += " WHERE source=?"
        params.append(source)
    return [KBEntry.from_dict(json.loads(r["entry"])) for r in db.query(sql + " ORDER BY id", params)]


def freeze(entries: list[KBEntry], dest: Path | None = None) -> Path:
    dest = dest or path("kb_frozen")
    dest.write_text(json.dumps([e.to_dict() for e in entries], indent=1))
    for e in entries:
        db.execute("UPDATE kb_entries SET frozen=1 WHERE id=?", [e.id])
    return dest


def load_frozen(p: Path | None = None) -> list[KBEntry]:
    p = p or path("kb_frozen")
    if not p.exists():
        raise FileNotFoundError(f"{p} missing: run scripts/build_kb.py first")
    return [KBEntry.from_dict(d) for d in json.loads(p.read_text())]


def lookup(site: str, task: str | Task, entries: list[KBEntry] | None = None, top_k: int = 5) -> list[KBEntry]:
    """Product interface: notes for a site, most relevant to the task first."""
    from src.kb.inject import retrieve

    if isinstance(task, str):
        task = Task(id="adhoc", site=site, category="", start_url="", instruction=task, task_type="lookup",
                    success_criteria="")
    return retrieve(task, entries if entries is not None else all_entries(), top_k)


def record_usage(run_rows: list[dict]) -> None:
    """Update times_used / times_used_success on stored entries from judged with_kb runs."""
    counts: dict[str, list[int]] = {}
    for r in run_rows:
        for eid in r["kb_entry_ids_used"]:
            c = counts.setdefault(eid, [0, 0])
            c[0] += 1
            c[1] += int(bool(r.get("success")))
    for e in all_entries():
        if e.id in counts:
            e.times_used, e.times_used_success = counts[e.id]
            contribute(e)
