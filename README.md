# Agent KB experiment: how often do web agents repeat the same mistakes?

A fully automated pilot that:

1. runs a browser agent (browser-use) on 24 everyday, non-coding tasks on live sites, 3 times each;
2. judges every run with an LLM judge that sees the action log and screenshots, not the agent's reasoning;
3. analyzes each failure: category, cause, and whether a short site-specific note would have prevented it;
4. measures how often failures **repeat**, including across different tasks on the same site;
5. writes knowledge-base notes from the failures, freezes the KB, and reruns every task with the notes injected;
6. writes `out/report.md`, `out/slide_numbers.md`, `out/results.json` and `out/charts/*.png`. These cover success
   and failure rates with 95% CIs, false-success rate, repeat and fixable shares, and the KB effect on success,
   steps and tokens. They also cover **tokens wasted on failures and how many a KB can save**.

Design decisions and deviations from the brief are in [`DECISIONS.md`](DECISIONS.md).

## Setup

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
cp .env.example .env        # add ANTHROPIC_API_KEY (and OPENAI_API_KEY only if config uses openai)
```

You also need Google Chrome installed; browser-use drives it over CDP. Models, concurrency, step limits, budget and
paths are all in `config.yaml`.

## Run everything

```bash
./run_all.sh     # baseline -> judge -> judge sample -> analyze -> build KB -> with_kb -> report
```

Every stage is **resumable**: completed runs are skipped and every analysis LLM call is cached by input hash. If it
crashes at run 47, re-run the same command. The run stops before exceeding `budget.max_budget_usd`, and a running
spend estimate is printed after each run.

## Run stages individually

| Stage | Command | Output |
|---|---|---|
| M1 single task | `.venv/bin/python scripts/run_baseline.py --task irs_01 --reps 1` | `data/traces/<run_id>/` + DB row |
| M2 smoke test | `scripts/run_baseline.py --reps 1 --condition smoke --retry-errors` | one run per task (not counted as baseline) |
| M3 baseline | `scripts/run_baseline.py` | 72 runs, judged |
| Judge sample | `scripts/export_judge_sample.py` | `out/judge_sample.csv` |
| M4 analysis | `scripts/analyze.py` | `failures` + `clusters` tables, `out/cluster_samples.md` |
| M5 KB | `scripts/build_kb.py` | `data/kb_frozen.json` |
| M5 rerun | `scripts/run_with_kb.py` | 72 with_kb runs, judged |
| M6 report | `scripts/make_report.py` | `out/report.md`, `slide_numbers.md`, `results.json`, `charts/` |
| M7 hindsight | `scripts/build_kb.py --force --hindsight` then `scripts/run_with_kb.py --hindsight` | oracle fraction |
| M7 transfer | `scripts/run_transfer.py` | transfer model with and without the KB |

`make_report.py` makes no network calls. It reads only the DB and the cache, so you can regenerate the report at
any time.

## Validating the judge (please do this)

1. `scripts/export_judge_sample.py` writes 20 runs (10 judged success, 10 judged failure) to `out/judge_sample.csv`.
2. For each row, open `final_screenshot_path` (and `data/traces/<run_id>/step_*.png` if needed) and fill in
   `human_success` (`true`/`false`) plus optional `human_notes`.
3. Re-run `scripts/make_report.py`. The report then shows judge–human agreement and Cohen's κ instead of
   "judge not yet validated". The export refuses to overwrite a filled-in CSV unless you pass `--force`.

Also skim `out/cluster_samples.md` to check that clustered failures really are the same mistake.

## Offline test mode

```bash
.venv/bin/python -m pytest                              # unit tests + real-browser guard test (no API calls)
EXPERIMENT_CONFIG=config.fake.yaml ./run_all.sh          # whole pipeline on SYNTHETIC data -> data_fake/, out_fake/
```

The fake config uses a synthetic runner and schema-valid fake LLM outputs. It only checks that the plumbing works,
and its outputs are stamped SYNTHETIC.

## Layout

```
config.yaml / config.fake.yaml   experiment config (real / synthetic)
tasks/tasks.yaml                 24 tasks (relative dates resolved once per experiment)
src/runner/                      AgentRunner interface, browser-use runner, irreversible-action guard, orchestration
src/judge.py                     success judge
src/analyze/                     failure summarizer, knowledge-fixable classifier, repeat clustering (A + B)
src/kb/                          KB schema, note writer (+ answer-leak check), store (lookup/contribute), injection
src/metrics.py, src/report.py    metrics with Wilson CIs + paired bootstrap; report and charts
scripts/                         pipeline entry points
data/ (gitignored)               experiment.db, traces/, llm_cache/, kb_frozen.json, resolved_dates.json
out/                             report.md, slide_numbers.md, results.json, charts/
```

## Safety

The agent never buys, books, pays, logs in or solves CAPTCHAs. The system prompt forbids it. The harness also blocks
any click whose label matches a stop word (`Place order`, `Pay`, `Confirm reservation`, task-specific words, ...)
and ends the run as `guard_stopped`. There are no stealth plugins or proxies. Each run gets a fresh browser profile,
with at most 2 concurrent sessions per site and a 1–3 s delay between actions.
