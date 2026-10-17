"""Note writer (§9.2) + hindsight notes (§9.4), with an automated answer-leak check.

A note must teach HOW, never leak WHAT: it is rejected (and regenerated, up to MAX_ATTEMPTS) if it contains the
task's ground truth, any number/entity from a successful run's final answer for that task, or a concrete date
from the run."""

from __future__ import annotations

import json
import re

from src import db
from src.analyze.summarize import failure_screens
from src.config import Task, resolved_dates
from src.kb.schema import KBEntry
from src.llm import complete_json, obj
from src.runner.base import RunResult

MAX_ATTEMPTS = 3

SYSTEM = "You write short, reusable notes that help future web agents avoid known mistakes on a specific website."

NOTE_RULES = """Rules for the note:
- Write for another AI agent, not a human. Imperative, specific, at most 5 sentences.
- Teach HOW to operate this website (controls, entry points, step order, where information lives, what success looks like).
- Do NOT include the answer to any task (no prices, counts, times, names of results, form numbers that are the answer, etc.).
- Do NOT mention specific calendar dates.
- Make it general enough to help other tasks on this website that hit the same quirk.
- Include a success_signal (what the page shows when this part is done right) when the failure was a false success or a premature stop; otherwise it may still help.
- irreversible_steps: buttons on this site that commit a purchase/booking and must not be clicked (empty list if none observed).
- handoff_points: places where a human is required (CAPTCHA, login) and the agent should stop (empty list if none)."""

NOTE_SCHEMA = obj(
    task_pattern={"type": "string"},
    applies_when={"type": "string"},
    note={"type": "string"},
    success_signal={"type": "string"},
    irreversible_steps={"type": "array", "items": {"type": "string"}},
    handoff_points={"type": "array", "items": {"type": "string"}},
)

FAILURE_PROMPT = """WEBSITE: {site}
These failures were all judged to be the same (or a closely related) mistake on this website:

{failures}

Write ONE knowledge-base note that would have prevented these failures.
{rules}
{feedback}
Return JSON with keys: task_pattern (short generic description of the kind of task), applies_when (the moment in the flow when the note matters), note, success_signal, irreversible_steps, handoff_points."""

HINDSIGHT_PROMPT = """WEBSITE: {site}
TASK: {instruction}

Below are two attempts at the SAME task on this website. One succeeded and one failed.

SUCCESSFUL RUN ACTIONS:
{good}

FAILED RUN ACTIONS (judge's reason: {reason}):
{bad}

What did the successful run know or do that the failed run did not? Write ONE knowledge-base note capturing that.
{rules}
{feedback}
Return JSON with keys: task_pattern, applies_when, note, success_signal, irreversible_steps, handoff_points."""


# --------------------------------------------------------------------------- leak check

_NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?|\d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?", re.I)
_ENTITY_RE = re.compile(r"\b(?:[A-Z][a-z0-9'&-]+)(?:\s+[A-Z][a-z0-9'&-]+)*\b")
_MONTHS = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_DATE_RE = re.compile(rf"\b{_MONTHS}\s+\d{{1,2}}\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b", re.I)
_GENERIC = {"the", "a", "an", "report", "find", "use", "click", "select", "search", "do", "not", "on", "for", "and",
            "if", "then", "stop", "yes", "no", "none", "synthetic"}


def forbidden_tokens(tasks: list[Task], success_answers: list[str]) -> set[str]:
    """Numbers and named entities that would leak an answer. Tokens that already appear in the task instruction
    (e.g. 'SFO', 'Berkeley', '94704') are allowed: they are inputs, not answers."""
    toks: set[str] = set()
    instr = " ".join(t.instruction for t in tasks).lower()
    for t in tasks:
        if t.ground_truth:
            toks.add(t.ground_truth.strip().lower())
    for ans in success_answers:
        for m in _NUM_RE.findall(ans or ""):
            m = m.strip().lower().rstrip(".,")
            digits = re.sub(r"\D", "", m)
            if len(digits) >= 2 and m not in instr and digits not in re.sub(r"\D", " ", instr).split():
                toks.add(m)
        for m in _ENTITY_RE.findall(ans or ""):
            m = m.strip().lower()
            if m not in _GENERIC and len(m) > 2 and m not in instr:
                toks.add(m)
    return toks


def leak_reason(entry: dict, forbidden: set[str], run_dates: list[str]) -> str | None:
    text = " ".join([entry.get("note", ""), entry.get("success_signal", "") or "", entry.get("applies_when", ""),
                     entry.get("task_pattern", "")])
    low = text.lower()
    for tok in sorted(forbidden, key=len, reverse=True):
        if re.search(r"(?<![a-z0-9])" + re.escape(tok) + r"(?![a-z0-9])", low):
            return f"contains answer token '{tok}'"
    if _DATE_RE.search(text):
        return f"mentions a specific date '{_DATE_RE.search(text).group(0)}'"
    for d in run_dates:
        if d in text:
            return f"mentions run date '{d}'"
    if len(re.findall(r"[.!?](?:\s|$)", entry.get("note", "").strip() + " ")) > 6:
        return "note longer than ~5 sentences"
    return None


def _success_answers(task_ids: set[str]) -> list[str]:
    rows = db.query(
        f"""SELECT r.final_answer FROM runs r JOIN judgments j USING(run_id)
            WHERE j.success=1 AND r.task_id IN ({','.join('?' * len(task_ids))})""", list(task_ids))
    return [r["final_answer"] or "" for r in rows]


def _write_with_check(prompt_fmt: str, fields: dict, tasks: list[Task], images: list[str], cluster_key: str) -> dict | None:
    forbidden = forbidden_tokens(tasks, _success_answers({t.id for t in tasks}))
    run_dates = list(resolved_dates()["dates"].values())
    feedback = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        text = prompt_fmt.format(rules=NOTE_RULES, feedback=feedback, **fields)
        out = complete_json("analysis", "kb_write", SYSTEM, text, NOTE_SCHEMA, images=images)
        reason = leak_reason(out, forbidden, run_dates)
        if reason is None:
            return out
        db.upsert("kb_rejections", {"cluster_key": cluster_key, "attempt": attempt, "note": out.get("note"),
                                    "reason": reason})
        feedback = (f"\nYOUR PREVIOUS DRAFT WAS REJECTED: it {reason}. Rewrite it without any task answers, "
                    f"result-specific values, or dates.\n")
    return None


def write_failure_note(site: str, members: list[dict], tasks: dict[str, Task], cluster_key: str) -> KBEntry | None:
    """members: failure rows (run_id, task_id, failure_category, cause, draft_note, judge_reason, trace_path)."""
    parts = []
    images: list[str] = []
    for m in members[:6]:
        t = tasks[m["task_id"]]
        parts.append(f"- TASK: {t.instruction}\n  CATEGORY: {m['failure_category']}\n  CAUSE: {m['cause']}\n"
                     f"  DRAFT NOTE FROM ANALYST: {m.get('draft_note') or '(none)'}")
        if len(images) < 4:
            res = RunResult.load(m["trace_path"])
            images += failure_screens(res, n=2)[:2]
    member_tasks = [tasks[m["task_id"]] for m in members]
    out = _write_with_check(FAILURE_PROMPT, {"site": site, "failures": "\n".join(parts)}, member_tasks, images[:4],
                            cluster_key)
    if out is None:
        return None
    return KBEntry(id="", site=site, source="failure", source_run_ids=[m["run_id"] for m in members],
                   source_failure_categories=sorted({m["failure_category"] for m in members}), **out)


def write_hindsight_note(task: Task, good: RunResult, bad: RunResult, bad_reason: str) -> KBEntry | None:
    from src.judge import action_log

    out = _write_with_check(HINDSIGHT_PROMPT, {"site": task.site, "instruction": task.instruction,
                                               "good": action_log(good), "bad": action_log(bad),
                                               "reason": bad_reason}, [task], [], f"hindsight|{task.id}")
    if out is None:
        return None
    return KBEntry(id="", site=task.site, source="hindsight", source_run_ids=[good.run_id, bad.run_id],
                   source_failure_categories=[], **out)


def dumps(e: KBEntry) -> str:
    return json.dumps(e.to_dict(), indent=1)
