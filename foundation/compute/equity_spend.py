#!/usr/bin/env python3
"""Company-wide equity-spend / burn-rate compute over the synthetic Acme equity plan.

The board's two questions are "is the spend sustainable?" and "when do we go back to shareholders, and
will we pass?" This engine answers both from an APPEND-ONLY GRANT LEDGER (`equity_grants.csv`) that is the
single source of truth: stock-based-compensation (SBC) expense and the pool roll-forward are DERIVED here by
amortizing the ledger and applying service-condition forfeitures off `workers.csv` term dates — there is no
hand-maintained derived file to drift.

METHODOLOGY-FAITHFUL vs ILLUSTRATIVE (the honesty line, stated like iss_screen.py):
- STRUCTURES are methodology-faithful: SBC % of revenue; gross/net burn; the CURRENT ISS Equity-Plan-Scorecard
  **Value-Adjusted Burn Rate (VABR)** structure — options at Black-Scholes value, full-value awards at price,
  over WASO×price, 3-yr average; overhang/dilution; pool longevity; the EPSC three-pillar framing (Plan Cost /
  Plan Features / Grant Practices).
- The VABR here is an ILLUSTRATIVE RECONSTRUCTION, not the exact ISS number: the PRICE INPUT is the
  grant-date / period-end close, whereas ISS's current convention uses a stock-price hierarchy led by a
  ~200-day average (QDD). The structure matches; the price input is simplified — so this is a directional
  reconstruction, not "the ISS VABR."
- ILLUSTRATIVE (labeled, never claimed as advisor output): the benchmark burn caps + EPSC weights/threshold
  (in `burn_benchmarks.csv`, gated on a `source_note` that must say "illustrative"); the EPSC "Plan Cost"
  figure is an OVERHANG proxy ((outstanding + pool) ÷ shares), NOT ISS's proprietary value-adjusted SVT —
  labeled as an overhang gauge. The legacy volatility-multiplier burn is the
  pre-2023 ISS convention, RETIRED in the 2023 policy year — retained only as a diagnostic because older
  board decks still quote it.

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
_PLAN_COLS = ("plan_id", "plan_name", "adoption_date", "expiration_date", "shareholder_approved",
              "initial_pool_shares", "evergreen", "share_recycling", "fungible_ratio", "min_vesting_months",
              "permits_repricing", "dividends_on_unvested", "discretionary_acceleration")
_GROUPS = ("ceo", "section16", "management", "staff", "director")
_AWARDS = ("rsu", "option", "psu")
_GRANT_TYPES = ("new_hire", "annual_refresh", "exec_annual", "director_annual", "promotion")


class EquityDataError(ValueError):
    pass


# ---------------------------------------------------------------- loading + validation
def _rows(path, cols):
    if not path.exists():
        raise EquityDataError(f"missing data file: {path.name}")
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames is None or [c for c in rd.fieldnames] != list(cols):
            raise EquityDataError(f"{path.name}: header {rd.fieldnames} != expected {list(cols)}")
        out = [dict(r) for r in rd]
    if not out:
        raise EquityDataError(f"{path.name}: no rows")
    return out


def _num(v, ctx, positive=False, allow_zero=True):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise EquityDataError(f"{ctx}: not a number ({v!r})")
    if not math.isfinite(f):
        raise EquityDataError(f"{ctx}: not finite ({v!r})")
    if positive and f <= 0:
        raise EquityDataError(f"{ctx}: must be > 0 ({v!r})")
    if not allow_zero and f == 0:
        raise EquityDataError(f"{ctx}: must be non-zero")
    return f


def _int_num(v, ctx, positive=False, allow_zero=True):
    f = _num(v, ctx, positive=positive, allow_zero=allow_zero)
    if not f.is_integer():
        raise EquityDataError(f"{ctx}: must be a whole number ({v!r})")
    return int(f)


def _pdate(v, ctx):
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise EquityDataError(f"{ctx}: bad date {v!r}")


def _months(a: date, b: date) -> int:
    """Whole months from a to b (>=0 clamps at 0)."""
    return max(0, (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0))


class EquityPlan:
    """Loads + validates the equity plan and derives the SBC-expense + pool roll-forward the board sees.
    Fail-closed on any structural or referential defect — a board figure must not rest on bad data."""

    def __init__(self, data_dir=_DATA):
        d = Path(data_dir)
        self.grants = _rows(d / "equity_grants.csv", _GRANT_COLS)
        self.plans = {}
        for p in _rows(d / "equity_plans.csv", _PLAN_COLS):
            if p["plan_id"] in self.plans:                    # last-row-wins would silently rewrite board metrics
                raise EquityDataError(f"duplicate plan_id {p['plan_id']} in equity_plans.csv")
            self.plans[p["plan_id"]] = p
        self.shares = _rows(d / "shares_outstanding.csv", _SHARE_COLS)
        self.financials = _rows(d / "financials.csv", ("period_end", "revenue_usd"))
        self.workers = self._load_workers(d)
        self.directors = {r["director_id"] for r in _rows(d / "directors.csv",
                                                          ("director_id", "independent", "committee"))}
        self.benchmarks = _rows(d / "burn_benchmarks.csv",
                                ("index_group", "gics_code", "gics_label", "fiscal_year", "vabr_benchmark_pct",
                                 "legacy_adjusted_cap_pct", "epsc_model", "epsc_pass_threshold", "source_note"))
        self._validate()

    @staticmethod
    def _load_workers(d):
        out = {}
        with open(d / "workers.csv", newline="", encoding="utf-8") as fh:
            for w in csv.DictReader(fh):
                if w["emp_id"] in out:                       # last-row-wins would silently rewrite term math
                    raise EquityDataError(f"duplicate employee_id {w['emp_id']} in workers.csv")
                out[w["emp_id"]] = w
        return out

    # -- quarter + fiscal-year spine (fiscal year = calendar year; Acme FY ends Dec 31) --
    def quarters(self):
        return [_pdate(s["period_end"], "shares_outstanding.period_end") for s in self.shares]

    def _validate(self):
        qs = self.quarters()
        if any(qs[i] >= qs[i + 1] for i in range(len(qs) - 1)):
            raise EquityDataError("shares_outstanding periods must be strictly increasing (spine order)")
        fin_q = [_pdate(f["period_end"], "financials.period_end") for f in self.financials]
        if qs != fin_q:
            raise EquityDataError("shares_outstanding periods != financials periods (spine mismatch)")
        for f in self.financials:
            _num(f["revenue_usd"], f"financials {f['period_end']} revenue", positive=True)
        # benchmark honesty gate — a note must AFFIRMATIVELY lead with 'illustrative' (a bare substring would
        # wave through "not illustrative"/"nonillustrative ..."); each fiscal year appears exactly once, with
        # a positive cap + integer threshold, so the EPSC lookup can never crash or silently pick a dupe.
        self.bench_by_fy = {}
        for b in self.benchmarks:
            if not b["source_note"].strip().lower().startswith("illustrative"):
                raise EquityDataError(f"benchmark FY{b['fiscal_year']} source_note must lead with 'illustrative'")
            fy = _int_num(b["fiscal_year"], "benchmark.fiscal_year", positive=True)
            if fy in self.bench_by_fy:
                raise EquityDataError(f"duplicate burn_benchmarks row for fiscal_year {fy}")
            _num(b["vabr_benchmark_pct"], f"benchmark FY{fy} vabr_cap", positive=True)
            _int_num(b["epsc_pass_threshold"], f"benchmark FY{fy} epsc_threshold", positive=True)
            self.bench_by_fy[fy] = b
        # shares-outstanding sanity + the market-cap identity on the final quarter
        prev_close = None
        for s in self.shares:
            cso = _num(s["common_shares_outstanding"], "cso", positive=True)
            wb = _num(s["waso_basic"], "waso_basic", positive=True)
            wd = _num(s["waso_diluted"], "waso_diluted", positive=True)
            _num(s["close_price_usd"], "close", positive=True)
            if wb > cso or wd < wb:
                raise EquityDataError(f"{s['period_end']}: require waso_basic<=CSO<=... and diluted>=basic")
        # grant referential + shape checks
        seen = set()
        for g in self.grants:
            gid = g["grant_id"]
            if gid in seen:
                raise EquityDataError(f"duplicate grant_id {gid}")
            seen.add(gid)
            if g["participant_group"] not in _GROUPS:
                raise EquityDataError(f"{gid}: bad participant_group {g['participant_group']!r}")
            if g["award_type"] not in _AWARDS:
                raise EquityDataError(f"{gid}: bad award_type {g['award_type']!r}")
            if g["grant_type"] not in _GRANT_TYPES:
                raise EquityDataError(f"{gid}: bad grant_type {g['grant_type']!r}")
            if g["plan_id"] not in self.plans:
                raise EquityDataError(f"{gid}: unknown plan_id {g['plan_id']!r}")
            emp = g["emp_id"]
            if g["participant_group"] == "director":
                if emp not in self.directors:
                    raise EquityDataError(f"{gid}: director grant to unknown director {emp!r}")
            elif emp not in self.workers:
                raise EquityDataError(f"{gid}: grant to unknown emp_id {emp!r}")
            _int_num(g["shares_granted"], f"{gid}.shares", positive=True)   # whole shares in an append-only ledger
            _num(g["stock_price_at_grant_usd"], f"{gid}.price", positive=True)
            _num(g["grant_date_fv_per_share_usd"], f"{gid}.fv", positive=True)
            vm = _int_num(g["vest_months_total"], f"{gid}.vest_months", positive=True)
            cliff = _int_num(g["cliff_months"], f"{gid}.cliff")
            if cliff > vm:
                raise EquityDataError(f"{gid}: cliff {cliff} > vest_months {vm}")
            mult = _num(g["psu_max_multiplier"], f"{gid}.psu_mult")
            if g["award_type"] == "psu":
                if mult <= 1.0:
                    raise EquityDataError(f"{gid}: PSU psu_max_multiplier must be > 1")
                if g["psu_share_basis"] != "target":         # shares_granted must be an unambiguous TARGET count
                    raise EquityDataError(f"{gid}: PSU psu_share_basis must be 'target' (got {g['psu_share_basis']!r})")
            else:
                if mult != 1.0:
                    raise EquityDataError(f"{gid}: non-PSU psu_max_multiplier must be exactly 1.0 (got {mult})")
                if g["psu_share_basis"] not in ("", None):
                    raise EquityDataError(f"{gid}: psu_share_basis set on a non-PSU award")
            # strike present iff option
            if g["award_type"] == "option":
                _num(g["strike_price_usd"], f"{gid}.strike", positive=True)
            elif g["strike_price_usd"] not in ("", None):
                raise EquityDataError(f"{gid}: strike present on a non-option award")
            gdate = _pdate(g["grant_date"], f"{gid}.grant_date")
            plan = self.plans[g["plan_id"]]
            if not (_pdate(plan["adoption_date"], "adopt") <= gdate <= _pdate(plan["expiration_date"], "exp")):
                raise EquityDataError(f"{gid}: grant_date {gdate} outside plan {g['plan_id']} active window")
            if g["participant_group"] != "director":         # a departed employee cannot receive a new grant
                term = self.workers[emp].get("term_date")
                if term and gdate > _pdate(term, f"{gid}.term"):
                    raise EquityDataError(f"{gid}: grant_date {gdate} after holder term_date {term}")

    # ---------------------------------------------------------------- derivation
    def _term_date(self, emp):
        w = self.workers.get(emp)
        if w and w.get("term_date"):
            return _pdate(w["term_date"], "term")
        return None

    def _vested_fraction(self, g, at: date) -> float:
        """Fraction of a grant vested at `at` — cliff then straight-line monthly (PSU = single cliff)."""
        gd = _pdate(g["vest_start_date"], "vs")
        vm = int(float(g["vest_months_total"]))
        cliff = int(float(g["cliff_months"]))
        el = _months(gd, at)
        if el < cliff:
            return 0.0
        return min(1.0, el / vm)

    def _grant_fv(self, g) -> float:
        return int(float(g["shares_granted"])) * float(g["grant_date_fv_per_share_usd"])

    def _cum_expense(self, g, at: date) -> float:
        """Cumulative SBC recognized for a grant at `at`: straight-line over the service period, trued-up to
        the vested value once the holder has terminated (service-condition forfeiture reverses unvested cost)."""
        gd = _pdate(g["vest_start_date"], "vs")
        if at < gd:
            return 0.0
        vm = int(float(g["vest_months_total"]))
        total = self._grant_fv(g)
        term = self._term_date(g["emp_id"])
        if term is not None and term <= at:
            return round(self._vested_fraction(g, term) * total, 6)   # forfeit unvested -> trued-up to vested
        return round(min(1.0, _months(gd, at) / vm) * total, 6)

    def sbc_by_quarter(self):
        """Derived quarterly SBC expense ($) = the change in cumulative recognized expense across the grant
        book. This is the board's expense line, reconstructed from the ledger."""
        qs = self.quarters()
        out = []
        # seed each grant's running total with its cumulative expense at the quarter BEFORE the window, so the
        # first reported quarter shows only ITS incremental expense (not the whole pre-window back-history).
        first = qs[0]
        prior_q = date(first.year, ((first.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
        prev = {g["grant_id"]: self._cum_expense(g, prior_q) for g in self.grants}
        for q in qs:
            tot = 0.0
            for g in self.grants:
                c = self._cum_expense(g, q)
                tot += c - prev[g["grant_id"]]
                prev[g["grant_id"]] = c
            out.append((q, round(tot, 2)))
        return out

    def unamortized_sbc(self, at: date):
        """(remaining unrecognized SBC $, weighted-avg remaining years) at `at` — the 'locked-in' backlog."""
        rem_total, weighted_months = 0.0, 0.0
        for g in self.grants:
            if _pdate(g["grant_date"], "gd") > at:
                continue                                      # not yet granted at `at` — no backlog to book
            term = self._term_date(g["emp_id"])
            if term is not None and term <= at:
                continue                                      # forfeited/settled: nothing left to recognize
            gd = _pdate(g["vest_start_date"], "vs")
            vm = int(float(g["vest_months_total"]))
            end = date(gd.year + vm // 12, gd.month, gd.day) if vm % 12 == 0 else gd
            rem_m = max(0, vm - _months(gd, at))
            if rem_m <= 0:
                continue
            rem_fv = self._grant_fv(g) * rem_m / vm
            rem_total += rem_fv
            weighted_months += rem_fv * rem_m
        yrs = (weighted_months / rem_total / 12.0) if rem_total else 0.0
        return round(rem_total, 2), round(yrs, 2)


# legacy ISS full-value-award multiplier by annualized volatility (pre-2023 EPSC convention, RETIRED 2023).
# Illustrative reconstruction of the published premium table — retained only as a diagnostic.
_LEGACY_MULT = ((0.546, 1.5), (0.361, 2.0), (0.249, 2.5), (0.165, 3.0), (0.079, 3.5), (0.0, 4.0))


def _legacy_multiplier(vol):
    for lo, m in _LEGACY_MULT:
        if vol >= lo:
            return m
    return 4.0


class EquitySpend:
    """The board metrics, all derived from the ledger. Fiscal year = calendar year (Acme FY ends Dec 31)."""

    def __init__(self, plan: EquityPlan):
        self.p = plan
        self.qs = plan.quarters()
        self.as_of = self.qs[-1]
        self._rev_q = {_pdate(f["period_end"], "fin"): _num(f["revenue_usd"], "rev") for f in plan.financials}
        self._sh_q = {_pdate(s["period_end"], "sh"): s for s in plan.shares}
        self.fys = sorted({q.year for q in self.qs})

    def _fy_quarters(self, fy):
        return [q for q in self.qs if q.year == fy]

    def revenue_fy(self, fy):
        return sum(self._rev_q[q] for q in self._fy_quarters(fy))

    def waso_fy(self, fy):
        qq = self._fy_quarters(fy)
        return sum(_num(self._sh_q[q]["waso_basic"], "w") for q in qq) / len(qq)

    def price_fy_end(self, fy):
        return _num(self._sh_q[self._fy_quarters(fy)[-1]]["close_price_usd"], "px")

    # -- grant slices --
    def _granted_fy(self, fy):
        return [g for g in self.p.grants if _pdate(g["grant_date"], "gd").year == fy]

    def _forfeited_shares_fy(self, fy):
        """Unvested shares returned to the pool in FY (strict recycling): grants whose holder terminated in FY."""
        tot = 0.0
        for g in self.p.grants:
            term = self.p._term_date(g["emp_id"])
            if term is not None and term.year == fy:
                unvested = int(float(g["shares_granted"])) * (1.0 - self.p._vested_fraction(g, term))
                tot += unvested
        return tot

    def gross_burn_fy(self, fy):
        shares = sum(int(float(g["shares_granted"])) for g in self._granted_fy(fy))
        return shares / self.waso_fy(fy)

    def net_burn_fy(self, fy):
        shares = sum(int(float(g["shares_granted"])) for g in self._granted_fy(fy))
        return (shares - self._forfeited_shares_fy(fy)) / self.waso_fy(fy)

    def vabr_fy(self, fy):
        """Value-Adjusted Burn Rate — the CURRENT ISS EPSC VABR STRUCTURE (illustrative reconstruction; the
        price input is grant-date / period-end, not ISS's ~200-day-average QDD hierarchy). Options at grant
        Black-Scholes value, full-value awards at grant-date price, over WASO x price."""
        num = 0.0
        for g in self._granted_fy(fy):
            sh = int(float(g["shares_granted"]))
            if g["award_type"] == "option":
                num += sh * float(g["grant_date_fv_per_share_usd"])
            else:                                             # rsu / psu (full-value)
                num += sh * float(g["stock_price_at_grant_usd"])
        return num / (self.waso_fy(fy) * self.price_fy_end(fy))

    def legacy_adjusted_burn_fy(self, fy):
        """Diagnostic only — the RETIRED (pre-2023) ISS volatility-multiplier convention."""
        vol = _num(self._sh_q[self._fy_quarters(fy)[-1]]["annualized_volatility"], "vol")
        mult = _legacy_multiplier(vol)
        num = 0.0
        for g in self._granted_fy(fy):
            sh = int(float(g["shares_granted"]))
            num += sh * (1.0 if g["award_type"] == "option" else mult)
        return num / self.waso_fy(fy)

    def _avg3(self, fn):
        if len(self.fys) < 3:                                 # a "3-year" average must actually have 3 years
            raise EquityDataError(f"3-year metric needs >= 3 fiscal years, have {len(self.fys)}")
        recent = self.fys[-3:]
        return sum(fn(fy) for fy in recent) / len(recent)

    # -- pool + overhang (snapshot at as_of, active plan P-2022) --
    def _outstanding_shares(self, at):
        """Shares granted but not yet delivered/forfeited: unvested RSU/PSU + non-forfeited option shares."""
        tot = 0.0
        for g in self.p.grants:
            if _pdate(g["grant_date"], "gd") > at:
                continue                                      # not yet granted at `at` — not outstanding
            term = self.p._term_date(g["emp_id"])
            vf = self.p._vested_fraction(g, at)
            sh = int(float(g["shares_granted"]))
            if g["award_type"] == "option":
                if term is not None and term <= at:
                    tot += sh * self.p._vested_fraction(g, term)
                else:
                    tot += sh                                 # options outstanding until exercise (unmodeled)
            else:
                if term is not None and term <= at:
                    continue                                  # forfeited unvested returned; vested delivered
                tot += sh * (1.0 - vf)                        # unvested full-value awards
        return tot

    def pool_available(self, at, plan_id="P-2022"):
        plan = self.p.plans[plan_id]
        initial = _num(plan["initial_pool_shares"], "pool")
        used = 0.0
        for g in self.p.grants:
            if g["plan_id"] != plan_id or _pdate(g["grant_date"], "gd") > at:
                continue
            sh = int(float(g["shares_granted"]))
            term = self.p._term_date(g["emp_id"])
            returned = sh * (1.0 - self.p._vested_fraction(g, term)) if (term is not None and term <= at) else 0.0
            used += sh - returned                             # strict recycling: forfeited unvested come back
        raw = initial - used
        if raw < 0:                                           # an overdrawn pool is a real defect, not a clean 0
            raise EquityDataError(f"{plan_id}: pool overdrawn by {-raw:.0f} shares (grants exceed authorized pool)")
        return raw

    def overhang(self, at=None):
        """Overhang = (outstanding awards + shares still available to grant) / CSO — the fully-loaded number."""
        at = at or self.as_of
        cso = _num(self._sh_q[at]["common_shares_outstanding"], "cso")
        return (self._outstanding_shares(at) + self.pool_available(at)) / cso

    def dilution_pct(self, at=None):
        """Dilution = outstanding awards ONLY / CSO — the narrower, standard number (no unallocated pool).
        Reported distinctly from overhang so the two aren't conflated in a board deck."""
        at = at or self.as_of
        cso = _num(self._sh_q[at]["common_shares_outstanding"], "cso")
        return self._outstanding_shares(at) / cso

    def pool_longevity_years(self, at=None):
        at = at or self.as_of
        net3 = self._avg3(lambda fy: sum(int(float(g["shares_granted"])) for g in self._granted_fy(fy))
                          - self._forfeited_shares_fy(fy))
        return self.pool_available(at) / net3 if net3 > 0 else float("inf")

    def value_per_fte_by_group(self, fy=None):
        fy = fy or self.fys[-1]
        agg = {}
        for g in self._granted_fy(fy):
            grp = g["participant_group"]
            v, r = agg.setdefault(grp, [0.0, set()])
            v_new = v + self.p._grant_fv(g)
            r.add(g["emp_id"])
            agg[grp] = [v_new, r]
        return {grp: {"value": round(v, 0), "recipients": len(r), "per_fte": round(v / len(r), 0)}
                for grp, (v, r) in agg.items()}

    def sbc_pct_revenue(self):
        by_q = self.p.sbc_by_quarter()
        rows = [{"period": q.isoformat(), "sbc": sbc, "revenue": self._rev_q[q],
                 "pct": round(sbc / self._rev_q[q] * 100, 2)} for q, sbc in by_q]
        ttm = sum(r["sbc"] for r in rows[-4:]) / sum(r["revenue"] for r in rows[-4:]) * 100
        return {"quarterly": rows, "ttm_pct": round(ttm, 2)}

    def epsc_readiness(self):
        """The ISS Equity-Plan-Scorecard three-pillar framing. Plan Features scored EXACTLY from plan facts;
        Grant Practices = 3-yr VABR vs an ILLUSTRATIVE benchmark cap; Plan Cost = a directional OVERHANG proxy
        (NOT a value-adjusted SVT)."""
        plan = self.p.plans["P-2022"]
        features = [
            ("Minimum vesting >= 1 year", int(float(plan["min_vesting_months"])) >= 12),
            ("No repricing without shareholder approval", plan["permits_repricing"] == "no"),
            ("No dividends on unvested awards", plan["dividends_on_unvested"] == "no"),
            ("No liberal / discretionary acceleration", plan["discretionary_acceleration"] == "no"),
            ("Strict share recycling (no add-backs)", plan["share_recycling"] == "strict"),
            ("No evergreen provision", plan["evergreen"] == "none"),
        ]
        vabr3 = self._avg3(self.vabr_fy) * 100
        latest = self.fys[-1]
        if latest not in self.p.bench_by_fy:                  # fail closed, not a raw KeyError
            raise EquityDataError(f"burn_benchmarks missing fiscal_year {latest}")
        bench = self.p.bench_by_fy[latest]
        cap = _num(bench["vabr_benchmark_pct"], "vabr_cap", positive=True)
        # Plan Cost — a directional OVERHANG proxy, NOT a value-adjusted SVT: (outstanding awards + available
        # pool) / common shares outstanding. ISS's real SVT is a proprietary binomial valuation with
        # company-specific caps we do not model, so this is labeled an overhang gauge, not an SVT.
        cso = _num(self._sh_q[self.as_of]["common_shares_outstanding"], "cso")
        overhang = (self._outstanding_shares(self.as_of) + self.pool_available(self.as_of)) / cso
        return {
            "plan_features": [{"test": t, "pass": bool(ok)} for t, ok in features],
            "features_passed": sum(1 for _, ok in features if ok), "features_total": len(features),
            "grant_practices": {"vabr_3yr_pct": round(vabr3, 2), "benchmark_cap_pct": cap,
                                "pass": vabr3 <= cap, "headroom_pct": round(cap - vabr3, 2),
                                "source_note": bench["source_note"]},
            "plan_cost_overhang_pct": round(overhang * 100, 1), "epsc_pass_threshold": int(float(bench["epsc_pass_threshold"])),
        }


def compute(data_dir=_DATA):
    """The full board results dict the equity-spend agent renders (it does no math of its own)."""
    plan = EquityPlan(data_dir)
    es = EquitySpend(plan)
    fys = es.fys
    px = _num(es._sh_q[es.as_of]["close_price_usd"], "px")
    cso = _num(es._sh_q[es.as_of]["common_shares_outstanding"], "cso")
    unamort, unamort_yrs = plan.unamortized_sbc(es.as_of)
    return {
        "as_of": es.as_of.isoformat(), "fiscal_years": fys,
        "market_cap": round(cso * px, 0), "shares_outstanding": cso, "price": px,
        "sbc_pct_revenue": es.sbc_pct_revenue(),
        "burn": [{"fy": fy, "gross_pct": round(es.gross_burn_fy(fy) * 100, 2),
                  "net_pct": round(es.net_burn_fy(fy) * 100, 2), "vabr_pct": round(es.vabr_fy(fy) * 100, 2),
                  "legacy_adjusted_pct": round(es.legacy_adjusted_burn_fy(fy) * 100, 2)} for fy in fys],
        "vabr_3yr_pct": round(es._avg3(es.vabr_fy) * 100, 2),
        "gross_burn_3yr_pct": round(es._avg3(es.gross_burn_fy) * 100, 2),
        "overhang_pct": round(es.overhang() * 100, 2),
        "dilution_pct": round(es.dilution_pct() * 100, 2),
        "pool_available": round(es.pool_available(es.as_of), 0),
        "pool_longevity_years": round(es.pool_longevity_years(), 2),
        "unamortized_sbc": unamort, "unamortized_sbc_years": unamort_yrs,
        "value_per_fte_by_group": es.value_per_fte_by_group(),
        "epsc": es.epsc_readiness(),
    }
