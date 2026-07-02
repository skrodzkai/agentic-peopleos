#!/usr/bin/env python3
"""Evals for the people-ops-reporting agent. Run: python3 evals/test_people_ops.py"""
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
ok(len(report["ok_ids"]) == 7 and report["pending_count"] == 10, "7 computed, 10 data_pending")
# pending grouped across the 4 routed domains
ok(set(report["pending_by_domain"]) >= {"health_safety", "compliance_ethics", "learning_development"},
   "pending coverage is grouped by domain")
ok(all(it["needs"] for items in report["pending_by_domain"].values() for it in items),
   "every data_pending metric names its source")

# presentation-only: cards equal engine values
cards = {c["label"]: c["value"] for c in report["cards"]}
ok(cards["SLA attainment"] == f"{eng.compute('sla_attainment')['value']}%", "SLA card == engine value")
ok(any(c["value"] == eng.compute("case_volume")["value"] for c in report["cards"]), "case volume card == engine")
# SLA denominator includes open-and-breached (recomputed from timestamps by the engine)
sla = eng.compute("sla_attainment")["extras"]
ok("open_past_sla" in sla, "SLA denominator includes open-and-breached")
# backlog age buckets reconcile to the open count
bk = eng.compute("open_case_backlog")
ok(sum(bk["extras"]["by_age"].values()) == bk["value"], "backlog age buckets reconcile to open count")

page, digest = run.render_html(report), run.render_digest(report)
ok("People Ops Service Desk" in page, "dashboard titled 'People Ops Service Desk'")
ok("What needs attention" in page and "metrics.registry.json" in page, "narrator + registry citation")
ok("Health &amp; Safety" in page and "Compliance &amp; Ethics" in page and "Learning &amp; Development" in page,
   "per-domain honest coverage sections present (titles HTML-escaped)")
ok("Publish gate" in digest, "digest carries publish gate")
# the coverage sentence is built from the engine's actual pending domains (incl. People Ops
# self-service deflection) — not a hardcoded list that could drift from the dashboard.
ok("People Ops" in digest and "Learning & Development" in digest,
   "digest coverage names the pending domains dynamically (People Ops self-service included)")
ok("alter_record" not in (page + digest).lower(), "output emits no decisional/forbidden action")
ok(run.render_html(report) == run.render_html(run.build_report(MetricEngine())), "rendering is deterministic")

# main(): publish gate + fail-closed
_orig = (run.OUT, run.REPORT, run.DIGEST)


def _set_out():
    d = Path(tempfile.mkdtemp()) / "output"
    run.OUT, run.REPORT, run.DIGEST = d, d / "r.html", d / "d.md"


try:
    _set_out(); ok(run.main([]) == 0 and run.REPORT.exists(), "draft run exits 0 and writes")
    _set_out(); ok(run.main(["--publish"]) == 2 and not run.REPORT.exists(), "publish without approver exits 2")
    _set_out(); ok(run.main(["--publish", "--approved-by", "People Ops Lead"]) == 0
                   and (run.OUT / "PUBLISHED.json").exists(), "publish with approver exits 0 + JSON")
    _set_out(); ok(run.main(["--publish", "--approved-by", "x\n- y"]) == 2, "control-char approver refused")
    _set_out(); ok(run.main(["--publish", "--approved-by", "Valid Name\n"]) == 2, "trailing-newline approver refused")
    _set_out()
    _real = run._load_engine
    run._load_engine = lambda: (_ for _ in ()).throw(FileNotFoundError("dataset missing"))
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main([])
        msg = err.getvalue().strip()
        ok(rc == 1 and not run.REPORT.exists(), "engine-unavailable fails closed")
        ok(msg.startswith("FAIL CLOSED:") and "Traceback" not in msg and "\n" not in msg, "one clean fail line")
    finally:
        run._load_engine = _real
finally:
    run.OUT, run.REPORT, run.DIGEST = _orig

print(f"OK — {passed} people-ops-reporting checks passed "
      f"({len(report['ok_ids'])} computed, {report['pending_count']} data_pending).")
