"""M6: regenerate out/report.md, results.json, slide_numbers.md and charts from the DB (no network)."""

import _common  # noqa: F401

from src.config import path
from src.report import make_report

make_report()
print(f"Wrote {path('out')}/report.md, results.json, slide_numbers.md, charts/\n")
print((path("out") / "slide_numbers.md").read_text())
