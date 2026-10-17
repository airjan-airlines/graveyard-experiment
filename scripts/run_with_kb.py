"""M5b: rerun every task with notes from the FROZEN KB injected (condition with_kb), then judge.
--hindsight uses the failure+hindsight KB (condition with_hindsight_kb)."""

import argparse
import asyncio

import _common  # noqa: F401

from src import db
from src.config import cfg, load_tasks, path
from src.judge import judge_pending
from src.kb import store
from src.kb.inject import retrieve
from src.runner.orchestrate import run_condition


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", action="append")
    ap.add_argument("--reps", type=int)
    ap.add_argument("--hindsight", action="store_true")
    ap.add_argument("--role", default="agent")
    ap.add_argument("--condition")
    args = ap.parse_args()
    if args.hindsight:
        kb, condition = store.load_frozen(path("kb_frozen").with_name("kb_frozen_hindsight.json")), "with_hindsight_kb"
    else:
        kb, condition = store.load_frozen(), "with_kb"
    condition = args.condition or condition
    tasks = load_tasks()
    if args.task:
        tasks = [t for t in tasks if t.id in set(args.task)]
    selected = {t.id: retrieve(t, kb) for t in tasks}  # retrieval is deterministic; compute once
    for t in tasks:
        print(f"  {t.id}: {len(selected[t.id])} notes {[e.id for e in selected[t.id]]}")
    asyncio.run(run_condition(tasks, condition, args.reps or cfg()["reps_per_task"], role=args.role,
                              notes_for=lambda t: selected[t.id]))
    print(f"Judged {judge_pending(condition)} runs.")
    store.record_usage(db.runs_with_judgments(condition))
    print(f"Total estimated spend so far: ${db.total_spend_usd():.2f}")


if __name__ == "__main__":
    main()
