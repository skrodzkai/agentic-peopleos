#!/usr/bin/env python3
"""Evals for the operating-review composer. Run: python3 evals/test_operating_review.py

Proves the composer is presentation-only over the engine, the coverage map is honest, and — the
distinguishing feature — the FULL role-scoped, ledger-backed publish gate: an entitled human's
approval is recorded + ledger-verified and publishes; a non-entitled actor is denied + escalated +
refused; the ledger validates in both outcomes.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run  # noqa: E402
from foundation.compute.engine import MetricEngine  # noqa: E402
from core import evidence_bundle as evidence_bundle_core  # noqa: E402

passed = 0
AUTH = {
    "bundle_id": "bundle.operating-review.test",
    "bundle_hash": "sha256:" + "1" * 64,
    "artifacts": [{"artifact_id": "artifact.operating-review.test",
                   "content_hash": "sha256:" + "2" * 64,
                   "evidence_hash": "sha256:" + "3" * 64}],
    "material_claim_ids_hash": "sha256:" + "4" * 64,
}


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


eng = MetricEngine()
report = run.build_report(eng)

# ---- presentation-only over the engine; honest coverage map ----
ok(len(report["ok_ids"]) == 14, "14 curated headline metrics, all computable")
ok(report["results"]["headcount"]["value"] == eng.compute("headcount")["value"], "headline == engine value")
ok(report["results"]["sla_attainment"]["value"] == eng.compute("sla_attainment")["value"], "SLA == engine value")
ok(len(report["coverage"]) == 12, "coverage map spans all 12 domains (incl. business linkage)")
for dom, (okc, tot) in report["coverage"].items():
    ok(0 <= okc <= tot and tot > 0, f"coverage for {dom} is sane")
instrumented = sum(o for o, _ in report["coverage"].values())
ok(instrumented == sum(1 for m in eng._reg_ids if eng.compute(m)["status"] == "ok"),
   "coverage map's instrumented count matches the engine")

page = run.render_html(report)
ok("People Operating Review" in page and "Executive summary" in page, "html carries title + exec summary")
ok("Instrumentation coverage" in page and "metrics.registry.json" in page, "coverage map + registry citation")
ok(run.render_html(report) == run.render_html(run.build_report(MetricEngine())), "rendering is deterministic")

# ---- FULL ledger gate: entitled approver -> approved + recorded + verified ----
_orig = (run.OUT, run.REPORT, run.DIGEST, run.LEDGER, run.BUNDLE)


def _set_out():
    d = Path(tempfile.mkdtemp()) / "output"
    run.OUT, run.REPORT, run.DIGEST = d, d / "r.html", d / "d.md"
    run.LEDGER = d / "decision.events.jsonl"
    run.BUNDLE = d / "review.evidence-bundle.json"


def _types():
    return [__import__("json").loads(l)["type"] for l in run.LEDGER.read_text().splitlines()]


try:
    _set_out()
    commits = []
    decision, acted, violations = run.publish_decision(
        "hr.business-partner", lambda: commits.append(1), AUTH)
    ok(decision == "approved" and acted and violations == [], "entitled approver: approved + ledger verifies clean")
    ok(commits == [1], "the report write (commit) ran exactly once on an entitled publish")
    ok(_types() == ["recommendation", "approval", "action"], "approved ledger is rec -> approval -> action")
    auths = [json.loads(line)["authorization"] for line in run.LEDGER.read_text().splitlines()
             if json.loads(line)["type"] in ("recommendation", "approval", "action")]
    ok(auths == [AUTH, AUTH, AUTH],
       "recommendation -> approval -> action carries one exact evidence authorization")
    ok(run.publish_decision("hr.business-partner", lambda: None, AUTH)[2] == [],
       "ledger re-verifies (deterministic, registry-backed)")
    # the decision ledger ships with a head-count anchor: a suffix-truncated copy fails against it (the same
    # defense visible-handoff ships — a 'ledger-backed governance' example must not be truncatable in silence)
    from core.event_log import validate_log as _vl
    _al = run.LEDGER.with_suffix(run.LEDGER.suffix + ".anchor.json")
    ok(_al.exists(), "an approved publish writes the ledger's head-count anchor sidecar")
    ok(_vl(run.LEDGER, anchor=str(_al)) == [], "the full ledger validates against its committed anchor")
    _tl = run.LEDGER.with_name("truncated.events.jsonl")
    _tl.write_text("\n".join(run.LEDGER.read_text().strip().splitlines()[:-1]) + "\n", encoding="utf-8")
    ok(any("truncat" in v.lower() or "MISMATCH" in v for v in _vl(_tl, anchor=str(_al))),
       "a suffix-truncated decision ledger is CAUGHT by the committed anchor")

    # HIGH-2: the published 'action' is recorded ONLY after the write succeeds. A failing write must
    # leave a truthful rec+approval trail with NO published action (the ledger can never lie).
    _set_out()
    raised = False
    try:
        run.publish_decision("hr.business-partner",
                             lambda: (_ for _ in ()).throw(OSError("disk full")), AUTH)
    except OSError:
        raised = True
    ok(raised, "a failing report write propagates (publish is not silently swallowed)")
    ok(_types() == ["recommendation", "approval"],
       "write failure leaves rec+approval but NO published action (ledger never claims an unwritten publish)")

    _set_out()
    decision, acted, violations = run.publish_decision("obs.engineering", lambda: commits.append(1), AUTH)
    ok(decision == "denied" and not acted and violations == [], "non-entitled approver: denied, no action, ledger still valid")
    ok(_types() == ["recommendation", "approval", "escalation"], "denied ledger is rec -> approval -> escalation")

    # MED-1: an unknown approver id is still recorded (rec + escalation), never silently dropped.
    _set_out()
    decision, acted, violations = run.publish_decision("nobody.unknown", lambda: commits.append(1), AUTH)
    ok(decision == "unknown_actor" and not acted and violations == [], "unknown approver rejected, ledger still verifies")
    ok(_types() == ["recommendation", "escalation"], "unknown-actor ledger records the attempt (rec -> escalation)")

    # ---- main(): draft, publish (entitled/denied/none/unknown/malformed), fail-closed ----
    _set_out(); ok(run.main([]) == 0 and run.REPORT.exists() and run.BUNDLE.exists(),
                   "draft run exits 0 and writes the review plus exact evidence bundle")
    _set_out(); ok(run.main(["--publish"]) == 2 and not run.REPORT.exists(), "publish without approver exits 2")
    _set_out()
    ok(run.main(["--publish", "--approved-by", "hr.business-partner"]) == 0, "publish by entitled approver exits 0")
    ok(run.LEDGER.exists() and run.REPORT.exists() and run.BUNDLE.exists(),
       "published: ledger + report + evidence bundle written")
    _published_events = [json.loads(line) for line in run.LEDGER.read_text().splitlines()]
    _published_auths = [event["authorization"] for event in _published_events
                        if event["type"] in ("recommendation", "approval", "action")]
    ok(len(_published_auths) == 3 and _published_auths[0] == _published_auths[1] == _published_auths[2],
       "published operating review preserves one authorization envelope end to end")
    ok(evidence_bundle_core.authorization_violations(
        evidence_bundle_core.load_bundle(run.BUNDLE), _published_auths[0]) == [],
       "published authorization resolves to the exact committed rendered/evidence bundle")
    _set_out()
    ok(run.main(["--publish", "--approved-by", "obs.engineering"]) == 2, "publish by non-entitled exits 2")
    ok(not run.REPORT.exists(), "refused publish writes no report")
    _set_out()
    ok(run.main(["--publish", "--approved-by", "nobody.unknown"]) == 2, "publish by unknown actor exits 2")
    # MED-2: malformed approver ids (trailing newline / control char / illegal charset) are rejected
    # on the RAW argument before any normalization — no trailing-newline bypass of an entitled id.
    for bad in ("hr.business-partner\n", "hr.business-partner\t", "hr business-partner", "hr.bp; rm -rf"):
        _set_out()
        ok(run.main(["--publish", "--approved-by", bad]) == 2 and not run.REPORT.exists(),
           f"malformed approver {bad!r} rejected, nothing written")

    _set_out()
    _real = run._load_engine
    run._load_engine = lambda: (_ for _ in ()).throw(FileNotFoundError("dataset missing"))
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main([])
        ok(rc == 1 and not run.REPORT.exists(), "engine-unavailable fails closed")
        ok(err.getvalue().strip().startswith("FAIL CLOSED:"), "one clean fail line")
    finally:
        run._load_engine = _real
finally:
    run.OUT, run.REPORT, run.DIGEST, run.LEDGER, run.BUNDLE = _orig

print(f"OK — {passed} operating-review checks passed (14 headline metrics, full ledger gate verified).")
