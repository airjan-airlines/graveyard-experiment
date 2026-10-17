"""Knowledge-fixable classifier (§8.2). Deliberately a separate call from the summarizer."""

from __future__ import annotations

from src.llm import complete_json, nullable, obj

SYSTEM = "You judge whether a web-agent failure could have been prevented by prior site-specific knowledge."

PROMPT = """Could a short, site-specific note written by a previous agent (at most ~5 sentences, about this website or this kind of task on this website) have realistically prevented this failure?

Examples of knowledge-fixable: a quirk of how a control works on this site; the right URL or entry point; what the success state looks like; where on the site the information lives; a required step order.
Examples of NOT knowledge-fixable: the model misread a number; general reasoning error; harness crash; the task is impossible; random site outage.
Human handoff (CAPTCHA/login) counts as knowledge-fixable ONLY in the sense that a note could tell the agent to stop early; mark it "handoff" instead.

FAILURE CATEGORY: {failure_category}
CAUSE: {cause}
SITE: {site}
TASK: {instruction}

Return JSON:
{{ "fixable": "yes" | "no" | "handoff", "draft_note": "<the note that would have prevented it, or null>", "rationale": "<one sentence>" }}"""

SCHEMA = obj(
    fixable={"type": "string", "enum": ["yes", "no", "handoff"]},
    draft_note=nullable({"type": "string"}),
    rationale={"type": "string"},
)


def classify_failure(summary: dict, site: str, instruction: str) -> dict:
    text = PROMPT.format(failure_category=summary["failure_category"], cause=summary["cause"], site=site,
                         instruction=instruction)
    return complete_json("analysis", "classify", SYSTEM, text, SCHEMA)
