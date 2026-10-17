"""SYNTHETIC runner for offline pipeline tests (config `runner: fake`). No browser, no network.
Outcomes are random but seeded; notes in the prompt raise the success probability so the KB-effect code path
has something to measure. Never use its numbers for anything but testing the pipeline."""

from __future__ import annotations

import datetime as dt
import random
import shutil
import zlib

from PIL import Image, ImageDraw

from src.config import Task, model_cfg, path
from src.kb.schema import KBEntry
from src.runner.base import RunResult, Step


class FakeRunner:
    def __init__(self, role: str = "agent"):
        self.model_name = model_cfg(role)["name"]

    async def run(self, task: Task, run_id: str, kb_notes: list[KBEntry] | None, condition: str = "baseline",
                  seed: int = 0) -> RunResult:
        rng = random.Random(run_id)
        trace_dir = path("traces") / run_id
        shutil.rmtree(trace_dir, ignore_errors=True)
        trace_dir.mkdir(parents=True)
        res = RunResult(run_id=run_id, task_id=task.id, condition=condition, model=self.model_name, seed=seed,
                        kb_entry_ids_used=[e.id for e in (kb_notes or [])], instruction=task.instruction,
                        started_at=dt.datetime.now(dt.timezone.utc).isoformat())
        p_success = 0.45 + (0.25 if kb_notes else 0.0) + (zlib.crc32(task.id.encode()) % 5) * 0.05
        good = rng.random() < p_success
        n = rng.randint(4, 10) if good else rng.randint(8, 30)
        url = task.start_url
        for i in range(n):
            shot = trace_dir / f"step_{i}.png"
            im = Image.new("RGB", (800, 500), (240, 240, 240))
            ImageDraw.Draw(im).text((20, 20), f"SYNTHETIC {task.id} step {i}", fill=(0, 0, 0))
            im.save(shot)
            obs = trace_dir / f"obs_{i}.txt"
            obs.write_text(f"URL: {url}\nSYNTHETIC page text for {task.id} step {i}")
            res.steps.append(Step(index=i, action_type=rng.choice(["click", "input", "scroll", "navigate"]),
                                  action_args=[{"index": rng.randint(1, 50)}], url_before=url, url_after=url,
                                  observation_summary="synthetic", screenshot_path=str(shot), timestamp=0.0,
                                  observation_path=str(obs)))
        res.num_steps = n
        res.final_url = url
        res.final_screenshot_path = res.steps[-1].screenshot_path
        roll = rng.random()
        if good:
            res.terminal_status, res.agent_claimed_success = "done", True
            res.final_answer = "SYNTHETIC answer"
        elif roll < 0.1:
            res.terminal_status, res.final_answer = "blocked_captcha", "BLOCKED_CAPTCHA"
        elif roll < 0.45:
            res.terminal_status, res.agent_claimed_success = "done", True
            res.final_answer = "SYNTHETIC wrong answer"
        elif roll < 0.5:
            res.terminal_status, res.error = "error", "SyntheticHarnessError"
        else:
            res.terminal_status = "max_steps"
        res.input_tokens = n * rng.randint(6000, 12000)
        res.output_tokens = n * rng.randint(150, 400)
        from src.config import est_cost

        # Price synthetic tokens as the configured real agent model would be, so cost charts are non-empty.
        res.est_cost_usd = round(est_cost("claude-sonnet-5-5", res.input_tokens, res.output_tokens), 5)
        res.wall_time_s = n * 6.0
        res.finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
        res.save(trace_dir)
        return res
