"""How KB notes enter the agent prompt (§9.3)."""

from __future__ import annotations

import numpy as np

from src.config import Task, cfg
from src.kb.schema import KBEntry

HEADER = "Notes from previous agents on this website (follow them unless the page clearly contradicts them):"


def format_notes(entries: list[KBEntry]) -> str:
    lines = [HEADER]
    for i, e in enumerate(entries, 1):
        line = f"{i}. [When: {e.applies_when}] {e.note}"
        if e.success_signal:
            line += f" (Success looks like: {e.success_signal})"
        lines.append(line)
    return "\n".join(lines)


def retrieve(task: Task, entries: list[KBEntry], top_k: int | None = None) -> list[KBEntry]:
    """Same-site entries, ranked by embedding similarity of task_pattern + applies_when to the instruction."""
    from src.llm import embed

    top_k = top_k or cfg()["kb"]["top_k"]
    pool = [e for e in entries if e.site == task.site]
    if len(pool) <= top_k:
        return pool
    vecs = embed([task.instruction] + [f"{e.task_pattern}. {e.applies_when}" for e in pool])
    sims = vecs[1:] @ vecs[0]
    order = np.argsort(-sims, kind="stable")[:top_k]
    return [pool[i] for i in order]
