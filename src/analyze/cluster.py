"""Repeat detection (§8.3): Method A (structured key) and Method B (semantic clustering of causes)."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from urllib.parse import urlparse

import numpy as np

from src import db
from src.config import cfg
from src.llm import complete_json, embed, obj


def normalize_page_context(pc: str | None) -> str:
    """'https://www.usps.com/manage/po-locator.htm?x=1' -> '/manage/po-locator.htm'; free text -> slug."""
    if not pc:
        return "unknown"
    pc = pc.strip().lower()
    if "://" in pc or pc.startswith("/"):
        p = urlparse(pc if "://" in pc else "http://x" + pc).path or "/"
        p = re.sub(r"/\d+(?=/|$)", "/:id", p)  # numeric ids
        return p.rstrip("/") or "/"
    return re.sub(r"[^a-z0-9]+", "_", pc).strip("_")[:40] or "unknown"


def method_a_key(f: dict) -> str:
    if f.get("ui_pattern"):
        up = re.sub(r"[^a-z0-9]+", "_", f["ui_pattern"].lower()).strip("_")
        return f"A|{f['site']}|{f['failure_category']}|ui:{up}"
    return f"A|{f['site']}|{f['failure_category']}|page:{normalize_page_context(f.get('page_context'))}"


def agglomerative(vecs: np.ndarray, threshold: float) -> list[int]:
    """Average-linkage agglomerative clustering on cosine distance; returns labels."""
    if len(vecs) == 1:
        return [0]
    from sklearn.cluster import AgglomerativeClustering

    model = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                    distance_threshold=threshold)
    return model.fit_predict(vecs).tolist()


NAME_SCHEMA = obj(name={"type": "string"})


def name_cluster(site: str, causes: list[str]) -> str:
    text = (f"These web-agent failure causes on {site} were grouped together:\n- " + "\n- ".join(causes[:12]) +
            "\n\nName the shared failure mode in at most 8 words. Return JSON {\"name\": \"...\"}.")
    out = complete_json("analysis", "cluster_name", "You name clusters of failure causes concisely.", text,
                        NAME_SCHEMA, max_tokens=2000)
    return " ".join(out["name"].split()[:8])


def cluster_failures(failures: list[dict]) -> dict[str, dict]:
    """failures: rows with run_id, task_id, site, failure_category, ui_pattern, cause, page_context.
    Writes cluster_a / cluster_b on failures + the clusters table. Returns {cluster_id: info}."""
    thr = float(cfg()["clustering"]["cosine_distance_threshold"])
    clusters: dict[str, dict] = {}

    # Method A
    for f in failures:
        k = method_a_key(f)
        f["cluster_a"] = k
        clusters.setdefault(k, {"method": "A", "site": f["site"], "members": []})["members"].append(f)

    # Method B (per site)
    by_site: dict[str, list[dict]] = defaultdict(list)
    for f in failures:
        by_site[f["site"]].append(f)
    for site, fs in by_site.items():
        labels = agglomerative(embed([f["cause"] for f in fs]), thr)
        for f, lab in zip(fs, labels):
            k = f"B|{site}|{lab}"
            f["cluster_b"] = k
            clusters.setdefault(k, {"method": "B", "site": site, "members": []})["members"].append(f)

    db.execute("DELETE FROM clusters")
    for cid, c in clusters.items():
        members = c["members"]
        if c["method"] == "A":
            name = cid.split("|", 2)[2]
        else:
            name = name_cluster(c["site"], [m["cause"] for m in members]) if len(members) >= 2 else members[0]["cause"]
        c.update(name=name, size=len(members), n_tasks=len({m["task_id"] for m in members}))
        db.upsert("clusters", {"cluster_id": cid, "method": c["method"], "site": c["site"], "name": name,
                               "size": c["size"], "n_tasks": c["n_tasks"],
                               "member_run_ids": json.dumps([m["run_id"] for m in members])})
    for f in failures:
        db.execute("UPDATE failures SET cluster_a=?, cluster_b=? WHERE run_id=?",
                   [f["cluster_a"], f["cluster_b"], f["run_id"]])
    return clusters
