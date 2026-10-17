import os
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Point the pipeline at a throwaway data dir with the fake provider."""
    c = yaml.safe_load((ROOT / "config.yaml").read_text())
    for role in c["models"]:
        c["models"][role] = {"provider": "fake", "name": "fake"}
    c["embeddings"]["provider"] = "tfidf"
    c["action_delay_s"] = [0.0, 0.0]
    for k, v in c["paths"].items():
        c["paths"][k] = str(tmp_path / v)
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(c))
    monkeypatch.setenv("EXPERIMENT_CONFIG", str(p))
    import src.config as config
    import src.db as db

    monkeypatch.setattr(config, "_CONFIG", None)
    monkeypatch.setattr(db, "_conn", None)
    return c
