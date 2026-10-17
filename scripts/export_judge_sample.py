"""Export a stratified random sample of judged baseline runs (10 success + 10 failure by default) for a human to
grade. Fill in human_success (true/false) and human_notes; make_report.py reads it back."""

import argparse
import csv
import random

import _common  # noqa: F401

from src import db
from src.config import ROOT, cfg, path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite an existing (possibly hand-filled) sample")
    args = ap.parse_args()
    dest = path("out") / "judge_sample.csv"
    if dest.exists() and not args.force:
        raise SystemExit(f"{dest} exists (may contain human labels). Use --force to overwrite.")
    rows = db.runs_with_judgments("baseline")
    rows = [r for r in rows if r["success"] is not None]
    half = cfg()["judge"]["sample_size"] // 2
    rng = random.Random(42)
    succ = [r for r in rows if r["success"]]
    fail = [r for r in rows if not r["success"]]
    sample = rng.sample(succ, min(half, len(succ))) + rng.sample(fail, min(half, len(fail)))
    rng.shuffle(sample)
    with dest.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "task", "final_answer", "final_screenshot_path", "judge_success", "judge_reason",
                    "human_success", "human_notes"])
        for r in sample:
            shot = path("traces") / r["run_id"] / "final.png"
            w.writerow([r["run_id"], r["task_id"], r["final_answer"],
                        str(shot.relative_to(ROOT)) if shot.is_relative_to(ROOT) else str(shot),
                        bool(r["success"]), r["judge_reason"], "", ""])
    print(f"Wrote {len(sample)} rows to {dest} ({len([s for s in sample if s['success']])} success / "
          f"{len([s for s in sample if not s['success']])} failure). Trace folder: data/traces/<run_id>/")


if __name__ == "__main__":
    main()
