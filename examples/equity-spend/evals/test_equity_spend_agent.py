#!/usr/bin/env python3
"""Evals for the equity-spend board agent: validation, render invariants, honest labeling, fail-closed, gate."""
import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[2]))
import run  # noqa: E402
from foundation.compute import equity_spend as E  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


result = E.compute()
report = run.build_report(result)
html = run.render_html(report)
digest = run.render_digest(report)

# -- the board view is coherent + renders --
ok(report["verdict"].startswith(("DEFENSIBLE", "WATCH")), "a board verdict is produced")
ok(html.count("<svg") >= 5, "the dashboard has all its charts (>=5 SVGs)")
ok("Board headline" in html and "Company-Wide Equity Spend" in html, "the board headline + title render")
ok("VABR" in html and f"{report['r']['vabr_3yr_pct']:.2f}%" in html, "the 3-yr VABR headline number renders")
ok("equity-spend" in digest.lower() or "Equity Spend" in digest, "the digest names the arm")

# -- honest labeling: illustrative, never claimed as advisor output; no real advisor named as the SOURCE --
low = html.lower()
ok("illustrative" in low, "the dashboard labels benchmark/EPSC/SVT as illustrative")
ok("not iss" in low or "not glass lewis or iss" in low or "not iss output" in low or "not glass lewis" in low,
   "the dashboard states the figures are NOT ISS/Glass Lewis output")
ok("illustrative" in digest.lower(), "the digest carries the illustrative disclaimer")

# -- no real person names / PII leak into the rendered output (company-wide plan uses synthetic ids only) --
import re as _re  # noqa: E402
ok(not _re.search(r"[\w.]+@[\w.]+\.[a-z]{2,}", html) and "SSN" not in html,
   "no email/SSN in the rendered dashboard")

# -- fail-closed: a self-contradictory engine result is refused before it can render a false board number --
for mut, why in [
    (lambda r: r.update(market_cap=r["market_cap"] * 2), "market-cap identity broken (CSO x price mismatch)"),
    (lambda r: r.update(vabr_3yr_pct=-1.0), "a negative 3-yr VABR"),
    (lambda r: r["epsc"]["grant_practices"].update(source_note="ISS 2025 official cap"),
     "a benchmark note that drops 'illustrative'"),
    (lambda r: r["value_per_fte_by_group"].pop("ceo", None), "the CEO grant group missing"),
    (lambda r: r["epsc"].update(features_total=7), "an impossible EPSC feature count"),
]:
    bad = copy.deepcopy(result)
    mut(bad)
    try:
        run.build_report(bad)
        ok(False, f"build_report rejects {why}")
    except run.ReportError:
        ok(True, f"build_report rejects {why}")

# -- publish gate refuses distribution without a named approver --
ok(run.main(["--publish"]) == 2, "publish without an approver is refused (rc 2)")
ok(run.main(["--publish", "--approved-by", "x\n7"]) == 2, "a control-char approver is refused")
ok(run.main([]) == 0, "a plain draft run succeeds (rc 0)")

print(f"OK — {passed} equity-spend agent checks passed "
      f"(verdict: {report['verdict'].split(' —')[0]}).")
