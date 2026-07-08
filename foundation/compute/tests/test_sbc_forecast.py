#!/usr/bin/env python3
"""Tests for the SBC-forecast engine: the fail-closed data contract, the runoff invariants, and the exact
reconciliation to the equity-spend arm's unamortized-SBC backlog (same amortization, split by fiscal year).
Fail-closed cases copy the committed CSVs to a tmp dir and corrupt exactly one thing."""
import csv
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.compute import sbc_forecast as S  # noqa: E402
from foundation.compute import equity_spend as E  # noqa: E402

_ACME = Path(__file__).resolve().parents[3] / "foundation" / "data" / "acme"
_FILES = ("equity_grants.csv", "workers.csv", "shares_outstanding.csv", "financials.csv",
          "equity_plans.csv", "directors.csv", "burn_benchmarks.csv")
passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


def raises(fn, label):
    global passed
    try:
        fn()
        assert False, f"FAILED (no raise): {label}"
    except S.SBCDataError:
        passed += 1


def _tmp_with(mutate):
    d = Path(tempfile.mkdtemp())
    for name in _FILES:
        shutil.copy(_ACME / name, d / name)
    mutate(d)
    return d


def _rewrite(path, fn):
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        rows = [r for r in rd]
    header, rows = fn(header, rows)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        if header is not None:
            wr.writerow(header)
        wr.writerows(rows)


def _set(header, row, col, val):
    row = list(row)
    row[header.index(col)] = val
    return row


def _shift_year(d):
    y, m, day = d.split("-")
    return f"{int(y) + 1}-{m}-{day}"


# ---- shape + anchoring ------------------------------------------------------------------------------
r = S.compute()
ok(set(r) >= {"company", "as_of", "horizon_fys", "assumptions", "locked_in", "new_grant_overlay",
              "total_forecast", "context", "disclaimer"}, "compute returns the documented top-level keys")
ok(r["as_of"] == "2025-12-31", "the forecast anchors at the fiscal close (last shares/financials period_end)")
ok(r["horizon_fys"][0] == 2026, "the first forecast fiscal year is the one after the close")

li = r["locked_in"]

# ---- THE reconciliation: the period-0 backlog is exactly the equity-spend arm's unamortized SBC ------
es = E.compute()
ok(abs(li["backlog_unrecognized_usd"] - es["unamortized_sbc"]) < 0.01,
   "the locked-in backlog equals equity_spend.unamortized_sbc to the cent (same amortization)")
ok(abs(li["wavg_remaining_years"] - es["unamortized_sbc_years"]) < 0.01,
   "the weighted-average remaining years matches the equity-spend arm too")

# ---- runoff invariants ------------------------------------------------------------------------------
gross_sum = sum(s["gross_expense"] for s in li["schedule"])
ok(abs(gross_sum - li["backlog_unrecognized_usd"]) < 1.0,
   "the gross fiscal-year runoff sums back to the backlog (the schedule just splits it by year)")
ok(all(s["gross_expense"] >= 0 for s in li["schedule"]), "no fiscal year recognizes a negative expense")
ok(all(s["forfeiture_adj_expense"] <= s["gross_expense"] + 1e-6 for s in li["schedule"]),
   "the forfeiture-adjusted line never exceeds the gross line (it is a haircut)")
cums = [s["cumulative_gross"] for s in li["schedule"]]
ok(cums == sorted(cums), "cumulative recognized expense is monotonically non-decreasing")
ok(li["runoff_complete_fy"] is not None and li["beyond_horizon_usd"] < 1.0,
   "the locked-in runoff completes within the horizon (no material tail beyond it)")
# the runoff declines as grants finish vesting — the newest data has a front-loaded book
ok(li["schedule"][0]["gross_expense"] > li["schedule"][-1]["gross_expense"],
   "the locked-in runoff declines across the horizon (already-granted equity rolls off)")

# ---- overlay + totals -------------------------------------------------------------------------------
ov = {o["fy"]: o["expense"] for o in r["new_grant_overlay"]["schedule"]}
for t in r["total_forecast"]:
    ok(abs(t["total"] - (t["locked_in"] + t["new_grants"])) < 0.01,
       f"FY{t['fy']} total = locked-in + new-grant overlay")
    ok(abs(t["new_grants"] - ov[t["fy"]]) < 0.01, f"FY{t['fy']} total pulls the overlay figure through unchanged")
ok(r["assumptions"]["new_grant_run_rate_usd"] > 0, "the modeled new-grant run-rate is the (positive) TTM value")
ok(0 < r["assumptions"]["forfeiture_rate_annual_pct"] < 100, "the illustrative forfeiture rate is a valid percent")
ok(r["context"]["backlog_pct_market_cap"] is not None and r["context"]["market_cap_usd"] > 0,
   "the market-cap context is populated for the dilution framing")

# ---- determinism ------------------------------------------------------------------------------------
ok(S.compute() == r, "compute() is deterministic (same committed data -> identical result)")

# ---- fail-closed data contract ----------------------------------------------------------------------
raises(lambda: S.compute(Path(tempfile.mkdtemp())), "a missing equity_grants.csv fails closed")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda h, rows: (h[:-1], rows)))), "a short/renamed grant header fails closed")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda h, rows: (h, [_set(h, row, "grant_date_fv_per_share_usd", "oops") for row in rows])))),
       "a non-numeric grant-date fair value fails closed")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "financials.csv",
       lambda h, rows: (h, rows[:-1] + [[_shift_year(rows[-1][0]), rows[-1][1]]])))),
       "a financials period_end that disagrees with shares fails closed")

# a fully-vested book (push every vest far in the past) has nothing left to forecast -> fail closed
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda h, rows: (h, [_set(h, row, "vest_start_date", "2000-01-01") for row in rows])))),
       "no outstanding unvested grants fails closed")

# malformed grant economics fail closed (not a silent truncation or a raw ValueError):
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda h, rows: (h, [_set(h, row, "shares_granted", "10.9") for row in rows])))),
       "a fractional shares_granted fails closed (no silent int truncation)")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda h, rows: (h, [_set(h, row, "grant_date_fv_per_share_usd", "-1") for row in rows])))),
       "a negative grant-date fair value fails closed")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda h, rows: (h, [_set(h, row, "grant_date_fv_per_share_usd", "0") for row in rows])))),
       "a zero grant-date fair value fails closed (0 cost is a defect, not a fact)")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda h, rows: (h, [_set(h, row, "vest_months_total", "oops") for row in rows])))),
       "a non-numeric vest_months_total fails closed (SBCDataError, not a raw ValueError)")

# workers.csv schema is pinned, not self-accepted: dropping the term_date column fails closed
def _drop_col(h, rows, col):
    i = h.index(col)
    return ([c for j, c in enumerate(h) if j != i],
            [[c for j, c in enumerate(row) if j != i] for row in rows])
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda h, rows: _drop_col(h, rows, "term_date")))),
       "a workers.csv missing the term_date column fails closed (schema pinned, forfeitures not silently zeroed)")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv", lambda h, rows: (h, rows + [rows[0]])))),
       "a duplicate emp_id in workers.csv fails closed")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda h, rows: (h, rows + [rows[0]])))), "a duplicate grant_id fails closed")

# the anchor must be a Dec-31 fiscal close (both shares + financials), else the current FY is mis-bucketed
raises(lambda: S.compute(_tmp_with(lambda d: (
       _rewrite(d / "shares_outstanding.csv", lambda h, rows: (h, rows[:-1] + [_set(h, rows[-1], "period_end", "2025-06-30")])),
       _rewrite(d / "financials.csv", lambda h, rows: (h, rows[:-1] + [_set(h, rows[-1], "period_end", "2025-06-30")]))))),
       "a mid-year (non Dec-31) fiscal anchor fails closed")

# revenue must be positive: a zero/negative TTM revenue would divide-by-zero / render an absurd % -> fail closed
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "financials.csv",
       lambda h, rows: (h, [_set(h, row, "revenue_usd", "0") for row in rows])))),
       "a zero revenue row fails closed (the % -of-revenue denominator)")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "financials.csv",
       lambda h, rows: (h, [_set(h, rows[0], "revenue_usd", "0")] + rows[1:])))),
       "a zero revenue row OUTSIDE the trailing-TTM window also fails closed (every row validated)")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "financials.csv",
       lambda h, rows: (h, [_set(h, rows[0], "revenue_usd", "-5")] + rows[1:])))),
       "a negative revenue row outside the TTM window fails closed")

# the period SPINES must be strictly increasing + duplicate-free + aligned — otherwise fin[-4:]/shares[-1]
# (which trust row order) would silently pick the wrong TTM window
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "financials.csv",
       lambda h, rows: (h, [rows[1], rows[0]] + rows[2:])))),
       "a REORDERED financials period spine fails closed (row order is trusted by the TTM slice)")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "financials.csv",
       lambda h, rows: (h, [rows[0]] + rows)))),
       "a DUPLICATE financials period fails closed")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "shares_outstanding.csv",
       lambda h, rows: (h, [rows[1], rows[0]] + rows[2:])))),
       "a REORDERED shares period spine fails closed")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "financials.csv",
       lambda h, rows: (h, rows[:-1])))),
       "a financials spine that no longer aligns with the shares spine fails closed")

# REFERENTIAL INTEGRITY: a grant to a non-existent recipient / unknown award type or group fails closed
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda h, rows: (h, [_set(h, row, "emp_id", "GHOST-9") for row in rows])))),
       "a grant to an emp_id in neither workers.csv nor directors.csv fails closed")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda h, rows: (h, [_set(h, row, "award_type", "bogus") for row in rows])))),
       "a grant with an unknown award_type fails closed")
raises(lambda: S.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda h, rows: (h, [_set(h, row, "participant_group", "bogus") for row in rows])))),
       "a grant with an unknown participant_group fails closed")
# a director grant (recipient in directors.csv, not workers.csv) is ACCEPTED — this must not over-reject
_ok_dir = S.compute()  # the committed data has 40 director grants; if referential integrity were too strict
ok(_ok_dir["locked_in"]["backlog_unrecognized_usd"] > 0, "director grants (in directors.csv) are valid recipients")

# TTM must be exactly four consecutive calendar quarters ending at the fiscal close
raises(lambda: S.compute(_tmp_with(lambda d: (
       _rewrite(d / "shares_outstanding.csv", lambda h, rows: (h, rows[-3:])),
       _rewrite(d / "financials.csv", lambda h, rows: (h, rows[-3:]))))),
       "fewer than four quarterly financials rows fails closed (cannot form a clean TTM)")

print(f"OK — {passed} SBC-forecast checks passed "
      f"(as of {r['as_of']}; backlog ${li['backlog_unrecognized_usd']:,.0f} reconciles to equity-spend; "
      f"runoff FY{li['schedule'][0]['fy']} ${li['schedule'][0]['gross_expense']:,.0f} -> "
      f"complete FY{li['runoff_complete_fy']}).")
