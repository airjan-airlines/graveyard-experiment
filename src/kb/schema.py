"""KB entry schema (§9.1)."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class KBEntry:
    id: str
    site: str
    task_pattern: str
    applies_when: str
    note: str
    success_signal: str | None = None
    irreversible_steps: list[str] = field(default_factory=list)
    handoff_points: list[str] = field(default_factory=list)
    source_run_ids: list[str] = field(default_factory=list)
    source_failure_categories: list[str] = field(default_factory=list)
    source: str = "failure"  # failure | hindsight
    created_at: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())
    times_used: int = 0
    times_used_success: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "KBEntry":
        known = KBEntry.__dataclass_fields__.keys()
        return KBEntry(**{k: v for k, v in d.items() if k in known})
