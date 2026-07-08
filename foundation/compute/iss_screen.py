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
     the comparison group, mapped to a Low / Medium / High concern level. The screen is PARAMETERIZED BY
     POLICY YEAR (`screen(policy_year=...)`, default 2026) so it tracks live ISS policy and keeps the
     prior season for a legible before/after — see `ISS_POLICIES`:
       - MOM (Multiple of Median CEO pay): 2025 = 1-year only; 2026 = a 50/50 blend of 1-year and 3-year;
       - RDA (Relative Degree of Alignment): the season-window TSR/performance-percentile MINUS
         pay-percentile vs the group — 2025 = 3-year, 2026 = 5-year (ISS sign: negative = pay ahead of
         performance = concern);
       - PTA (Pay-TSR Alignment), an absolute 5-year alignment from WEIGHTED least-squares trends of
         indexed TSR vs pay (negative = concern); a simplified-but-faithful build of ISS's published method;
       - FPA (Financial Performance Assessment), modeled here as a simplified single-score EVA-style PROXY
         (the real ISS FPA is a 5-year, four-EVA-metric screen — a documented approximation, not that).
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
import math
import re
from pathlib import Path

from foundation.compute.peers import real_peer_identifiers, name_matches_real

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / "foundation" / "data" / "acme"

# The ISS universe is synthetic by construction: the subject Acme (ACMQ) + numbered issuers S001..S060. This
# shape rejects ANY real ticker structurally (stronger than a deny-list that can't track a growing real roster).
_ISS_TICKER_RE = re.compile(r"^(ACMQ|S\d{3})$")

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

# ISS revises its quantitative P4P screen every proxy season. To make the illustration track live policy
# — and to keep the *prior* season available for a legible before/after — the screen is PARAMETERIZED BY
# POLICY YEAR. Each entry carries the published NON-S&P-500 (Russell 3000) concern thresholds and the
# measurement WINDOWS that season used. Thresholds are the ISS "Pay-for-Performance Mechanics" published
# tables (CAP's 2026 policy-update summary corroborates the year-over-year moves). MOM: higher = concern.
# RDA & PTA follow ISS's published sign — performance-minus-pay — so LOWER (more negative) = concern. The
# "fpa_eligible" border is ISS's own "Eligible for FPA Adjustment" column.
#
# S&P-500 issuers use a DIFFERENT published MOM table (2026: 1.73 / 2.04 / 2.99); this illustration is
# non-S&P-500 throughout (the subject Acme is a mid-cap Russell-3000 filer), so only the non-S&P-500
# tables operate here — the S&P-500 case is out of scope, not silently mis-scored.
ISS_POLICIES = {
    2025: {
        "label": "ISS 2025 policy",
        "effective_note": "meetings on/after Feb 1, 2025",
        "mom_blend": (1,),                      # 2025 MOM: 1-year only
        "rda_years": 3,                         # 2025 RDA: 3-year pay/TSR percentile comparison
        "bands": {
            "mom": {"fpa_eligible": 1.84, "medium": 2.33, "high": 3.33},
            "rda": {"fpa_eligible": -38.0, "medium": -50.0, "high": -60.0},
            "pta": {"fpa_eligible": -25.0, "medium": -30.0, "high": -45.0},
        },
    },
    2026: {
        "label": "ISS 2026 policy",
        "effective_note": "meetings on/after Feb 1, 2026",
        "mom_blend": (1, 3),                    # 2026 MOM: 50/50 average of 1-year and 3-year MOM
        "rda_years": 5,                         # 2026 RDA: extended to a 5-year comparison
        "bands": {
            "mom": {"fpa_eligible": 1.89, "medium": 2.33, "high": 3.40},
            "rda": {"fpa_eligible": -41.0, "medium": -54.0, "high": -64.0},
            "pta": {"fpa_eligible": -28.0, "medium": -30.0, "high": -45.0},
        },
    },
}
# the specific, verified 2026-vs-2025 delta — surfaced on the dashboard so the "tracks live policy" claim
# is concrete and checkable, not decorative.
ISS_2026_DELTA = ("RDA extended 3yr -> 5yr; MOM now a 50/50 blend of 1yr and 3yr (was 1yr only); "
                  "concern thresholds refreshed (RDA -38/-50/-60 -> -41/-54/-64, MOM 1.84/2.33/3.33 -> "
                  "1.89/2.33/3.40, PTA eligible -25% -> -28%); PTA WLS mechanics unchanged.")
DEFAULT_POLICY_YEAR = 2026
_RANK_ROUND = 9                                  # round a measured value before percentile-ranking, so a
#                                                # near-tie can't flip a rank on last-ULP float drift (macOS
#                                                # ARM vs Linux x86) — mirrors the Glass Lewis arm's guard.

# Back-compat alias: the default-season non-S&P-500 bands, still importable by name.
ISS_BANDS_NON_SP500 = ISS_POLICIES[DEFAULT_POLICY_YEAR]["bands"]
_LABEL = {0: "Low", 1: "Medium", 2: "High"}


def policy_for(policy_year):
    """The frozen ISS policy for a season. Fails closed on an unmodeled year (never a silent default)."""
    if policy_year not in ISS_POLICIES:
        raise ISSDataError(f"no ISS policy modeled for {policy_year!r} "
                           f"(available: {sorted(ISS_POLICIES)})")
    return ISS_POLICIES[policy_year]

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
        f = float(v)
    except (TypeError, ValueError) as e:
        raise ISSDataError(f"{name} must be numeric (got {v!r})") from e
    if not math.isfinite(f):     # a NaN/inf must fail closed, not flow into a rendered measure (or GL synthesis)
        raise ISSDataError(f"{name} must be finite (got {v!r})")
    return f


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


def _rank(value, population):
    """Percentile-rank a value within a population after ROUNDING both to `_RANK_ROUND` decimals, so a
    near-tie can't flip on last-ULP float drift across platforms (matches the Glass Lewis arm's guard).
    The rounded value must still be present in the rounded population (the caller includes it)."""
    r = round(value, _RANK_ROUND)
    return _percentile_rank(r, [round(v, _RANK_ROUND) for v in population])


def _pay_avg(pay, k):
    """Average CEO pay over the most recent `k` years (k=1 -> the latest year)."""
    if not (1 <= k <= len(pay)):
        raise ISSDataError(f"pay window {k} out of range for {len(pay)} years")
    return sum(pay[-k:]) / float(k)


def _tsr_window(tsr, years):
    """Cumulative TSR over the most recent `years` on a $100-at-year-0 index (tsr = end-of-year 1..5 values).
    years=5 -> tsr[-1]/100 - 1; years=3 -> tsr[-1]/index_at_year2 - 1. Fails closed on a non-positive base."""
    path = [100.0] + list(tsr)                     # index at years 0..5
    if not (1 <= years < len(path)):
        raise ISSDataError(f"TSR window {years} out of range")
    base = path[len(path) - 1 - years]
    if base <= 0:
        raise ISSDataError("TSR window base index must be positive")
    return path[-1] / base - 1.0


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
        co_rows = _rows(Path(data_dir) / "iss_universe.csv", _PEER_COLS)   # SYNTHETIC universe (not the real peers)
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
        # public-safety: the ISS universe is SYNTHETIC by construction (the real peers live only in
        # peer_universe.csv). Enforce the synthetic ticker SHAPE — 'ACMQ' or 'S###' — which structurally
        # rejects ANY real ticker (a real peer like GTLB/KVYO can never match), a strictly stronger guard than
        # a static deny-list that can't keep up with a growing real-peer roster.
        bad_shape = sorted(t for t in self.co if not _ISS_TICKER_RE.fullmatch(t))
        if bad_shape:
            raise ISSDataError(f"ISS universe must be synthetic tickers (ACMQ or S###); got: {bad_shape[:5]}")
        # ...and a synthetic ticker must NOT carry a real company NAME — otherwise a fabricated pay/TSR figure
        # would attach to a real company (e.g. 'GitLab Inc.' under ticker S001). Reject the real peer names,
        # canonicalized (punctuation/suffix-insensitive), and FAIL CLOSED if the roster can't load (require=True)
        # so an unavailable roster can never silently disable this guard.
        _, _real_names = real_peer_identifiers(require=True)
        name_hits = sorted({r.get("company_name", "").strip() for r in self.co.values()
                            if name_matches_real(r.get("company_name", ""), _real_names)})
        if name_hits:
            raise ISSDataError(f"ISS universe must not carry real company names; got: {name_hits[:5]}")
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
    def screen(self, policy_year=DEFAULT_POLICY_YEAR, bands=None):
        policy = policy_for(policy_year)
        bands = bands or policy["bands"]
        rda_years = policy["rda_years"]
        mom_blend = policy["mom_blend"]
        cg = self.comparison_group()
        if not cg["scorable"]:
            raise ISSDataError(
                f"comparison group has only {cg['n_group']} names (< {cg['criteria']['minimum']} scorable minimum)")
        members = cg["group"]
        stk = self.subject["ticker"]
        se = self.exec[stk]
        me = [self.exec[m["ticker"]] for m in members]

        # MOM — multiple of the peer-median CEO pay, averaged over the season's blend windows.
        # 2025 = 1-year only; 2026 = 50/50 of the 1-year and 3-year multiples (mom_blend encodes it).
        mom_components = {}
        for k in mom_blend:
            mom_components[k] = _pay_avg(se["pay"], k) / _median([_pay_avg(e["pay"], k) for e in me])
        mom = sum(mom_components.values()) / len(mom_components)

        # RDA — ISS sign: the season-window TSR/performance percentile MINUS the pay percentile vs the group
        # (2025 = 3-year, 2026 = 5-year). Negative = pay outranks performance = concern. Pay percentile is
        # taken over the same window and reused by the FPA below (both are relative pay-vs-X measures).
        pay_win = [_pay_avg(e["pay"], rda_years) for e in me] + [_pay_avg(se["pay"], rda_years)]
        tsr_win = [_tsr_window(e["tsr"], rda_years) for e in me] + [_tsr_window(se["tsr"], rda_years)]
        pay_pct = _rank(_pay_avg(se["pay"], rda_years), pay_win)
        tsr_pct = _rank(_tsr_window(se["tsr"], rda_years), tsr_win)
        rda = round(tsr_pct - pay_pct, 2)

        # PTA — ISS weighted-least-squares: normalized TSR trend minus pay trend, in %. TSR uses 6
        # fence-post points (years 0-5, $100 invested at year 0); pay uses 5 (years 1-5). Negative = pay
        # outran shareholder return = concern. (ISS Mechanics, page 9.)
        pay_slope = _wls_norm_slope(se["pay"], (1, 2, 3, 4, 5), _PAY_W)
        tsr_slope = _wls_norm_slope([100.0] + se["tsr"], (0, 1, 2, 3, 4, 5), _TSR_W)
        pta = round((tsr_slope - pay_slope) * 100.0, 2)

        # FPA — a simplified EVA-style PROXY: financial-performance percentile minus pay percentile
        # (negative = pay ahead of financial performance). The real ISS FPA is a 5-year (2026) screen
        # blending FOUR EVA metrics (EVA Margin, EVA Spread, EVA Momentum vs Sales / vs Capital); this
        # single-score proxy is horizon-agnostic and explicitly not that — see the methodology note.
        fin_pct = _rank(se["fin_eva"], [e["fin_eva"] for e in me] + [se["fin_eva"]])
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
        triggers = self._qualitative_triggers(mom, rda, pta, fpa, bands) if overall >= 1 else []

        policy = policy_for(policy_year)
        return {
            "subject": self.subject, "comparison_group": cg, "concern": concern,
            "policy": {"year": policy_year, "label": policy["label"], "effective": policy["effective_note"],
                       "rda_years": rda_years, "mom_blend": list(mom_blend),
                       "delta_from_prior": ISS_2026_DELTA if policy_year == 2026 else None,
                       "index_group": "non_sp500"},
            "measures": {
                "mom": {"value": round(mom, 3),
                        "mom_1yr": round(mom_components[1], 3) if 1 in mom_components else None,
                        "mom_3yr": round(mom_components[3], 3) if 3 in mom_components else None,
                        "blend_years": list(mom_blend), "band": _LABEL[mom_c]},
                "rda": {"value": rda, "window_years": rda_years, "pay_pctile": pay_pct,
                        "tsr_pctile": tsr_pct, "band": _LABEL[rda_c]},
                "pta": {"value": pta, "pay_trend_pct": round(pay_slope * 100.0, 2),
                        "tsr_trend_pct": round(tsr_slope * 100.0, 2), "band": _LABEL[pta_c]},
                "fpa": {"value": fpa, "pay_pctile": pay_pct, "fin_pctile": fin_pct,
                        "borderline_low_eligible": borders, "fpa_applied": fpa_applied, "note": fpa_note},
            },
            "bands": bands, "triggers_qualitative": overall >= 1, "qualitative_triggers": triggers,
        }

    @staticmethod
    def _qualitative_triggers(mom, rda, pta, fpa, bands):
        """The second-stage ISS factors a Medium/High concern puts in scope (illustrative checklist),
        keyed off the SAME policy-year bands the quantitative screen used (never a stale global)."""
        flags = []
        if mom >= bands["mom"]["fpa_eligible"]:
            flags.append("Magnitude: CEO pay is a high multiple of the peer median — review absolute level.")
        if rda <= bands["rda"]["fpa_eligible"]:
            flags.append("Pay-for-performance: pay percentile outruns TSR percentile vs the peer group.")
        if pta <= bands["pta"]["fpa_eligible"]:
            flags.append("Trend: CEO pay has grown faster than indexed shareholder return over the "
                         "five-year pay-vs-TSR trend window (PTA is a 5-year WLS measure in every policy year).")
        if fpa < 0:
            flags.append("Financials: pay also outruns financial performance (EVA-style) — not just TSR.")
        # always-in-scope second-stage factors when any quantitative concern is raised
        flags.append("Incentive design: goal rigor, metric choice, and payout curves vs market.")
        flags.append("Problematic practices: single-trigger CIC, excessive perks, discretionary payouts.")
        flags.append("Responsiveness: prior say-on-pay support and disclosed shareholder engagement.")
        return flags
