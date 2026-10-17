"""Failure summarizer (§8.1)."""

from __future__ import annotations

from pathlib import Path

from src import db
from src.config import task_map
from src.judge import action_log
from src.llm import complete_json, nullable, obj
from src.runner.base import RunResult

CATEGORIES = [
    "ui_interaction", "navigation", "wrong_entity_or_variant", "false_success", "premature_stop", "popup_or_banner",
    "human_handoff", "domain_knowledge", "reasoning", "long_horizon", "harness_or_tooling", "other",
]

SYSTEM = "You analyze failed web-agent runs. Be specific and evidence-based."

PROMPT = """A web agent failed a task. Identify the single most important cause of failure.

TASK: {instruction}
SITE: {site}
JUDGE'S REASON FOR FAILURE: {judge_reason}
ACTION LOG WITH PAGE OBSERVATIONS (truncated):
{steps_with_observations}
[Screenshots attached for the steps around the failure]

Return JSON:
{{
  "failure_step_index": <int or null>,
  "failure_category": one of [
    "ui_interaction"        // a control didn't behave as expected: date picker, autocomplete, custom dropdown, hover menu, iframe, modal
    "navigation"            // wrong entry point, wrong link, loop, couldn't find the right page
    "wrong_entity_or_variant" // right type of thing, wrong instance: wrong city, size, date, product, form version
    "false_success"         // declared done but the goal wasn't actually achieved
    "premature_stop"        // stopped before gathering all required info
    "popup_or_banner"       // blocked by cookie banner, modal, overlay
    "human_handoff"         // CAPTCHA, login wall, bot block
    "domain_knowledge"      // didn't know the process or where the information lives
    "reasoning"             // had the right info but drew the wrong conclusion
    "long_horizon"          // lost track of constraints or progress over many steps
    "harness_or_tooling"    // the agent framework itself misbehaved
    "other"
  ],
  "ui_pattern": "<short label if relevant, e.g. 'date_picker', 'autocomplete_requires_selection', 'div_dropdown', 'cookie_modal'; else null>",
  "cause": "<one sentence, specific to this site, e.g. 'Typed the date into the departure field but the site requires picking from the calendar widget, so the search used today\\'s date.'>",
  "page_context": "<URL path or page name where it happened>"
}}"""

SCHEMA = obj(
    failure_step_index=nullable({"type": "integer"}),
    failure_category={"type": "string", "enum": CATEGORIES},
    ui_pattern=nullable({"type": "string"}),
    cause={"type": "string"},
    page_context={"type": "string"},
)

OBS_PER_STEP = 600
MAX_LOG_CHARS = 14000


def steps_with_observations(res: RunResult) -> str:
    """Action log interleaved with truncated page text. Long runs keep the first 3 and last steps."""
    lines = action_log(res).split("\n")
    blocks = []
    for s, line in zip(res.steps, lines):
        obs = ""
        if s.observation_path and Path(s.observation_path).exists():
            obs = Path(s.observation_path).read_text()[:OBS_PER_STEP].replace("\n", " ")
        blocks.append(f"{line}\n   page: {obs}" if obs else line)
    blocks += lines[len(res.steps):]  # guard line, if any
    text = "\n".join(blocks)
    while len(text) > MAX_LOG_CHARS and len(blocks) > 6:
        blocks.pop(3)
        text = "\n".join(blocks[:3] + ["   ... (middle steps omitted) ..."] + blocks[3:])
    return text


def failure_screens(res: RunResult, around: int | None = None, n: int = 4) -> list[str]:
    """Screenshots around the failure step (or the last steps) instead of all 30."""
    if not res.steps:
        return [res.final_screenshot_path] if res.final_screenshot_path else []
    if around is None or not (0 <= around < len(res.steps)):
        around = len(res.steps) - 1
    lo = max(0, around - 1)
    shots = [s.screenshot_path for s in res.steps[lo:lo + n - 1] if s.screenshot_path]
    if res.final_screenshot_path and res.final_screenshot_path not in shots:
        shots.append(res.final_screenshot_path)
    return shots


def summarize_failure(run_id: str) -> dict:
    row = db.query("SELECT r.*, j.reason AS judge_reason FROM runs r JOIN judgments j USING(run_id) WHERE run_id=?",
                   [run_id])[0]
    res = RunResult.load(row["trace_path"])
    task = task_map()[res.task_id]
    text = PROMPT.format(instruction=res.instruction or task.instruction, site=task.site,
                         judge_reason=row["judge_reason"], steps_with_observations=steps_with_observations(res))
    return complete_json("analysis", "summarize", SYSTEM, text, SCHEMA, images=failure_screens(res))
