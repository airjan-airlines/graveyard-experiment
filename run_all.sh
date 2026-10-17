#!/usr/bin/env bash
# Full pipeline: baseline -> analyze -> build_kb -> run_with_kb -> report. Every stage is resumable/cached.
# Usage: ./run_all.sh            (real run; needs .env with API keys)
#        EXPERIMENT_CONFIG=config.fake.yaml ./run_all.sh   (offline synthetic pipeline test)
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-.venv/bin/python}
$PY scripts/run_baseline.py
$PY scripts/export_judge_sample.py || true   # keeps an existing hand-labelled sample
$PY scripts/analyze.py
$PY scripts/build_kb.py || true              # refuses to rebuild once with_kb runs exist (KB stays frozen)
$PY scripts/run_with_kb.py
$PY scripts/make_report.py
