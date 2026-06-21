#!/usr/bin/env python3
"""Evals for the attrition-reporting agent. Run: python evals/test_attrition.py"""
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

ok(all(report["results"][m]["status"] in ("ok", "data_pending") for m in run.METRIC_IDS),
   "every declared metric resolves (none unknown)")
ok(len(report["ok_ids"]) == 7 and len(report["pending"]) == 2, "7 computed, 2 data_pending")
ok(all(p["needs"] for p in report["pending"]), "every data_pending metric names its source")

# presentation-only: KPI cards equal the engine's values
cards = {c["label"]: c["value"] for c in report["cards"]}
ok(cards["Voluntary attrition · ann."] == f"{eng.compute('voluntary_attrition')['value']}%",
   "voluntary card == engine value")
ok(cards["Total turnover · ann."] == f"{eng.compute('total_turnover_rate')['value']}%",
   "total turnover card == engine value")
# turnover splits reconcile (voluntary + involuntary == total exits)
v = eng.compute("voluntary_attrition")["extras"]["voluntary_exits"]
iv = eng.compute("involuntary_turnover_rate")["extras"]["involuntary_exits"]
tot = eng.compute("total_turnover_rate")["extras"]["all_exits"]
ok(v + iv == tot, "voluntary + involuntary exits == total exits")
# segment hotspot is the max of the engine's by-level segmentation
ok(report["hotspot"][1] == max(report["seg_level"].values()), "hotspot is the engine's max segment")

page, digest = run.render_html(report), run.render_digest(report)
ok("Agentic People" in page and "Attrition Report" in page, "html carries brand + title")
ok("What needs attention" in page and "Annualization: simple" in page, "html carries narrator + annualization note")
ok("metrics.registry.json" in page and "Coverage" in page, "html cites registry + honest coverage")
ok("Publish gate" in digest, "digest carries publish gate")
low = (page + digest).lower()
for banned in ("recommend_termination", "recommend a raise", "change salary", "terminate "):
    ok(banned not in low, f"output never says '{banned}'")
ok(run.render_html(report) == run.render_html(run.build_report(MetricEngine())), "rendering is deterministic")

# main(): publish gate + fail-closed
_orig = (run.OUT, run.REPORT, run.DIGEST)


def _set_out():
    d = Path(tempfile.mkdtemp()) / "output"
    run.OUT, run.REPORT, run.DIGEST = d, d / "r.html", d / "d.md"


try:
    _set_out(); ok(run.main([]) == 0 and run.REPORT.exists(), "draft run exits 0 and writes")
    _set_out(); ok(run.main(["--publish"]) == 2 and not run.REPORT.exists(), "publish without approver exits 2, no write")
    _set_out(); ok(run.main(["--publish", "--approved-by", "PA Lead"]) == 0 and (run.OUT / "PUBLISHED.json").exists(),
                   "publish with approver exits 0 + JSON record")
    _set_out(); ok(run.main(["--publish", "--approved-by", "Bad\n- x"]) == 2, "control-char approver refused")
    _set_out(); ok(run.main(["--publish", "--approved-by", "Valid Name\n"]) == 2, "trailing-newline approver refused")
    _set_out()
    _real = run._load_engine
    run._load_engine = lambda: (_ for _ in ()).throw(FileNotFoundError("dataset missing"))
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main([])
        msg = err.getvalue().strip()
        ok(rc == 1 and not run.REPORT.exists(), "engine-unavailable fails closed, no report")
        ok(msg.startswith("FAIL CLOSED:") and "Traceback" not in msg and "\n" not in msg, "one clean fail line")
    finally:
        run._load_engine = _real
finally:
    run.OUT, run.REPORT, run.DIGEST = _orig

print(f"OK — {passed} attrition-reporting checks passed "
      f"({len(report['ok_ids'])} computed, {len(report['pending'])} data_pending).")
