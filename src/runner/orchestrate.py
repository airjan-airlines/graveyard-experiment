"""Run many (task, rep) jobs for one condition: resumable, concurrency-limited, budget-guarded."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from typing import Callable

from src import db
from src.config import Task, cfg, model_cfg
from src.kb.schema import KBEntry
from src.llm import BudgetExceeded, check_budget
from src.runner.base import RunResult

log = logging.getLogger(__name__)


def make_run_id(condition: str, model: str, task_id: str, seed: int) -> str:
    return f"{condition}__{re.sub(r'[^A-Za-z0-9.-]+', '-', model)}__{task_id}__r{seed}"


def make_runner(role: str = "agent"):
    if cfg().get("runner") == "fake":
        from src.runner.fake_runner import FakeRunner

        return FakeRunner(role)
    from src.runner.browser_use_runner import BrowserUseRunner

    return BrowserUseRunner(role)


def save_run(res: RunResult, site: str, trace_path: str) -> None:
    db.upsert("runs", {
        "run_id": res.run_id, "task_id": res.task_id, "site": site, "condition": res.condition, "model": res.model,
        "seed": res.seed, "terminal_status": res.terminal_status,
        "agent_claimed_success": int(res.agent_claimed_success), "final_answer": res.final_answer,
        "final_url": res.final_url, "num_steps": res.num_steps, "wall_time_s": res.wall_time_s,
        "input_tokens": res.input_tokens, "output_tokens": res.output_tokens, "est_cost_usd": res.est_cost_usd,
        "kb_entry_ids_used": json.dumps(res.kb_entry_ids_used), "trace_path": trace_path, "error": res.error,
        "started_at": res.started_at, "finished_at": res.finished_at,
    })


async def run_condition(
    tasks: list[Task],
    condition: str,
    reps: int,
    role: str = "agent",
    notes_for: Callable[[Task], list[KBEntry]] | None = None,
    retry_errors: bool = False,
) -> list[str]:
    c = cfg()
    model = model_cfg(role)["name"]
    runner = make_runner(role)
    global_sem = asyncio.Semaphore(c["concurrency"]["global"])
    site_sems: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(c["concurrency"]["per_site"]))

    jobs = []
    # Rep-major order: every task gets rep 0 before any task gets rep 1, so a partial run is still balanced.
    for seed in range(reps):
        for t in tasks:
            rid = make_run_id(condition, model, t.id, seed)
            existing = db.query("SELECT terminal_status FROM runs WHERE run_id=?", [rid])
            if existing and not (retry_errors and existing[0]["terminal_status"] == "error"):
                continue
            jobs.append((t, seed, rid))
    total = len(jobs)
    print(f"[{condition}] {total} runs to do (model={model}); already done runs are skipped.")
    done_ids: list[str] = []
    abort = asyncio.Event()

    async def one(t: Task, seed: int, rid: str) -> None:
        async with global_sem, site_sems[t.site]:
            if abort.is_set():
                return
            try:
                check_budget()
            except BudgetExceeded as e:
                print(f"!! {e}")
                abort.set()
                return
            notes = notes_for(t) if notes_for else None
            res = await runner.run(t, rid, notes, condition=condition, seed=seed)
            from src.config import path

            save_run(res, t.site, str(path("traces") / rid / "trace.json"))
            done_ids.append(rid)
            print(f"  [{len(done_ids)}/{total}] {rid}: {res.terminal_status} steps={res.num_steps} "
                  f"${res.est_cost_usd:.3f} | total spend ${db.total_spend_usd():.2f}", flush=True)

    await asyncio.gather(*(one(*j) for j in jobs))
    return done_ids
