#!/usr/bin/env python3
"""Illustrative ISS-style pay-for-performance screen over the synthetic exec-comp dataset.

PUBLIC METHODOLOGY ONLY, on SYNTHETIC data. This models the *publicly described* mechanics of the ISS
quantitative pay-for-performance screen so a committee can anticipate it — it is NOT ISS's proprietary
model and produces no real ISS output. Two clearly-separated steps:

  1. COMPARISON GROUP — an ILLUSTRATION of ISS's published peer-construction logic (not a full
     replication): a self-peer graph (the subject's self-selected peers ∪ companies that name the
     subject) + a peer-of-peer walk, then a same-sub-industry/sector + size screen, then a selection of
     ~14-24 names. (The full ISS method adds GICS 8/6/4 precision caps, market-cap bucket sizing, a
     ~20%-of-median centering test, and prior-year-ISS-peer priority — out of scope for this illustration.)
  2. QUANTITATIVE SCREEN — three measures plus a financial-performance adjustment, each computed against
     the comparison group, mapped to a Low / Medium / High concern level:
       - MOM (Multiple of Median CEO pay), a 50/50 blend of 1-year and 3-year (the ISS-2026 change);
       - RDA (Relative Degree of Alignment), 5-year TSR/performance-percentile MINUS pay-percentile vs the
         group (ISS sign: negative = pay ahead of performance = concern);
       - PTA (Pay-TSR Alignment), an absolute 5-year alignment from WEIGHTED least-squares trends of
         indexed TSR vs pay (negative = concern); a simplified-but-faithful build of ISS's published method;
       - FPA (Financial Performance Assessment), modeled here as a simplified EVA-style PROXY.
     Aggregation follows ISS's published rules: the three primary measures' concerns ACCUMULATE (two or
     three elevated ⇒ High), then the FPA can modify a borderline result. A Medium/High concern trips a
     qualitative-review checklist (the second-stage ISS factors).

Pure, stdlib-only, deterministic, fail-closed. SOURCING: ISS PUBLISHES its quantitative concern threshold
table and the WLS/aggregation mechanics in the "Pay-for-Performance Mechanics" document (effective Feb. 1,
2026) — the thresholds and weights here are taken directly from it (CAP's 2026 summary corroborates them).
What ISS does NOT publish is the exact FPA threshold and the qualitative-evaluation outcome, which still
require ISS/consultant review. This is an illustration on SYNTHETIC data — not ISS's actual output. The
engine SCREENS and EXPLAINS; a human decides.

    from foundation.compute.iss_screen import ISSUniverse
    u = ISSUniverse()
    result = u.screen()                 # default: subject = Acme, non-S&P-500 bands
    result["concern"]                   # "Low" | "Medium" | "High"
"""
from __future__ import annotations

import csv
from pathlib import Path

from foundation.compute.peers import REAL_TICKERS   # shared public-safety deny-list (single source)

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / "foundation" / "data" / "acme"

_PEER_COLS = ("ticker", "company_name", "gics_sector", "gics_subindustry", "revenue_usd",
              "market_cap_usd", "employees", "total_assets_usd", "is_subject")
_EXEC_COLS = ("ticker", "self_peers", "pay_y1", "pay_y2", "pay_y3", "pay_y4", "pay_y5",
              "tsrval_y1", "tsrval_y2", "tsrval_y3", "tsrval_y4", "tsrval_y5", "fin_eva")

# Comparison-group construction (illustrative of ISS's published peer logic).
COMP_GROUP = {
    "revenue_mult": (0.4, 2.5),         # ISS size screen is wider than a committee peer screen
    "market_cap_mult": (0.25, 4.0),     # market-cap bucket band
    "target": (14, 24),                 # aim 14-24 names
    "minimum": 12,                      # ISS needs a scorable minimum
}

# Concern thresholds — NON-S&P-500 (Russell 3000), taken DIRECTLY from the ISS "Pay-for-Performance
# Mechanics" published threshold table (effective Feb. 1, 2026); CAP's 2026 summary corroborates them.
# MOM: higher = concern. RDA & PTA follow ISS's published sign — performance-minus-pay — so LOWER (more
# negative) = concern. The "fpa_eligible" border is ISS's own "Eligible for FPA Adjustment" column.
ISS_BANDS_NON_SP500 = {
    "mom": {"fpa_eligible": 1.89, "medium": 2.33, "high": 3.40},      # multiple of median CEO pay
    "rda": {"fpa_eligible": -41.0, "medium": -54.0, "high": -64.0},   # perf percentile - pay percentile (pts)
    "pta": {"fpa_eligible": -28.0, "medium": -30.0, "high": -45.0},   # WLS indexed-TSR trend - pay trend (%)
}
_LABEL = {0: "Low", 1: "Medium", 2: "High"}

# ISS weighted-least-squares weights for the PTA regressions (decay factor 0.85, geometric mean 1), taken
# directly from the ISS Mechanics doc. TSR has 6 "fence-post" points (years 0-5, $100 invested at year 0);
# pay has 5 points (years 1-5).
_TSR_W = (0.6661, 0.7837, 0.9220, 1.0847, 1.2761, 1.5012)
_PAY_W = (0.7225, 0.8500, 1.0000, 1.1765, 1.3841)

# FPA neutral band — the FPA only modifies a concern when financial performance and pay diverge
# materially. ISS sets the exact FPA threshold per company (index membership + initial concern) and does
# NOT disclose it; ISS back-testing notes <10% of companies are FPA-modified, so this is a deliberately
# wide ILLUSTRATIVE band on |fpa| (= |fin percentile − pay percentile|) within which the FPA does nothing.
FPA_NEUTRAL = 20.0


class ISSDataError(ValueError):
    """The ISS inputs are missing, malformed, or have no single subject (fail closed)."""


def _rows(path, cols):
    if not path.exists():
        raise ISSDataError(f"ISS input not found: {path}")
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or set(reader.fieldnames) != set(cols):
            raise ISSDataError(f"{path.name} schema mismatch: expected {sorted(cols)}, "
                               f"got {sorted(reader.fieldnames or [])}")
        return list(reader)


def _num(v, name):
    try:
        return float(v)
    except (TypeError, ValueError) as e:
        raise ISSDataError(f"{name} must be numeric (got {v!r})") from e


def _percentile_rank(value, population):
    """0-100 percentile of `value` within `population` (which must include it), average rank for ties."""
    ordered = sorted(population)
    n = len(ordered)
    if n < 2:
        raise ISSDataError("percentile rank needs at least two observations")
    positions = [i for i, v in enumerate(ordered) if v == value]
    if not positions:
        raise ISSDataError("value not present in population")
    return round(100.0 * (sum(positions) / len(positions)) / (n - 1), 2)


def _median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        raise ISSDataError("median of empty series")
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _band_high(value, bands):
    """MOM-style: a HIGHER value is worse."""
    if value >= bands["high"]:
        return 2
    if value >= bands["medium"]:
        return 1
    return 0


def _band_low(value, bands):
    """RDA/PTA-style (ISS sign): a LOWER / more-negative value is worse."""
    if value <= bands["high"]:
        return 2
    if value <= bands["medium"]:
        return 1
    return 0


def _wls_norm_slope(values, xs, weights):
    """ISS weighted-least-squares slope, normalized by the weighted-average value (a 'trend rate').
    slope = (ΣW·ΣWXP − ΣWX·ΣWP) / (ΣW·ΣWX² − (ΣWX)²), then divided by ΣWP/ΣW. Per the ISS Mechanics doc."""
    sw = sum(weights)
    swx = sum(w * x for w, x in zip(weights, xs))
    swp = sum(w * p for w, p in zip(weights, values))
    swxp = sum(w * x * p for w, x, p in zip(weights, xs, values))
    swxx = sum(w * x * x for w, x in zip(weights, xs))
    denom = sw * swxx - swx * swx
    if denom == 0:
        return 0.0
    slope = (sw * swxp - swx * swp) / denom
    wavg = swp / sw
    return slope / wavg if wavg else 0.0


class ISSUniverse:
    def __init__(self, data_dir=DATA):
        co_rows = _rows(Path(data_dir) / "peer_universe.csv", _PEER_COLS)
        ex_rows = _rows(Path(data_dir) / "exec_pay_tsr.csv", _EXEC_COLS)
        for r in co_rows:
            for k in ("revenue_usd", "market_cap_usd", "employees", "total_assets_usd"):
                r[k] = int(_num(r[k], k))
        self.co = {r["ticker"]: r for r in co_rows}
        if len(self.co) != len(co_rows):
            raise ISSDataError("peer universe has duplicate tickers")
        self.exec = {}
        for r in ex_rows:
            tk = r["ticker"]
            self.exec[tk] = {
                "ticker": tk,
                "self_peers": [t for t in r["self_peers"].split(";") if t],
                "pay": [int(_num(r[f"pay_y{i}"], f"pay_y{i}")) for i in range(1, 6)],
                "tsr": [_num(r[f"tsrval_y{i}"], f"tsrval_y{i}") for i in range(1, 6)],
                "fin_eva": _num(r["fin_eva"], "fin_eva"),
            }
        if len(self.exec) != len(ex_rows):
            raise ISSDataError("exec_pay_tsr.csv has duplicate tickers")
        if set(self.exec) != set(self.co):
            raise ISSDataError("peer_universe.csv and exec_pay_tsr.csv cover different tickers")
        # public-safety defense-in-depth: the universe is deny-listed at generation, but never load a
        # real, recognizable ticker here either (single shared source of truth)
        real = sorted(set(self.co) & REAL_TICKERS)
        if real:
            raise ISSDataError(f"ISS inputs contain real, recognizable tickers: {real}")
        # fail closed on degenerate exec data: unknown self-peer refs (silently dropped otherwise) and
        # non-positive CEO pay / TSR index (would break medians, growth ratios, and the PTA normalization)
        for tk, e in self.exec.items():
            unknown = [p for p in e["self_peers"] if p not in self.co]
            if unknown:
                raise ISSDataError(f"{tk} references unknown self-peer tickers: {unknown[:5]}")
            if any(p <= 0 for p in e["pay"]):
                raise ISSDataError(f"{tk} has non-positive CEO pay")
            if any(t <= 0 for t in e["tsr"]):
                raise ISSDataError(f"{tk} has a non-positive TSR index value (PTA baseline must be positive)")
        subjects = [r for r in co_rows if r.get("is_subject") == "yes"]
        if len(subjects) != 1:
            raise ISSDataError(f"exactly one subject (is_subject=yes) required; found {len(subjects)}")
        self.subject = subjects[0]
        for k in ("revenue_usd", "market_cap_usd"):   # anchor every size ratio — fail closed if degenerate
            if self.subject[k] <= 0:
                raise ISSDataError(f"subject has non-positive {k}={self.subject[k]}")

    # ---------------------------------------------------------------- step 1: ISS comparison group (illustrative)
    def comparison_group(self, criteria=None):
        c = {**COMP_GROUP, **(criteria or {})}
        subj = self.subject
        stk = subj["ticker"]
        se = self.exec[stk]
        # peer graph: first-degree (self-peers ∪ companies that name the subject), then peer-of-peer
        self_peers = set(se["self_peers"])
        namers = {tk for tk, e in self.exec.items() if stk in e["self_peers"]}
        first_degree = (self_peers | namers) - {stk}
        pop = set()
        for tk in first_degree:
            pop |= set(self.exec.get(tk, {}).get("self_peers", []))
        candidates = (first_degree | pop) - {stk}

        rlo, rhi = c["revenue_mult"]
        mlo, mhi = c["market_cap_mult"]

        def size_ok(co):
            r = co["revenue_usd"] / subj["revenue_usd"]
            m = co["market_cap_usd"] / subj["market_cap_usd"]
            return rlo <= r <= rhi and mlo <= m <= mhi

        pool = [self.co[tk] for tk in candidates if tk in self.co and size_ok(self.co[tk])]
        # GICS precision + first-degree priority, then median-center on revenue, ticker tie-break
        def key(co):
            gics = 0 if co["gics_subindustry"] == subj["gics_subindustry"] else \
                (1 if co["gics_sector"] == subj["gics_sector"] else 2)
            return (gics, 0 if co["ticker"] in first_degree else 1,
                    abs(co["revenue_usd"] - subj["revenue_usd"]), co["ticker"])
        ranked = sorted(pool, key=key)
        group = ranked[:c["target"][1]]
        scorable = len(group) >= c["minimum"]
        return {
            "subject": subj, "group": group, "n_group": len(group),
            "first_degree": sorted(first_degree), "n_first_degree": len(first_degree),
            "n_candidates": len(candidates), "scorable": scorable, "criteria": c,
        }

    # ---------------------------------------------------------------- step 2: quantitative screen
    def screen(self, bands=None):
        bands = bands or ISS_BANDS_NON_SP500
        cg = self.comparison_group()
        if not cg["scorable"]:
            raise ISSDataError(
                f"comparison group has only {cg['n_group']} names (< {cg['criteria']['minimum']} scorable minimum)")
        members = cg["group"]
        stk = self.subject["ticker"]
        se = self.exec[stk]
        me = [self.exec[m["ticker"]] for m in members]

        def pay1(e):
            return e["pay"][4]

        def pay3(e):
            return sum(e["pay"][2:]) / 3.0

        def pay5(e):
            return sum(e["pay"]) / 5.0

        def tsr5(e):
            return e["tsr"][4] / 100.0 - 1.0          # 5-yr cumulative total return ($100 base)

        # MOM — multiple of median, 50/50 blend of 1-yr and 3-yr
        mom1 = pay1(se) / _median([pay1(e) for e in me])
        mom3 = pay3(se) / _median([pay3(e) for e in me])
        mom = 0.5 * mom1 + 0.5 * mom3

        # RDA — ISS sign: 5-yr TSR/performance percentile MINUS pay percentile vs the group.
        # Negative = pay outranks performance = concern.
        pay_pct = _percentile_rank(pay5(se), [pay5(e) for e in me] + [pay5(se)])
        tsr_pct = _percentile_rank(tsr5(se), [tsr5(e) for e in me] + [tsr5(se)])
        rda = round(tsr_pct - pay_pct, 2)

        # PTA — ISS weighted-least-squares: normalized TSR trend minus pay trend, in %. TSR uses 6
        # fence-post points (years 0-5, $100 invested at year 0); pay uses 5 (years 1-5). Negative = pay
        # outran shareholder return = concern. (ISS Mechanics, page 9.)
        pay_slope = _wls_norm_slope(se["pay"], (1, 2, 3, 4, 5), _PAY_W)
        tsr_slope = _wls_norm_slope([100.0] + se["tsr"], (0, 1, 2, 3, 4, 5), _TSR_W)
        pta = round((tsr_slope - pay_slope) * 100.0, 2)

        # FPA — a simplified EVA-style PROXY: financial-performance percentile minus pay percentile
        # (negative = pay ahead of financial performance). Real ISS FPA blends four EVA-based metrics.
        fin_pct = _percentile_rank(se["fin_eva"], [e["fin_eva"] for e in me] + [se["fin_eva"]])
        fpa = round(fin_pct - pay_pct, 2)

        # ---- Aggregation, per the ISS published rules (Mechanics, "Quantitative Concern Levels") ----
        mom_c = _band_high(mom, bands["mom"])
        rda_c = _band_low(rda, bands["rda"])
        pta_c = _band_low(pta, bands["pta"])
        levels = [mom_c, rda_c, pta_c]
        n_high, n_med = levels.count(2), levels.count(1)
        # a measure "borders Medium" when it is in its Eligible-for-FPA zone (toward concern, not yet Medium)
        borders = ((bands["mom"]["fpa_eligible"] <= mom < bands["mom"]["medium"]) or
                   (bands["rda"]["medium"] < rda <= bands["rda"]["fpa_eligible"]) or
                   (bands["pta"]["medium"] < pta <= bands["pta"]["fpa_eligible"]))
        fpa_poor = fpa < -FPA_NEUTRAL    # pay materially ahead of financial performance -> raises concern
        fpa_strong = fpa > FPA_NEUTRAL   # financial performance materially ahead of pay -> lowers concern
        fpa_note = "not applied"
        fpa_applied = False          # did the FPA actually move the screened outcome (any of the four paths)?
        if n_high + n_med >= 2:      # two or three elevated measures -> High, NOT subject to FPA
            overall = 2
            fpa_note = "not applied (two or more elevated measures)"
        elif n_high == 1:            # one High, two Low: FPA can only lower High -> Medium
            overall = 2
            if fpa_strong:
                overall, fpa_note, fpa_applied = 1, "lowered High->Medium (strong financial performance)", True
        elif n_med == 1:             # one Medium, two Low: FPA can raise -> High or lower -> Low
            overall = 1
            if fpa_poor:
                overall, fpa_note, fpa_applied = 2, "raised Medium->High (pay ahead of financial performance)", True
            elif fpa_strong:
                overall, fpa_note, fpa_applied = 0, "lowered Medium->Low (strong financial performance)", True
        else:                        # all Low: FPA can raise Low -> Medium only if a measure borders Medium
            overall = 0
            if borders and fpa_poor:
                overall, fpa_note, fpa_applied = 1, "raised Low->Medium (borderline + pay ahead of financial performance)", True
            elif borders:
                fpa_note = "eligible (no change)"

        concern = _LABEL[overall]
        triggers = self._qualitative_triggers(mom, rda, pta, fpa) if overall >= 1 else []

        return {
            "subject": self.subject, "comparison_group": cg, "concern": concern,
            "measures": {
                "mom": {"value": round(mom, 3), "mom_1yr": round(mom1, 3), "mom_3yr": round(mom3, 3),
                        "band": _LABEL[mom_c]},
                "rda": {"value": rda, "pay_pctile": pay_pct, "tsr_pctile": tsr_pct, "band": _LABEL[rda_c]},
                "pta": {"value": pta, "pay_trend_pct": round(pay_slope * 100.0, 2),
                        "tsr_trend_pct": round(tsr_slope * 100.0, 2), "band": _LABEL[pta_c]},
                "fpa": {"value": fpa, "pay_pctile": pay_pct, "fin_pctile": fin_pct,
                        "borderline_low_eligible": borders, "fpa_applied": fpa_applied, "note": fpa_note},
            },
            "bands": bands, "triggers_qualitative": overall >= 1, "qualitative_triggers": triggers,
        }

    @staticmethod
    def _qualitative_triggers(mom, rda, pta, fpa):
        """The second-stage ISS factors a Medium/High concern puts in scope (illustrative checklist)."""
        flags = []
        if mom >= ISS_BANDS_NON_SP500["mom"]["fpa_eligible"]:
            flags.append("Magnitude: CEO pay is a high multiple of the peer median — review absolute level.")
        if rda <= ISS_BANDS_NON_SP500["rda"]["fpa_eligible"]:
            flags.append("Pay-for-performance: pay percentile outruns TSR percentile vs the peer group.")
        if pta <= ISS_BANDS_NON_SP500["pta"]["fpa_eligible"]:
            flags.append("Trend: CEO pay has grown faster than indexed shareholder return over 5 years.")
        if fpa < 0:
            flags.append("Financials: pay also outruns financial performance (EVA-style) — not just TSR.")
        # always-in-scope second-stage factors when any quantitative concern is raised
        flags.append("Incentive design: goal rigor, metric choice, and payout curves vs market.")
        flags.append("Problematic practices: single-trigger CIC, excessive perks, discretionary payouts.")
        flags.append("Responsiveness: prior say-on-pay support and disclosed shareholder engagement.")
        return flags
