#!/usr/bin/env python3
"""Evals for the merit-comp-planning agent: validation + render invariants + illustrative labeling +
fail-closed + the publish gate. The agent renders the engine's cycle plan; it authorizes no pay."""
import copy
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EX = HERE.parent
REPO = EX.parents[1]
for p in (EX, REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import run as R  # noqa: E402
from foundation.compute import merit_comp as MC  # noqa: E402
from foundation.compute import equity_spend as _E  # noqa: E402
MC_E_GRANT_COLS = _E._GRANT_COLS

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


result = MC.compute()
report = R.build_report(result)
ok(report["m"]["within_budget"], "committed plan is within the merit budget")
ok("ON-BUDGET" in report["verdict"], "verdict reads ON-BUDGET")

html = R.render_html(report)
digest = R.render_digest(report)

# ---- render invariants -----------------------------------------------------------------------------------
ok(html.startswith("<!doctype html>") and html.rstrip().endswith("</html>"), "renders a full HTML doc")
for token in ("Merit matrix", "compa-ratio", "Bonus pool", "Equity refreshers", "equity-ledger schema"):
    ok(token in html, f"HTML surfaces {token!r}")
ok("mtx" in html and "guards" in html, "the merit matrix + guardrail tiles render")
# every matrix cell renders
for rt in ("outstanding", "exceeds", "meets", "below"):
    ok(rt in html, f"matrix row {rt!r} renders")
# the on-leave disclosure is honest: the active / protected-leave split is surfaced, not hidden
ok("protected leave" in html.lower() and f"{result['active_count']:,}" in html
   and f"{result['on_leave_count']:,}" in html,
   "HTML discloses the active + on-protected-leave split behind 'eligible headcount'")
ok("protected leave" in digest.lower(), "digest discloses the protected-leave split")

# ---- the equity handoff is HONEST: append-valid rows that feed the NEXT period, not a current-period free lunch
low = html.lower()
ok("append-valid" in low and "next" in low, "HTML frames the refreshers as append-valid rows for the NEXT period")
ok("update for free" not in low and "at cycle close" not in low,
   "HTML does NOT claim current-period board metrics 'update for free' (they don't — the grants are future-dated)")
ok("update for free" not in digest.lower() and "append-valid" in digest.lower(),
   "digest reframes the handoff honestly (append-valid rows, no 'update for free' overclaim)")
# durable guard: the "board metrics update for free" overclaim must not reappear in ANY of the source/doc
# files that describe the handoff (the grants are future-dated, so current-period metrics do NOT change)
for _src in ("foundation/compute/merit_comp.py", "examples/merit-comp-planning/run.py",
             "examples/merit-comp-planning/SOUL.md", "examples/merit-comp-planning/SPEC.md",
             "examples/merit-comp-planning/README.md", "examples/equity-spend/README.md",
             "governance/equity-plan-methodology.md", "README.md"):
    ok("update for free" not in (REPO / _src).read_text(encoding="utf-8").lower(),
       f"{_src} carries no 'update for free' handoff overclaim")
# the refreshers preserve the equity-ledger participant groups (an exec is not silently 'management')
bg = result["equity_refresh"]["by_group"]
ok("ceo" in bg and "section16" in bg, "the refreshers preserve exec ledger groups (CEO + Section 16), not just management")
for lab in ("CEO", "Sec 16", "staff"):
    ok(lab in html, f"HTML surfaces the refresher group split label {lab!r}")

# ---- illustrative / honesty labeling ---------------------------------------------------------------------
ok("illustrative" in low, "HTML labels the matrix/targets illustrative")
ok("synthetic" in low, "HTML labels the workforce synthetic")
ok("authorizes no pay" in low or "authorize no pay" in low, "HTML states the agent authorizes no pay")
ok("illustrative" in digest.lower(), "digest carries the illustrative disclaimer")
ok("authorizes no pay" in digest.lower(),
   "digest carries the authorizes-no-pay guardrail (parity with the HTML footer for the portable artifact)")

# ---- public-safety: no email-like PII in the rendered output ---------------------------------------------
ok(not re.search(r"[\w.]+@[\w.]+\.[a-z]{2,}", html), "no email-like PII in the HTML")

# ---- determinism ---------------------------------------------------------------------------------------
ok(R.render_html(R.build_report(MC.compute())) == html, "render is deterministic")


# ---- fail-closed: a corrupted engine result is refused ---------------------------------------------------
def _raises(mutate, label):
    global passed
    bad = copy.deepcopy(result)
    mutate(bad)
    try:
        R.build_report(bad)
        assert False, f"FAILED (no raise): {label}"
    except R.ReportError:
        passed += 1


_raises(lambda b: b["merit"].update(within_budget=False, spend=b["merit"]["budget"] + 1e6),
        "an over-budget plan is refused")
_raises(lambda b: b["merit"].update(spend_pct=float("nan")), "a NaN merit-spend % is refused")
_raises(lambda b: b.update(avg_new_compa_ratio=b["avg_compa_ratio"] - 0.1),
        "a compa-ratio that DROPS after merit is refused (merit must move toward market)")
_raises(lambda b: b["matrix"]["below"].update(q1=0.0, q4=0.5),
        "a merit matrix that rises as compa-ratio rises (bad discipline) is refused")
_raises(lambda b: b["matrix"]["below"].update(q1=0.20),
        "a matrix where a LOW rating out-pays a high rating at the same compa (cross-rating discipline) is refused")
_raises(lambda b: b["matrix"].pop("outstanding"), "a matrix missing a rating tier is refused")
_raises(lambda b: b["by_rating"]["meets"].update(merit_amount=float("nan")),
        "a non-finite by-rating spend (would poison the histogram geometry) is refused")
_raises(lambda b: b["by_rating"]["meets"].update(n=float("nan")),
        "a non-finite by-rating headcount (rendered in the allocation strip) is refused")
_raises(lambda b: b.update(on_leave_count=b["eligible_headcount"] + 5),
        "an on-leave disclosure that doesn't reconcile with eligible headcount is refused")
_raises(lambda b: b.update(active_count=-100, on_leave_count=b["eligible_headcount"] + 100),
        "a negative active_count (even if the split still sums to eligible) is refused")
# every rendered COUNT must be a real non-negative int — NaN / negative / bool all fail closed
_raises(lambda b: b.update(promotions=float("nan")), "a non-finite promotions count is refused")
_raises(lambda b: b.update(promotions=-5), "a negative promotions count is refused")
_raises(lambda b: b.update(promotions=True),
        "a boolean promotions count is refused (bool is an int subclass — must not slip through)")
_raises(lambda b: b["equity_refresh"].update(grant_count=-1), "a negative refresher grant_count is refused")
_raises(lambda b: b["equity_refresh"].update(total_shares=float("nan")),
        "a non-finite refresher share count (rendered in the equity-handoff tile) is refused")
_raises(lambda b: b.update(over_max_after_merit=True), "a boolean over-band-max count is refused")
# sane ranges + cross-field reconciliation (a corrupt-but-individually-typed field must not render garbage)
_raises(lambda b: b["merit"].update(budget=-1.0), "a negative merit budget is refused")
_raises(lambda b: b["merit"].update(budget_pct=250.0), "an absurd merit budget % (>100% of payroll) is refused")
_raises(lambda b: b.update(avg_compa_ratio=-1.0), "a negative average compa-ratio is refused")
_raises(lambda b: b.update(by_rating={}), "an empty by_rating (would render a blank allocation strip) is refused")
_raises(lambda b: b["by_rating"]["meets"].update(merit_amount=-1.0),
        "a negative by-rating merit spend (would render '$-1.5M') is refused")
_raises(lambda b: b.update(promotions=b["eligible_headcount"] + 1),
        "more promotions than eligible employees is refused")
_raises(lambda b: b["by_rating"]["meets"].update(n=b["by_rating"]["meets"]["n"] + 500),
        "by-rating headcounts that don't reconcile to the eligible population are refused")
# every emitted grant ROW must be append-valid, not just aggregate-consistent
_raises(lambda b: b["equity_refresh"]["grants"][0].update(participant_group="intern"),
        "a grant row with a bad participant_group (not equity-ledger vocab) is refused")
_raises(lambda b: b["equity_refresh"]["grants"][0].update(participant_group="director"),
        "a 'director' grant is refused (append-valid in the ledger, but not a merit group and hidden from the split)")
_raises(lambda b: b["equity_refresh"]["grants"][0].update(award_type="warrant"),
        "a grant row with a bad award_type is refused")
_raises(lambda b: b["equity_refresh"]["grants"][0].update(shares_granted=-5),
        "a grant row with negative shares is refused (would render as 'append-valid')")
_raises(lambda b: b["equity_refresh"]["grants"][1].__setitem__("grant_id", b["equity_refresh"]["grants"][0]["grant_id"]),
        "duplicate grant ids among the emitted rows are refused")
# the REAL append-valid guarantee (not just structure): rows the equity ledger would reject are refused
_raises(lambda b: b["equity_refresh"]["grants"][0].update(plan_id="P-NOPE"),
        "a grant row referencing an unknown plan_id is refused (real append against the equity ledger)")
_raises(lambda b: b["equity_refresh"]["grants"][0].update(emp_id="E-NOPE99"),
        "a grant row referencing an unknown emp_id is refused (real append against the equity ledger)")
_raises(lambda b: b["equity_refresh"]["grants"][0].update(grant_date="not-a-date"),
        "a grant row with a bad grant_date is refused (real append against the equity ledger)")
# the by_group split must be RECOMPUTED from the rows — moving a CEO grant to staff (totals still reconcile)
# must be refused
_raises(lambda b: (b["equity_refresh"]["grants"][next(i for i, g in enumerate(b["equity_refresh"]["grants"])
                                                      if g["participant_group"] == "ceo")]
                   .update(participant_group="staff")),
        "moving a CEO grant to 'staff' while the totals still reconcile is refused (by_group recomputed)")
# cross-field reconciliation of the RENDERED charts vs the headline (a chart must not contradict the KPI)
_raises(lambda b: b["by_rating"]["meets"].update(merit_amount=b["by_rating"]["meets"]["merit_amount"] + 5e5),
        "by-rating merit spend that doesn't reconcile to the headline merit spend is refused")
_raises(lambda b: b.update(promo_spend=0.0),
        "191 promotions with $0 promo spend (count/spend contradiction) is refused")
_raises(lambda b: b["equity_refresh"].update(grant_count=b["equity_refresh"]["grant_count"] + 1),
        "an equity grant_count that doesn't match the emitted grant rows is refused")
_raises(lambda b: b["equity_refresh"]["by_group"].__setitem__("staff",
        {"grants": 0, "shares": 0}),
        "a participant-group split that doesn't sum back to the grant/share totals is refused")

# ---- the equity handoff is a REAL artifact: the emitted CSV is append-valid against the equity engine -----
ok(R.main([]) == 0, "draft run writes the artifacts")
_csv = EX / "output" / "equity_refresh_grants.sample.csv"
ok(_csv.exists(), "the appendable equity_refresh_grants.sample.csv artifact is written")
import csv as _csvmod  # noqa: E402
_hdr = next(_csvmod.reader(open(_csv)))
ok(tuple(_hdr) == MC_E_GRANT_COLS, "the CSV header is the equity ledger's exact schema (append-ready)")
_rows = list(_csvmod.DictReader(open(_csv)))
ok(len(_rows) == result["equity_refresh"]["grant_count"], "the CSV row count matches the reported grant count")
ok(all(r["grant_type"] == "annual_refresh" for r in _rows), "every emitted row is an annual_refresh grant")
ok(any(r["participant_group"] in ("ceo", "section16") for r in _rows),
   "exec ledger groups (CEO/Section 16) are preserved in the emitted rows, not flattened to management")

# ---- publish gate --------------------------------------------------------------------------------------
ok(R.main(["--publish"]) == 2, "publish without a named approver is refused")
ok(R.main(["--publish", "--approved-by", "Bad\nName"]) == 2, "control chars in approver are refused")
ok(R.main([]) == 0, "draft run succeeds")
pub = EX / "output" / "PUBLISHED.json"
ok(R.main(["--publish", "--approved-by", "Compensation Committee Chair"]) == 0 and pub.exists(),
   "publish with a named approver writes PUBLISHED.json")
ok(R.main(["--publish"]) == 2 and not pub.exists() and pub.with_name("PUBLISHED.json.stale").exists(),
   "a refused re-publish stales the prior PUBLISHED.json")
R.main([])
pub.unlink(missing_ok=True)

print(f"OK — {passed} merit-comp agent checks passed (verdict: {report['verdict'].split(' —')[0]}).")
