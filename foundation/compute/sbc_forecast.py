#!/usr/bin/env python3
"""Forward stock-based-compensation (SBC) expense forecast over the synthetic Acme equity plan.

Where the equity-spend arm answers "what did we spend, and is the plan defensible?", this arm answers the
CFO/controller's forecasting question: **"how much SBC expense is already locked in for the next few years,
and what will the run-rate be?"** SBC is a large, non-cash P&L line that a company must guide on — and most
of the next few years' expense is NOT a choice: it is the amortization of grants ALREADY made, rolling off a
fixed schedule. This engine reads the append-only grant ledger and projects that runoff period by period.

Three layers, most-certain first:
1. LOCKED-IN RUNOFF — the future recognition of grants already outstanding at the as-of date, straight-line
   over each grant's remaining service period, with service-condition forfeitures trued up off `workers.csv`
   term dates. This ties exactly (before the forfeiture-rate overlay) to the equity-spend arm's "unamortized
   SBC backlog": the same amortization, split into future fiscal years instead of a single number.
2. FORFEITURE-ADJUSTED RUNOFF — the locked-in runoff haircut by an illustrative estimated annual forfeiture
   rate. GAAP (ASU 2016-09) lets an issuer estimate forfeitures rather than wait for them to occur; a forward
   forecast has no future actuals, so it estimates.
3. NEW-GRANT OVERLAY (illustrative) — a steady-state assumption that the company keeps granting at its
   trailing-twelve-month run-rate, each vintage amortized straight-line, layered on top to show a TOTAL
   go-forward SBC forecast rather than just the declining runoff.

METHODOLOGY-FAITHFUL vs ILLUSTRATIVE (the honesty line, stated like equity_spend.py):
- FAITHFUL: the locked-in runoff and the backlog reconciliation are pure amortization arithmetic off the
  committed grant ledger and actual term dates — no assumption.
- ILLUSTRATIVE (labeled): the forfeiture RATE and the new-grant run-rate/attribution are assumptions, not
  facts; the % -of-revenue context holds revenue flat at the last trailing-twelve-months figure. All are
  marked as such and are never presented as guidance.

Standard library only. Deterministic. Fail-closed. Presentation layers render it; they never decide.
"""
from __future__ import annotations

import csv
import math
from datetime import date, datetime, timedelta
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "data" / "acme"

_GRANT_COLS = ("grant_id", "plan_id", "emp_id", "participant_group", "grant_type", "award_type",
               "grant_date", "shares_granted", "psu_max_multiplier", "psu_share_basis",
               "stock_price_at_grant_usd", "strike_price_usd", "grant_date_fv_per_share_usd",
               "vest_start_date", "vest_months_total", "cliff_months", "vest_frequency",
               "performance_period_end")
_SHARE_COLS = ("period_end", "common_shares_outstanding", "waso_basic", "waso_diluted", "close_price_usd",
               "annualized_volatility", "risk_free_rate", "dividend_yield")
_FIN_COLS = ("period_end", "revenue_usd")
# pin the FULL workers.csv header (we only read a few columns, but a schema change must fail closed here
# rather than silently shift a column or drop term_date — which would mis-state forfeitures)
_WORKER_COLS = ("emp_id", "worker_type", "status", "hire_date", "term_date", "term_type", "regrettable",
                "level", "job_family", "location", "manager_id", "is_people_manager", "scheduled_hours",
                "standard_full_time_hours", "base_salary", "band_id", "rating", "gender_group",
                "ethnicity_group", "promotion_eligible", "promoted_this_period", "level_entry_date",
                "potential")

HORIZON_FYS = 5                            # forecast at most this many fiscal years forward
NEW_GRANT_VEST_MONTHS = 48                 # illustrative straight-line vesting for the modeled new-grant run-rate
ILLUSTRATIVE_FORFEITURE_RATE = 0.06        # illustrative estimated annual forfeiture rate on future unvested cost
_DP = 2


class SBCDataError(ValueError):
    """A grant-ledger / financial-data defect the engine refuses to forecast past (fail closed)."""


# ---------------------------------------------------------------- loading + validation
def _rows(path, cols):
    if not path.exists():
        raise SBCDataError(f"missing data file: {path.name}")
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames is None or list(rd.fieldnames) != list(cols):
            raise SBCDataError(f"{path.name}: header {rd.fieldnames} != expected {list(cols)}")
        out = [dict(r) for r in rd]
    if not out:
        raise SBCDataError(f"{path.name}: no rows")
    return out


def _num(v, ctx, positive=False):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise SBCDataError(f"{ctx}: not a number ({v!r})")
    if not math.isfinite(f):
        raise SBCDataError(f"{ctx}: not finite ({v!r})")
    if positive and f <= 0:
        raise SBCDataError(f"{ctx}: must be > 0 ({v!r})")
    return f


def _int_num(v, ctx, positive=False, allow_zero=True):
    """Parse a whole number, failing closed (SBCDataError, never a raw ValueError) on a non-integer or a
    fractional value like '10.9' that int(float(...)) would silently truncate."""
    f = _num(v, ctx)
    if not f.is_integer():
        raise SBCDataError(f"{ctx}: must be a whole number ({v!r})")
    n = int(f)
    if positive and n <= 0:
        raise SBCDataError(f"{ctx}: must be a positive whole number ({v!r})")
    if not allow_zero and n == 0:
        raise SBCDataError(f"{ctx}: must be non-zero ({v!r})")
    if n < 0:
        raise SBCDataError(f"{ctx}: must not be negative ({v!r})")
    return n


def _pdate(v, ctx):
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise SBCDataError(f"{ctx}: bad date {v!r}")


def _months(a: date, b: date) -> int:
    """Whole months elapsed from a to b (>=0 clamps at 0). A month is not complete until the same day-of-month,
    matching foundation/compute/equity_spend.py exactly so the two arms' amortization reconciles."""
    return max(0, (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0))


# ---------------------------------------------------------------- amortization (same convention as equity_spend)
def _grant_fv(g) -> float:
    # shares must be a positive WHOLE number (int(float('10.9')) would silently truncate) and the grant-date
    # fair value must be positive — a zero/negative FV is a data defect, not $0 of cost.
    return _int_num(g["shares_granted"], f"{g['grant_id']}.shares_granted", positive=True) \
        * _num(g["grant_date_fv_per_share_usd"], f"{g['grant_id']}.grant_date_fv_per_share_usd", positive=True)


def _cum_expense(g, at: date, term) -> float:
    """Cumulative SBC recognized for a grant at `at`: straight-line over the service period (vest_start ..
    vest_start+vest_months), trued up to the vested value once the holder has terminated (service-condition
    forfeiture reverses unvested cost). Identical convention to foundation/compute/equity_spend.py."""
    gd = _pdate(g["vest_start_date"], f"{g['grant_id']}.vest_start_date")
    if at < gd:
        return 0.0
    vm = _int_num(g["vest_months_total"], f"{g['grant_id']}.vest_months_total", positive=True)
    total = _grant_fv(g)
    if term is not None and term <= at:
        cliff = _int_num(g["cliff_months"], f"{g['grant_id']}.cliff_months")   # non-negative whole
        el = _months(gd, term)
        frac = 0.0 if el < cliff else min(1.0, el / vm)
        return frac * total                                   # forfeit unvested -> trued up to the vested value
    return min(1.0, _months(gd, at) / vm) * total


def _load(data_dir):
    grants = _rows(data_dir / "equity_grants.csv", _GRANT_COLS)
    gid_seen = set()
    for g in grants:                                          # validate economics once, fail closed at load
        if g["grant_id"] in gid_seen:
            raise SBCDataError(f"duplicate grant_id: {g['grant_id']}")
        gid_seen.add(g["grant_id"])
        _grant_fv(g)                                          # positive whole shares + positive fair value
        _int_num(g["vest_months_total"], f"{g['grant_id']}.vest_months_total", positive=True)
        _int_num(g["cliff_months"], f"{g['grant_id']}.cliff_months")
        _pdate(g["grant_date"], f"{g['grant_id']}.grant_date")
        _pdate(g["vest_start_date"], f"{g['grant_id']}.vest_start_date")
    worker_rows = _rows(data_dir / "workers.csv", _WORKER_COLS)   # pinned schema (never self-accepted)
    workers = {}
    for w in worker_rows:
        if w["emp_id"] in workers:
            raise SBCDataError(f"duplicate emp_id in workers.csv: {w['emp_id']}")
        workers[w["emp_id"]] = w
    shares = _rows(data_dir / "shares_outstanding.csv", _SHARE_COLS)
    fin = _rows(data_dir / "financials.csv", _FIN_COLS)
    for f in fin:                                            # EVERY revenue row must be a positive number,
        _num(f["revenue_usd"], "financials.revenue_usd", positive=True)   # not only the trailing-TTM window
    return grants, workers, shares, fin


def _term_of(workers, emp):
    w = workers.get(emp)
    if w and w.get("term_date"):
        return _pdate(w["term_date"], "term")
    return None


# ---------------------------------------------------------------- forecast
def _fy_end(fy: int) -> date:
    return date(fy, 12, 31)


def compute(data_dir=None):
    """Return the forward SBC expense forecast. Reads the committed grant ledger + workers + shares +
    financials, deterministic, no other I/O. Raises SBCDataError (fail closed) on any data defect."""
    data_dir = Path(data_dir) if data_dir is not None else _DATA
    grants, workers, shares, fin = _load(data_dir)

    # Anchor the forecast at the FISCAL CLOSE — the last shares/financials period_end (the books-close date the
    # backlog is measured at) — not a mid-quarter "today". This makes every forecast fiscal year a full year
    # and ties the period-0 backlog to the equity-spend board snapshot, which is measured at the same close.
    as_of = _pdate(shares[-1]["period_end"], "shares.period_end")
    if _pdate(fin[-1]["period_end"], "financials.period_end") != as_of:
        raise SBCDataError("shares and financials disagree on the latest period_end — cannot anchor the forecast")
    # the runoff is bucketed by FULL calendar fiscal years and the dashboard says "as of fiscal close", so the
    # anchor must actually BE a Dec-31 fiscal close — otherwise the current FY would be a partial-year bucket
    # silently presented as a full year. Fail closed on a mid-year anchor (calendar fiscal year).
    if (as_of.month, as_of.day) != (12, 31):
        raise SBCDataError(f"forecast anchor {as_of} is not a Dec-31 fiscal close — this engine assumes a "
                           "calendar fiscal year; a mid-year anchor would mis-bucket the current fiscal year")

    # outstanding = granted on/before the as-of date, holder not terminated before it, still recognizing
    live = []
    for g in grants:
        if _pdate(g["grant_date"], f"{g['grant_id']}.gd") > as_of:
            continue
        term = _term_of(workers, g["emp_id"])
        if term is not None and term <= as_of:
            continue                                          # already forfeited/settled — nothing left
        gd = _pdate(g["vest_start_date"], "vs")
        vm = int(float(g["vest_months_total"]))
        if _months(gd, as_of) >= vm:
            continue                                          # fully vested — expense already recognized
        live.append((g, term))
    if not live:
        raise SBCDataError("no outstanding unvested grants at the as-of date — nothing to forecast")

    # backlog (== equity_spend unamortized_sbc): remaining straight-line cost + weighted-avg remaining years
    backlog, weighted_months = 0.0, 0.0
    for g, term in live:
        gd = _pdate(g["vest_start_date"], "vs")
        vm = int(float(g["vest_months_total"]))
        rem_m = max(0, vm - _months(gd, as_of))
        rem_fv = _grant_fv(g) * rem_m / vm
        backlog += rem_fv
        weighted_months += rem_fv * rem_m
    wavg_years = (weighted_months / backlog / 12.0) if backlog else 0.0

    # LOCKED-IN RUNOFF by fiscal year, anchored at the fiscal close.
    first_fy = (as_of + timedelta(days=1)).year          # the first FY not yet closed at the anchor
    horizon = list(range(first_fy, first_fy + HORIZON_FYS))
    # First pass: the UNROUNDED gross incremental recognition per FY. GROSS assumes full vesting (term=None):
    # a forecast as of the close cannot know a future termination, so it recognizes the whole remaining cost
    # and ties exactly to the gross backlog. Estimated future forfeitures are the SEPARATE (1-rate)^k overlay.
    gross_raw, prev_anchor = [], as_of
    for fy in horizon:
        end = _fy_end(fy)
        g = max(0.0, sum(_cum_expense(gr, end, None) - _cum_expense(gr, prev_anchor, None) for gr, _t in live))
        gross_raw.append(g)
        prev_anchor = end
    beyond_raw = max(0.0, backlog - sum(gross_raw))
    # Quantize to INTEGER CENTS with largest-remainder residual assignment, so the DISPLAYED gross figures +
    # the displayed beyond-horizon tail sum to the displayed backlog EXACTLY (to the penny) — the dashboard
    # claims an exact reconciliation, so build_report can enforce it with zero tolerance in integer cents.
    backlog_c = round(backlog * 100)
    beyond_c = round(beyond_raw * 100)
    gross_c = [round(g * 100) for g in gross_raw]
    residual = backlog_c - beyond_c - sum(gross_c)
    if gross_c:                                          # push any rounding residual into the largest bucket
        gross_c[max(range(len(gross_c)), key=lambda i: gross_c[i])] += residual
    schedule, cum_c = [], 0
    for k, fy in enumerate(horizon):
        cum_c += gross_c[k]
        adj = (gross_c[k] / 100.0) * (1.0 - ILLUSTRATIVE_FORFEITURE_RATE) ** k
        schedule.append({"fy": fy, "gross_expense": round(gross_c[k] / 100.0, _DP),
                         "forfeiture_adj_expense": round(adj, _DP), "cumulative_gross": round(cum_c / 100.0, _DP)})
    beyond = round(beyond_c / 100.0, _DP)
    runoff_complete_fy = next((r["fy"] for r in schedule
                               if abs(r["cumulative_gross"] - backlog) < 0.01), None)

    # NEW-GRANT OVERLAY (illustrative steady-state): keep granting at the TTM run-rate, each vintage
    # straight-line over NEW_GRANT_VEST_MONTHS. A vintage granted at the start of FY y contributes 12/vm of
    # its value in each of the vm/12 following years.
    ttm_grant_fv = sum(_grant_fv(g) for g in grants
                       if 0 <= (as_of - _pdate(g["grant_date"], "gd")).days < 365)
    annual_new = ttm_grant_fv
    per_year_frac = 12.0 / NEW_GRANT_VEST_MONTHS
    new_overlay = []
    for k, fy in enumerate(horizon):
        # vintages granted at the start of FY horizon[0]..fy, each still within its vesting window this FY
        exp = 0.0
        for j in range(0, k + 1):
            age_years = k - j                                 # how many years since this vintage was granted
            if age_years < NEW_GRANT_VEST_MONTHS / 12:
                exp += annual_new * per_year_frac
        new_overlay.append({"fy": fy, "expense": round(exp, _DP)})

    total_forecast = []
    # every revenue row must be a POSITIVE number, and the TTM denominator must be > 0 — otherwise the % line
    # is a divide-by-zero (or a nonsensical negative), and it is rendered. Fail closed here.
    rev_rows = fin[-4:] if len(fin) >= 4 else fin[-1:]
    last_ttm_rev = sum(_num(r["revenue_usd"], "financials.revenue_usd", positive=True) for r in rev_rows)
    if last_ttm_rev <= 0:
        raise SBCDataError("trailing-twelve-months revenue must be > 0 to express SBC as a % of revenue")
    for k, fy in enumerate(horizon):
        locked = schedule[k]["forfeiture_adj_expense"]
        newg = new_overlay[k]["expense"]
        total = locked + newg
        total_forecast.append({"fy": fy, "locked_in": locked, "new_grants": newg,
                               "total": round(total, _DP),
                               "pct_ttm_revenue": round(total / last_ttm_rev * 100.0, _DP)})

    last_share = shares[-1]
    cso = _num(last_share["common_shares_outstanding"], "cso", positive=True)
    price = _num(last_share["close_price_usd"], "price", positive=True)
    market_cap = cso * price

    return {
        "company": "Acme Corp (ACMQ)", "as_of": as_of.isoformat(),
        "horizon_fys": horizon,
        "assumptions": {
            "forfeiture_rate_annual_pct": round(ILLUSTRATIVE_FORFEITURE_RATE * 100, 2),
            "new_grant_run_rate_usd": round(annual_new, _DP),
            "new_grant_vest_months": NEW_GRANT_VEST_MONTHS,
            "revenue_basis": "last trailing-twelve-months revenue, held flat (illustrative)",
            "note": "Forfeiture rate, new-grant run-rate/attribution, and flat revenue are ILLUSTRATIVE "
                    "assumptions — never guidance. The locked-in runoff itself is assumption-free.",
        },
        "locked_in": {
            "backlog_unrecognized_usd": round(backlog, _DP),
            "wavg_remaining_years": round(wavg_years, _DP),
            "schedule": schedule,
            "beyond_horizon_usd": beyond,
            "runoff_complete_fy": runoff_complete_fy,
            "reconciles_to": "equity_spend.unamortized_sbc (same amortization, split by fiscal year)",
        },
        "new_grant_overlay": {"schedule": new_overlay, "annual_run_rate_usd": round(annual_new, _DP)},
        "total_forecast": total_forecast,
        "context": {
            "common_shares_outstanding": int(cso), "close_price_usd": round(price, 2),
            "market_cap_usd": round(market_cap, _DP),
            "backlog_pct_market_cap": round(backlog / market_cap * 100.0, _DP) if market_cap else None,
            "last_ttm_revenue_usd": round(last_ttm_rev, _DP),
        },
        "disclaimer": "Illustrative SBC-expense forecast on synthetic data. The locked-in runoff is pure "
                      "amortization of grants already made (assumption-free, reconciles to the equity-spend "
                      "backlog); the forfeiture rate, new-grant run-rate, and flat-revenue basis are labeled "
                      "assumptions, not financial guidance. Presentation + governance only.",
    }


if __name__ == "__main__":
    import json
    print(json.dumps(compute(), indent=2))
