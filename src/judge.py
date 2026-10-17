"""Success judge (§7). Sees the factual action log + screenshots, never the agent's reasoning."""

from __future__ import annotations

import json

from src import db
from src.config import model_cfg, task_map
from src.llm import complete_json, obj, pmap
from src.runner.base import RunResult

JUDGE_SYSTEM = "You are grading whether a web agent completed a task. Be strict and evidence-based."

JUDGE_PROMPT = """TASK: {instruction}
SUCCESS CRITERIA: {success_criteria}
GROUND TRUTH (may be null): {ground_truth}
FINAL URL: {final_url}
ACTIONS TAKEN (factual log, not the agent's claims):
{actions}
AGENT'S FINAL ANSWER: {final_answer}
TERMINAL STATUS: {terminal_status}
[Screenshots attached: last 3 steps, final state]

Grade using only evidence visible in the screenshots and action log. The agent's final answer counts only if the screenshots support it. If the task asked to stop before booking/purchasing, reaching the correct pre-confirmation page with correct details counts as success. BLOCKED_CAPTCHA or BLOCKED_LOGIN is a failure.

Return JSON:
{{
  "success": true | false,
  "confidence": "high" | "medium" | "low",
  "agent_claimed_success_but_failed": true | false,
  "reason": "<one or two sentences>"
}}"""

JUDGE_SCHEMA = obj(
    success={"type": "boolean"},
    confidence={"type": "string", "enum": ["high", "medium", "low"]},
    agent_claimed_success_but_failed={"type": "boolean"},
    reason={"type": "string"},
)


def action_log(res: RunResult, with_obs: bool = False) -> str:
    """Factual, numbered action list: action type + args + URL change + harness errors. No model thoughts."""
    lines = []
    for s in res.steps:
        args = json.dumps(s.action_args, ensure_ascii=False)[:300]
        line = f"{s.index + 1}. {s.action_type} {args}"
        if s.url_after and s.url_after != s.url_before:
            line += f"  -> {s.url_after}"
        if s.error:
            line += f"  [error: {s.error[:160]}]"
        lines.append(line)
    if res.guard_blocked_text:
        lines.append(f"{len(lines) + 1}. HARNESS GUARD blocked click on irreversible control '{res.guard_blocked_text}'")
    return "\n".join(lines) or "(no actions)"


def judge_run(run_id: str) -> dict:
    row = db.query("SELECT * FROM runs WHERE run_id=?", [run_id])[0]
    res = RunResult.load(row["trace_path"])
    task = task_map()[res.task_id]
    shots = [s.screenshot_path for s in res.steps[-3:] if s.screenshot_path]
    if res.final_screenshot_path:
        shots.append(res.final_screenshot_path)
    text = JUDGE_PROMPT.format(
        instruction=res.instruction or task.instruction,
        success_criteria=task.success_criteria,
        ground_truth=task.ground_truth,
        final_url=res.final_url,
        actions=action_log(res),
        final_answer=res.final_answer or "(empty)",
        terminal_status=res.terminal_status,
    )
    out = complete_json("analysis", "judge", JUDGE_SYSTEM, text, JUDGE_SCHEMA, images=shots)
    if res.terminal_status in ("blocked_captcha", "blocked_login"):
        out["success"] = False  # protocol rule, regardless of what the judge returned
    # false-success is defined against the harness-recorded claim, not the judge's guess about it
    out["agent_claimed_success_but_failed"] = bool(res.agent_claimed_success and not out["success"])
    db.upsert("judgments", {
        "run_id": run_id, "success": int(out["success"]), "confidence": out["confidence"],
        "agent_claimed_success_but_failed": int(out["agent_claimed_success_but_failed"]),
        "reason": out["reason"], "judge_model": model_cfg("analysis")["name"],
    })
    return out


def judge_pending(condition: str | None = None) -> int:
    sql = """SELECT r.run_id FROM runs r LEFT JOIN judgments j USING(run_id)
             WHERE j.run_id IS NULL AND r.terminal_status != 'error'"""
    params = []
    if condition:
        sql += " AND r.condition=?"
        params.append(condition)
    ids = [r["run_id"] for r in db.query(sql, params)]
    def one(rid: str) -> None:
        out = judge_run(rid)
        print(f"  judged {rid}: success={out['success']} ({out['confidence']})", flush=True)

    pmap(one, ids)
    return len(ids)
