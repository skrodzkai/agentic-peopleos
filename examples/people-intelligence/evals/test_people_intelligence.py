#!/usr/bin/env python3
"""Evals for the People Intelligence (Executive View) composer.
Run: python3 evals/test_people_intelligence.py

Proves the marquee is presentation-only over the engine, the charts render deterministically and
injection-safe, the SVG <defs> ids don't collide across tiles, the publish gate refuses an invalid
approver, and it fails closed when the engine is unavailable.
"""
import contextlib
import io
import re
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
page = run.render_html(report)
digest = run.render_digest(report)

# ---- presentation-only: headline values equal the engine (the composer does no math) ----
ok(report["results"]["revenue_per_fte"]["value"] == eng.compute("revenue_per_fte")["value"],
   "Revenue/FTE on the dashboard == the engine value")
ok(report["results"]["headcount"]["value"] == eng.compute("headcount")["value"], "headcount == engine value")
ok(report["results"]["regrettable_attrition"]["value"] == eng.compute("regrettable_attrition")["value"],
   "regrettable attrition == engine value")
ok(report["attrition_by_team"] == eng.segment("voluntary_attrition", "job_family"),
   "the attrition-by-team hotspots come straight from engine.segment")
_obm = re.search(r"Out-of-band pay.*?k-val mono'>([0-9]+)%", page, re.S)
ok(_obm and int(_obm.group(1)) == eng.compute("out_of_band_rate")["value"],
   "the out-of-band KPI shows the engine's value (not an agent-summed pair of rounded rates)")
ok(len(report["series"]["revenue_per_fte"]) == 8 and len(report["quarters"]) == 8,
   "the sparkline history is 8 point-in-time quarters")

# ---- the page carries the signature, every tile, the logo, and the registry citation ----
for needle in ["People Intelligence", "logomark", "Revenue / FTE", "Operating leverage",
               "Headcount bridge", "Pay positioning", "Attrition by team", "Org shape",
               "Individual contributors", "Talent grid (9-box)", "Total Rewards", "metric registry"]:
    ok(needle in page, f"dashboard renders '{needle}'")
ok(report["results"]["span_of_control"]["extras"]["by_level"] ==
   eng.compute("span_of_control")["extras"]["by_level"], "the org-shape (by-level mgr/IC split) comes from the engine")
ok("instrumented" in page and "/" in page, "footer reports instrumentation coverage (measured vs defined)")
ok("illustrative SaaS benchmark" in digest and "illustrative SaaS benchmark" in page,
   "both the digest and the page qualify the percentile as an ILLUSTRATIVE benchmark (not a real pctile)")

# ---- no DEI / pay-equity reporting on the flagship (business-operations only) ----
_low = (page + digest).lower()
for term in ("diversity", "pay gap", "gender", "ethnic", "representation", " urm"):
    ok(term not in _low, f"the flagship carries no '{term.strip()}' reporting")

# ---- honest, never silent: a data_pending strip metric shows a placeholder, not omission ----
_engp = MetricEngine()
_rc = _engp.compute
_engp.compute = lambda mid, **k: (
    {"status": "data_pending", "needs": "x", "value": None, "name": "Regrettable attrition", "metric_id": mid}
    if mid == "regrettable_attrition" else _rc(mid, **k))
_page_p = run.render_html(run.build_report(_engp))
ok("Total Rewards" in _page_p and "data_pending" in _page_p,
   "a data_pending strip metric renders 'data_pending' (not a silently missing stat)")
# the digest degrades an optional metric to data_pending too (never a bare 'None%')
_engd = MetricEngine()
_rcd = _engd.compute
_engd.compute = lambda mid, **k: (
    {"status": "data_pending", "needs": "x", "value": None, "name": "Operating leverage", "metric_id": mid}
    if mid == "operating_leverage" else _rcd(mid, **k))
_dig = run.render_digest(run.build_report(_engd))
ok("data_pending" in _dig and "None%" not in _dig, "the digest degrades an optional metric to data_pending, not None%")

# a team name carrying HTML (from engine.segment) is escaped — no injection through the hotspot label
_enge = MetricEngine()
_rse = _enge.segment
_enge.segment = lambda mid, by, **k: (
    {"</span><script>x</script>": 99.0, "Safe": 1.0} if mid == "voluntary_attrition" else _rse(mid, by, **k))
_page_e = run.render_html(run.build_report(_enge))
ok("<script>x</script>" not in _page_e, "a team name containing HTML is escaped (no injection via the hotspot label)")

# a KPI with a MISSING history quarter shows 'trend n/a' — never a faked continuous sparkline
_engs = MetricEngine()
_rsm = _engs.series_multi


def _sm_hole(ids, n=8):
    q, s = _rsm(ids, n)
    s["voluntary_attrition"][3] = None   # punch a hole in one KPI's history
    return q, s


_engs.series_multi = _sm_hole
_page_s = run.render_html(run.build_report(_engs))
ok("trend n/a" in _page_s, "a KPI missing a history quarter renders 'trend n/a' (no silently-dropped points)")

# ---- deterministic: same engine -> identical bytes ----
ok(run.render_html(run.build_report(MetricEngine())) == page, "the dashboard renders deterministically")

# ---- SVG <defs> ids are unique across the whole document (no cross-tile gradient/filter collision) ----
ids = re.findall(r"id='([^']+)'", page)
ok(len(ids) == len(set(ids)), f"no duplicate SVG ids across tiles ({len(ids)} ids, all unique)")

# ---- injection / safety: no script tags, and no raw employee identifiers leaked ----
ok("<script" not in page, "the dashboard contains no <script>")
ok(not re.search(r"\bE-\d{4}\b", page) and not re.search(r"\bC-\d{4}\b", page),
   "no per-person employee/contractor ids appear (aggregate-only)")

# ---- governance: the composer reads a read-only engine; no decisional mutator is reachable ----
for danger in ("change_salary", "change_rating", "terminate"):
    ok(not hasattr(eng, danger), f"the engine the composer reads has no '{danger}' method")

# ---- publish gate + fail-closed via main(), writing to a throwaway output dir ----
_orig = (run.OUT, run.REPORT, run.DIGEST)


def _set_out():
    d = Path(tempfile.mkdtemp()) / "output"
    run.OUT, run.REPORT, run.DIGEST = d, d / "r.html", d / "d.md"


try:
    _set_out(); ok(run.main([]) == 0 and run.REPORT.exists(), "draft run exits 0 and writes the dashboard")
    _set_out(); ok(run.main(["--publish"]) == 2 and not run.REPORT.exists(), "publish without an approver exits 2")
    _set_out()
    ok(run.main(["--publish", "--approved-by", "People Analytics Lead"]) == 0, "valid approver publishes (exit 0)")
    ok((run.OUT / "PUBLISHED.json").exists(), "an approved publish writes the PUBLISHED.json record")
    # a subsequent DRAFT redraw must invalidate the prior approval record (no stale 'approved')
    ok(run.main([]) == 0 and not (run.OUT / "PUBLISHED.json").exists(),
       "a redrawn draft removes the prior PUBLISHED.json (no stale approval)")
    for bad in ("bad\nname", "bad\tname", "x" * 120):
        _set_out()
        ok(run.main(["--publish", "--approved-by", bad]) == 2 and not run.REPORT.exists(),
           f"a malformed approver {bad!r} is refused (exit 2, nothing written)")

    # fail closed when the engine is unavailable
    _set_out()
    _real = run._load_engine
    run._load_engine = lambda: (_ for _ in ()).throw(FileNotFoundError("dataset missing"))
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main([])
        ok(rc == 1 and not run.REPORT.exists(), "engine-unavailable fails closed (exit 1, no report)")
        ok(err.getvalue().strip().startswith("FAIL CLOSED:"), "one clean fail-closed line")
    finally:
        run._load_engine = _real
finally:
    run.OUT, run.REPORT, run.DIGEST = _orig

print(f"OK — {passed} people-intelligence checks passed "
      f"(Rev/FTE ${round(report['results']['revenue_per_fte']['value']/1000)}K, "
      f"{len(ids)} unique SVG ids).")
