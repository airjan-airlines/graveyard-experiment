"""Judge any un-judged runs (all conditions, or one with --condition)."""

import argparse

import _common  # noqa: F401

from src.judge import judge_pending

ap = argparse.ArgumentParser()
ap.add_argument("--condition")
print(f"Judged {judge_pending(ap.parse_args().condition)} runs.")
