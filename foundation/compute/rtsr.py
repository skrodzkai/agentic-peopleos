#!/usr/bin/env python3
"""Relative-TSR PSU tracking and illustrative Monte Carlo valuation.

This module is deterministic math only. It does not fetch market data, select peers, or issue
accounting advice. Callers supply already-approved plan terms, prices, dividends, and valuation
assumptions; this module computes repeatable tracking and valuation outputs from those inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import random

from foundation.compute.peers import REAL_TICKERS


class RTSRError(ValueError):
    """Raised when rTSR inputs are structurally invalid."""


def _as_float(value, name):
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise RTSRError(f"{name} must be numeric") from exc
    if not math.isfinite(out):
        raise RTSRError(f"{name} must be finite")
    return out


def _mean(values, name, expected_len=None):
    vals = [_as_float(v, name) for v in values]
    if expected_len is not None and len(vals) != expected_len:
        raise RTSRError(f"{name} must contain exactly {expected_len} prices")
    if not vals:
        raise RTSRError(f"{name} must not be empty")
    if any(v <= 0 for v in vals):
        raise RTSRError(f"{name} prices must be positive")
    return sum(vals) / len(vals)


def calculate_tsr(start_prices, end_prices, dividends=0.0, averaging_days=30):
    """Compute TSR as (ending average price + dividends) / beginning average price.

    Public software-company rTSR designs often use 30-day simple average closing prices at the
    beginning/end of the performance period. Dividends are modeled as a cash amount paid during the
    period, not reinvested, because plan documents vary and the chosen convention must be explicit.
    """
    if averaging_days <= 0:
        raise RTSRError("averaging_days must be positive")
    start_avg = _mean(start_prices, "start_prices", averaging_days)
    end_avg = _mean(end_prices, "end_prices", averaging_days)
    div = _as_float(dividends, "dividends")
    if div < 0:
        raise RTSRError("dividends must be non-negative")
    ratio = (end_avg + div) / start_avg
    return {
        "start_avg": round(start_avg, 6),
        "end_avg": round(end_avg, 6),
        "dividends": round(div, 6),
        "ratio": round(ratio, 6),
        "return_pct": round((ratio - 1.0) * 100.0, 4),
    }


def percentile_rank(values_by_ticker, issuer):
    """Return issuer percentile on a 0-100 scale, with average rank for ties.

    The issuer is ranked together with the comparator companies. Lowest TSR receives 0th percentile,
    highest receives 100th percentile. A middle rank in a five-name set is therefore 50th percentile.
    """
    if issuer not in values_by_ticker:
        raise RTSRError(f"issuer {issuer!r} missing from values")
    vals = {str(k): _as_float(v, f"value for {k}") for k, v in values_by_ticker.items()}
    if len(vals) < 2:
        raise RTSRError("at least issuer plus one comparator is required")
    issuer_value = vals[issuer]
    ordered = sorted(vals.values())
    positions = [i for i, v in enumerate(ordered) if v == issuer_value]
    if not positions:
        raise RTSRError("issuer value not rankable")
    avg_index = sum(positions) / len(positions)
    return round(100.0 * avg_index / (len(ordered) - 1), 4)


@dataclass(frozen=True)
class PayoutCurve:
    """Piecewise-linear percentile-to-payout curve."""

    points: tuple

    def __init__(self, points):
        pts = tuple((_as_float(p, "payout percentile"), _as_float(v, "payout value")) for p, v in points)
        if len(pts) < 2:
            raise RTSRError("payout curve requires at least two points")
        if pts != tuple(sorted(pts)):
            raise RTSRError("payout curve points must be sorted by percentile")
        if pts[0][0] < 0 or pts[-1][0] > 100:
            raise RTSRError("payout percentiles must be between 0 and 100")
        if any(v < 0 for _p, v in pts):
            raise RTSRError("payout values must be non-negative")
        for (p0, _v0), (p1, _v1) in zip(pts, pts[1:]):
            if p1 <= p0:
                raise RTSRError("payout curve percentiles must be strictly increasing")
        object.__setattr__(self, "points", pts)

    def payout(self, percentile):
        p = _as_float(percentile, "percentile")
        if p < 0 or p > 100:
            raise RTSRError("percentile must be between 0 and 100")
        if p < self.points[0][0]:
            return 0.0
        if p >= self.points[-1][0]:
            return round(self.points[-1][1], 4)
        for (p0, v0), (p1, v1) in zip(self.points, self.points[1:]):
            if p0 <= p <= p1:
                f = (p - p0) / (p1 - p0)
                return round(v0 + f * (v1 - v0), 4)
        return round(self.points[-1][1], 4)

    @property
    def max_payout(self):
        return max(v for _p, v in self.points)


def _ticker(company):
    t = str(company.get("ticker", "")).strip().upper()
    if not t:
        raise RTSRError("company is missing ticker")
    return t


def _reject_real_tickers(tickers, label):
    # This universe is synthetic by construction (subject ACMQ + Q-marked issuers like AXQA/BEXQ). Enforce the
    # synthetic SHAPE — 'ACMQ' or contains 'Q' — which structurally rejects any real ticker (a real peer like
    # GTLB/KVYO has no Q), a strictly stronger guard than the static deny-list.
    ups = {str(t).strip().upper() for t in tickers}
    hits = sorted(ups & REAL_TICKERS)
    if hits:
        raise RTSRError(f"{label} contains real ticker collision(s): {', '.join(hits)}")
    non_synth = sorted(t for t in ups if t != "ACMQ" and "Q" not in t)
    if non_synth:
        raise RTSRError(f"{label} must be synthetic tickers (ACMQ or Q-marked); got: {', '.join(non_synth[:5])}")


def evaluate_performance(companies, prices, payout_curve, issuer_role="issuer", averaging_days=30):
    """Compute actual rTSR percentile and payout from supplied price/dividend observations."""
    if not companies:
        raise RTSRError("companies must not be empty")
    _reject_real_tickers((_ticker(c) for c in companies), "companies")
    issuers = [c for c in companies if c.get("role") == issuer_role]
    if len(issuers) != 1:
        raise RTSRError("exactly one issuer is required")
    issuer = _ticker(issuers[0])
    included = []
    excluded = []
    for c in companies:
        t = _ticker(c)
        if t == issuer:
            continue
        if bool(c.get("index_start")) and bool(c.get("index_end")):
            included.append(c)
        else:
            excluded.append(c)
    if not included:
        raise RTSRError("at least one included peer is required")

    tsrs = {}
    rows = []
    for c in [issuers[0]] + included:
        t = _ticker(c)
        if t not in prices:
            raise RTSRError(f"missing prices for {t}")
        p = prices[t]
        tsr = calculate_tsr(p.get("start", []), p.get("end", []), p.get("dividends", 0.0), averaging_days)
        tsrs[t] = tsr["ratio"]
        rows.append({"ticker": t, "name": c.get("name", t), "role": c.get("role", "peer"), "tsr": tsr})

    pct = percentile_rank(tsrs, issuer)
    payout = payout_curve.payout(round(pct, 2))
    rows.sort(key=lambda r: (-r["tsr"]["ratio"], r["ticker"]))
    rank = 0
    last_ratio = None
    for i, row in enumerate(rows, start=1):
        if last_ratio is None or row["tsr"]["ratio"] != last_ratio:
            rank = i
            last_ratio = row["tsr"]["ratio"]
        row["rank"] = rank
        row["percentile"] = percentile_rank(tsrs, row["ticker"])

    return {
        "issuer": issuer,
        "issuer_tsr": rows[[r["ticker"] for r in rows].index(issuer)]["tsr"],
        "issuer_percentile": round(pct, 2),
        "payout_percent": round(payout, 4),
        "included_peer_tickers": sorted(_ticker(c) for c in included),
        "excluded_peer_tickers": sorted(_ticker(c) for c in excluded),
        "ranked": rows,
        "n_ranked": len(rows),
    }


def _cholesky(matrix):
    n = len(matrix)
    l = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(l[i][k] * l[j][k] for k in range(j))
            if i == j:
                val = matrix[i][i] - s
                if val < -1e-9:
                    raise RTSRError("correlation matrix is not positive semidefinite")
                l[i][j] = math.sqrt(max(val, 0.0))
            else:
                l[i][j] = 0.0 if abs(l[j][j]) < 1e-12 else (matrix[i][j] - s) / l[j][j]
    return l


def _correlated_normals(rng, chol):
    z = [rng.gauss(0.0, 1.0) for _ in chol]
    return [sum(row[j] * z[j] for j in range(i + 1)) for i, row in enumerate(chol)]


def _percentile(sorted_values, q):
    if not sorted_values:
        raise RTSRError("cannot percentile empty series")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    f = pos - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * f


def _validate_mc_inputs(inp):
    tickers = [str(t).strip().upper() for t in inp.get("tickers", [])]
    if len(tickers) < 2 or len(set(tickers)) != len(tickers):
        raise RTSRError("tickers must contain unique issuer plus peers")
    _reject_real_tickers(tickers, "tickers")
    issuer = str(inp.get("issuer", "")).strip().upper()
    if issuer not in tickers:
        raise RTSRError("issuer must be included in tickers")
    raw_paths = inp.get("paths", 0)
    try:
        if isinstance(raw_paths, bool):
            raise TypeError
        if isinstance(raw_paths, int):
            paths = raw_paths
        elif isinstance(raw_paths, str) and raw_paths.strip().isdigit():
            paths = int(raw_paths)
        else:
            raise TypeError
    except (TypeError, ValueError) as exc:
        raise RTSRError("paths must be an integer") from exc
    if paths < 1:
        raise RTSRError("paths must be positive")
    years = _as_float(inp.get("performance_years"), "performance_years")
    if years <= 0:
        raise RTSRError("performance_years must be positive")
    rf = _as_float(inp.get("risk_free_rate"), "risk_free_rate")
    spot = inp.get("spot_prices", {})
    vols = inp.get("volatilities", {})
    divs = inp.get("dividend_yields", {})
    corrs = inp.get("correlations", {})
    for t in tickers:
        if _as_float(spot.get(t), f"spot {t}") <= 0:
            raise RTSRError(f"spot {t} must be positive")
        if _as_float(vols.get(t), f"volatility {t}") < 0:
            raise RTSRError(f"volatility {t} must be non-negative")
        if _as_float(divs.get(t, 0.0), f"dividend yield {t}") < 0:
            raise RTSRError(f"dividend yield {t} must be non-negative")
    matrix = []
    for a in tickers:
        row = []
        for b in tickers:
            v = _as_float(corrs.get(a, {}).get(b), f"correlation {a}/{b}")
            if v < -1.0 or v > 1.0:
                raise RTSRError("correlations must be between -1 and 1")
            row.append(v)
        matrix.append(row)
    for i in range(len(tickers)):
        if abs(matrix[i][i] - 1.0) > 1e-9:
            raise RTSRError("correlation diagonal must be 1")
        for j in range(i):
            if abs(matrix[i][j] - matrix[j][i]) > 1e-9:
                raise RTSRError("correlation matrix must be symmetric")
    return tickers, issuer, paths, years, rf, spot, vols, divs, matrix


def monte_carlo_valuation(inputs, payout_curve):
    """Estimate fair value for one target share of a stock-settled rTSR award.

    Risk-neutral geometric Brownian motion is used for terminal prices. The payoff is modeled as
    issuer terminal stock price times payout percentage, discounted at the risk-free rate. This is an
    educational estimator: valuation policy, dividend equivalents, forfeitures, service conditions,
    and audit-approved assumptions remain caller responsibilities.
    """
    tickers, issuer, paths, years, rf, spots, vols, divs, corr = _validate_mc_inputs(inputs)
    chol = _cholesky(corr)
    rng = random.Random(int(inputs.get("seed", 0)))
    issuer_i = tickers.index(issuer)
    payouts = []
    percentiles = []
    terminal_issuer = []
    raw_payoffs = []
    expected_payoff = 0.0

    for _ in range(paths):
        normals = _correlated_normals(rng, chol)
        terminal = {}
        ratios = {}
        for i, t in enumerate(tickers):
            s0 = _as_float(spots[t], f"spot {t}")
            vol = _as_float(vols[t], f"volatility {t}")
            q = _as_float(divs.get(t, 0.0), f"dividend yield {t}")
            drift = (rf - q - 0.5 * vol * vol) * years
            shock = vol * math.sqrt(years) * normals[i]
            st = s0 * math.exp(drift + shock)
            terminal[t] = st
            # rTSR ranks total shareholder return. In the valuation estimator, dividend yield is a
            # continuous total-return approximation so dividend-paying names are not misranked as
            # underperformers simply because the ex-dividend stock price drifts lower.
            ratios[t] = (st / s0) * math.exp(q * years)
        pct = percentile_rank(ratios, issuer)
        pay_pct = payout_curve.payout(round(pct, 2))
        payoff = terminal[issuer] * (pay_pct / 100.0)
        expected_payoff += payoff
        raw_payoffs.append(payoff)
        payouts.append(pay_pct)
        percentiles.append(pct)
        terminal_issuer.append(terminal[issuer])

    disc = math.exp(-rf * years)
    fair_value = expected_payoff / paths * disc
    discounted_payoffs = [p * disc for p in raw_payoffs]
    if paths > 1:
        variance = sum((p - fair_value) ** 2 for p in discounted_payoffs) / (paths - 1)
        fair_value_se = math.sqrt(variance / paths)
    else:
        fair_value_se = 0.0
    ci_low = fair_value - 1.96 * fair_value_se
    ci_high = fair_value + 1.96 * fair_value_se
    payouts_s = sorted(payouts)
    pct_s = sorted(percentiles)
    term_s = sorted(terminal_issuer)
    spot0 = _as_float(spots[issuer], f"spot {issuer}")
    return {
        "paths": paths,
        "seed": int(inputs.get("seed", 0)),
        "issuer": issuer,
        "risk_free_rate": round(rf, 6),
        "performance_years": round(years, 6),
        "fair_value_per_target_share": round(fair_value, 4),
        "fair_value_standard_error": round(fair_value_se, 4),
        "fair_value_ci95": {"low": round(ci_low, 4), "high": round(ci_high, 4)},
        "fair_value_ratio_to_spot": round(fair_value / spot0, 4),
        "mean_payout_percent": round(sum(payouts) / paths, 4),
        "mean_percentile": round(sum(percentiles) / paths, 4),
        "payout_distribution": {
            "p10": round(_percentile(payouts_s, 0.10), 4),
            "p25": round(_percentile(payouts_s, 0.25), 4),
            "p50": round(_percentile(payouts_s, 0.50), 4),
            "p75": round(_percentile(payouts_s, 0.75), 4),
            "p90": round(_percentile(payouts_s, 0.90), 4),
        },
        "percentile_distribution": {
            "p10": round(_percentile(pct_s, 0.10), 4),
            "p50": round(_percentile(pct_s, 0.50), 4),
            "p90": round(_percentile(pct_s, 0.90), 4),
        },
        "issuer_terminal_price_distribution": {
            "p10": round(_percentile(term_s, 0.10), 4),
            "p50": round(_percentile(term_s, 0.50), 4),
            "p90": round(_percentile(term_s, 0.90), 4),
        },
    }
