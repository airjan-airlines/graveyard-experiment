from src.analyze.cluster import method_a_key, normalize_page_context
from src.metrics import paired_bootstrap, wilson


def test_wilson_bounds():
    w = wilson(5, 10)
    assert w["p"] == 0.5 and 0.2 < w["lo"] < 0.5 < w["hi"] < 0.8
    assert wilson(0, 0)["p"] is None
    assert wilson(0, 10)["lo"] == 0.0


def test_paired_bootstrap_detects_shift():
    a = {f"t{i}": [0, 0, 1] for i in range(20)}
    b = {f"t{i}": [1, 1, 1] for i in range(20)}
    r = paired_bootstrap(a, b, n=2000)
    assert abs(r["diff"] - 2 / 3) < 1e-9 and r["lo"] > 0.5


def test_cluster_keys():
    assert normalize_page_context("https://www.usps.com/manage/po-locator.htm?x=1") == "/manage/po-locator.htm"
    f = {"site": "s", "failure_category": "ui_interaction", "ui_pattern": "Date Picker", "page_context": "/x"}
    assert method_a_key(f) == "A|s|ui_interaction|ui:date_picker"
    f["ui_pattern"] = None
    assert method_a_key(f) == "A|s|ui_interaction|page:/x"
