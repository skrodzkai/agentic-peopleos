#!/usr/bin/env python3
"""Tests for the company-wide equity-spend engine: the fail-closed inventory + the board-metric invariants.
Fail-closed cases copy the committed CSVs to a tmp dir and corrupt exactly one thing."""
import csv
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.compute import equity_spend as E  # noqa: E402

_ACME = Path(__file__).resolve().parents[3] / "foundation" / "data" / "acme"
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
    except E.EquityDataError:
        passed += 1


def _tmp_with(mutate):
    """A tmp acme dir with the required CSVs copied, then `mutate(dir)` applied. Returns the dir path."""
    d = Path(tempfile.mkdtemp())
    for name in ("equity_grants.csv", "equity_plans.csv", "shares_outstanding.csv", "financials.csv",
                 "workers.csv", "directors.csv", "burn_benchmarks.csv"):
        shutil.copy(_ACME / name, d / name)
    mutate(d)
    return d


def _rewrite(path, fn):
    rows = list(csv.DictReader(open(path)))
    fields = rows[0].keys()
    fn(rows)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# ---- happy path: the committed data computes a coherent board view -------------------------------------
r = E.compute()
ok(abs(r["market_cap"] - r["shares_outstanding"] * r["price"]) < 1.0, "market-cap identity holds (CSO x price)")
ok(r["fiscal_years"] == [2023, 2024, 2025], "three fiscal years")
ok(0.0 < r["vabr_3yr_pct"] < 5.0, "3-yr VABR in a believable single-digit range")
gp = r["epsc"]["grant_practices"]
ok(gp["pass"] is True and gp["headroom_pct"] > 0, "3-yr VABR passes the illustrative EPSC cap with headroom")
ok(8.0 <= r["overhang_pct"] <= 18.0, "overhang in a believable SaaS range (8-18%)")
ok(1.5 <= r["pool_longevity_years"] <= 4.0, "pool longevity is a believable few years")
ok(r["epsc"]["features_passed"] == r["epsc"]["features_total"] == 6, "all 6 EPSC plan-feature tests pass")
ok(r["unamortized_sbc"] > 0 and r["unamortized_sbc_years"] > 0, "a positive SBC backlog + horizon")
# SBC % of revenue: the ledger begins FY2023, so the earliest quarters understate expense (no prior-grant
# amortization tail) — the honest signal is that the RECENT book is flat-to-declining, and the forward
# signals (grant value + VABR) decline. TTM is a believable SaaS level.
q = r["sbc_pct_revenue"]["quarterly"]
mid4 = sum(x["pct"] for x in q[4:8]) / 4
last4 = sum(x["pct"] for x in q[8:12]) / 4
ok(last4 <= mid4 + 0.5, "recent SBC % of revenue is flat-to-declining (a maturing grant book)")
ok(r["burn"][0]["vabr_pct"] > r["burn"][-1]["vabr_pct"], "VABR (the forward equity-spend signal) declines")
ok(5.0 <= r["sbc_pct_revenue"]["ttm_pct"] <= 18.0, "SBC % of revenue TTM is believable SaaS (5-18%)")
# every FY: net burn <= gross burn (forfeitures can only reduce), VABR < gross (value-weighting FVAs at price
# but options below price is < share-count when the price is above strike is not guaranteed — assert net<=gross)
for b in r["burn"]:
    ok(b["net_pct"] <= b["gross_pct"] + 1e-9, f"FY{b['fy']}: net burn <= gross burn")
    ok(b["legacy_adjusted_pct"] > b["vabr_pct"], f"FY{b['fy']}: legacy multiplier burn > VABR (as expected)")
# value-per-group: CEO present, exec/person >> staff/person (a sane hierarchy), staff the broadest group
vg = r["value_per_fte_by_group"]
ok("ceo" in vg and vg["ceo"]["per_fte"] > vg["staff"]["per_fte"] > 0, "CEO grant present + exceeds staff/person")
ok(vg["staff"]["recipients"] > vg["management"]["recipients"] > 0, "staff is the broadest recipient group")

# ---- determinism: two computes are identical ------------------------------------------------------------
import json  # noqa: E402
ok(json.dumps(E.compute(), sort_keys=True) == json.dumps(r, sort_keys=True), "compute() is deterministic")

# ---- FAIL-CLOSED inventory -------------------------------------------------------------------------------
raises(lambda: E.EquityPlan(Path(tempfile.mkdtemp())), "missing data files fail closed")
raises(lambda: E.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda rows: rows[0].__setitem__("emp_id", "E-NOPE99")))), "grant to an unknown emp_id")
raises(lambda: E.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda rows: rows[0].__setitem__("plan_id", "P-XXXX")))), "grant referencing an unknown plan")
raises(lambda: E.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda rows: rows[0].__setitem__("participant_group", "intern")))), "bad participant_group")


def _first_rsu(rows):
    return next(x for x in rows if x["award_type"] == "rsu")


raises(lambda: E.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda rows: _first_rsu(rows).__setitem__("strike_price_usd", "50")))), "strike present on a non-option")
raises(lambda: E.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda rows: _first_rsu(rows).__setitem__("psu_max_multiplier", "2.0")))), "psu multiplier on a non-PSU")
raises(lambda: E.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda rows: rows[0].__setitem__("cliff_months", "999")))), "cliff > vesting period")
raises(lambda: E.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda rows: rows[0].__setitem__("shares_granted", "0")))), "non-positive shares_granted")
raises(lambda: E.compute(_tmp_with(lambda d: _rewrite(d / "equity_grants.csv",
       lambda rows: rows[0].__setitem__("grant_date", "1990-01-01")))), "grant outside the plan active window")
raises(lambda: E.compute(_tmp_with(lambda d: _rewrite(d / "burn_benchmarks.csv",
       lambda rows: [rw.__setitem__("source_note", "ISS 2025 official cap") for rw in rows]))),
       "benchmark whose source_note omits 'illustrative' is refused")
raises(lambda: E.compute(_tmp_with(lambda d: _rewrite(d / "shares_outstanding.csv",
       lambda rows: rows.pop()))), "shares_outstanding period spine != financials")
raises(lambda: E.compute(_tmp_with(lambda d: _rewrite(d / "shares_outstanding.csv",
       lambda rows: rows[0].__setitem__("waso_basic", str(int(float(rows[0]["common_shares_outstanding"]) * 2)))))),
       "waso_basic > CSO fails closed")

print(f"OK — {passed} equity-spend engine checks passed "
      f"({len(E.EquityPlan().grants)} grants across the company-wide ledger).")
