"""M5a: write KB notes from BASELINE failures only (one per Method-B cluster of fixable failures, incl. singletons),
reject answer leaks, and freeze to data/kb_frozen.json. With --hindsight, also write hindsight notes from
(successful, failed) baseline pairs of the same task and freeze kb_frozen_hindsight.json (failure + hindsight)."""

import argparse
import json
from collections import defaultdict

import _common  # noqa: F401

from src import db
from src.config import path, task_map
from src.kb import store
from src.kb.writer import write_failure_note, write_hindsight_note
from src.llm import pmap
from src.runner.base import RunResult


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hindsight", action="store_true")
    ap.add_argument("--force", action="store_true", help="rebuild even though with_kb runs already exist")
    args = ap.parse_args()
    if db.query("SELECT 1 FROM runs WHERE condition IN ('with_kb','with_hindsight_kb') LIMIT 1") and not args.force:
        raise SystemExit("with_kb runs already exist for the frozen KB; refusing to rebuild (use --force).")

    tasks = task_map()
    fails = db.query("""SELECT f.*, r.task_id, r.site, r.trace_path, j.reason AS judge_reason
                        FROM failures f JOIN runs r USING(run_id) JOIN judgments j USING(run_id)
                        WHERE r.condition='baseline' AND f.fixable='yes' ORDER BY f.run_id""")
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in fails:
        groups[f["cluster_b"] or f"single|{f['run_id']}"].append(f)
    print(f"{len(fails)} fixable baseline failures in {len(groups)} clusters -> one note each")

    db.execute("DELETE FROM kb_entries")
    db.execute("DELETE FROM kb_rejections")
    items = sorted(groups.items())
    notes = pmap(lambda kv: write_failure_note(kv[1][0]["site"], kv[1], tasks, kv[0]), items)
    kept = []
    for (key, _), e in zip(items, notes):
        if e is None:
            print(f"  dropped {key}: every draft leaked an answer")
            continue
        kept.append(store.contribute(e))
        print(f"  {e.id} [{e.site}] {e.note[:100]}")
    store.freeze(kept)
    print(f"Froze {len(kept)} failure-derived entries to {path('kb_frozen')}")

    if args.hindsight:
        rows = db.query("""SELECT r.run_id, r.task_id, r.trace_path, j.success, j.reason FROM runs r
                           JOIN judgments j USING(run_id) WHERE r.condition='baseline' AND r.terminal_status!='error'
                           ORDER BY r.run_id""")
        by_task: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_task[r["task_id"]].append(r)
        pairs = []
        for tid, rs in sorted(by_task.items()):
            good = [r for r in rs if r["success"]]
            bad = [r for r in rs if not r["success"]]
            if good and bad:
                pairs.append((tasks[tid], good[0], bad[0]))
        hs = pmap(lambda p: write_hindsight_note(p[0], RunResult.load(p[1]["trace_path"]),
                                                 RunResult.load(p[2]["trace_path"]), p[2]["reason"]), pairs)
        h_kept = [store.contribute(e) for e in hs if e is not None]
        dest = path("kb_frozen").with_name("kb_frozen_hindsight.json")
        store.freeze(kept + h_kept, dest)
        print(f"Froze {len(kept) + len(h_kept)} entries (incl. {len(h_kept)} hindsight) to {dest}")


if __name__ == "__main__":
    main()
