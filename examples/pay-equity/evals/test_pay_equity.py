#!/usr/bin/env python3
"""Evals for the pay-equity agent: render invariants, honest labeling, no-PII, fail-closed, publish gate."""
import copy
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[2]))
import run  # noqa: E402
from foundation.compute import pay_equity as PE  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


result = PE.compute()
report = run.build_report(result)
html = run.render_html(report)
digest = run.render_digest(report)

# -- the view is coherent + renders --
ok("Pay-Equity Assessment" in html and "Pay Equity" in html, "the title + kicker render")
ok(html.count("<svg") >= 2, "both forest plots (gender + ethnicity) render as SVG")
ok("parity (0%)" in html, "the forest plot draws the parity reference")
ok("joint-assessment screen" in html and "Article 10" in html, "the EU 5% screen + its legal basis render")
ok("▸" in html, "the EU table names advantaged▸disadvantaged groups per category")
h = result["headline"]
ok(f"{h['unadjusted_median_gap_pct']:.1f}%" in html, "the raw median gap headline number renders")
ok("pay-equity" in digest.lower() or "Pay-equity" in digest, "the digest names the arm")
# the EU trigger surfaced by the engine is reflected in the render
if report["eu"]["joint_assessment_required"]:
    ok("assessment" in html.lower() and "Indicated" in html, "a triggered joint assessment is shown (as a screen flag)")

# -- honest labeling --
low = (html + digest).lower()
for phrase in ("illustrative", "pseudonymised", "not legal advice", "base only", "observable controls only"):
    ok(phrase in low, f"the dashboard/digest states '{phrase}' (honest about what this is)")
ok("synthetic" in low, "the artifacts label the data synthetic")

# -- no PII / injection: no email/SSN, no per-person ids, no <script> --
ok(not re.search(r"[\w.]+@[\w.]+\.[a-z]{2,}", html) and "SSN" not in html, "no email/SSN in the dashboard")
ok(not re.search(r"\bE-\d{4}\b", html), "no per-employee ids appear")
ok("<script" not in html, "no <script> in the dashboard")

# -- fail-closed: a self-contradictory engine result is refused before it can render a false number --
def _bad(mut, why):
    global passed
    r = copy.deepcopy(result)
    mut(r)
    try:
        run.build_report(r)
        assert False, f"FAILED (no raise): build_report rejects {why}"
    except run.ReportError:
        passed += 1


def _eu(r):
    return next(d for d in r["dimensions"] if d["key"] == "gender_group")["eu_pay_transparency"]


def _flip_eu_flag(r):
    c = next(c for c in _eu(r)["categories"] if c.get("assessable"))
    c["exceeds_threshold"] = not c["exceeds_threshold"]


def _flip_joint(r):
    eu = next(d for d in r["dimensions"] if d["key"] == "gender_group")["eu_pay_transparency"]
    eu["joint_assessment_required"] = not eu["joint_assessment_required"]


def _nan_eu(r):
    eu = next(d for d in r["dimensions"] if d["key"] == "gender_group")["eu_pay_transparency"]
    next(c for c in eu["categories"] if c.get("assessable"))["mean_gap_pct"] = float("nan")


_bad(lambda r: r["dimensions"][0]["unadjusted"]["groups"][0].update(n=999999),
     "group counts that don't partition the population")
_bad(lambda r: r["dimensions"][0]["adjusted"]["groups"][0].update(adjusted_gap_pct=float("nan")),
     "a non-finite adjusted gap")
_bad(lambda r: r["dimensions"][0]["unadjusted"]["groups"][1].update(mean_gap_pct=float("inf")),
     "a non-finite RAW mean gap (every rendered number is finite-checked, not just adjusted)")
_bad(_nan_eu, "a non-finite EU category mean gap")
_bad(lambda r: r["headline"].update(unadjusted_median_gap_pct=float("nan")), "a non-finite headline gap")
_bad(lambda r: r["dimensions"][0]["adjusted"]["groups"][0].update(ci_lo_pct=99.0),
     "a CI that doesn't bracket its point estimate")
_bad(lambda r: r["dimensions"][0]["adjusted"].update(r2=2.0), "an out-of-range R^2")
_bad(_flip_eu_flag, "an EU category flag inconsistent with its own mean gap vs 5%")
_bad(_flip_joint, "a joint-assessment flag inconsistent with the flagged-category count")
_bad(lambda r: r.pop("headline"), "a result missing the headline block")
# the rendered COUNTERS + model n are validated too (not just the gap fields)
_bad(lambda r: _eu(r).update(n_flagged=_eu(r)["n_flagged"] + 5), "an EU n_flagged that disagrees with the category list")
_bad(lambda r: _eu(r).update(n_categories=999), "an EU n_categories that disagrees with the category list")
_bad(lambda r: r["dimensions"][0]["adjusted"].update(n=float("inf")), "a non-finite / non-int adjusted model n")

# -- publish gate --
ok(run.main(["--publish"]) == 2, "publish without an approver is refused (rc 2)")
ok(run.main(["--publish", "--approved-by", "  "]) == 2, "a blank approver is refused (rc 2)")
ok(run.main(["--publish", "--approved-by", "Bad\nName"]) == 2, "a control-char approver is refused (rc 2)")

# -- determinism --
ok(run.render_html(run.build_report(PE.compute())) == html, "the dashboard renders deterministically")

print(f"OK — {passed} pay-equity agent checks passed "
      f"(raw median gap {h['unadjusted_median_gap_pct']:.1f}% -> adjusted {h['adjusted_gap_pct']:+.1f}%; "
      f"EU flagged {report['eu']['n_flagged']} category).")
