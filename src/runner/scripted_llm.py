"""A scripted, offline stand-in for the agent LLM (provider: fake). It drives the REAL browser harness
without any API calls so trace capture and the irreversible-action guard can be tested end to end.

Script items are browser-use action dicts, plus one pseudo-action:
  {"click_text": "Place order"}  -> click the element whose label in the DOM listing matches that text.
"""

from __future__ import annotations

import re
from typing import Any

from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage

DEFAULT_SCRIPT: list[dict[str, Any]] = [
    {"scroll": {"down": True, "pages": 1.0}},
    {"done": {"text": "SYNTHETIC: scripted agent finished without doing the task.", "success": True}},
]


class ScriptedLLM:
    model = "fake"
    _verified_api_keys = True

    def __init__(self, script: list[dict[str, Any]] | None = None):
        self.script = list(script or DEFAULT_SCRIPT)
        self.calls = 0

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake"

    async def ainvoke(self, messages, output_format=None, **kwargs):
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        page = "\n".join(getattr(m, "text", "") for m in messages)
        if "click_text" in step:
            idx = _find_index(page, step["click_text"])
            step = {"click": {"index": idx}} if idx else {"scroll": {"down": True, "pages": 0.5}}
        payload = {
            "thinking": None,
            "evaluation_previous_goal": "scripted",
            "memory": "scripted",
            "next_goal": "scripted",
            "action": [step],
        }
        completion = output_format.model_validate(payload) if output_format else str(payload)
        usage = ChatInvokeUsage(prompt_tokens=len(page) // 4, prompt_cached_tokens=0, prompt_cache_creation_tokens=0,
                                prompt_image_tokens=0, completion_tokens=20, total_tokens=len(page) // 4 + 20)
        return ChatInvokeCompletion(completion=completion, usage=usage)


def _find_index(page: str, text: str) -> int | None:
    # browser-use renders interactive elements as "[12]<button ... />\n\tPlace order"
    m = re.findall(r"\[(\d+)\]<[^>]*>\s*\n\s*" + re.escape(text), page)
    return int(m[-1]) if m else None
