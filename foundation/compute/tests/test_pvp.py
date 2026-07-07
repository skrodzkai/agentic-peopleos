#!/usr/bin/env python3
"""Unit tests for the Pay-versus-Performance / Compensation Actually Paid engine (Item 402(v)).

Runs the CAP reconciliation against HAND-COMPUTED expectations for every equity roll-forward bucket
(restricted-stock fair value is price x shares, so the arithmetic is exact and the implementation is not
its own oracle), checks Black-Scholes against a known value, and confirms the itemized bridge ties to
reported CAP for every NEO and year of the shipped sample.

Run: python3 foundation/compute/tests/test_pvp.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from foundation.compute import pvp as P  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


def approx(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


# --------------------------------------------------------------------------- Black-Scholes known answers
# S=K=100, T=1, r=0, sigma=0.2, q=0 -> ~7.9656 (standard reference value)
ok(approx(P.bs_call(100, 100, 1.0, 0.0, 0.2), 7.965567, tol=1e-4), "Black-Scholes ATM reference value")
ok(P.bs_call(100, 80, 0.0, 0.05, 0.3) == 20.0, "at expiry an in-the-money call is intrinsic (S-K)")
ok(P.bs_call(80, 100, 0.0, 0.05, 0.3) == 0.0, "at expiry an out-of-the-money call is worth 0")
ok(approx(P.bs_call(100, 90, 1.0, 0.10, 0.0), 100 - 90 * P.math.exp(-0.10)), "zero-vol call = discounted intrinsic")
ok(P.bs_call(100, 100, 1.0, 0.0, 0.5) > P.bs_call(100, 100, 1.0, 0.0, 0.2), "call value rises with volatility")
try:
    P.bs_call(-1, 100, 1.0, 0.0, 0.2); ok(False, "negative spot must raise")
except P.PVPError:
    ok(True, "Black-Scholes fails closed on a non-positive spot")


# --------------------------------------------------------------------------- crafted panel: one bucket per tranche
# Restricted stock only (FV = price x shares, exact), so every CAP component is hand-checkable.
PRICES = {
    "2022-12-31": 100.0, "2023-02-15": 102.0, "2023-06-30": 105.0, "2023-12-31": 120.0,
    "2024-06-30": 110.0, "2024-12-31": 90.0, "2025-06-30": 130.0, "2025-12-31": 150.0,
}
SH = 1000  # shares per tranche, so a dollar figure reads as price x 1000


def _rsu(tid, gfy, gd, vest, forfeited=False, forfeit_date=None):
    t = {"id": tid, "type": "rsu", "grant_fy": gfy, "grant_date": gd, "shares": SH,
         "vest_date": vest, "forfeited": forfeited}
    if forfeited:
        t["forfeit_date"] = forfeit_date
    return t


CRAFTED_TRANCHES = [
    _rsu("t1", 2024, "2024-02-15", "2027-02-15"),                      # granted-this-yr, unvested at YE  -> +YE FV
    _rsu("t2", 2022, "2022-02-15", "2026-02-15"),                      # prior, unvested at YE            -> +Δ(YE - prior YE)
    _rsu("t3", 2024, "2024-02-15", "2024-06-30"),                      # granted-this-yr, vested in-yr    -> +vest FV
    _rsu("t4", 2022, "2022-02-15", "2024-06-30"),                      # prior, vested in-yr             -> +Δ(vest - prior YE)
    _rsu("t5", 2022, "2022-02-15", "2026-02-15", True, "2024-08-01"),  # prior, forfeited in-yr          -> -prior YE FV
    _rsu("t6", 2020, "2020-02-15", "2023-02-15"),                      # vested BEFORE the year          -> ignored
]
# year-specific dividends: t2 gets 2000 in FY2023 + 3000 in FY2024 (and nothing after — no carryover);
# t5 gets 5000 in FY2024, the year it forfeits (paid on the still-unvested award BEFORE forfeiture)
CRAFTED_TRANCHES[1]["dividends_paid_unvested_by_fy"] = {"2023": 2000, "2024": 3000}
CRAFTED_TRANCHES[4]["dividends_paid_unvested_by_fy"] = {"2024": 5000}
CRAFTED = {
    "subject": {"ticker": "ACMQ", "name": "Acme Corp"},
    "fiscal_years": [2023, 2024, 2025],
    "fiscal_year_end": "12-31",
    "price_by_date": PRICES,
    "market": {"volatility": 0.4, "risk_free_rate": 0.04, "dividend_yield": 0.0,
               "rtsr": {"peer_tickers": ["BEXQ"], "peer_spots": {"BEXQ": 100.0},
                        "correlation": {"diagonal": 1.0, "off_diagonal": 0.4}, "seed": 1, "paths": 500,
                        "payout_curve": [{"percentile": 25, "payout_percent": 50},
                                         {"percentile": 50, "payout_percent": 100},
                                         {"percentile": 75, "payout_percent": 200}]}},
    "neos": [
        {"id": "peo", "role": "CEO", "is_peo": True,
         "sct": {"2023": {"total": 5000000, "stock_awards": 0, "option_awards": 0},
                 "2024": {"total": 6000000, "stock_awards": 300000, "option_awards": 0},
                 "2025": {"total": 5500000, "stock_awards": 0, "option_awards": 0}},
         "tranches": CRAFTED_TRANCHES},
        {"id": "cfo", "role": "CFO", "is_peo": False,
         "sct": {"2023": {"total": 2000000, "stock_awards": 0, "option_awards": 0},
                 "2024": {"total": 2200000, "stock_awards": 0, "option_awards": 0},
                 "2025": {"total": 2100000, "stock_awards": 0, "option_awards": 0}},
         "tranches": [_rsu("c1", 2022, "2022-02-15", "2026-02-15")]},
    ],
}
CRAFTED_FIN = {
    "base_date": "2022-12-31", "csm_label": "Operating Income",
    "years": [{"fy": 2023, "peer_tsr_value": 110.0, "net_income_usd": 10000000, "csm_usd": 40000000},
              {"fy": 2024, "peer_tsr_value": 95.0, "net_income_usd": -5000000, "csm_usd": 30000000},
              {"fy": 2025, "peer_tsr_value": 140.0, "net_income_usd": 30000000, "csm_usd": 60000000}],
}

pvp = P.PayVersusPerformance(CRAFTED, CRAFTED_FIN)
peo = pvp.peo
b24 = P.cap_for_neo_year(pvp, peo, 2024)
c = b24["components"]

# hand-computed expectations for FY2024 (year-end price 90, prior year-end 120, vest date 2024-06-30 = 110):
ok(c["less_sct_equity_fv"] == -300000.0, "less SCT equity FV = -(stock+option awards reported for 2024)")
ok(c["ye_fv_new_grants"] == 90.0 * SH, "new-grant YE FV = year-end price x shares (t1)")
ok(c["change_fv_prior_unvested"] == (90.0 - 120.0) * SH, "prior-unvested Δ = (YE - prior YE) x shares (t2)")
ok(c["vest_fv_new_grants"] == 110.0 * SH, "new-grant vested FV = vest-date price x shares (t3)")
ok(c["change_fv_to_vest_prior"] == (110.0 - 120.0) * SH, "prior-vested Δ = (vest - prior YE) x shares (t4)")
ok(c["less_forfeited_prior_ye_fv"] == -120.0 * SH, "forfeited term = -(prior YE FV) x shares (t5)")
ok(c["dividends"] == 8000.0, "FY2024 dividends = t2's 3000 + forfeited t5's 5000 (paid before forfeiture)")

expected_equity_adj = (-300000.0 + 90.0 * SH + (90.0 - 120.0) * SH + 110.0 * SH
                       + (110.0 - 120.0) * SH - 120.0 * SH + 8000.0)
ok(approx(b24["equity_adjustment"], expected_equity_adj), "equity adjustment = sum of the 402(v) terms + dividends")
ok(approx(b24["cap"], 6000000 + expected_equity_adj), "CAP = SCT total + equity adjustment (no pension)")

# dividends are YEAR-SPECIFIC: FY2023 adds only t2's 2000; FY2025 adds NOTHING (no carryover/repeat)
b23 = P.cap_for_neo_year(pvp, peo, 2023)
ok(b23["components"]["dividends"] == 2000.0, "FY2023 dividends = t2's 2000 only")
b25 = P.cap_for_neo_year(pvp, peo, 2025)
ok(b25["components"]["dividends"] == 0.0, "FY2025 dividends = 0 — a tranche-level amount never repeats across years")

# t6 vested in 2023 (before 2024) — it must not appear anywhere in the 2024 bridge
ok(P._resolved_before(pvp._load_tranche("peo", _rsu("t6", 2020, "2020-02-15", "2023-02-15")),
                      P.date(2024, 1, 1)), "a tranche vested before the year is out of the roll-forward")

# --- the itemized bridge must tie to reported CAP for EVERY NEO and covered year ---
for neo in pvp.neos:
    for fy in pvp.fiscal_years:
        br = P.cap_for_neo_year(pvp, neo, fy)
        total0 = br["bridge"][0][1]
        adj = sum(v for _l, v, k in br["bridge"] if k != "total")
        ok(approx(total0 + adj, br["cap"], tol=0.02),
           f"bridge ties to CAP for {neo['id']} FY{fy}")

# --- company TSR ties to the price path ---
table = P.pvp_table(pvp)
ok(len(table["rows"]) == 3 and [r["fy"] for r in table["rows"]] == [2023, 2024, 2025],
   "table builds one row per covered fiscal year, in order")
ok(approx(table["rows"][1]["company_tsr_value"], 100.0 * 90.0 / 100.0), "company TSR($100) = 100 x price(YE)/price(base)")
ok(approx(table["rows"][2]["company_tsr_value"], 100.0 * 150.0 / 100.0), "company TSR compounds on the same price path")

# --- average of the non-PEO column ---
peo24 = P.cap_for_neo_year(pvp, pvp.peo, 2024)["cap"]
ok(table["rows"][1]["peo_cap"] == peo24, "PEO CAP column = PEO reconciliation")
cfo24 = P.cap_for_neo_year(pvp, pvp.non_peo[0], 2024)["cap"]
ok(approx(table["rows"][1]["avg_nonpeo_cap"], cfo24), "avg non-PEO CAP = mean over non-PEO NEOs (one here)")

# --- alignment direction logic ---
al = P.alignment(table)
ok(al["cap_direction"] in ("up", "down", "flat") and isinstance(al["aligned"], bool), "alignment reports a direction")

# --- determinism ---
ok(P.pvp_table(P.PayVersusPerformance(CRAFTED, CRAFTED_FIN)) == table, "table is deterministic across builds")

# --- fail-closed contract ---
def bad(mutate, label):
    aw = json.loads(json.dumps(CRAFTED))
    fn = json.loads(json.dumps(CRAFTED_FIN))
    mutate(aw, fn)
    try:
        P.PayVersusPerformance(aw, fn)
        ok(False, f"{label} must raise")
    except P.PVPError:
        ok(True, f"fails closed: {label}")


bad(lambda a, f: a.update({"fiscal_years": [2024, 2025]}), "fewer than three covered years")
bad(lambda a, f: a["neos"].append({"id": "x", "role": "CTO", "is_peo": True,
    "sct": {"2023": {"total": 1}, "2024": {"total": 1}, "2025": {"total": 1}}, "tranches": []}),
    "two principal executive officers")
bad(lambda a, f: [n.update({"is_peo": False}) for n in a["neos"]], "no principal executive officer")
bad(lambda a, f: a["neos"][0]["tranches"].append(
    {"id": "z", "type": "warrant", "grant_fy": 2024, "grant_date": "2024-01-01", "vest_date": "2026-01-01"}),
    "an unknown award type")
bad(lambda a, f: f["years"].pop(), "financials missing a covered year")

# a missing measurement-date price fails closed at scoring time, not silently
aw = json.loads(json.dumps(CRAFTED))
del aw["price_by_date"]["2024-12-31"]
try:
    P.pvp_table(P.PayVersusPerformance(aw, CRAFTED_FIN))
    ok(False, "missing YE price must raise at scoring")
except P.PVPError:
    ok(True, "missing measurement-date price fails closed at scoring")


# ------------------------------------------------------------------ adversarial regressions
# (each of these was a confirmed silent-wrong-number or fail-open path in review — must stay dead)

# REGRESSION: duplicate tranche ids poisoned the PSU Monte Carlo memo (one award served another's value)
bad(lambda a, f: a["neos"][0]["tranches"].extend([
    {"id": "dup", "type": "psu_rtsr", "grant_fy": 2024, "grant_date": "2024-02-15", "target_shares": 100,
     "vest_date": "2027-02-15", "performance_end": "2027-02-15", "forfeited": False},
    {"id": "dup", "type": "psu_rtsr", "grant_fy": 2024, "grant_date": "2024-02-15", "target_shares": 200,
     "vest_date": "2027-02-15", "performance_end": "2027-02-15", "forfeited": False}]),
    "duplicate tranche ids (MC-cache poisoning)")

# REGRESSION: the legacy tranche-level dividends scalar double-counted across covered years
bad(lambda a, f: a["neos"][0]["tranches"][0].update({"dividends_paid_unvested": 5000}),
    "the retired tranche-level dividends scalar")

# REGRESSION: grant_fy contradicting grant_date mis-bucketed prior-year awards as covered-year grants
bad(lambda a, f: a["neos"][0]["tranches"].append(
    {"id": "gx", "type": "rsu", "grant_fy": 2024, "grant_date": "2023-12-01", "shares": 1000,
     "vest_date": "2024-06-01", "forfeited": False}),
    "grant_fy that contradicts grant_date")
bad(lambda a, f: a["neos"][0]["tranches"].append(
    {"id": "gy", "type": "rsu", "grant_fy": 2024, "grant_date": "2025-01-15", "shares": 1000,
     "vest_date": "2027-01-15", "forfeited": False}),
    "a grant dated after its claimed fiscal year")

# REGRESSION: a pre-netted pension scalar hid the rule's three buckets
bad(lambda a, f: a["neos"][0].update({"pension_adjustment_by_fy": {"2024": 100000}}),
    "a pre-netted pension scalar (buckets required)")

# pension three-bucket math: net = service + prior service - change in actuarial PV
aw = json.loads(json.dumps(CRAFTED))
aw["neos"][0]["pension_adjustment_by_fy"] = {
    "2024": {"service_cost": 40000, "prior_service_cost": 10000, "change_in_actuarial_pv": 30000}}
pb = P.cap_for_neo_year(P.PayVersusPerformance(aw, CRAFTED_FIN), P.PayVersusPerformance(aw, CRAFTED_FIN).peo, 2024)
ok(approx(pb["pension_adjustment"], 40000 + 10000 - 30000), "pension = service + prior service - actuarial change")
ok(approx(pb["cap"], b24["cap"] + 20000), "pension bucket flows into CAP")

# REGRESSION: non-calendar fiscal years dropped first-half activity (y_start was a bare Jan 1)
NCF_AW = {
    "subject": {"ticker": "ACMQ", "name": "Acme"}, "fiscal_years": [2023, 2024, 2025],
    "fiscal_year_end": "06-30",
    "price_by_date": {"2022-06-30": 90.0, "2023-06-30": 100.0, "2023-10-01": 110.0,
                      "2024-06-30": 120.0, "2025-06-30": 120.0},
    "market": CRAFTED["market"],
    "neos": [{"id": "p", "role": "CEO", "is_peo": True,
              "sct": {str(y): {"total": 1000000, "stock_awards": 50000 if y == 2024 else 0,
                               "option_awards": 0} for y in (2023, 2024, 2025)},
              "tranches": [{"id": "n1", "type": "rsu", "grant_fy": 2024, "grant_date": "2023-08-01",
                            "shares": 1000, "vest_date": "2023-10-01", "forfeited": False}]},
             {"id": "o", "role": "CFO", "is_peo": False,
              "sct": {str(y): {"total": 1, "stock_awards": 0, "option_awards": 0} for y in (2023, 2024, 2025)},
              "tranches": []}]}
NCF_FIN = {"base_date": "2022-06-30", "csm_label": "OI",
           "years": [{"fy": y, "peer_tsr_value": 100.0, "net_income_usd": 1.0, "csm_usd": 1.0}
                     for y in (2023, 2024, 2025)]}
ncf = P.PayVersusPerformance(NCF_AW, NCF_FIN)
nb = P.cap_for_neo_year(ncf, ncf.peo, 2024)
ok(nb["components"]["vest_fv_new_grants"] == 110.0 * 1000,
   "06-30 FYE: an Aug-grant Oct-vest inside FY2024 (Jul23-Jun24) is counted as vested-in-year")
ok(approx(nb["cap"], 1000000 - 50000 + 110000), "non-calendar FY CAP = SCT - grant FV + vest FV")

# REGRESSION: a closed PSU performance period silently assumed a 100%-of-target payout
aw = json.loads(json.dumps(CRAFTED))
aw["neos"][0]["tranches"].append(
    {"id": "px1", "type": "psu_rtsr", "grant_fy": 2022, "grant_date": "2022-02-15", "target_shares": 100,
     "vest_date": "2024-06-30", "performance_end": "2024-06-30", "forfeited": False})
try:
    P.cap_for_neo_year((pp := P.PayVersusPerformance(aw, CRAFTED_FIN)), pp.peo, 2024)
    ok(False, "closed PSU period without earned_payout_pct must raise")
except P.PVPError:
    ok(True, "closed PSU period without earned_payout_pct fails closed (no silent target payout)")
aw["neos"][0]["tranches"][-1]["earned_payout_pct"] = 150
pp = P.PayVersusPerformance(aw, CRAFTED_FIN)
vb = P.cap_for_neo_year(pp, pp.peo, 2024)
# the bucket now carries t4's (110-120)x1000 plus px1's (vest FV - prior-YE MC value), where the
# closed-period vest FV = vest price 110 x 100 target x 150% earned = 16,500
px1 = pp.peo["tranches"][-1]
px1_prior = pp.fair_value(px1, P.date(2023, 12, 31))       # open period at prior YE -> Monte Carlo value
expected_bucket = (110.0 - 120.0) * SH + (110.0 * 100 * 1.5 - px1_prior)
ok(approx(vb["components"]["change_fv_to_vest_prior"], expected_bucket, tol=0.01),
   "closed PSU vests at price x target x earned% (150%), netted against its prior-YE Monte Carlo value")
bad(lambda a, f: a["neos"][0]["tranches"].append(
    {"id": "px2", "type": "psu_rtsr", "grant_fy": 2022, "grant_date": "2022-02-15", "target_shares": 100,
     "vest_date": "2024-06-30", "performance_end": "2024-06-30", "forfeited": False, "earned_payout_pct": 400}),
    "earned_payout_pct outside [0, 300]")

# REGRESSION: extreme-but-finite inputs escaped as raw OverflowError/ValueError
for args in ((100, 100, 100, -1000, 0.2), (1e-308, 1e308, 1, 0.05, 0.2), (100, 100, 1, 0.05, 50.0)):
    try:
        P.bs_call(*args)
        ok(False, f"bs_call{args} must fail closed")
    except P.PVPError:
        ok(True, f"bs_call rejects extreme input {args} with a clean PVPError")


# --------------------------------------------------------------------------- shipped sample reconciles end-to-end
DATA = ROOT / "examples" / "pay-versus-performance" / "data"
sample = P.PayVersusPerformance(
    json.loads((DATA / "awards.sample.json").read_text(encoding="utf-8")),
    json.loads((DATA / "pvp_financials.sample.json").read_text(encoding="utf-8")))
stable = P.pvp_table(sample)
for neo in sample.neos:
    for fy in sample.fiscal_years:
        br = P.cap_for_neo_year(sample, neo, fy)
        adj = sum(v for _l, v, k in br["bridge"] if k != "total")
        ok(approx(br["bridge"][0][1] + adj, br["cap"], tol=0.02),
           f"shipped sample bridge ties for {neo['id']} FY{fy}")
ship_align = P.alignment(stable)
ok(ship_align["cap_direction"] == "up" and ship_align["tsr_direction"] == "up" and ship_align["aligned"],
   "shipped sample shows PEO CAP rising with company TSR (pay-for-performance aligned)")
ok(P.pvp_table(sample) == stable, "shipped sample table is deterministic")

print(f"OK — {passed} Pay-versus-Performance engine checks passed "
      f"(shipped PEO CAP FY{stable['rows'][-1]['fy']} = ${stable['rows'][-1]['peo_cap']:,.0f}).")
