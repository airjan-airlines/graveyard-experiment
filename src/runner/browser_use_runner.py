"""browser-use (0.13.x) implementation of AgentRunner.

Hooks used:
- GuardedTools.act: runs before every action -> irreversible-click guard + polite random delay.
- Agent.run(on_step_end=...): after every agent step -> screenshot (downscaled to width 800), compact page text,
  url_after, action record.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
import random
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image

from src.config import Task, cfg, est_cost, model_cfg, path
from src.kb.schema import KBEntry
from src.runner.base import RunResult, Step, build_system_extension
from src.runner.guards import IrreversibleActionGuard, element_labels

log = logging.getLogger(__name__)

OBS_CHARS = 4000
PROVIDER_ERROR_RE = re.compile(r"Error code: (?:413|429|5\d\d)|rate_limit_exceeded|Request too large|overloaded",
                               re.IGNORECASE)


def make_llm(role: str = "agent"):
    """browser-use chat model for the agent role."""
    mc = model_cfg(role)
    if mc["provider"] == "anthropic":
        from browser_use import ChatAnthropic

        # thinking=adaptive makes browser-use use tool_choice=auto; current Claude models reject forced tool_choice.
        return ChatAnthropic(
            model=mc["name"],
            thinking={"type": "adaptive"},
            output_config={"effort": mc["effort"]} if mc.get("effort") else None,
            max_tokens=16000,
        )
    if mc["provider"] == "groq":
        from browser_use import ChatGroq

        return ChatGroq(model=mc["name"], max_retries=10)
    if mc["provider"] == "openai":
        from browser_use import ChatOpenAI

        return ChatOpenAI(model=mc["name"])
    if mc["provider"] == "fake":
        from src.runner.scripted_llm import ScriptedLLM

        return ScriptedLLM()
    raise ValueError(mc["provider"])


def _save_png(data: bytes, dest: Path, width: int = 800) -> str:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if im.width > width:
        im = im.resize((width, int(im.height * width / im.width)))
    im.save(dest, format="PNG", optimize=True)
    return str(dest)


def _guarded_tools_cls():
    from browser_use import Tools
    from browser_use.agent.views import ActionResult

    class GuardedTools(Tools):
        """Tools subclass whose act() inspects each pending click before delegating to browser-use."""

        def __init__(self, guard: IrreversibleActionGuard, delay_range: tuple[float, float], **kw):
            super().__init__(**kw)
            self.guard = guard
            self.delay_range = delay_range
            self.tripped: str | None = None

        async def _click_labels(self, params: dict[str, Any], browser_session) -> list[str]:
            node = None
            if params.get("index") is not None:
                node = await browser_session.get_element_by_index(params["index"])
            elif params.get("coordinate_x") is not None:
                from browser_use.tools.service import _convert_llm_coordinates_to_viewport

                x, y = _convert_llm_coordinates_to_viewport(params["coordinate_x"], params["coordinate_y"], browser_session)
                node = await browser_session.get_dom_element_at_coordinates(x, y)
            return element_labels(node) if node is not None else []

        async def act(self, action, browser_session, *args, **kwargs):
            for name, params in action.model_dump(exclude_unset=True).items():
                if params is None:
                    continue
                if name == "click":
                    try:
                        labels = await self._click_labels(params, browser_session)
                    except Exception as e:  # never let guard lookup crash the run; fail closed is too disruptive
                        log.warning("guard lookup failed: %s", e)
                        labels = []
                    decision = self.guard.check_labels(labels)
                    if decision.blocked:
                        self.tripped = decision.label
                        msg = (f"GUARD_STOPPED: blocked click on irreversible control '{decision.label}' "
                               f"(matched '{decision.matched}'). The run ends here.")
                        log.info(msg)
                        return ActionResult(is_done=True, success=False, extracted_content=msg, long_term_memory=msg)
                if name != "done":
                    await asyncio.sleep(random.uniform(*self.delay_range))
            return await super().act(action, browser_session, *args, **kwargs)

    return GuardedTools


class BrowserUseRunner:
    def __init__(self, role: str = "agent", llm_factory=None):
        self.role = role
        self.model_name = model_cfg(role)["name"]
        self.llm_factory = llm_factory or (lambda: make_llm(role))

    async def run(self, task: Task, run_id: str, kb_notes: list[KBEntry] | None, condition: str = "baseline",
                  seed: int = 0) -> RunResult:
        from browser_use import Agent, BrowserProfile

        c = cfg()
        trace_dir = path("traces") / run_id
        if trace_dir.exists():
            shutil.rmtree(trace_dir)  # a partial trace from a crashed process; this run never reached the DB
        trace_dir.mkdir(parents=True)
        result = RunResult(run_id=run_id, task_id=task.id, condition=condition, model=self.model_name, seed=seed,
                           kb_entry_ids_used=[e.id for e in (kb_notes or [])], instruction=task.instruction,
                           started_at=dt.datetime.now(dt.timezone.utc).isoformat())
        t0 = time.time()
        guard = IrreversibleActionGuard(task.irreversible_stop_words)
        tools = _guarded_tools_cls()(guard, tuple(c.get("action_delay_s", [1.0, 3.0])))
        w, h = c["browser"]["viewport"]
        headless = c["browser"]["headless"] and task.site not in c["browser"].get("headed_sites", [])
        user_data_dir = tempfile.mkdtemp(prefix="bu_profile_")
        profile = BrowserProfile(
            headless=headless,
            viewport={"width": w, "height": h},
            window_size={"width": w, "height": h},
            user_data_dir=user_data_dir,  # fresh profile per run: no cookies/KB leakage between runs
            keep_alive=True,  # we close it ourselves after capturing the final state
        )
        llm = self.llm_factory()
        agent = Agent(
            task=f"{task.instruction}\n\nStart at: {task.start_url}",
            llm=llm,
            browser_profile=profile,
            tools=tools,
            extend_system_message=build_system_extension(kb_notes, c["dummy_values"]["zip"]),
            initial_actions=[{"navigate": {"url": task.start_url, "new_tab": False}}],
            use_judge=False,
            use_vision=bool(model_cfg(self.role).get("vision", True)),
            generate_gif=False,
            calculate_cost=False,
            max_failures=3,
            directly_open_url=False,
            enable_signal_handler=False,
        )
        # Coordinate clicks are guarded too (element looked up at the point), but index clicks give the guard
        # a reliable element; keep browser-use's per-model default.

        async def on_step_end(ag) -> None:
            try:
                await self._record_step(ag, result, trace_dir)
            except Exception as e:
                log.warning("step capture failed: %s", e)

        history = None
        try:
            history = await asyncio.wait_for(agent.run(max_steps=c["max_steps"], on_step_end=on_step_end),
                                             timeout=c["run_timeout_s"])
        except asyncio.TimeoutError:
            result.timed_out = True
            history = agent.history
        except Exception as e:
            log.exception("harness error in %s", run_id)
            result.error = f"{type(e).__name__}: {e}"
            history = agent.history
        finally:
            try:
                await self._final_capture(agent, result, trace_dir)
            except Exception as e:
                log.warning("final capture failed: %s", e)
            try:
                await agent.browser_session.kill()
            except Exception:
                pass
            shutil.rmtree(user_data_dir, ignore_errors=True)

        self._finalize(result, history, agent, tools)
        result.wall_time_s = round(time.time() - t0, 2)
        result.finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
        result.save(trace_dir)
        return result

    # ----------------------------------------------------------------------- capture

    async def _record_step(self, agent, result: RunResult, trace_dir: Path) -> None:
        bs = agent.browser_session
        if not agent.history.history:
            return
        item = agent.history.history[-1]
        i = len(result.steps)
        actions = []
        if item.model_output is not None:
            actions = [a.model_dump(exclude_unset=True, exclude_none=True) for a in item.model_output.action]
        names = [next(iter(a)) for a in actions if a]
        errors = [r.error for r in (item.result or []) if r.error]
        obs_bits = [(r.long_term_memory or r.extracted_content or "")[:300] for r in (item.result or [])]
        url_after = await bs.get_current_page_url()
        shot = None
        try:
            png = await bs.take_screenshot()
            shot = _save_png(png, trace_dir / f"step_{i}.png")
        except Exception as e:
            log.debug("screenshot failed: %s", e)
        obs_path = None
        try:
            page_text = await _page_text(bs)
            obs_path = trace_dir / f"obs_{i}.txt"
            obs_path.write_text(page_text)
        except Exception as e:
            log.debug("page text failed: %s", e)
        result.steps.append(Step(
            index=i,
            action_type="+".join(names) or "none",
            action_args=[a[next(iter(a))] if a else {} for a in actions],
            url_before=getattr(item.state, "url", None),
            url_after=url_after,
            observation_summary=" | ".join(b for b in obs_bits if b)[:800],
            screenshot_path=shot,
            timestamp=time.time(),
            observation_path=str(obs_path) if obs_path else None,
            error="; ".join(errors)[:500] or None,
        ))

    async def _final_capture(self, agent, result: RunResult, trace_dir: Path) -> None:
        bs = agent.browser_session
        if bs is None:
            return
        result.final_url = await bs.get_current_page_url()
        png = await bs.take_screenshot()
        result.final_screenshot_path = _save_png(png, trace_dir / "final.png")
        obs = trace_dir / "final_obs.txt"
        obs.write_text(await _page_text(bs))
        result.final_observation_path = str(obs)

    def _finalize(self, result: RunResult, history, agent, tools) -> None:
        final = ""
        is_done = False
        claimed = False
        if history is not None:
            try:
                final = history.final_result() or ""
                is_done = history.is_done()
                claimed = bool(history.is_successful())
            except Exception:
                pass
        result.final_answer = final
        result.num_steps = len(result.steps)
        if not result.final_url and result.steps:
            result.final_url = result.steps[-1].url_after
        try:
            usage = agent.token_cost_service.get_usage_tokens_for_model(self.model_name)
            result.input_tokens = int(usage.prompt_tokens)
            result.output_tokens = int(usage.completion_tokens)
        except Exception:
            try:
                u = history.usage
                result.input_tokens, result.output_tokens = u.total_prompt_tokens, u.total_completion_tokens
            except Exception:
                pass
        result.est_cost_usd = round(est_cost(self.model_name, result.input_tokens, result.output_tokens), 5)

        # Provider/API failures (request too large, rate limit, 5xx) are infrastructure faults, not agent mistakes:
        # record them as harness errors so they are excluded from success rates instead of judged as failures.
        provider_errs = [st.error for st in result.steps if st.error and PROVIDER_ERROR_RE.search(st.error)]
        if provider_errs and not result.error:
            result.error = f"provider_error ({len(provider_errs)} steps): {provider_errs[-1][:300]}"

        upper = final.upper()
        if result.error and result.error.startswith("provider_error"):
            result.terminal_status = "error"
        elif tools.tripped:
            result.terminal_status = "guard_stopped"
            result.guard_blocked_text = tools.tripped
            # Agent "claimed" only if it had already said done; the guard's own done is not a claim.
            claimed = False
        elif "BLOCKED_CAPTCHA" in upper:
            result.terminal_status = "blocked_captcha"
        elif "BLOCKED_LOGIN" in upper:
            result.terminal_status = "blocked_login"
        elif result.error and not is_done:
            result.terminal_status = "error"
        elif is_done:
            result.terminal_status = "done"
        else:
            result.terminal_status = "max_steps"
        if result.terminal_status.startswith("blocked"):
            claimed = False
        result.agent_claimed_success = bool(claimed and result.terminal_status == "done")


async def _page_text(bs) -> str:
    """Compact text of the current page for analysis LLMs: browser-use's interactive-element view
    (accessibility-ish), truncated."""
    state = await bs.get_browser_state_summary(include_screenshot=False, cached=False)
    text = state.dom_state.llm_representation() if state.dom_state else ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    head = f"URL: {state.url}\nTITLE: {state.title}\n"
    return (head + text)[:OBS_CHARS]
