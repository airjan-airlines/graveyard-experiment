"""All metric computations (§10) + token economics. Pure functions over DB rows; no network."""

from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter, defaultdict
from typing import Any

from src import db
from src.config import path

BOOTSTRAP_N = 10_000

DEFINITIONS = {
    "R": "Runs in a condition excluding terminal_status == error (harness errors are reported separately).",
    "baseline_failure_rate": "failed(R) / |R| for the baseline condition; 95% Wilson interval.",
    "pass@1": "Mean judged success over runs in R (overall and per site).",
    "pass^k": "Fraction of tasks whose every non-error run succeeded (k = reps per task, 3 by default); "
              "tasks with zero non-error runs are excluded.",
    "task_flakiness": "Fraction of tasks with both successes and failures across their non-error runs.",
    "false_success_rate": "count(agent_claimed_success and judge failed) / count(agent_claimed_success). "
                          "agent_claimed_success = the agent called done(success=True) without BLOCKED_* "
                          "and the run ended with status done.",
    "failure_category_breakdown": "Distribution of the summarizer's failure_category over failed runs in R.",
    "repeat_failure_rate": "Failures in clusters of size >= 2 / all failures (Method A: structured key "
                           "(site, category, ui_pattern|page_context); Method B: per-site agglomerative "
                           "clustering of cause embeddings).",
    "cross_task_repeat_rate": "Failures in clusters that contain failures from >= 2 different tasks on the same "
                              "site / all failures.",
    "knowledge_fixable_share": "fixable == yes / all failures; handoff share reported separately.",
    "waste": "Total steps, tokens (input + output) and est. cost spent on failed runs, and their share of all "
             "baseline spend.",
    "preventable_tokens": "Tokens spent on failed runs whose failure was classified knowledge-fixable (yes).",
    "projected_savings": "For each knowledge-fixable failed run: its tokens minus the expected tokens of a "
                         "successful run of the same task (mean over that task's successful baseline runs, or "
                         "over all successful baseline runs if the task never succeeded), floored at 0. "
                         "An estimate of what the KB could save if it converted those failures into successes.",
    "tokens_per_success": "Total tokens spent in a condition / number of successful runs (cost of one completed "
                          "task, including the spend on failures it took to get there).",
    "kb_token_savings": "Total tokens (and tokens per success) with_kb vs baseline on the same tasks and reps; "
                        "95% CI from a paired bootstrap over tasks.",
    "kb_effect": "Success rate baseline vs with_kb (same tasks, model, reps); difference with a paired "
                 "bootstrap over tasks (10,000 resamples). Mean steps on successful runs in each condition.",
    "per_category_effect": "For each failure category c: tasks with >= 1 baseline failure of category c; their "
                           "baseline success rate vs with_kb success rate.",
    "recovered_failures": "Of tasks that failed >= 1 time in baseline, the fraction whose with_kb success rate "
                          "is strictly higher.",
    "regressions": "Tasks whose with_kb success rate is strictly lower than baseline (always listed).",
    "transfer": "KB built from the agent model's baseline, applied to the transfer model: transfer_baseline vs "
                "transfer success rates.",
    "oracle_fraction": "(with_kb - baseline) / (with_hindsight_kb - baseline) success rates.",
    "judge_agreement": "Share of the human-labelled judge sample where human_success equals judge_success; "
                       "Cohen's kappa.",
}


# --------------------------------------------------------------------------- stats helpers


def wilson(k: int, n: int, z: float = 1.96) -> dict[str, float | None]:
    if n == 0:
        return {"p": None, "lo": None, "hi": None, "k": k, "n": n}
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"p": p, "lo": max(0.0, c - h), "hi": min(1.0, c + h), "k": k, "n": n}


def paired_bootstrap(per_task_a: dict[str, list[float]], per_task_b: dict[str, list[float]], stat: str = "mean",
                     n: int = BOOTSTRAP_N, seed: int = 0) -> dict[str, float | None]:
    """Resample tasks with replacement; statistic = pooled mean (or sum) of B minus that of A over resampled tasks."""
    tasks = sorted(set(per_task_a) & set(per_task_b))
    tasks = [t for t in tasks if per_task_a[t] and per_task_b[t]]
    if not tasks:
        return {"diff": None, "lo": None, "hi": None, "n_tasks": 0}

    def agg(sample, d):
        vals = [v for t in sample for v in d[t]]
        return sum(vals) / len(vals) if stat == "mean" else sum(vals)

    point = agg(tasks, per_task_b) - agg(tasks, per_task_a)
    rng = random.Random(seed)
    diffs = []
    for _ in range(n):
        s = [rng.choice(tasks) for _ in tasks]
        diffs.append(agg(s, per_task_b) - agg(s, per_task_a))
    diffs.sort()
    return {"diff": point, "lo": diffs[int(0.025 * n)], "hi": diffs[int(0.975 * n) - 1], "n_tasks": len(tasks)}


def _tok(r: dict) -> int:
    return int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


# --------------------------------------------------------------------------- per-condition


def condition_rows(condition: str) -> tuple[list[dict], list[dict]]:
    rows = db.runs_with_judgments(condition)
    errors = [r for r in rows if r["terminal_status"] == "error"]
    judged = [r for r in rows if r["terminal_status"] != "error" and r["success"] is not None]
    return judged, errors


def success_summary(rows: list[dict]) -> dict[str, Any]:
    by_task: dict[str, list[int]] = defaultdict(list)
    by_site: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        by_task[r["task_id"]].append(int(r["success"]))
        by_site[r["site"]].append(int(r["success"]))
    k = sum(int(r["success"]) for r in rows)
    claimed = [r for r in rows if r["agent_claimed_success"]]
    fs = sum(1 for r in claimed if not r["success"])
    tasks_pass_all = [t for t, v in by_task.items() if v and all(v)]
    flaky = [t for t, v in by_task.items() if 0 < sum(v) < len(v)]
    per_site = {}
    for site, v in sorted(by_site.items()):
        site_tasks = {r["task_id"] for r in rows if r["site"] == site}
        per_site[site] = {
            "runs": len(v), "pass@1": wilson(sum(v), len(v)),
            "pass^k": sum(1 for t in site_tasks if all(by_task[t])) / len(site_tasks),
            "tasks": len(site_tasks),
        }
    status = Counter(r["terminal_status"] for r in rows)
    return {
        "runs": len(rows),
        "success": wilson(k, len(rows)),
        "failure": wilson(len(rows) - k, len(rows)),
        "pass@1": wilson(k, len(rows)),
        "pass^k": wilson(len(tasks_pass_all), len(by_task)),
        "flakiness": wilson(len(flaky), len(by_task)),
        "false_success": wilson(fs, len(claimed)),
        "terminal_status": dict(status),
        "per_site": per_site,
        "per_task": {t: {"success": sum(v), "n": len(v)} for t, v in sorted(by_task.items())},
        "steps_success_mean": _mean([r["num_steps"] for r in rows if r["success"]]),
        "steps_all_mean": _mean([r["num_steps"] for r in rows]),
        "tokens_total": sum(_tok(r) for r in rows),
        "cost_total": sum(r["est_cost_usd"] or 0 for r in rows),
        "tokens_per_success": (sum(_tok(r) for r in rows) / k) if k else None,
        "cost_per_success": (sum(r["est_cost_usd"] or 0 for r in rows) / k) if k else None,
        "tokens_success_mean": _mean([_tok(r) for r in rows if r["success"]]),
    }


# --------------------------------------------------------------------------- failures


def failure_rows() -> list[dict]:
    return db.query("""SELECT f.*, r.task_id, r.site, r.num_steps, r.input_tokens, r.output_tokens, r.est_cost_usd,
                              r.trace_path, j.reason AS judge_reason
                       FROM failures f JOIN runs r USING(run_id) JOIN judgments j USING(run_id)
                       WHERE r.condition='baseline' AND j.success=0 AND r.terminal_status!='error'""")


def repeat_stats(fails: list[dict], col: str) -> dict[str, Any]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in fails:
        if f.get(col):
            groups[f[col]].append(f)
    n = len(fails)
    rep = sum(len(g) for g in groups.values() if len(g) >= 2)
    cross = sum(len(g) for g in groups.values() if len({x["task_id"] for x in g}) >= 2)
    return {"repeat": wilson(rep, n), "cross_task": wilson(cross, n), "n_clusters": len(groups),
            "n_clusters_size2plus": sum(1 for g in groups.values() if len(g) >= 2)}


def waste_and_tokens(base_rows: list[dict], fails: list[dict]) -> dict[str, Any]:
    failed = [r for r in base_rows if not r["success"]]
    total_tok = sum(_tok(r) for r in base_rows)
    total_cost = sum(r["est_cost_usd"] or 0 for r in base_rows)
    total_steps = sum(r["num_steps"] or 0 for r in base_rows)
    w_tok = sum(_tok(r) for r in failed)
    w_cost = sum(r["est_cost_usd"] or 0 for r in failed)
    w_steps = sum(r["num_steps"] or 0 for r in failed)

    fix_by_run = {f["run_id"]: f["fixable"] for f in fails}
    by_fix: dict[str, dict[str, float]] = defaultdict(lambda: {"runs": 0, "tokens": 0, "cost": 0.0, "steps": 0})
    for r in failed:
        k = fix_by_run.get(r["run_id"], "unanalyzed")
        by_fix[k]["runs"] += 1
        by_fix[k]["tokens"] += _tok(r)
        by_fix[k]["cost"] += r["est_cost_usd"] or 0
        by_fix[k]["steps"] += r["num_steps"] or 0

    succ_tok_by_task: dict[str, list[int]] = defaultdict(list)
    succ_cost_by_task: dict[str, list[float]] = defaultdict(list)
    for r in base_rows:
        if r["success"]:
            succ_tok_by_task[r["task_id"]].append(_tok(r))
            succ_cost_by_task[r["task_id"]].append(r["est_cost_usd"] or 0)
    all_succ_tok = [t for v in succ_tok_by_task.values() for t in v]
    all_succ_cost = [c for v in succ_cost_by_task.values() for c in v]
    proj_tok = proj_cost = 0.0
    for r in failed:
        if fix_by_run.get(r["run_id"]) != "yes":
            continue
        exp_tok = _mean(succ_tok_by_task.get(r["task_id"], [])) or _mean(all_succ_tok) or 0
        exp_cost = _mean(succ_cost_by_task.get(r["task_id"], [])) or _mean(all_succ_cost) or 0
        proj_tok += max(0.0, _tok(r) - exp_tok)
        proj_cost += max(0.0, (r["est_cost_usd"] or 0) - exp_cost)

    share = lambda a, b: (a / b) if b else None  # noqa: E731
    return {
        "failed_runs": len(failed),
        "steps": {"wasted": w_steps, "total": total_steps, "share": share(w_steps, total_steps)},
        "tokens": {"wasted": w_tok, "total": total_tok, "share": share(w_tok, total_tok)},
        "cost": {"wasted": w_cost, "total": total_cost, "share": share(w_cost, total_cost)},
        "wasted_by_fixable": {k: dict(v) for k, v in by_fix.items()},
        "preventable_tokens": by_fix.get("yes", {}).get("tokens", 0),
        "preventable_share_of_total": share(by_fix.get("yes", {}).get("tokens", 0), total_tok),
        "projected_savings_tokens": proj_tok,
        "projected_savings_cost": proj_cost,
        "projected_savings_share_of_total": share(proj_tok, total_tok),
        "mean_tokens_failed_run": _mean([_tok(r) for r in failed]),
        "mean_tokens_success_run": _mean(all_succ_tok),
    }


# --------------------------------------------------------------------------- comparisons


def _per_task(rows: list[dict], fn) -> dict[str, list[float]]:
    d: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        d[r["task_id"]].append(fn(r))
    return d


def compare(a_rows: list[dict], b_rows: list[dict], fails: list[dict] | None = None) -> dict[str, Any]:
    tasks = sorted({r["task_id"] for r in a_rows} & {r["task_id"] for r in b_rows})
    a_rows = [r for r in a_rows if r["task_id"] in tasks]
    b_rows = [r for r in b_rows if r["task_id"] in tasks]
    sa, sb = success_summary(a_rows), success_summary(b_rows)
    succ_a, succ_b = _per_task(a_rows, lambda r: int(r["success"])), _per_task(b_rows, lambda r: int(r["success"]))
    tok_a, tok_b = _per_task(a_rows, _tok), _per_task(b_rows, _tok)
    rate = lambda d, t: sum(d[t]) / len(d[t]) if d.get(t) else None  # noqa: E731
    failed_tasks = [t for t in tasks if succ_a.get(t) and min(succ_a[t]) == 0]
    recovered = [t for t in failed_tasks if rate(succ_b, t) is not None and rate(succ_b, t) > rate(succ_a, t)]
    regress = [{"task_id": t, "baseline": rate(succ_a, t), "with_kb": rate(succ_b, t)} for t in tasks
               if rate(succ_b, t) is not None and rate(succ_b, t) < rate(succ_a, t)]
    per_site = {}
    for site in sorted({r["site"] for r in a_rows}):
        ra = [r for r in a_rows if r["site"] == site]
        rb = [r for r in b_rows if r["site"] == site]
        per_site[site] = {"a": wilson(sum(int(r["success"]) for r in ra), len(ra)),
                          "b": wilson(sum(int(r["success"]) for r in rb), len(rb))}
    per_cat = {}
    if fails:
        cat_tasks: dict[str, set[str]] = defaultdict(set)
        for f in fails:
            cat_tasks[f["failure_category"]].add(f["task_id"])
        for cat, ts in sorted(cat_tasks.items()):
            ra = [r for r in a_rows if r["task_id"] in ts]
            rb = [r for r in b_rows if r["task_id"] in ts]
            per_cat[cat] = {"tasks": len(ts), "a": wilson(sum(int(r["success"]) for r in ra), len(ra)),
                            "b": wilson(sum(int(r["success"]) for r in rb), len(rb))}
    tps_a, tps_b = sa["tokens_per_success"], sb["tokens_per_success"]
    return {
        "a": sa, "b": sb,
        "success_diff": paired_bootstrap(succ_a, succ_b),
        "steps_success": {"a": sa["steps_success_mean"], "b": sb["steps_success_mean"],
                          "pct_change": ((sb["steps_success_mean"] - sa["steps_success_mean"]) /
                                         sa["steps_success_mean"]) if sa["steps_success_mean"] and
                          sb["steps_success_mean"] else None},
        "tokens": {
            "a_total": sa["tokens_total"], "b_total": sb["tokens_total"],
            "total_pct_change": ((sb["tokens_total"] - sa["tokens_total"]) / sa["tokens_total"])
            if sa["tokens_total"] else None,
            "mean_per_run_diff": paired_bootstrap(tok_a, tok_b),
            "a_per_success": tps_a, "b_per_success": tps_b,
            "per_success_pct_change": ((tps_b - tps_a) / tps_a) if tps_a and tps_b else None,
            "a_cost_per_success": sa["cost_per_success"], "b_cost_per_success": sb["cost_per_success"],
        },
        "recovered": {"tasks": recovered, "n_failed_tasks": len(failed_tasks),
                      "share": wilson(len(recovered), len(failed_tasks))},
        "regressions": regress,
        "per_site": per_site,
        "per_category": per_cat,
        "n_tasks": len(tasks),
    }


# --------------------------------------------------------------------------- judge validation


def judge_agreement() -> dict[str, Any]:
    p = path("out") / "judge_sample.csv"
    if not p.exists():
        return {"status": "judge not yet validated (no sample exported)"}
    rows = list(csv.DictReader(p.open()))
    labelled = [r for r in rows if r.get("human_success", "").strip().lower() in ("true", "false", "1", "0", "yes", "no")]
    if not labelled:
        return {"status": "judge not yet validated", "sample_size": len(rows)}
    yes = ("true", "1", "yes")
    pairs = [(r["judge_success"].strip().lower() in yes, r["human_success"].strip().lower() in yes) for r in labelled]
    agree = sum(1 for j, h in pairs if j == h)
    n = len(pairs)
    pj = sum(j for j, _ in pairs) / n
    ph = sum(h for _, h in pairs) / n
    pe = pj * ph + (1 - pj) * (1 - ph)
    po = agree / n
    kappa = (po - pe) / (1 - pe) if pe < 1 else None
    return {"status": "validated", "n": n, "agreement": wilson(agree, n), "kappa": kappa,
            "judge_false_positive": sum(1 for j, h in pairs if j and not h),
            "judge_false_negative": sum(1 for j, h in pairs if h and not j)}


# --------------------------------------------------------------------------- all


def compute_all() -> dict[str, Any]:
    base, base_err = condition_rows("baseline")
    fails = failure_rows()
    out: dict[str, Any] = {"definitions": DEFINITIONS}
    out["baseline"] = success_summary(base)
    out["baseline"]["harness_errors"] = len(base_err)
    out["failures"] = {
        "n": len(fails),
        "categories": dict(Counter(f["failure_category"] for f in fails).most_common()),
        "fixable": {k: wilson(v, len(fails)) for k, v in Counter(f["fixable"] for f in fails).items()},
        "repeat_A": repeat_stats(fails, "cluster_a"),
        "repeat_B": repeat_stats(fails, "cluster_b"),
    }
    out["waste"] = waste_and_tokens(base, fails)
    clusters = db.query("SELECT * FROM clusters ORDER BY size DESC, n_tasks DESC")
    out["top_clusters"] = {m: [dict(c, member_run_ids=json.loads(c["member_run_ids"])) for c in clusters
                               if c["method"] == m][:8] for m in ("A", "B")}

    for cond, key in (("with_kb", "kb_effect"), ("with_hindsight_kb", "hindsight_effect")):
        rows, errs = condition_rows(cond)
        if rows:
            out[key] = compare(base, rows, fails)
            out[key]["harness_errors"] = len(errs)
    tb, _ = condition_rows("transfer_baseline")
    tk, _ = condition_rows("transfer")
    if tb and tk:
        out["transfer"] = compare(tb, tk)
    if "kb_effect" in out and "hindsight_effect" in out:
        a = out["kb_effect"]["success_diff"]["diff"]
        b = out["hindsight_effect"]["success_diff"]["diff"]
        out["oracle_fraction"] = (a / b) if a is not None and b else None
    out["judge_validation"] = judge_agreement()
    out["kb"] = {
        "entries_frozen": len(json.loads(path("kb_frozen").read_text())) if path("kb_frozen").exists() else 0,
        "rejected_drafts": db.query("SELECT COUNT(*) n FROM kb_rejections")[0]["n"],
    }
    llm = db.query("SELECT purpose, COUNT(*) n, SUM(input_tokens) i, SUM(output_tokens) o, SUM(est_cost_usd) c "
                   "FROM llm_calls WHERE cached=0 GROUP BY purpose")
    out["analysis_spend"] = {r["purpose"]: {"calls": r["n"], "input_tokens": r["i"], "output_tokens": r["o"],
                                            "cost": r["c"]} for r in llm}
    return out
