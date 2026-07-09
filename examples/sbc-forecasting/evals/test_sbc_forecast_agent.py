#!/usr/bin/env python3
"""Evals for the SBC-forecast agent: render invariants, honest labeling, no-PII, fail-closed, publish gate."""
import copy
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[2]))
import run  # noqa: E402
from foundation.compute import sbc_forecast as SBC  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


result = SBC.compute()
report = run.build_report(result)
html = run.render_html(report)
digest = run.render_digest(report)

# -- renders + is coherent --
ok("Stock-Based-Compensation Forecast" in html and "SBC Expense Forecast" in html, "title + kicker render")
ok(html.count("<svg") >= 1, "the total-forecast chart renders as SVG")
ok("Locked-in SBC runoff" in html and "Total go-forward SBC forecast" in html, "both sections render")
li = result["locked_in"]
ok(run._m(li["backlog_unrecognized_usd"]) in html, "the locked-in backlog headline number renders")
ok("FY" + str(li["schedule"][0]["fy"]) in html, "the first forecast fiscal year renders")
ok("sbc" in digest.lower() and "forecast" in digest.lower(), "the digest names the arm")

# -- honest labeling --
low = (html + digest).lower()
for phrase in ("illustrative", "not financial guidance", "reconciles to"):
    ok(phrase in low, f"the dashboard/digest states '{phrase}' (honest about certain vs assumed)")
ok("synthetic" in low, "the artifacts label the data synthetic")
ok("assumes continued service" in low or "assumes full vesting" in low,
   "the dashboard/digest states the gross runoff's continued-service (full-vesting) assumption plainly")

# -- no PII / injection --
ok(not re.search(r"[\w.]+@[\w.]+\.[a-z]{2,}", html) and "SSN" not in html, "no email/SSN in the dashboard")
ok(not re.search(r"\bE-\d{4}\b", html) and not re.search(r"\bG-\d{4,}\b", html),
   "no per-employee / per-grant ids appear")
ok("<script" not in html, "no <script> in the dashboard")

# -- fail-closed: a self-contradictory engine result is refused --
def _bad(mut, why):
    global passed
    r = copy.deepcopy(result)
    mut(r)
    try:
        run.build_report(r)
        assert False, f"FAILED (no raise): build_report rejects {why}"
    except run.ReportError:
        passed += 1


_bad(lambda r: r["locked_in"]["schedule"][0].update(gross_expense=r["locked_in"]["schedule"][0]["gross_expense"] * 2),
     "a runoff that no longer sums to the backlog")
_bad(lambda r: r["locked_in"]["schedule"][0].update(gross_expense=round(r["locked_in"]["schedule"][0]["gross_expense"] + 0.02, 2)),
     "even a TWO-CENT runoff drift is caught (exact-penny reconciliation, not a loose tolerance)")
_bad(lambda r: [s.update(cumulative_gross=round(s["cumulative_gross"] + 100.0, 2)) for s in r["locked_in"]["schedule"]],
     "a cumulative that stays monotonic but is not the running sum of gross")
_bad(lambda r: r["locked_in"]["schedule"][0].update(forfeiture_adj_expense=1e15),
     "a forfeiture-adjusted figure above its gross (a haircut can't add expense)")
_bad(lambda r: r["locked_in"]["schedule"][1].update(gross_expense=float("nan")), "a non-finite runoff figure")
_bad(lambda r: r["locked_in"]["schedule"][0].update(cumulative_gross=1e15), "a non-monotonic cumulative")
_bad(lambda r: r["total_forecast"][0].update(total=r["total_forecast"][0]["total"] + 1e6),
     "a total that isn't locked-in + new-grant overlay")
_bad(lambda r: r["assumptions"].update(forfeiture_rate_annual_pct=150.0), "an out-of-range forfeiture assumption")
_bad(lambda r: r.pop("locked_in"), "a result missing the locked-in block")

# -- publish gate --
ok(run.main(["--publish"]) == 2, "publish without an approver is refused (rc 2)")
ok(run.main(["--publish", "--approved-by", "  "]) == 2, "a blank approver is refused (rc 2)")
ok(run.main(["--publish", "--approved-by", "Bad\tName"]) == 2, "a control-char approver is refused (rc 2)")

# -- determinism --
ok(run.render_html(run.build_report(SBC.compute())) == html, "the dashboard renders deterministically")

print(f"OK — {passed} SBC-forecast agent checks passed "
      f"(backlog {run._m(li['backlog_unrecognized_usd'])}; FY{li['schedule'][0]['fy']} "
      f"{run._m(li['schedule'][0]['gross_expense'])} -> runoff complete FY{li['runoff_complete_fy']}).")
