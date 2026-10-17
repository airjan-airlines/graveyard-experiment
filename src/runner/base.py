"""AgentRunner interface + RunResult record shared by all runners."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from src.config import Task
from src.kb.schema import KBEntry

TERMINAL_STATUSES = ("done", "max_steps", "error", "blocked_captcha", "blocked_login", "guard_stopped")

SYSTEM_PROMPT = """You are a web agent completing a task for a user in a real browser.

Rules:
- Never complete a purchase, payment, booking, or any final submission. If the task involves booking or buying, stop at the page right before the final confirm/pay/book button and report what you see.
- Never log in or create an account. If a login is required to continue, stop and output BLOCKED_LOGIN.
- Never try to solve a CAPTCHA or bypass bot detection. If you encounter one, stop and output BLOCKED_CAPTCHA.
- Use dummy values if a form needs input to show results (ZIP {zip}, party size as specified).
- Before declaring the task done, verify on the current page that the result actually satisfies every part of the request.
- When finished, give a concise final answer containing exactly the information the task asks for."""


@dataclass
class Step:
    index: int
    action_type: str
    action_args: list[dict[str, Any]]
    url_before: str | None
    url_after: str | None
    observation_summary: str
    screenshot_path: str | None
    timestamp: float
    observation_path: str | None = None  # compact page text (~4k chars) for analysis LLMs
    error: str | None = None


@dataclass
class RunResult:
    run_id: str
    task_id: str
    condition: str
    model: str
    seed: int
    steps: list[Step] = field(default_factory=list)
    final_answer: str = ""
    agent_claimed_success: bool = False
    terminal_status: str = "error"
    num_steps: int = 0
    wall_time_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    est_cost_usd: float = 0.0
    final_url: str | None = None
    final_screenshot_path: str | None = None
    final_observation_path: str | None = None  # compact page text of the final state (for text-only judges)
    kb_entry_ids_used: list[str] = field(default_factory=list)
    error: str | None = None
    guard_blocked_text: str | None = None
    timed_out: bool = False
    instruction: str = ""
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, trace_dir: Path) -> Path:
        trace_dir.mkdir(parents=True, exist_ok=True)
        p = trace_dir / "trace.json"
        p.write_text(json.dumps(self.to_dict(), indent=1, default=str))
        return p

    @staticmethod
    def load(p: str | Path) -> "RunResult":
        d = json.loads(Path(p).read_text())
        d["steps"] = [Step(**s) for s in d.get("steps", [])]
        return RunResult(**d)


class AgentRunner(Protocol):
    async def run(self, task: Task, run_id: str, kb_notes: list[KBEntry] | None) -> RunResult: ...


def build_system_extension(kb_notes: list[KBEntry] | None, dummy_zip: str) -> str:
    from src.kb.inject import format_notes

    text = SYSTEM_PROMPT.format(zip=dummy_zip)
    if kb_notes:
        text = format_notes(kb_notes) + "\n\n" + text
    return text
