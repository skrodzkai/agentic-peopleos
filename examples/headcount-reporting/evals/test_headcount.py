#!/usr/bin/env python3
"""Evals for the headcount-reporting agent. Run: python3 evals/test_headcount.py

Proves the agent is presentation-only over the engine (every rendered number equals the engine's),
surfaces data_pending honestly, never emits a decisional instruction, fails closed, and the publish
gate behaves. Plain stdlib asserts.
"""
import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run  # noqa: E402
from foundation.compute.engine import MetricEngine  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


eng = MetricEngine()
report = run.build_report(eng)

# ---- registry-bound: every declared metric resolves (none unknown); 9 ok / 5 pending ----
ok(all(report["results"][m]["status"] in ("ok", "data_pending") for m in run.METRIC_IDS),
   "every declared metric resolves to ok or data_pending (none unknown)")
ok(len(report["ok_ids"]) == 9 and len(report["pending"]) == 5, "9 computed, 5 data_pending")
ok(all(p["needs"] for p in report["pending"]), "every data_pending metric names its missing source")

# ---- presentation-only: rendered KPIs equal the engine's values (the agent did no math) ----
cards = {c["label"]: c["value"] for c in report["cards"]}
ok(cards["Employees"] == eng.compute("headcount")["value"], "headcount card == engine value")
ok(cards["FTE"] == eng.compute("fte")["value"], "fte card == engine value")
ok(cards["Avg span of control"] == eng.compute("span_of_control")["extras"]["mean"], "span card == engine value")

# ---- reconciliation surfaces in the agent's model ----
ng = report["results"]["net_headcount_growth"]["extras"]
ok(ng["ending"] - ng["beginning"] == report["results"]["net_headcount_growth"]["value"],
   "net-growth bridge reconciles (ending - beginning)")
ok(ng["bridge_reconciles"] is True, "engine bridge reconciles")
# consistency invariant (round-A fix): the headcount KPI equals the bridge ending
ok(cards["Employees"] == ng["ending"], "headcount KPI == bridge ending (same snapshot population)")
# the narrator's exit total comes from the engine, not agent-side addition
ok(ng["total_exits"] == ng["voluntary_exits"] + ng["involuntary_exits"], "engine exposes total_exits")

# ---- HTML + digest carry brand, narrator, citation, draft gate ----
page = run.render_html(report)
ok("Agentic People" in page and "Workforce Report" in page, "html carries brand + title")
ok("What needs attention" in page and report["narrative"][:20] in page, "html carries the narrator")
ok("metrics.registry.json" in page and "Metric definitions" in page, "html cites the registry")
ok("Draft" in page and "Coverage" in page, "html carries draft badge + honest coverage section")
ok("Publish gate" in run.render_digest(report), "digest carries the publish gate line")

# ---- the agent never emits a decisional instruction ----
low = (page + run.render_digest(report)).lower()
for banned in ("change salary", "recommend a raise", "set salary to", "terminate ", "make hiring decision"):
    ok(banned not in low, f"output never says '{banned}'")

# ---- determinism ----
ok(run.render_html(report) == run.render_html(run.build_report(MetricEngine())), "rendering is deterministic")

# ---- main(): publish gate, side effects, fail-closed (redirected to temp) ----
_orig = (run.OUT, run.REPORT, run.DIGEST)


def _set_out():
    d = Path(tempfile.mkdtemp()) / "output"
    run.OUT, run.REPORT, run.DIGEST = d, d / "r.html", d / "d.md"


try:
    _set_out()
    ok(run.main([]) == 0, "draft run exits 0")
    ok(run.REPORT.exists() and run.DIGEST.exists(), "draft writes report + digest")

    _set_out()
    ok(run.main(["--publish"]) == 2, "publish without approver exits 2")
    ok(not run.REPORT.exists(), "publish gate refuses before writing")

    _set_out()
    ok(run.main(["--publish", "--approved-by", "People Analytics Lead"]) == 0, "publish with approver exits 0")
    import json as _json
    ok((run.OUT / "PUBLISHED.json").exists()
       and _json.loads((run.OUT / "PUBLISHED.json").read_text())["scope"] == run.SCOPE,
       "approval recorded as structured JSON with the scope")

    _set_out()
    ok(run.main(["--publish", "--approved-by", "Bad\n- inject"]) == 2, "approver with control chars refused")
    _set_out()
    ok(run.main(["--publish", "--approved-by", "Valid Name\n"]) == 2, "trailing-newline approver refused")

    # fail-closed: engine unavailable -> exit 1, no report, one clean line, no traceback
    _set_out()
    _real = run._load_engine
    run._load_engine = lambda: (_ for _ in ()).throw(FileNotFoundError("dataset missing"))
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main([])
        ok(rc == 1, "engine-unavailable fails closed (exit 1)")
        ok(not run.REPORT.exists(), "fail-closed writes no report")
        msg = err.getvalue().strip()
        ok(msg.startswith("FAIL CLOSED:") and "Traceback" not in msg and "\n" not in msg,
           "fail-closed prints one clean line, no traceback")
    finally:
        run._load_engine = _real
finally:
    run.OUT, run.REPORT, run.DIGEST = _orig

print(f"OK — {passed} headcount-reporting checks passed "
      f"({len(report['ok_ids'])} computed, {len(report['pending'])} data_pending).")
