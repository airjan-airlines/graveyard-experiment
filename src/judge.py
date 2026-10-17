"""Success judge (§7). Sees the factual action log + screenshots, never the agent's reasoning."""

from __future__ import annotations

import json

from src import db
from src.config import model_cfg, task_map
from pathlib import Path

from src.llm import complete_json, obj, pmap, supports_vision
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


PAGE_TEXT_CHARS = 2500


def page_text_evidence(res: RunResult, n_last: int = 2) -> str:
    """Stand-in for screenshots when the analysis model is text-only: the compact page text (interactive-element
    listing) after the last steps and at the final state."""
    parts = []
    for s in res.steps[-n_last:]:
        if s.observation_path and Path(s.observation_path).exists():
            parts.append(f"--- after step {s.index + 1} ---\n{Path(s.observation_path).read_text()[:PAGE_TEXT_CHARS]}")
    if res.final_observation_path and Path(res.final_observation_path).exists():
        parts.append(f"--- FINAL STATE ---\n{Path(res.final_observation_path).read_text()[:PAGE_TEXT_CHARS]}")
    return "\n".join(parts) or "(no page text captured)"


def with_evidence(text: str, res: RunResult, role: str = "analysis") -> str:
    if supports_vision(role):
        return text
    return (text.replace("[Screenshots attached: last 3 steps, final state]",
                         "[Page text (visible interactive elements and text) attached below in place of screenshots]")
            .replace("[Screenshots attached for the steps around the failure]",
                     "[Page text attached below in place of screenshots]")
            .replace("evidence visible in the screenshots", "evidence in the page text")
            .replace("only if the screenshots support it", "only if the page text supports it")
            + "\n\nPAGE TEXT EVIDENCE:\n" + page_text_evidence(res))


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
    out = complete_json("analysis", "judge", JUDGE_SYSTEM, with_evidence(text, res), JUDGE_SCHEMA, images=shots)
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
