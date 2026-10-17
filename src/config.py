"""Config + task loading. Everything path-like is resolved relative to the repo root."""

from __future__ import annotations

import calendar
import datetime as dt
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

_CONFIG: dict[str, Any] | None = None


def config_path() -> Path:
    return Path(os.environ.get("EXPERIMENT_CONFIG", ROOT / "config.yaml"))


def cfg() -> dict[str, Any]:
    global _CONFIG
    if _CONFIG is None:
        with open(config_path()) as f:
            _CONFIG = yaml.safe_load(f)
    return _CONFIG


def path(key: str) -> Path:
    p = Path(cfg()["paths"][key])
    p = p if p.is_absolute() else ROOT / p
    if key in ("traces", "cache", "out"):
        p.mkdir(parents=True, exist_ok=True)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def model_cfg(role: str) -> dict[str, Any]:
    return cfg()["models"][role]


def price(model: str) -> dict[str, float]:
    table = cfg().get("pricing", {})
    if model in table:
        return table[model]
    for k, v in table.items():
        if model.startswith(k):
            return v
    return {"input": 0.0, "output": 0.0}


def est_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = price(model)
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000


# --------------------------------------------------------------------------- tasks


@dataclass
class Task:
    id: str
    site: str
    category: str
    start_url: str
    instruction: str
    task_type: str
    success_criteria: str
    ground_truth: str | None = None
    hard_patterns: list[str] = field(default_factory=list)
    irreversible_stop_words: list[str] = field(default_factory=list)
    replaced_from: str | None = None  # set if this task replaced a hard-blocking site

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


# Relative-date resolvers. Each returns a date given "today".
def _next_weekday(today: dt.date, weekday: int, min_days: int = 1) -> dt.date:
    d = today + dt.timedelta(days=min_days)
    while d.weekday() != weekday:
        d += dt.timedelta(days=1)
    return d


def _first_weekday_next_month(today: dt.date, weekday: int) -> dt.date:
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    d = dt.date(y, m, 1)
    while d.weekday() != weekday:
        d += dt.timedelta(days=1)
    return d


_WEEKDAYS = {name.lower(): i for i, name in enumerate(calendar.day_name)}

DATE_RESOLVERS = {
    "first_friday_next_month": lambda t: _first_weekday_next_month(t, 4),
    "first_saturday_next_month": lambda t: _first_weekday_next_month(t, 5),
    "first_tuesday_next_month": lambda t: _first_weekday_next_month(t, 1),
    # "next_<weekday>" is at least 7 days out so restaurants/campsites have inventory and results are stable.
    **{f"next_{name}": (lambda t, i=i: _next_weekday(t, i, min_days=7)) for name, i in _WEEKDAYS.items()},
}

_DATE_RE = re.compile(r"\{date:([a-z_]+)(?:\+(\d+))?\}")


def fmt_date(d: dt.date) -> str:
    return f"{calendar.day_name[d.weekday()]}, {calendar.month_name[d.month]} {d.day}, {d.year}"


def resolved_dates(today: dt.date | None = None) -> dict[str, Any]:
    """Resolve relative dates ONCE per experiment and freeze them on disk, so baseline and
    with_kb runs see identical concrete dates. Delete data/resolved_dates.json to re-resolve."""
    p = path("resolved_dates")
    if p.exists():
        return json.loads(p.read_text())
    today = today or dt.date.today()
    out = {"resolved_on": today.isoformat(), "dates": {k: f(today).isoformat() for k, f in DATE_RESOLVERS.items()}}
    p.write_text(json.dumps(out, indent=2))
    return out


def resolve_instruction(text: str) -> str:
    dates = resolved_dates()["dates"]

    def sub(m: re.Match) -> str:
        key, plus = m.group(1), int(m.group(2) or 0)
        if key not in dates:
            raise KeyError(f"Unknown date token {m.group(0)}")
        return fmt_date(dt.date.fromisoformat(dates[key]) + dt.timedelta(days=plus))

    return _DATE_RE.sub(sub, text)


def load_tasks(resolve: bool = True) -> list[Task]:
    with open(ROOT / "tasks" / "tasks.yaml") as f:
        raw = yaml.safe_load(f)
    tasks = []
    for r in raw:
        t = Task(**r)
        t.instruction = " ".join(t.instruction.split())
        t.success_criteria = " ".join(t.success_criteria.split())
        if resolve:
            t.instruction = resolve_instruction(t.instruction)
            t.success_criteria = resolve_instruction(t.success_criteria)
        tasks.append(t)
    ids = [t.id for t in tasks]
    assert len(ids) == len(set(ids)), "duplicate task ids"
    return tasks


def task_map() -> dict[str, Task]:
    return {t.id: t for t in load_tasks()}
