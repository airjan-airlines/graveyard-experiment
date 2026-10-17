"""M1/M3: run the baseline condition (default: all tasks x reps_per_task), then judge every run.

Resumable: runs already in the DB are skipped. Harness errors are NOT retried unless --retry-errors is given
(use that only for the M2 smoke test, never for the measured baseline).
"""

import argparse
import asyncio

import _common  # noqa: F401

from src import db
from src.config import cfg, load_tasks, path, resolved_dates
from src.judge import judge_pending
from src.runner.orchestrate import run_condition


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", action="append", help="task id (repeatable); default all")
    ap.add_argument("--reps", type=int, default=None)
    ap.add_argument("--condition", default="baseline", help="override only for smoke tests, e.g. 'smoke'")
    ap.add_argument("--retry-errors", action="store_true")
    ap.add_argument("--no-judge", action="store_true")
    args = ap.parse_args()

    if not db.query("SELECT 1 FROM runs LIMIT 1") and path("resolved_dates").exists():
        path("resolved_dates").unlink()  # nothing run yet: resolve relative dates fresh for today
    print("Resolved dates:", resolved_dates())
    tasks = load_tasks()
    if args.task:
        tasks = [t for t in tasks if t.id in set(args.task)]
        assert tasks, f"no tasks match {args.task}"
    reps = args.reps or cfg()["reps_per_task"]
    asyncio.run(run_condition(tasks, args.condition, reps, retry_errors=args.retry_errors))
    if not args.no_judge:
        print(f"Judged {judge_pending(args.condition)} runs.")
    print(f"Total estimated spend so far: ${db.total_spend_usd():.2f}")


if __name__ == "__main__":
    main()
