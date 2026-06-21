#!/usr/bin/env python3
"""Evals for the shared dashboard renderer. Run: python foundation/render/tests/test_dashboard.py

Proves the renderer is well-formed, escapes ALL interpolated values (including dataset-derived
strings), cites the registry, surfaces data_pending honestly, and is deterministic.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.render import dashboard as d  # noqa: E402
from core.metrics import MetricRegistry  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


reg = MetricRegistry.load()

# ---- page wrapper ----
body = d.brand_header() + d.title_block("Workforce", "Headcount Report", "Acme Corp · synthetic")
doc = d.page("Acme — Headcount", body)
ok(doc.startswith("<!doctype html>") and doc.rstrip().endswith("</html>"), "page is a full HTML document")
ok("<style>" in doc and "--cyan:#1ba7ff" in doc, "page carries the skrodzkai dark style")
ok("Agentic People" in doc and "Headcount Report" in doc, "page carries brand + title")

# ---- KPI cards + tones ----
cards = d.kpi_cards([{"value": 202, "label": "Employees"},
                     {"value": "0.99", "label": "Avg compa", "tone": "good"},
                     {"value": 2, "label": "Out of band", "tone": "bad"}])
ok(cards.count("class='metric") == 3, "three KPI cards rendered")
ok("metric bad" in cards and "metric good" in cards, "tones map to styles")

# ---- bars + table ----
ok("width:" in d.bars([{"label": "L5", "value": 5, "max": 10}]), "bars compute a width")
tbl = d.data_table(["Level", "Count"], [["L5", 12]])
ok("<th" in tbl and "<td" in tbl and "L5" in tbl, "data table renders")

# ---- ESCAPING: every interpolated value is escaped (no raw injection) ----
evil = "</style><script>x</script>|<b>"
ok("<script>" not in d.kpi_cards([{"value": evil, "label": evil}]), "kpi values are escaped")
ok("<script>" not in d.data_table(["h"], [[evil]]), "table cells are escaped")
ok("<script>" not in d.narrator(evil), "narrator text is escaped")
ok("<script>" not in d.bars([{"label": evil, "value": 1, "max": 2}]), "bar labels are escaped")
ok("<script>" not in d.data_pending_block([{"name": evil, "needs": evil}]), "pending items are escaped")
# (round 9) a color is a CSS-context sink — a malicious value must NOT survive into the style attr,
# and the width is clamped so it can't exceed the track.
_evilbar = d.bars([{"label": "x", "value": 1, "max": 2, "color": "red;width:999%;background:url(x)"}])
ok("url(" not in _evilbar and "999%" not in _evilbar, "a CSS-injection color is rejected (whitelisted)")
ok("background:var(--cyan)" in _evilbar, "an unsafe color falls back to the cyan accent")
ok("width:100%" in d.bars([{"label": "x", "value": 50, "max": 10}]), "bar width is clamped to 100%")
ok("background:var(--green)" in d.bars([{"label": "x", "value": 1, "max": 2, "color": "var(--green)"}]),
   "a legitimate CSS var color passes through")
ok("background:#ff4d4f" in d.bars([{"label": "x", "value": 1, "max": 2, "color": "#ff4d4f"}]),
   "a #hex color passes through")
# brand discipline: even a harmless bare color word is rejected (tokens/hex only)
ok("background:var(--cyan)" in d.bars([{"label": "x", "value": 1, "max": 2, "color": "rebeccapurple"}]),
   "an arbitrary named color falls back to the brand accent (tokens/hex only)")

# ---- metric_definitions cites the registry and never leaks a forbidden action ----
defs = d.metric_definitions(reg, ["compa_ratio", "span_of_control"])
ok("metrics.registry.json" in defs and "Compa-ratio" in defs, "definitions cite the registry")
ok("change_salary" not in defs and "recommend_pay_change" not in defs, "definitions never print a forbidden action")

# ---- data_pending is honest (and empty -> nothing) ----
ok(d.data_pending_block([]) == "", "no pending block when nothing is pending")
pend = d.data_pending_block([{"name": "Vacancy rate", "needs": "approved-positions table"}])
ok("Vacancy rate" in pend and "approved-positions table" in pend, "pending names its missing source")

# ---- governance footer ----
ok("human owns every decision" in d.governance_footer("headcount-reporting"), "footer states human-in-the-loop")

# ---- determinism: same inputs -> identical HTML ----
ok(d.page("t", body) == d.page("t", body), "rendering is deterministic")

print(f"OK — {passed} dashboard-renderer checks passed.")
