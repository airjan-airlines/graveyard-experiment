"""report.md, results.json, slide_numbers.md and charts — generated from the DB only (no network)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from src import db  # noqa: E402
from src.analyze import classify, summarize  # noqa: E402
from src.config import cfg, load_tasks, model_cfg, path, resolved_dates  # noqa: E402
from src.judge import JUDGE_PROMPT  # noqa: E402
from src.kb.writer import FAILURE_PROMPT, HINDSIGHT_PROMPT, NOTE_RULES  # noqa: E402
from src.metrics import compute_all  # noqa: E402
from src.runner.base import SYSTEM_PROMPT  # noqa: E402

# Validated reference palette (dataviz skill), light mode; baseline is a recessive neutral, KB is the accent.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e4e3df"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
NEUTRAL = "#a3a29c"

plt.rcParams.update({
    "font.size": 15, "axes.titlesize": 19, "axes.labelsize": 15, "axes.edgecolor": INK2, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "text.color": INK, "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE, "axes.spines.top": False, "axes.spines.right": False, "savefig.facecolor": SURFACE,
    "legend.frameon": False,
})


def pct(x: float | None, digits: int = 0) -> str:
    return "n/a" if x is None else f"{100 * x:.{digits}f}%"


def ci(w: dict | None) -> str:
    if not w or w.get("p") is None:
        return "n/a"
    return f"{pct(w['p'])} (95% CI {pct(w['lo'])}–{pct(w['hi'])}, n={w['n']})"


def tok(n: float | None) -> str:
    if n is None:
        return "n/a"
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else f"{n:.0f}"


def usd(x: float | None) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def _save(fig, name: str) -> str:
    d = path("out") / "charts"
    d.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(d / name, dpi=160)
    plt.close(fig)
    return f"charts/{name}"


def _grid(ax, axis="x"):
    ax.grid(axis=axis, color=GRID, linewidth=1)
    ax.set_axisbelow(True)


# --------------------------------------------------------------------------- charts


def chart_failure_categories(m: dict) -> str | None:
    cats = m["failures"]["categories"]
    if not cats:
        return None
    items = sorted(cats.items(), key=lambda kv: kv[1])
    fig, ax = plt.subplots(figsize=(10, 0.55 * len(items) + 1.6))
    ax.barh([k.replace("_", " ") for k, _ in items], [v for _, v in items], color=BLUE, height=0.6)
    for i, (_, v) in enumerate(items):
        ax.text(v + 0.1, i, str(v), va="center", color=INK2, fontsize=13)
    ax.set_xlabel("failed runs")
    ax.set_title("Why agents fail (baseline)", loc="left")
    _grid(ax)
    return _save(fig, "failure_categories.png")


def chart_repeat_breakdown(m: dict) -> str | None:
    f = m["failures"]
    n = f["n"]
    if not n:
        return None
    fig, ax = plt.subplots(figsize=(10, 4.2))
    rows = []
    for label, key in (("Repeat? (A: structured)", "repeat_A"), ("Repeat? (B: semantic)", "repeat_B")):
        r = f[key]["repeat"]["k"]
        rows.append((label, [(r, "repeat", BLUE), (n - r, "one-off", NEUTRAL)]))
    fx = {k: v["k"] for k, v in f["fixable"].items()}
    rows.append(("Knowledge-fixable?", [(fx.get("yes", 0), "fixable", BLUE), (fx.get("handoff", 0), "handoff", ORANGE),
                                        (fx.get("no", 0), "not fixable", NEUTRAL)]))
    LEGEND = {BLUE: "repeat / fixable", ORANGE: "handoff (CAPTCHA, login)", NEUTRAL: "one-off / not fixable"}
    seen = set()
    for i, (label, segs) in enumerate(reversed(rows)):
        left = 0
        for v, name, color in segs:
            if v <= 0:
                continue
            ax.barh(i, v / n, left=left, color=color, height=0.55, edgecolor=SURFACE, linewidth=2,
                    label=LEGEND[color] if color not in seen else None)
            seen.add(color)
            if v / n > 0.07:
                ax.text(left + v / n / 2, i, f"{name}\n{pct(v / n)}", ha="center", va="center", fontsize=12,
                        color="white" if color != NEUTRAL else INK)
            left += v / n
    ax.set_yticks(range(len(rows)), [r[0] for r in reversed(rows)])
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_title(f"Are failures repeats? Are they knowledge-fixable? (n={n} failures)", loc="left", fontsize=16)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=3, fontsize=12)
    return _save(fig, "repeat_breakdown.png")


def chart_kb_success(m: dict) -> str | None:
    k = m.get("kb_effect")
    if not k:
        return None
    groups = [("Overall", k["a"]["success"], k["b"]["success"])]
    groups += [(s.replace("google.com/travel/flights", "google flights"), v["a"], v["b"]) for s, v in k["per_site"].items()]
    fig, ax = plt.subplots(figsize=(max(10, 1.25 * len(groups) + 2), 5.5))
    w = 0.38
    for j, (name, color, off) in enumerate((("baseline", NEUTRAL, -w / 2), ("with KB", BLUE, w / 2))):
        xs, ys, lo, hi = [], [], [], []
        for i, g in enumerate(groups):
            s = g[1 + j]
            if s["p"] is None:
                continue
            xs.append(i + off)
            ys.append(s["p"])
            lo.append(s["p"] - s["lo"])
            hi.append(s["hi"] - s["p"])
        ax.bar(xs, ys, width=w - 0.04, color=color, label=name)
        ax.errorbar(xs, ys, yerr=[lo, hi], fmt="none", ecolor=INK2, elinewidth=1.2, capsize=3)
    ax.set_xticks(range(len(groups)), [g[0] for g in groups], rotation=30, ha="right", fontsize=12)
    ax.set_ylim(0, 1.05)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_ylabel("judged success rate")
    ax.set_title("Success rate: baseline vs with knowledge base (95% Wilson CI)", loc="left", fontsize=16)
    ax.legend(loc="upper left", ncol=2)
    _grid(ax, "y")
    return _save(fig, "kb_effect_success.png")


def chart_kb_steps_tokens(m: dict) -> tuple[str | None, str | None]:
    k = m.get("kb_effect")
    if not k:
        return None, None
    out = []
    for fname, title, vals, fmt in (
        ("kb_effect_steps.png", "Mean steps on successful runs",
         [k["steps_success"]["a"], k["steps_success"]["b"]], lambda v: f"{v:.1f}"),
        ("kb_effect_tokens.png", "Tokens per successful task (incl. failed attempts)",
         [k["tokens"]["a_per_success"], k["tokens"]["b_per_success"]], tok),
    ):
        if any(v is None for v in vals):
            out.append(None)
            continue
        fig, ax = plt.subplots(figsize=(7, 4.6))
        ax.bar(["baseline", "with KB"], vals, color=[NEUTRAL, BLUE], width=0.55)
        for i, v in enumerate(vals):
            ax.text(i, v, fmt(v), ha="center", va="bottom", fontsize=15, color=INK)
        ax.set_title(title, loc="left", fontsize=16)
        ax.set_ylim(0, max(vals) * 1.18)
        _grid(ax, "y")
        out.append(_save(fig, fname))
    return out[0], out[1]


def chart_token_waste(m: dict) -> str | None:
    w = m["waste"]
    total = w["tokens"]["total"]
    if not total:
        return None
    by = w["wasted_by_fixable"]
    succ = total - w["tokens"]["wasted"]
    segs = [(succ, "successful runs", NEUTRAL), (by.get("yes", {}).get("tokens", 0), "failed: knowledge-fixable", BLUE),
            (by.get("handoff", {}).get("tokens", 0), "failed: handoff", ORANGE),
            (by.get("no", {}).get("tokens", 0) + by.get("unanalyzed", {}).get("tokens", 0), "failed: other", "#52514e")]
    fig, ax = plt.subplots(figsize=(10, 2.9))
    left = 0
    for v, name, color in segs:
        if v <= 0:
            continue
        ax.barh(0, v / total, left=left, color=color, height=0.5, edgecolor=SURFACE, linewidth=2, label=name)
        if v / total > 0.08:
            ax.text(left + v / total / 2, 0, pct(v / total), ha="center", va="center", fontsize=13,
                    color=INK if color == NEUTRAL else "white")
        left += v / total
    ax.set_yticks([])
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_title(f"Where baseline tokens went ({tok(total)} tokens total)", loc="left", fontsize=16)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.3), ncol=4, fontsize=11)
    return _save(fig, "token_waste.png")


def chart_top_clusters(m: dict) -> str | None:
    cl = [c for c in m["top_clusters"]["B"] if c["size"] >= 2][:8]
    if not cl:
        return None
    cl = list(reversed(cl))
    fig, ax = plt.subplots(figsize=(11, 0.6 * len(cl) + 1.6))
    labels = [f"{c['site'].replace('google.com/travel/flights', 'google flights')}: {c['name']}" for c in cl]
    labels = [lab if len(lab) <= 58 else lab[:57] + "…" for lab in labels]
    ax.barh(labels, [c["size"] for c in cl], color=[BLUE if c["n_tasks"] >= 2 else NEUTRAL for c in cl], height=0.6)
    for i, c in enumerate(cl):
        ax.text(c["size"] + 0.05, i, f"{c['size']} runs, {c['n_tasks']} task{'s' if c['n_tasks'] > 1 else ''}",
                va="center", fontsize=12, color=INK2)
    ax.set_xlabel("failed runs in cluster")
    ax.set_xlim(0, max(c["size"] for c in cl) * 1.45)
    fig.suptitle("Top repeated failure modes (blue = same quirk across ≥2 tasks)", x=0.01, ha="left", fontsize=16)
    ax.tick_params(axis="y", labelsize=12)
    _grid(ax)
    return _save(fig, "top_clusters.png")


# --------------------------------------------------------------------------- text


def headline(m: dict) -> str:
    b = m["baseline"]
    f = m["failures"]
    fx = f["fixable"].get("yes")
    s = (f"Agents fail {pct(b['failure']['p'])} of everyday web tasks (95% CI {pct(b['failure']['lo'])}–"
         f"{pct(b['failure']['hi'])}), and {pct(f['repeat_B']['repeat']['p'])} of those failures are repeats (semantic clustering; "
         f"{pct(f['repeat_A']['repeat']['p'])} by the stricter structured key). "
         f"{pct(fx['p'] if fx else 0)} were preventable with a single shared note.")
    k = m.get("kb_effect")
    if k:
        sp = k["steps_success"]["pct_change"]
        s += (f" With our knowledge base: {pct(k['a']['success']['p'])} → {pct(k['b']['success']['p'])} success, "
              f"{pct(-sp) if sp is not None else 'n/a'} {'fewer' if (sp or 0) <= 0 else 'more'} steps.")
    return s


def slide_numbers(m: dict) -> str:
    b, f, w = m["baseline"], m["failures"], m["waste"]
    lines = [f"**{headline(m)}**", ""]
    lines.append(f"- When an agent says it's done, it's wrong {pct(b['false_success']['p'])} of the time "
                 f"(false-success rate, n={b['false_success']['n']}).")
    lines.append(f"- Reliability gap: pass@1 {pct(b['pass@1']['p'])} vs pass^3 {pct(b['pass^k']['p'])}; "
                 f"{pct(b['flakiness']['p'])} of tasks are flaky across 3 identical runs.")
    lines.append(f"- {pct(f['repeat_B']['cross_task']['p'])} of failures are the same quirk biting a *different* "
                 f"task on the same site (cross-task repeats).")
    lines.append(f"- {pct(w['tokens']['share'])} of all agent tokens ({tok(w['tokens']['wasted'])}, "
                 f"{usd(w['cost']['wasted'])}) were burned on runs that failed; "
                 f"{pct(w['preventable_share_of_total'])} went to knowledge-fixable failures.")
    k = m.get("kb_effect")
    if k:
        t = k["tokens"]
        lines.append(f"- Tokens per successful task: {tok(t['a_per_success'])} → {tok(t['b_per_success'])} with the "
                     f"KB ({pct(t['per_success_pct_change'])} change); {len(k['regressions'])} task(s) regressed.")
    else:
        lines.append(f"- Projected savings if fixable failures succeeded: ~{tok(w['projected_savings_tokens'])} tokens "
                     f"({pct(w['projected_savings_share_of_total'])} of baseline spend).")
    return "\n".join(lines) + "\n"


def _examples(n: int = 5) -> tuple[list[dict], list[dict]]:
    rows = db.query("""SELECT f.*, r.site, r.task_id FROM failures f JOIN runs r USING(run_id)
                       WHERE r.condition='baseline' ORDER BY f.run_id""")
    seen, causes = set(), []
    for r in rows:  # one per category for variety
        if r["failure_category"] not in seen:
            causes.append(r)
            seen.add(r["failure_category"])
    notes = [r for r in rows if r["fixable"] == "yes" and r["draft_note"]]
    return causes[:n], notes[:3]


def write_report(m: dict, charts: dict[str, str | None], synthetic: bool) -> str:
    b, f, w = m["baseline"], m["failures"], m["waste"]
    k = m.get("kb_effect")
    tasks = load_tasks()
    sites = sorted({t.site for t in tasks})
    runs_dates = db.query("SELECT MIN(started_at) a, MAX(finished_at) b FROM runs")[0]
    L: list[str] = []
    add = L.append
    if synthetic:
        add("> ⚠️ **SYNTHETIC DATA.** This report was generated by the offline fake provider/runner to test the "
            "pipeline. None of these numbers are real measurements.\n")
    add("# Agents repeat the same web mistakes — and a shared knowledge base fixes many of them\n")
    add("*Small pilot. All numbers are generated from `data/experiment.db` by `scripts/make_report.py`.*\n")
    add("## 1. Headline\n")
    add(f"**{headline(m)}**\n")
    add("## 2. Setup\n")
    jv = m["judge_validation"]
    add(f"- **Tasks:** {len(tasks)} everyday non-coding tasks on {len(sites)} live sites ({', '.join(sites)}).")
    add(f"- **Runs:** {cfg()['reps_per_task']} reps per task per condition; baseline judged runs = {b['runs']} "
        f"(+{b['harness_errors']} harness errors excluded).")
    add(f"- **Agent model:** `{model_cfg('agent')['name']}` via browser-use; **analysis/judge model:** "
        f"`{model_cfg('analysis')['name']}`.")
    add(f"- **Max steps:** {cfg()['max_steps']}, timeout {cfg()['run_timeout_s']} s, headless Chromium "
        f"{cfg()['browser']['viewport'][0]}×{cfg()['browser']['viewport'][1]}.")
    add(f"- **Run window:** {runs_dates['a']} → {runs_dates['b']}; relative dates resolved on "
        f"{resolved_dates()['resolved_on']}.")
    add(f"- **Judge validation:** {jv['status']}" + (f" — agreement {ci(jv['agreement'])}, κ={jv['kappa']:.2f}"
                                                      if jv.get("status") == "validated" and jv.get("kappa") is not None
                                                      else "") + ".\n")
    add("## 3. How often agents fail\n")
    add(f"- **Failure rate:** {ci(b['failure'])}")
    add(f"- **pass@1:** {ci(b['pass@1'])}; **pass^3 (all 3 reps succeed):** {ci(b['pass^k'])}")
    add(f"- **Task flakiness (mixed outcomes):** {ci(b['flakiness'])}")
    add(f"- **False-success rate** (agent said done, judge said no): {ci(b['false_success'])}")
    add(f"- **Terminal statuses:** " + ", ".join(f"{k_}: {v}" for k_, v in sorted(b["terminal_status"].items())) + "\n")
    add("| Site | Runs | pass@1 | pass^3 |\n|---|---|---|---|")
    for s, v in b["per_site"].items():
        add(f"| {s} | {v['runs']} | {pct(v['pass@1']['p'])} | {pct(v['pass^k'])} |")
    add("")
    causes, notes = _examples()
    add("## 4. Why they fail\n")
    if charts.get("failure_categories"):
        add(f"![failure categories]({charts['failure_categories']})\n")
    for c in causes:
        add(f"- *{c['site']} / {c['failure_category']}:* “{c['cause']}”")
    add("")
    add("## 5. Failures repeat\n")
    for meth, key in (("A (structured key)", "repeat_A"), ("B (semantic clusters)", "repeat_B")):
        r = f[key]
        add(f"- **Method {meth}:** repeat-failure rate {ci(r['repeat'])}; cross-task repeat rate {ci(r['cross_task'])}; "
            f"{r['n_clusters_size2plus']} clusters of size ≥2.")
    add("")
    if charts.get("top_clusters"):
        add(f"![top clusters]({charts['top_clusters']})\n")
    add("| Site | Cluster (Method B) | Failures | Tasks |\n|---|---|---|---|")
    for c in m["top_clusters"]["B"][:5]:
        add(f"| {c['site']} | {c['name']} | {c['size']} | {c['n_tasks']} |")
    add("\nSee `out/cluster_samples.md` for member causes of 5 random clusters (human sanity check).\n")
    add("## 6. Failures are knowledge-fixable\n")
    for lab in ("yes", "handoff", "no"):
        add(f"- **{lab}:** {ci(f['fixable'].get(lab))}")
    add("")
    if charts.get("repeat_breakdown"):
        add(f"![repeat breakdown]({charts['repeat_breakdown']})\n")
    add("Example draft notes from the classifier:\n")
    for n_ in notes:
        add(f"> **{n_['site']}** — {n_['draft_note']}\n")
    add("## 7. The KB fixes them\n")
    if not k:
        add("*with_kb condition not run yet.*\n")
    else:
        d = k["success_diff"]
        add(f"- **Success rate:** baseline {ci(k['a']['success'])} → with KB {ci(k['b']['success'])}.")
        add(f"- **Difference:** {pct(d['diff'], 1)} (paired bootstrap over {d['n_tasks']} tasks, 95% CI "
            f"{pct(d['lo'], 1)} to {pct(d['hi'], 1)}).")
        sp = k["steps_success"]
        add(f"- **Mean steps on successful runs:** {sp['a']:.1f} → {sp['b']:.1f} ({pct(sp['pct_change'])})."
            if sp["a"] and sp["b"] else "- **Mean steps on successful runs:** n/a")
        add(f"- **Recovered tasks:** {ci(k['recovered']['share'])} of tasks that failed ≥1 baseline run improved: "
            f"{', '.join(k['recovered']['tasks']) or 'none'}.")
        reg = k["regressions"]
        add(f"- **Regressions ({len(reg)}):** " + (", ".join(f"{r['task_id']} ({pct(r['baseline'])}→{pct(r['with_kb'])})"
                                                          for r in reg) or "none") + ".")
        add(f"- **KB size:** {m['kb']['entries_frozen']} frozen entries; {m['kb']['rejected_drafts']} drafts rejected "
            f"by the answer-leak check.\n")
        for key_ in ("kb_effect_success", "kb_effect_steps"):
            if charts.get(key_):
                add(f"![{key_}]({charts[key_]})\n")
        add("| Failure category (baseline) | Tasks | Baseline success | With-KB success |\n|---|---|---|---|")
        for cat, v in k["per_category"].items():
            add(f"| {cat} | {v['tasks']} | {pct(v['a']['p'])} | {pct(v['b']['p'])} |")
        add("")
        h = m.get("hindsight_effect")
        if h:
            of = m.get("oracle_fraction")
            add(f"- **Hindsight KB (oracle-ish upper bound):** {ci(h['b']['success'])}; oracle fraction "
                f"{'n/a' if of is None else f'{of:.2f}'}.")
        tr = m.get("transfer")
        if tr:
            add(f"- **Transfer to `{model_cfg('transfer_agent')['name']}`:** {ci(tr['a']['success'])} → "
                f"{ci(tr['b']['success'])} (diff {pct(tr['success_diff']['diff'], 1)}, 95% CI "
                f"{pct(tr['success_diff']['lo'], 1)} to {pct(tr['success_diff']['hi'], 1)}).")
        add("")
    add("## 8. Cost of failure: wasted tokens and potential savings\n")
    add(f"- **Wasted on failed runs:** {tok(w['tokens']['wasted'])} of {tok(w['tokens']['total'])} tokens "
        f"({pct(w['tokens']['share'])}), {w['steps']['wasted']} of {w['steps']['total']} steps "
        f"({pct(w['steps']['share'])}), {usd(w['cost']['wasted'])} of {usd(w['cost']['total'])} est. cost.")
    add(f"- **Mean tokens per run:** failed {tok(w['mean_tokens_failed_run'])} vs successful "
        f"{tok(w['mean_tokens_success_run'])}.")
    add("- **Wasted tokens by failure type:**\n")
    add("| Failure type | Runs | Tokens | Steps | Est. cost |\n|---|---|---|---|---|")
    for lab, v in sorted(w["wasted_by_fixable"].items()):
        add(f"| {lab} | {v['runs']} | {tok(v['tokens'])} | {v['steps']} | {usd(v['cost'])} |")
    add("")
    add(f"- **Preventable (knowledge-fixable) tokens:** {tok(w['preventable_tokens'])} "
        f"({pct(w['preventable_share_of_total'])} of all baseline tokens).")
    add(f"- **Projected savings** (fixable failures replaced by a typical successful run of the same task): "
        f"~{tok(w['projected_savings_tokens'])} tokens, {usd(w['projected_savings_cost'])} "
        f"({pct(w['projected_savings_share_of_total'])} of baseline spend).")
    if k:
        t = k["tokens"]
        add(f"- **Measured with KB:** total tokens {tok(t['a_total'])} → {tok(t['b_total'])} "
            f"({pct(t['total_pct_change'])}); tokens per successful task {tok(t['a_per_success'])} → "
            f"{tok(t['b_per_success'])} ({pct(t['per_success_pct_change'])}); est. cost per successful task "
            f"{usd(t['a_cost_per_success'])} → {usd(t['b_cost_per_success'])}. Mean tokens per run diff "
            f"{tok(t['mean_per_run_diff']['diff']) if (t['mean_per_run_diff']['diff'] or 0) >= 0 else '-' + tok(-t['mean_per_run_diff']['diff'])} "
            f"(95% CI {t['mean_per_run_diff']['lo']:,.0f} to {t['mean_per_run_diff']['hi']:,.0f}).")
    if charts.get("token_waste"):
        add(f"\n![token waste]({charts['token_waste']})\n")
    if charts.get("kb_effect_tokens"):
        add(f"![tokens per success]({charts['kb_effect_tokens']})\n")
    spend = m["analysis_spend"]
    if spend:
        add("Analysis-pipeline spend (judge, summarizer, classifier, notes): " +
            ", ".join(f"{p}: {v['calls']} calls {usd(v['cost'])}" for p, v in sorted(spend.items())) + ".\n")
    add("## 9. Limitations\n")
    swaps = [t for t in tasks if t.replaced_from]
    add(f"""- **Small pilot.** {len(tasks)} tasks × {cfg()['reps_per_task']} reps; confidence intervals are wide and per-site numbers rest on 3–9 runs.
- **LLM judge.** Success is graded by `{model_cfg('analysis')['name']}` from the action log and screenshots; {jv['status']}. Failure categories, fixability, and notes are also LLM-produced.
- **Live sites change.** Prices, availability and page layouts drift; baseline and with-KB runs were run as close together as possible but not simultaneously.
- **Site swaps:** {len(swaps)} task(s) moved to a different site after hard blocking ({', '.join(f'{t.replaced_from}→{t.site}' for t in swaps) or 'none'}).
- **Harness dependency.** Results reflect browser-use {_bu_version()} + this prompt; another harness may fail differently. {b['harness_errors']} baseline runs ended in harness errors and are excluded from rates.
- **Same tasks for KB construction and evaluation.** The KB is frozen from baseline failures before the with-KB reruns (never from with-KB runs) and an automated check rejects notes that leak answers, but the reruns are on the same tasks the notes came from; cross-task repeats are the better estimate of generalization.
- **Token costs** are estimates from list prices and count cached prompt tokens at the full input rate (an upper bound).
""")
    add("## 10. Appendix\n")
    add("### Metric definitions\n")
    for k_, v in m["definitions"].items():
        add(f"- **{k_}** — {v}")
    add("\n### Prompts\n")
    for name, p in (("Agent system prompt", SYSTEM_PROMPT), ("Judge", JUDGE_PROMPT), ("Failure summarizer", summarize.PROMPT),
                    ("Knowledge-fixable classifier", classify.PROMPT), ("KB note writer", FAILURE_PROMPT),
                    ("Hindsight note writer", HINDSIGHT_PROMPT), ("Note rules", NOTE_RULES)):
        add(f"<details><summary>{name}</summary>\n\n```\n{p}\n```\n</details>\n")
    add("### Config\n\n```yaml\n" + Path(cfg_path()).read_text() + "```\n")
    add("### Tasks\n\n| ID | Site | Type | Instruction |\n|---|---|---|---|")
    for t in tasks:
        add(f"| {t.id} | {t.site} | {t.task_type} | {t.instruction} |")
    add(f"\n### Swapped sites\n\n{', '.join(f'{t.id}: {t.replaced_from} → {t.site}' for t in swaps) or 'None.'}\n")
    add(f"\n*Generated {dt.datetime.now().isoformat(timespec='seconds')}.*\n")
    return "\n".join(L)


def cfg_path() -> Path:
    from src.config import config_path

    return config_path()


def _bu_version() -> str:
    try:
        import importlib.metadata as md

        return md.version("browser-use")
    except Exception:
        return "?"


def make_report() -> dict[str, Any]:
    m = compute_all()
    synthetic = model_cfg("agent")["provider"] == "fake" or cfg().get("runner") == "fake"
    m["synthetic"] = synthetic
    charts = {
        "failure_categories": chart_failure_categories(m),
        "repeat_breakdown": chart_repeat_breakdown(m),
        "kb_effect_success": chart_kb_success(m),
        "top_clusters": chart_top_clusters(m),
        "token_waste": chart_token_waste(m),
    }
    charts["kb_effect_steps"], charts["kb_effect_tokens"] = chart_kb_steps_tokens(m)
    out = path("out")
    (out / "results.json").write_text(json.dumps(m, indent=1, default=str))
    (out / "report.md").write_text(write_report(m, charts, synthetic))
    sn = slide_numbers(m)
    if synthetic:
        sn = "> ⚠️ SYNTHETIC DATA — pipeline test only.\n\n" + sn
    (out / "slide_numbers.md").write_text(sn)
    return m
