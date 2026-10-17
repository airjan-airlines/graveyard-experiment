"""M4: failure analysis over baseline failures: summarize (8.1) -> classify (8.2) -> cluster A+B (8.3).
All LLM calls are cached, so re-running is free."""

import json
import random

import _common  # noqa: F401

from src import db
from src.analyze.classify import classify_failure
from src.analyze.cluster import cluster_failures
from src.analyze.summarize import summarize_failure
from src.config import path, task_map
from src.llm import pmap


def main() -> None:
    tasks = task_map()
    failed = db.query("""SELECT r.run_id, r.task_id, r.site FROM runs r JOIN judgments j USING(run_id)
                         WHERE r.condition='baseline' AND j.success=0 AND r.terminal_status!='error'
                         ORDER BY r.run_id""")
    print(f"{len(failed)} baseline failures to analyze")

    def one(r: dict) -> dict:
        s = summarize_failure(r["run_id"])
        t = tasks[r["task_id"]]
        c = classify_failure(s, t.site, t.instruction)
        db.upsert("failures", {
            "run_id": r["run_id"], "failure_step_index": s["failure_step_index"],
            "failure_category": s["failure_category"], "ui_pattern": s["ui_pattern"], "cause": s["cause"],
            "page_context": s["page_context"], "fixable": c["fixable"], "draft_note": c["draft_note"],
            "fixable_rationale": c["rationale"],
        })
        print(f"  {r['run_id']}: {s['failure_category']} / fixable={c['fixable']}", flush=True)
        return r

    pmap(one, failed)
    # drop stale analysis rows (e.g. a run later re-judged as success)
    keep = {r["run_id"] for r in failed}
    for r in db.query("SELECT run_id FROM failures"):
        if r["run_id"] not in keep:
            db.execute("DELETE FROM failures WHERE run_id=?", [r["run_id"]])

    rows = db.query("""SELECT f.*, r.task_id, r.site FROM failures f JOIN runs r USING(run_id) ORDER BY f.run_id""")
    if not rows:
        print("No failures; nothing to cluster.")
        return
    clusters = cluster_failures(rows)
    write_cluster_samples(clusters)
    nb = sum(1 for c in clusters.values() if c["method"] == "B" and c["size"] >= 2)
    print(f"Clustered: {sum(1 for c in clusters.values() if c['method'] == 'A')} A-clusters, "
          f"{sum(1 for c in clusters.values() if c['method'] == 'B')} B-clusters ({nb} with size>=2).")


def write_cluster_samples(clusters: dict) -> None:
    rng = random.Random(0)
    multi = [(k, c) for k, c in sorted(clusters.items()) if c["size"] >= 2]
    pool = multi if len(multi) >= 5 else sorted(clusters.items())
    pick = rng.sample(pool, min(5, len(pool)))
    lines = ["# Cluster samples (human sanity check)\n",
             "Five random clusters (size >= 2 preferred) with their member causes. Do the members look like the "
             "same mistake?\n"]
    for k, c in pick:
        lines.append(f"## {c['method']}: {c['name']}  \n`{k}` — {c['size']} failures, {c['n_tasks']} task(s)\n")
        for mbr in c["members"]:
            lines.append(f"- **{mbr['task_id']}** ({mbr['failure_category']}, ui={mbr.get('ui_pattern')}): "
                         f"{mbr['cause']}")
        lines.append("")
    (path("out") / "cluster_samples.md").write_text("\n".join(lines))
    print("Wrote out/cluster_samples.md")


if __name__ == "__main__":
    main()
