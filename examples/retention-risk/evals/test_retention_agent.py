#!/usr/bin/env python3
"""Evals for the Retention Risk (Committee View) composer.
Run: python3 evals/test_retention_agent.py

Proves the marquee is presentation-only over the retention engine (segment/company numbers equal the
engine's), renders the honesty rails (guardrails, no-skill baselines, the model-vs-observed gaps, the
decoy readout, the UNCHECKED fairness boxes), leaks no per-employee identifier or country-level region,
draws deterministically and injection-safe, refuses an invalid publish approver, and fails closed when
the engine is unavailable or the realism guard trips.
"""
import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run  # noqa: E402
from foundation.compute import retention as R  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


report = run.build_report()
page = run.render_html(report)
digest = run.render_digest(report)

# ---- independent engine recompute (the composer must equal the engine — it does no math) ----
rows = R.load_panel()
design = R.build_design(rows)
slices = R.temporal_slices(design)
model, cal, bands = R.model_from_manifest()
company = R.company_risk(rows, model, cal, design, slices, horizon=run.HORIZON)
segs = R.segment_risk(rows, model, cal, design, slices)
recon = R.reconciliation_summary(segs)
metrics = R.evaluate(model, cal, design, slices)

ok(report["company"] == company, "company risk on the dashboard == engine company_risk (no agent math)")
ok(report["recon"] == recon, "reconciliation summary == engine reconciliation_summary")
ok(report["metrics"]["roc_auc"] == metrics["roc_auc"] and report["metrics"]["pr_auc"] == metrics["pr_auc"],
   "the headline discrimination metrics == the engine's evaluate()")
below = {s["value"]: s for s in segs["comp_position_band"] if not s.get("suppressed")}["below"]
ok(run._pct(below["bottom_up_6mo"]) in page and run._pct(below["top_down_6mo"]) in page,
   "the below-band model + observed risks on the page == the engine's segment_risk")
ok(run._pct(company["top_down"]) in page and run._pct(company["bottom_up"]) in page,
   "the beacon shows BOTH the observed and the model company risk (leads with observed, badges the model)")

# ---- the published model is reconstructed from the manifest, not re-fit (fast render path) ----
m2, c2, b2 = R.model_from_manifest()
ok(m2["coef"] == model["coef"] and c2 == cal, "model_from_manifest is deterministic (the pinned artifact)")
ok(R.evaluate(m2, c2, design, slices)["roc_auc"] == metrics["roc_auc"],
   "the manifest-reconstructed model reproduces the committed evaluation exactly")

# ---- every panel + the five reading rails render ----
for needle in ["Workforce Planning — Retention Risk", "logomark",
               "SEGMENT-FIRST", "PLANNING SIGNAL", "SYNTHETIC DATA",
               "FAIRNESS: NOT YET VALIDATED", "VOLUNTARY EXITS ONLY",
               "the spread is the story", "Risk by comp position", "Risk by function",
               "Segment ledger", "Why people leave", "Trust panel", "Survival outlook",
               "Support tiers", "planning levers", "Fairness — NOT YET VALIDATED",
               "retention-risk-model-card.md"]:
    ok(needle in page, f"dashboard renders '{needle}'")

# ---- honesty rails are the visual system, not a footnote ----
ok(f"{metrics['base_rate']:.3f}" in page, "the no-skill PR-AUC baseline (base rate) is printed next to the metric")
ok("no-skill" in page and "0.500" in page, "the trust panel shows the no-skill anchor for every metric")
ok("guard fails" in page and f"{R.REALISM_AUC_MAX:g}" in page,
   "the realism-guard ceiling is printed on the ROC KPI (a better-looking number would fail the build)")
decoy_rank = sorted(i + 1 for i, (n, _) in enumerate(report["coef_ranked"]) if n in R.DECOY_FEATURES)[0]
ok(f"#{decoy_rank}" in page, "the decoy-feature rank readout appears (the legible leakage tripwire)")
ok("[ ]" in page and "[x]" in page, "the fairness panel ships a VISIBLY UNCHECKED checklist")
ok("associational" in page.lower() and "not causal" in page.lower(),
   "the driver panel is labeled associational, not causal")

# ---- segment-first: no per-employee identifier, no country-level region ----
ok(not re.search(r"\bR-\d{4}\b", page) and "emp_id" not in page,
   "no per-person employee id appears on the dashboard (segment-level only)")
ok("Americas" in page and "EMEA" in page and "APAC" in page, "regions are the broad bands")
for country in ("United States", "Germany", "India", "France", "Japan", "Ireland"):
    ok(country not in page, f"no country-level region leaks ('{country}')")

# ---- a suppressed segment (if any) carries NO estimate, only a coarse size band ----
for dim_segs in segs.values():
    for s in dim_segs:
        if s.get("suppressed"):
            ok("bottom_up_6mo" not in s and "size_band" in s, "a suppressed segment carries a size band, never an estimate")

# ---- adversarial: a malicious segment label cannot inject markup into the HTML or the Markdown digest ----
# Today's synthetic labels are metacharacter-light; escaping must be structural, not argmax-dependent — so we
# force a hostile label into the segments the narrative / footnote / digest read and assert nothing survives raw.
_evil = "</b></span><script>alert(1)</script>[x](javascript:1)"
_er = run.build_report()
_er["top_tenure"] = dict(_er["top_tenure"], value=_evil)
_er["worst"] = {"dim": _er["worst"]["dim"], "seg": dict(_er["worst"]["seg"], value=_evil)}
for _dim in _er["segments"]:                                  # poison the highest-risk rendered segment (digest "Where")
    for _s in _er["segments"][_dim]:
        if not _s.get("suppressed"):
            _s["value"] = _evil
            break
_epage, _edigest = run.render_html(_er), run.render_digest(_er)
ok("<script>" not in _epage, "a hostile segment label cannot inject <script> into the dashboard HTML")
ok("<script>" not in _edigest and "](javascript:" not in _edigest,
   "a hostile segment label cannot inject an HTML tag or an active link into the Markdown digest")
# a hostile BARE URL in a segment label must not become a clickable GFM autolink in the digest
_eu = run.build_report()
_eu["top_tenure"] = dict(_eu["top_tenure"], value="https://evil.example/phish")
ok("https://evil.example" not in run.render_digest(_eu),
   "a bare URL in a segment label is neutralized in the digest (no clickable autolink)")

# ---- injection / determinism / unique ids ----
ok("<script" not in page, "the dashboard contains no <script>")
ids = re.findall(r"id='([^']+)'", page)
ok(len(ids) == len(set(ids)), f"no duplicate SVG ids across tiles ({len(ids)} ids, all unique)")
ok(run.render_html(run.build_report()) == page, "the dashboard renders deterministically (identical bytes)")
ok(run.render_digest(run.build_report()) == digest, "the digest renders deterministically")

# ---- the engine the composer reads exposes no decisional mutator ----
for danger in ("change_salary", "change_rating", "terminate", "score_employee"):
    ok(not hasattr(R, danger), f"the retention engine has no '{danger}' entry point")

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
    ok(run.main([]) == 0 and not (run.OUT / "PUBLISHED.json").exists(),
       "a redrawn draft removes the prior PUBLISHED.json (no stale approval)")
    for bad in ("bad\nname", "bad\tname", "x" * 120):
        _set_out()
        ok(run.main(["--publish", "--approved-by", bad]) == 2 and not run.REPORT.exists(),
           f"a malformed approver {bad!r} is refused (exit 2, nothing written)")

    # a REFUSED publish must invalidate any prior approval marker — no stale 'approved' state may survive
    _set_out()
    run.OUT.mkdir(parents=True, exist_ok=True)
    (run.OUT / "PUBLISHED.json").write_text('{"approved_by": "stale"}\n', encoding="utf-8")
    ok(run.main(["--publish", "--approved-by", "bad\nname"]) == 2
       and not (run.OUT / "PUBLISHED.json").exists(),
       "a refused publish clears a pre-existing PUBLISHED.json (no stale approval marker survives)")

    # fail closed when the panel/engine is unavailable
    _set_out()
    _real_load = R.load_panel
    R.load_panel = lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("panel missing"))
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main([])
        ok(rc == 1 and not run.REPORT.exists(), "panel-unavailable fails closed (exit 1, no report)")
        ok(err.getvalue().strip().startswith("FAIL CLOSED:"), "one clean fail-closed line")
    finally:
        R.load_panel = _real_load

    # fail closed when the realism guard trips (an implausibly-perfect model is treated as leakage)
    _set_out()
    _real_guard = R.realism_guard
    R.realism_guard = lambda *a, **k: (_ for _ in ()).throw(R.ModelError("realism guard tripped"))
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main([])
        ok(rc == 1 and not run.REPORT.exists(), "a tripped realism guard fails closed (exit 1, no report)")
    finally:
        R.realism_guard = _real_guard
finally:
    run.OUT, run.REPORT, run.DIGEST = _orig

print(f"OK — {passed} retention-risk checks passed "
      f"(6-mo risk model {run._pct(company['bottom_up'])} / obs {run._pct(company['top_down'])}, "
      f"{recon['n_segments']} segments, {recon['n_flagged']} flagged, {len(ids)} unique SVG ids).")
