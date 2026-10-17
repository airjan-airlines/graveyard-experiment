# Decisions log

Every judgment call and deviation from the build brief, newest sections last. Keep appending.

## Setup / stack

- **Repo layout.** The brief's tree is rooted at `agent-kb-experiment/`; this repo *is* that root, so files live at the
  top level.
- **Python 3.12 venv** (`.venv`, created with `uv`). Brief requires 3.11+.
- **browser-use 0.13.11**, pinned. It no longer uses Playwright: it drives Chrome over CDP directly and uses the
  installed Google Chrome. "Playwright Chromium" in the brief is therefore "Chrome via CDP". The viewport is still
  1280×800.
- **Models.** Agent = `claude-sonnet-5-5`; analysis/judge = `claude-opus-5-5` (the brief asks for "one agent model
  and one stronger analysis model"). Transfer (stretch) = `claude-haiku-5-5`. All are in `config.yaml` and nothing
  else in the code names a model.
- **Temperature 0 is not available** on the current Claude models (sampling params return 400). Determinism of the
  analysis comes from the **input-hash disk cache** instead (`data/llm_cache/`): re-running analysis replays identical
  outputs at zero cost. Effort is pinned (`medium`) in config.
- **Structured outputs** (`output_config.format` JSON schema) are used for every analysis call, so the JSON
  is always valid.
- **No server-side refusal fallback** on the judge/analysis model. Silently switching the grading model mid-experiment would
  confound the measurements. A refusal raises `LLMRefusal` and stops that call instead. Turn fallbacks on only
  if refusals actually show up.
- **browser-use + current Claude models.** By default browser-use's `ChatAnthropic` forces `tool_choice`, which the
  current models reject. Passing `thinking={"type": "adaptive"}` switches it to `tool_choice=auto`. Agent effort is
  `medium` (configurable).
- **Embeddings:** local `sentence-transformers/all-MiniLM-L6-v2` (downloaded on first use). A `tfidf` fallback
  exists for offline tests only; it is lexical, not semantic.
- **No API key** was available while building, so M1–M6 were built and verified offline (see "Verification"). The
  live 72+72 runs need `ANTHROPIC_API_KEY` in `.env`.

## Tasks

- **Sites follow the brief's explicit task list.** That list has IRS ×2 + CA DMV ×1 and NPS ×2 + Recreation.gov ×1, so it
  covers 10 domains in 8 categories ("3 per site across 8 sites" is read as 3 per category). Per-site tables
  report all 10 domains.
- **usps_01 address changed** from Berkeley City Hall (ZIP 94704) to San Francisco City Hall (ZIP 94102). 94704 is
  also the dummy ZIP in the agent's system prompt, so the agent could have "answered" from the prompt.
  Ground truth `94102`.
- **Ground truth** is filled only for irs_01 (`4506-T`) and usps_01 (`94102`). Everything else drifts (prices, fees,
  availability, ratings), so the judge relies on the trajectory and screenshots.
- **Named items** that may need fixing after the M2 smoke test: REI Co-op Rainier jacket (men's, M, Black), REI Co-op
  Flash 22 pack, Allrecipes "Best Chocolate Chip Cookies" / "World's Best Lasagna", OpenTable "Rivoli" (Berkeley),
  Upper Pines campground. Record any rewording below under "Smoke test".
- **Relative dates.** `next_<weekday>` is at least 7 days out (restaurants and campsites need inventory).
  `first_friday_next_month+2` gives the return date. Dates are resolved once and frozen in
  `data/resolved_dates.json`. `run_baseline.py` re-resolves them automatically only if the DB has no runs yet. After
  that, baseline and with_kb share the same concrete dates.
- **Stop words.** NPS tasks have no task-specific stop words, because "Buy"/"Pay" there are navigation links to
  recreation.gov, not commits. The global guard list still applies.

## Runner / harness

- **Guard hook.** `GuardedTools(Tools).act()` runs before every browser-use action. For `click` it resolves the
  target element: by index via `get_element_by_index`, by coordinates via `get_dom_element_at_coordinates`. It
  collects aria-label/value/title/alt/data-testid, the accessible name and the visible text, and matches stop words as
  whole words, case-insensitive ("Pay" blocks "Pay $12", not "PayPal"/"Payment"). Labels over 60 chars are ignored,
  because a card or row container carries all its children's text. A blocked click returns
  `ActionResult(is_done=True, success=False)`, which ends the run with `guard_stopped`. Tested with fake elements
  and in a real browser against a local "Place order" page (`tests/test_harness_guard.py`).
  - Known gap: a form submitted by pressing Enter (`send_keys`) is not inspected. The system prompt still forbids it.
- **Polite pacing.** A uniform 1–3 s delay before each non-`done` action, plus at most 2 concurrent sessions per site
  and 4 globally.
- **One `Step` per agent step, not per action.** browser-use can emit several actions per step. `action_type` is the
  `+`-joined action names and `action_args` is the list of their params. `num_steps` counts agent steps.
- **Screenshots** are captured in `on_step_end`, i.e. *after* the step's actions, downscaled to width 800 PNG.
  The page observation is browser-use's interactive-element text (accessibility-like), truncated to 4,000 chars, in
  `obs_{i}.txt`.
- **Fresh browser profile per run** (temp `user_data_dir`), so no cookies or state leak between runs or conditions.
- **Run timeout (6 min)** maps to `terminal_status=max_steps` with `timed_out=true`, because it is budget exhaustion, not
  a harness fault. Harness exceptions map to `error`. Errors are not retried in the measured conditions
  (`--retry-errors` exists for the smoke test only).
- **agent_claimed_success** is true only if the agent called `done(success=True)`, the final answer has no
  BLOCKED_* marker, and the run wasn't guard-stopped.
- **browser-use's own judge is disabled** (`use_judge=False`); we use our own.
- **Resumability.** `run_id = {condition}__{model}__{task}__r{rep}`. Any run already in the DB is skipped. Runs go in
  rep-major order, so a partial run stays balanced across tasks.
- **Costs** use list prices from `config.yaml` and count cached prompt tokens at the full input rate, which makes them
  an upper bound. The budget guard (`max_budget_usd`) covers agent runs plus uncached analysis calls and is
  checked before every run and every LLM call.

## Judge / analysis

- **Judge input** is the numbered action log (action names, args, URL changes, harness errors) plus the final answer,
  the last 3 step screenshots and the final screenshot. The agent's thoughts, memory and eval fields are never shown.
- **`agent_claimed_success_but_failed`** is recomputed from the harness-recorded claim and the judge's verdict rather
  than taken from the judge's guess. BLOCKED_* runs are forced to failure, per protocol.
- **The summarizer gets the screenshots around the last steps** (not the failure step, which it hasn't identified yet)
  and an action log interleaved with up to 600 chars of page text per step. Long runs keep the first 3 and the
  latest steps.
- **Method A key:** `(site, category, ui_pattern)` when ui_pattern is non-null, else
  `(site, category, normalized page path)`.
- **Method B:** per-site average-linkage agglomerative clustering on cosine distance, threshold 0.25
  (configurable). Clusters of size ≥2 are named by the analysis model (cached).
- **Headline repeat rate** uses Method B; the report always shows Method A next to it.

## KB

- **One note per Method-B cluster of `fixable == yes` baseline failures**, singletons included. Handoff failures are not
  turned into notes (fixable == handoff).
- **Leak check.** A note is rejected if it contains:
  - the task's ground truth;
  - any multi-digit number, price or time, or any capitalized entity, from a *successful* run's final answer for any
    member task, unless that token already appears in the task instruction (inputs such as SFO or 94704 are not
    answers);
  - a calendar date;
  - more than ~6 sentences.

  Rejected drafts are regenerated up to 3 times with feedback. If all 3 fail, the note is dropped. Rejections are
  logged in `kb_rejections` and counted in the report.
- **Frozen KB.** `build_kb.py` refuses to rebuild once with_kb runs exist (unless `--force`). with_kb reads only
  `data/kb_frozen.json`.
- **Retrieval** is computed once per task before the with_kb runs: same-site entries, ranked by MiniLM similarity of
  `task_pattern + applies_when` to the instruction, top 5.
- **Hindsight notes (M7.1)** are implemented: `build_kb.py --hindsight` writes `kb_frozen_hindsight.json`, and
  `run_with_kb.py --hindsight` runs the `with_hindsight_kb` condition. Each pair is the first successful + first failed
  baseline run of the same task.
- **Transfer (M7.2)** is implemented as `run_transfer.py` (conditions `transfer_baseline` and `transfer`).
- **MCP server and web UI (M7.3/4)** are not built. `kb/store.py` exposes `lookup(site, task)` / `contribute(entry)`
  for that later.

## Metrics additions (requested mid-build)

- **Token economics.** On top of the brief's "Waste" row, the report and `results.json` include:
  - wasted tokens/cost/steps split by fixable / handoff / not-fixable;
  - **preventable tokens** (spent on knowledge-fixable failures);
  - **projected savings**: each fixable failure's tokens minus the mean tokens of a successful run of the same task;
  - **measured savings**: total tokens and **tokens per successful task** (total spend / successes), baseline vs
    with_kb, with a paired bootstrap CI on mean tokens per run.

  Definitions are in the report appendix.
- **pass^k** is computed over a task's non-error runs. Tasks with zero non-error runs are excluded.

## Verification (no API key available at build time)

- `pytest`: 15 tests. They cover the guard (fake elements), the guard and trace capture through the **real browser-use
  harness and headless Chrome** (a scripted offline LLM against a local page), the leak check, Wilson/bootstrap,
  cluster keys and date resolution.
- **Full synthetic pipeline:** `EXPERIMENT_CONFIG=config.fake.yaml ./run_all.sh` runs every stage end to end (fake
  runner + fake schema-valid LLM outputs) into `data_fake/` and `out_fake/`. Its report and slide numbers carry a
  SYNTHETIC banner and mean nothing.
- `browser-use` blocks `file://` URLs, so the harness tests serve fixtures over a local HTTP server.

## Smoke test (M2) — fill in after running with real keys

- Site swaps: _none yet_ (count = 0)
- Task rewordings: _none yet_
- Headed-only sites (`browser.headed_sites`): _none yet_
