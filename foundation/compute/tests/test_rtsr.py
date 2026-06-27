#!/usr/bin/env python3
"""Evals for relative-TSR PSU tracking and Monte Carlo valuation.
Run: python foundation/compute/tests/test_rtsr.py
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
ranked = {"ISSUER": 1.40, "P1": 1.00, "P2": 1.20, "P3": 1.60, "P4": 1.80}
ok(percentile_rank(ranked, "ISSUER") == 50.0, "middle rank in five names is the 50th percentile")
ties = {"ISSUER": 1.10, "P1": 1.10, "P2": 0.90, "P3": 1.30}
ok(percentile_rank(ties, "ISSUER") == 50.0, "ties use average rank")

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
    {"ticker": "ISSUER", "name": "Synthetic Issuer", "role": "issuer", "index_start": True, "index_end": True},
    {"ticker": "P1", "name": "Peer One", "role": "peer", "index_start": True, "index_end": True},
    {"ticker": "P2", "name": "Peer Two", "role": "peer", "index_start": True, "index_end": True},
    {"ticker": "P3", "name": "Peer Three", "role": "peer", "index_start": True, "index_end": False},
]
prices = {
    "ISSUER": {"start": [10.0] * 30, "end": [14.0] * 30, "dividends": 0.0},
    "P1": {"start": [10.0] * 30, "end": [12.0] * 30, "dividends": 0.0},
    "P2": {"start": [10.0] * 30, "end": [16.0] * 30, "dividends": 0.0},
    "P3": {"start": [10.0] * 30, "end": [20.0] * 30, "dividends": 0.0},
}
perf = evaluate_performance(companies, prices, curve)
ok(perf["included_peer_tickers"] == ["P1", "P2"], "comparators must be index members at start and end")
ok(perf["issuer_percentile"] == 50.0, "issuer percentile is ranked against included peers")
ok(round(perf["payout_percent"], 4) == 91.6667, "performance evaluation applies the payout curve")

real_ticker_companies = [dict(c) for c in companies]
real_ticker_companies[1]["ticker"] = "NOVA"
real_ticker_prices = dict(prices)
real_ticker_prices["NOVA"] = prices["P1"]
try:
    evaluate_performance(real_ticker_companies, real_ticker_prices, curve)
    ok(False, "real ticker collisions are rejected in performance tracking")
except RTSRError as e:
    ok("real ticker" in str(e).lower() and "NOVA" in str(e),
       "real ticker collisions are rejected in performance tracking")
collision_examples = {"NOVA", "MTRX", "PULS", "JUNO", "KITE", "FLUX", "LUMA", "HUBX", "RIVR", "NSTR"}
ok(collision_examples <= REAL_TICKERS, "shared real-ticker deny-list covers rTSR collision examples")

# ---- Monte Carlo valuation: deterministic seed, risk-neutral stock-settled payoff, no real data needed ----
valuation_input = {
    "issuer": "ISSUER",
    "tickers": ["ISSUER", "P1", "P2", "P3"],
    "spot_prices": {"ISSUER": 100.0, "P1": 100.0, "P2": 100.0, "P3": 100.0},
    "volatilities": {"ISSUER": 0.0, "P1": 0.0, "P2": 0.0, "P3": 0.0},
    "dividend_yields": {"ISSUER": 0.0, "P1": 0.0, "P2": 0.0, "P3": 0.0},
    "correlations": {a: {b: (1.0 if a == b else 0.0) for b in ["ISSUER", "P1", "P2", "P3"]}
                     for a in ["ISSUER", "P1", "P2", "P3"]},
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
dividend_case["tickers"] = ["ISSUER", "P1"]
dividend_case["spot_prices"] = {"ISSUER": 100.0, "P1": 100.0}
dividend_case["volatilities"] = {"ISSUER": 0.0, "P1": 0.0}
dividend_case["dividend_yields"] = {"ISSUER": 0.10, "P1": 0.0}
dividend_case["correlations"] = {
    "ISSUER": {"ISSUER": 1.0, "P1": 0.0},
    "P1": {"ISSUER": 0.0, "P1": 1.0},
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
bad_real_ticker["tickers"] = ["ISSUER", "NOVA"]
bad_real_ticker["spot_prices"] = {"ISSUER": 100.0, "NOVA": 100.0}
bad_real_ticker["volatilities"] = {"ISSUER": 0.0, "NOVA": 0.0}
bad_real_ticker["dividend_yields"] = {"ISSUER": 0.0, "NOVA": 0.0}
bad_real_ticker["correlations"] = {
    "ISSUER": {"ISSUER": 1.0, "NOVA": 0.0},
    "NOVA": {"ISSUER": 0.0, "NOVA": 1.0},
}
try:
    monte_carlo_valuation(bad_real_ticker, curve)
    ok(False, "real ticker collisions are rejected in Monte Carlo inputs")
except RTSRError as e:
    ok("real ticker" in str(e).lower() and "NOVA" in str(e),
       "real ticker collisions are rejected in Monte Carlo inputs")

try:
    curve.payout(100.1)
    ok(False, "out-of-range percentile payout is rejected")
except RTSRError:
    ok(True, "out-of-range percentile payout is rejected")

tied_companies = [
    {"ticker": "ISSUER", "name": "Issuer", "role": "issuer", "index_start": True, "index_end": True},
    {"ticker": "P1", "name": "Peer One", "role": "peer", "index_start": True, "index_end": True},
    {"ticker": "P2", "name": "Peer Two", "role": "peer", "index_start": True, "index_end": True},
]
tied_prices = {
    "ISSUER": {"start": [10.0] * 30, "end": [12.0] * 30, "dividends": 0.0},
    "P1": {"start": [10.0] * 30, "end": [12.0] * 30, "dividends": 0.0},
    "P2": {"start": [10.0] * 30, "end": [9.0] * 30, "dividends": 0.0},
}
tied = evaluate_performance(tied_companies, tied_prices, curve)
ok([r["rank"] for r in tied["ranked"] if r["ticker"] in ("ISSUER", "P1")] == [1, 1],
   "display ranks are tie-aware and match percentile tie handling")

print(f"OK — {passed} rTSR engine checks passed.")
