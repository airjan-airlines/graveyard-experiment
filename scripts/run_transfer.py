"""M7 stretch: does a KB built from the agent model's failures help a different model?
Runs models.transfer_agent without notes (transfer_baseline) and with the frozen KB (transfer)."""

import argparse
import asyncio

import _common  # noqa: F401

from src.config import cfg, load_tasks
from src.judge import judge_pending
from src.kb import store
from src.kb.inject import retrieve
from src.runner.orchestrate import run_condition


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int)
    args = ap.parse_args()
    reps = args.reps or cfg()["reps_per_task"]
    tasks = load_tasks()
    kb = store.load_frozen()
    selected = {t.id: retrieve(t, kb) for t in tasks}
    asyncio.run(run_condition(tasks, "transfer_baseline", reps, role="transfer_agent"))
    asyncio.run(run_condition(tasks, "transfer", reps, role="transfer_agent", notes_for=lambda t: selected[t.id]))
    for c in ("transfer_baseline", "transfer"):
        print(f"Judged {judge_pending(c)} {c} runs.")


if __name__ == "__main__":
    main()
