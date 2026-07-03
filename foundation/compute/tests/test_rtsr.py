#!/usr/bin/env python3
"""Evals for relative-TSR PSU tracking and Monte Carlo valuation.
Run: python3 foundation/compute/tests/test_rtsr.py
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from foundation.compute.rtsr import (  # noqa: E402
    PayoutCurve,
    RTSRError,
    calculate_tsr,
    evaluate_performance,
    monte_carlo_valuation,
    percentile_rank,
)
from foundation.compute.peers import REAL_TICKERS  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


curve = PayoutCurve([(25.0, 50.0), (55.0, 100.0), (75.0, 200.0)])

# ---- TSR formula: 30-day simple average end price + dividends, divided by start average ----
start = [10.0] * 30
end = [12.0] * 30
tsr = calculate_tsr(start, end, dividends=0.30)
ok(tsr["start_avg"] == 10.0 and tsr["end_avg"] == 12.0, "TSR uses simple 30-day averages")
ok(tsr["ratio"] == 1.23 and tsr["return_pct"] == 23.0, "TSR includes dividends in the numerator")

try:
    calculate_tsr([10.0] * 29, end, dividends=0.0, averaging_days=30)
    ok(False, "wrong averaging window is rejected")
except ValueError as e:
    ok("30" in str(e), "wrong averaging window fails closed")

# ---- Percentile convention: issuer ranked with the comparator companies, ties use average rank ----
ranked = {"ACMQ": 1.40, "PVAQ": 1.00, "PVBQ": 1.20, "PVCQ": 1.60, "PVDQ": 1.80}
ok(percentile_rank(ranked, "ACMQ") == 50.0, "middle rank in five names is the 50th percentile")
ties = {"ACMQ": 1.10, "PVAQ": 1.10, "PVBQ": 0.90, "PVCQ": 1.30}
ok(percentile_rank(ties, "ACMQ") == 50.0, "ties use average rank")

# ---- Payout curve: below threshold is zero; interpolation and max cap are explicit ----
ok(curve.payout(24.99) == 0.0, "below threshold pays zero")
ok(curve.payout(25.0) == 50.0, "25th percentile pays 50%")
ok(curve.payout(40.0) == 75.0, "linear interpolation between threshold and target")
ok(curve.payout(55.0) == 100.0, "55th percentile pays 100%")
ok(curve.payout(65.0) == 150.0, "linear interpolation between target and max")
ok(curve.payout(75.0) == 200.0 and curve.payout(99.0) == 200.0, "75th and above is capped at 200%")

try:
    PayoutCurve([(25.0, -50.0), (55.0, 100.0)])
    ok(False, "negative payout values are rejected")
except RTSRError:
    ok(True, "negative payout values are rejected")

# ---- Performance evaluation: only companies in the index at both endpoints are comparators ----
companies = [
    {"ticker": "ACMQ", "name": "Synthetic Issuer", "role": "issuer", "index_start": True, "index_end": True},
    {"ticker": "PVAQ", "name": "Peer One", "role": "peer", "index_start": True, "index_end": True},
    {"ticker": "PVBQ", "name": "Peer Two", "role": "peer", "index_start": True, "index_end": True},
    {"ticker": "PVCQ", "name": "Peer Three", "role": "peer", "index_start": True, "index_end": False},
]
prices = {
    "ACMQ": {"start": [10.0] * 30, "end": [14.0] * 30, "dividends": 0.0},
    "PVAQ": {"start": [10.0] * 30, "end": [12.0] * 30, "dividends": 0.0},
    "PVBQ": {"start": [10.0] * 30, "end": [16.0] * 30, "dividends": 0.0},
    "PVCQ": {"start": [10.0] * 30, "end": [20.0] * 30, "dividends": 0.0},
}
perf = evaluate_performance(companies, prices, curve)
ok(perf["included_peer_tickers"] == ["PVAQ", "PVBQ"], "comparators must be index members at start and end")
ok(perf["issuer_percentile"] == 50.0, "issuer percentile is ranked against included peers")
ok(round(perf["payout_percent"], 4) == 91.6667, "performance evaluation applies the payout curve")

real_ticker_companies = [dict(c) for c in companies]
real_ticker_companies[1]["ticker"] = "NOVA"
real_ticker_prices = dict(prices)
real_ticker_prices["NOVA"] = prices["PVAQ"]
try:
    evaluate_performance(real_ticker_companies, real_ticker_prices, curve)
    ok(False, "real ticker collisions are rejected in performance tracking")
except RTSRError as e:
    ok("real ticker" in str(e).lower() and "NOVA" in str(e),
       "real ticker collisions are rejected in performance tracking")
collision_examples = {"NOVA", "MTRX", "PULS", "JUNO", "KITE", "FLUX", "LUMA", "HUBX", "RIVR", "NSTR"}
ok(collision_examples <= REAL_TICKERS, "shared real-ticker deny-list covers rTSR collision examples")

# ---- round-5 boundary: the synthetic ticker SHAPE (ACMQ or 3 letters + Q) rejects a real peer ticker that
# merely CONTAINS a Q (QTWO / QLYS / MQ) — the old 'contains Q' test let those through ----
for real_q in ("QTWO", "QLYS", "MQ", "GTLB"):
    q_companies = [dict(c) for c in companies]
    q_companies[1]["ticker"] = real_q
    q_prices = dict(prices)
    q_prices[real_q] = prices["PVAQ"]
    try:
        evaluate_performance(q_companies, q_prices, curve)
        ok(False, f"real ticker {real_q} must be rejected by the synthetic shape")
    except RTSRError:
        ok(True, f"real ticker {real_q} rejected by synthetic shape (not treated as Q-marked synthetic)")

# ---- round-5 boundary: a synthetic ticker carrying a REAL company NAME is rejected (no fabricated TSR
# figure may attach to a real company) ----
for variant in ("GitLab Inc.", "GitLab Inc", "GITLAB, INCORPORATED", "  GitLab  Inc  ",
                "Descartes Systems Group", "Descartes Systems", "ZoomInfo"):   # recognizable SHORT FORMS
    name_companies = [dict(c) for c in companies]
    name_companies[1]["name"] = variant            # a real company name (punctuation/suffix variant) under a synthetic ticker
    try:
        evaluate_performance(name_companies, prices, curve)
        ok(False, f"a real company name {variant!r} under a synthetic ticker must be rejected")
    except RTSRError as e:
        ok("real company name" in str(e).lower(), f"real company name variant {variant!r} is rejected (canonicalized match)")

# ---- Monte Carlo valuation: deterministic seed, risk-neutral stock-settled payoff, no real data needed ----
valuation_input = {
    "issuer": "ACMQ",
    "tickers": ["ACMQ", "PVAQ", "PVBQ", "PVCQ"],
    "spot_prices": {"ACMQ": 100.0, "PVAQ": 100.0, "PVBQ": 100.0, "PVCQ": 100.0},
    "volatilities": {"ACMQ": 0.0, "PVAQ": 0.0, "PVBQ": 0.0, "PVCQ": 0.0},
    "dividend_yields": {"ACMQ": 0.0, "PVAQ": 0.0, "PVBQ": 0.0, "PVCQ": 0.0},
    "correlations": {a: {b: (1.0 if a == b else 0.0) for b in ["ACMQ", "PVAQ", "PVBQ", "PVCQ"]}
                     for a in ["ACMQ", "PVAQ", "PVBQ", "PVCQ"]},
    "risk_free_rate": 0.0,
    "performance_years": 3.0,
    "paths": 256,
    "seed": 718,
}
v1 = monte_carlo_valuation(valuation_input, curve)
v2 = monte_carlo_valuation(valuation_input, curve)
ok(v1 == v2, "Monte Carlo valuation is deterministic for the same seed")
ok(v1["mean_percentile"] == 50.0, "zero-vol equal-return paths tie at the 50th percentile")
ok(round(v1["fair_value_per_target_share"], 4) == 91.6667, "stock-settled fair value discounts expected payout")
ok(v1["payout_distribution"]["p50"] == 91.6667, "valuation returns payout distribution percentiles")
ok("fair_value_standard_error" in v1 and v1["fair_value_standard_error"] == 0.0,
   "Monte Carlo valuation reports standard error; zero-vol paths have zero SE")

dividend_case = dict(valuation_input)
dividend_case["tickers"] = ["ACMQ", "PVAQ"]
dividend_case["spot_prices"] = {"ACMQ": 100.0, "PVAQ": 100.0}
dividend_case["volatilities"] = {"ACMQ": 0.0, "PVAQ": 0.0}
dividend_case["dividend_yields"] = {"ACMQ": 0.10, "PVAQ": 0.0}
dividend_case["correlations"] = {
    "ACMQ": {"ACMQ": 1.0, "PVAQ": 0.0},
    "PVAQ": {"ACMQ": 0.0, "PVAQ": 1.0},
}
dividend_case["paths"] = 32
vd = monte_carlo_valuation(dividend_case, curve)
ok(vd["mean_percentile"] == 50.0 and round(vd["mean_payout_percent"], 4) == 91.6667,
   "Monte Carlo ranks total return, so dividend yield is not penalized as price underperformance")

bad_paths = dict(valuation_input)
bad_paths["paths"] = "five thousand"
try:
    monte_carlo_valuation(bad_paths, curve)
    ok(False, "malformed path count is rejected with a controlled error")
except RTSRError:
    ok(True, "malformed path count is rejected with a controlled error")

bad_paths_float = dict(valuation_input)
bad_paths_float["paths"] = 12.5
try:
    monte_carlo_valuation(bad_paths_float, curve)
    ok(False, "non-integer path count is rejected")
except RTSRError:
    ok(True, "non-integer path count is rejected")

bad_real_ticker = dict(valuation_input)
bad_real_ticker["tickers"] = ["ACMQ", "NOVA"]
bad_real_ticker["spot_prices"] = {"ACMQ": 100.0, "NOVA": 100.0}
bad_real_ticker["volatilities"] = {"ACMQ": 0.0, "NOVA": 0.0}
bad_real_ticker["dividend_yields"] = {"ACMQ": 0.0, "NOVA": 0.0}
bad_real_ticker["correlations"] = {
    "ACMQ": {"ACMQ": 1.0, "NOVA": 0.0},
    "NOVA": {"ACMQ": 0.0, "NOVA": 1.0},
}
try:
    monte_carlo_valuation(bad_real_ticker, curve)
    ok(False, "real ticker collisions are rejected in Monte Carlo inputs")
except RTSRError as e:
    ok("real ticker" in str(e).lower() and "NOVA" in str(e),
       "real ticker collisions are rejected in Monte Carlo inputs")

# round-6: the MC path must also reject real tickers that merely CONTAIN Q (the shape guard, not just the
# deny-list) — a regression to "contains Q" logic on this path would otherwise slip QTWO/QLYS/MQ through
for real_q in ("QTWO", "QLYS", "MQ"):
    mc_q = dict(valuation_input)
    mc_q["tickers"] = ["ACMQ", real_q]
    mc_q["spot_prices"] = {"ACMQ": 100.0, real_q: 100.0}
    mc_q["volatilities"] = {"ACMQ": 0.0, real_q: 0.0}
    mc_q["dividend_yields"] = {"ACMQ": 0.0, real_q: 0.0}
    mc_q["correlations"] = {"ACMQ": {"ACMQ": 1.0, real_q: 0.0}, real_q: {"ACMQ": 0.0, real_q: 1.0}}
    try:
        monte_carlo_valuation(mc_q, curve)
        ok(False, f"MC path must reject real ticker {real_q} by shape")
    except RTSRError:
        ok(True, f"MC path rejects real ticker {real_q} by the synthetic shape (not treated as Q-marked)")

try:
    curve.payout(100.1)
    ok(False, "out-of-range percentile payout is rejected")
except RTSRError:
    ok(True, "out-of-range percentile payout is rejected")

tied_companies = [
    {"ticker": "ACMQ", "name": "Issuer", "role": "issuer", "index_start": True, "index_end": True},
    {"ticker": "PVAQ", "name": "Peer One", "role": "peer", "index_start": True, "index_end": True},
    {"ticker": "PVBQ", "name": "Peer Two", "role": "peer", "index_start": True, "index_end": True},
]
tied_prices = {
    "ACMQ": {"start": [10.0] * 30, "end": [12.0] * 30, "dividends": 0.0},
    "PVAQ": {"start": [10.0] * 30, "end": [12.0] * 30, "dividends": 0.0},
    "PVBQ": {"start": [10.0] * 30, "end": [9.0] * 30, "dividends": 0.0},
}
tied = evaluate_performance(tied_companies, tied_prices, curve)
ok([r["rank"] for r in tied["ranked"] if r["ticker"] in ("ACMQ", "PVAQ")] == [1, 1],
   "display ranks are tie-aware and match percentile tie handling")

print(f"OK — {passed} rTSR engine checks passed.")
