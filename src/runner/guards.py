"""Irreversible-action guard (§6.4).

Every pending click is checked against the task's `irreversible_stop_words` plus a global list. If the target
element's visible text / accessible name matches, the click is blocked and the run ends with
terminal_status=guard_stopped. The hook into browser-use lives in browser_use_runner.GuardedTools.act.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

GLOBAL_STOP_WORDS = [
    "Place order",
    "Pay",
    "Purchase",
    "Complete booking",
    "Confirm reservation",
    "Submit payment",
    "Reserve now",
    "Complete purchase",
    "Complete reservation",
    "Pay now",
    "Buy now",
]

# Container elements (cards, rows) carry the text of everything inside them; only short labels are
# reliable signals of what a click does.
MAX_LABEL_CHARS = 60


@dataclass
class GuardDecision:
    blocked: bool
    matched: str | None = None
    label: str | None = None


def _compile(words: Iterable[str]) -> list[tuple[str, re.Pattern]]:
    out = []
    for w in words:
        w = w.strip()
        if w:
            # Whole-word match, case-insensitive, flexible whitespace: "Pay" matches "Pay $12" but not "PayPal"/"Payment".
            pat = r"(?<![A-Za-z0-9])" + r"\s+".join(map(re.escape, w.split())) + r"(?![A-Za-z0-9])"
            out.append((w, re.compile(pat, re.IGNORECASE)))
    return out


class IrreversibleActionGuard:
    def __init__(self, task_stop_words: Iterable[str] | None = None, use_global: bool = True):
        words = list(task_stop_words or []) + (GLOBAL_STOP_WORDS if use_global else [])
        self.patterns = _compile(words)

    def check_labels(self, labels: Iterable[str | None]) -> GuardDecision:
        for label in labels:
            if not label:
                continue
            label = " ".join(str(label).split())
            if not label or len(label) > MAX_LABEL_CHARS:
                continue
            for word, pat in self.patterns:
                if pat.search(label):
                    return GuardDecision(True, word, label)
        return GuardDecision(False)

    def check_element(self, el: Any) -> GuardDecision:
        """`el` is anything with the duck-typed fields of a browser-use EnhancedDOMTreeNode
        (attributes dict, ax_node.name, get_all_children_text()). Used directly by unit tests with a fake."""
        return self.check_labels(element_labels(el))


def element_labels(el: Any) -> list[str]:
    labels: list[str] = []
    attrs = getattr(el, "attributes", None) or {}
    for k in ("aria-label", "value", "title", "alt", "data-testid"):
        if attrs.get(k):
            labels.append(attrs[k])
    ax = getattr(el, "ax_node", None)
    if ax is not None and getattr(ax, "name", None):
        labels.append(ax.name)
    try:
        txt = el.get_all_children_text()
    except Exception:
        txt = None
    if txt:
        labels.append(txt)
    return labels
