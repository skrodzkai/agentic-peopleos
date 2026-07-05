#!/usr/bin/env python3
"""Company-wide merit / comp-cycle planning over the synthetic Acme workforce.

The annual compensation cycle a VP of Total Rewards runs: allocate a merit-increase budget through a
performance x compa-ratio matrix, size bonus payouts (target x individual x company attainment), apply
promotion increases, and generate equity refreshers. The equity refreshers are emitted as grant rows in the
SAME schema as the equity ledger (`equity_grants.csv`), preserving each holder's existing participant group.
Being FY2026-dated grants (past the equity as-of), they append as valid rows that carry into the NEXT period's
board equity metrics (burn, SBC, overhang), not the current close -- the ledger schema is the contract between
the two arms.

METHODOLOGY-FAITHFUL vs ILLUSTRATIVE:
- STRUCTURES are standard Total-Rewards practice: compa-ratio (base / range-mid), range penetration, a merit
  matrix keyed on performance rating x compa-ratio quartile (higher performers below market get the most),
  bonus = base x target% x individual-factor x company-attainment, promotion increases, budget conformance.
- ILLUSTRATIVE (labeled): the specific matrix cells, bonus targets, attainment factor, refresh grid, and the
  overall merit budget are defensible neutral placeholders -- a real cycle calibrates these to the plan.

Standard library only. Deterministic. Fail-closed. Presentation layers render it; they never decide.
"""
from __future__ import annotations

import csv
import math
import re
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "data" / "acme"

_WORKER_COLS_MIN = ("emp_id", "worker_type", "status", "level", "job_family", "location", "base_salary",
                    "band_id", "rating", "promoted_this_period", "promotion_eligible", "potential",
                    "is_people_manager", "scheduled_hours", "standard_full_time_hours")
_BAND_COLS = ("band_id", "level", "job_family", "location", "range_min", "range_mid", "range_max", "market_p50")
_WORKER_TYPES = ("employee", "contractor")
# the equity ledger's participant groups, most-senior first — a refresher preserves the holder's existing group
_LEDGER_GROUPS = ("ceo", "section16", "management", "staff")

_RATINGS = ("below", "meets", "exceeds", "outstanding")
_LEVELS = ("L3", "L4", "L5", "L6", "L7")

# ---- ILLUSTRATIVE reconstruction constants (labeled placeholders; a real cycle calibrates these) -----------
_MERIT = {
    # merit % by (rating, compa-quartile). Classic shape: reward high performers who sit BELOW market most;
    # zero for low performers already above market. Quartiles are compa-ratio bands (see _compa_quartile).
    "matrix": {
        "outstanding": {"q1": 0.070, "q2": 0.060, "q3": 0.050, "q4": 0.040},
        "exceeds":     {"q1": 0.055, "q2": 0.045, "q3": 0.035, "q4": 0.025},
        "meets":       {"q1": 0.040, "q2": 0.030, "q3": 0.025, "q4": 0.015},
        "below":       {"q1": 0.010, "q2": 0.005, "q3": 0.000, "q4": 0.000},
    },
    "merit_budget_pct": 0.035,                 # 3.5% of eligible payroll is the merit pool
    # bonus target % of base by level, scaled by an individual-performance factor and a company attainment
    "bonus_target": {"L3": 0.08, "L4": 0.10, "L5": 0.15, "L6": 0.25, "L7": 0.40},
    "bonus_rating_factor": {"below": 0.50, "meets": 1.00, "exceeds": 1.20, "outstanding": 1.50},
    "company_attainment": 1.05,                # 105% of target (illustrative; a real cycle ties this to results)
    "promo_increase_pct": 0.10,                # promotion bump applied on top of merit
    # annual equity refresher grant-date VALUE ($) by level x performance tier (staff/mgmt refreshers)
    "refresh_grid": {
        "L3": {"below": 0, "meets": 18_000, "exceeds": 28_000, "outstanding": 40_000},
        "L4": {"below": 0, "meets": 30_000, "exceeds": 45_000, "outstanding": 65_000},
        "L5": {"below": 0, "meets": 55_000, "exceeds": 80_000, "outstanding": 115_000},
        "L6": {"below": 0, "meets": 110_000, "exceeds": 160_000, "outstanding": 230_000},
        "L7": {"below": 0, "meets": 240_000, "exceeds": 340_000, "outstanding": 480_000},
    },
    "refresh_price_usd": 85.20,                # grant-date price for refreshers (matches the equity arm anchor)
    "refresh_vest_months": 48, "refresh_cliff_months": 12,
}


class MeritDataError(ValueError):
    pass


def _rows(path, cols):
    if not path.exists():
        raise MeritDataError(f"missing data file: {path.name}")
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames is None:
            raise MeritDataError(f"{path.name}: no header")
        missing = [c for c in cols if c not in rd.fieldnames]
        if missing:
            raise MeritDataError(f"{path.name}: missing columns {missing}")
        out = [dict(r) for r in rd]
    if not out:
        raise MeritDataError(f"{path.name}: no rows")
    return out


def _num(v, ctx, positive=False):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise MeritDataError(f"{ctx}: not a number ({v!r})")
    if not math.isfinite(f):
        raise MeritDataError(f"{ctx}: not finite ({v!r})")
    if positive and f <= 0:
        raise MeritDataError(f"{ctx}: must be > 0 ({v!r})")
    return f


def _compa_quartile(compa):
    """Compa-ratio band -> matrix quartile key. Below market gets the biggest merit; above market the least."""
    if compa < 0.90:
        return "q1"
    if compa < 1.00:
        return "q2"
    if compa < 1.10:
        return "q3"
    return "q4"


class MeritCycle:
    """Loads + validates the workforce, then plans the annual comp cycle (merit / bonus / promotion / equity
    refresher) with budget conformance. Fail-closed on any structural or referential defect."""

    def __init__(self, data_dir=_DATA):
        d = Path(data_dir)
        self.bands = {}
        for b in _rows(d / "comp_bands.csv", _BAND_COLS):
            if b["band_id"] in self.bands:
                raise MeritDataError(f"duplicate band_id {b['band_id']} in comp_bands.csv")
            lo = _num(b["range_min"], f"{b['band_id']}.range_min", positive=True)
            mid = _num(b["range_mid"], f"{b['band_id']}.range_mid", positive=True)
            hi = _num(b["range_max"], f"{b['band_id']}.range_max", positive=True)
            if not (lo < mid < hi):
                raise MeritDataError(f"{b['band_id']}: require range_min < range_mid < range_max")
            if b["level"] not in _LEVELS:
                raise MeritDataError(f"{b['band_id']}: unknown band level {b['level']!r}")
            self.bands[b["band_id"]] = {"level": b["level"], "job_family": b["job_family"],
                                        "location": b["location"], "min": lo, "mid": mid, "max": hi}
        # OPTIONAL: the equity ledger's existing participant group per employee, so a refresher preserves the
        # ledger's ceo/section16/management/staff allocation (an exec's refresher must not read "management").
        # Absent in a minimal dataset -> refreshers fall back to a level-based group.
        self._ledger_group = {}
        self._max_grant_num = 0                                    # so refresher grant_ids never collide with the ledger
        gpath = d / "equity_grants.csv"
        if gpath.exists():
            rank = {g: i for i, g in enumerate(reversed(_LEDGER_GROUPS))}   # staff=0 .. ceo=3
            with open(gpath, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    emp, grp = row.get("emp_id"), row.get("participant_group")
                    if emp and grp in rank and rank[grp] > rank.get(self._ledger_group.get(emp), -1):
                        self._ledger_group[emp] = grp   # keep the MOST-SENIOR group the holder has ever held
                    m = re.match(r"^G-(\d+)$", (row.get("grant_id") or ""))
                    if m:
                        self._max_grant_num = max(self._max_grant_num, int(m.group(1)))
        # Eligible population: active employees. "Active" here MATCHES the People Analytics headcount
        # definition (foundation/compute/engine.py) — it INCLUDES protected leave, since an employee on leave
        # remains employed and merit-eligible. Contractors and terminated workers are out of the merit cycle.
        # The on-leave subset is counted separately and disclosed (see compute()), never silently folded in.
        seen = set()
        self.emps = []
        self.on_leave = 0
        for w in _rows(d / "workers.csv", _WORKER_COLS_MIN):
            if w["emp_id"] in seen:
                raise MeritDataError(f"duplicate emp_id {w['emp_id']} in workers.csv")
            seen.add(w["emp_id"])
            if w["worker_type"] not in _WORKER_TYPES:      # catch a typo'd worker_type instead of silently dropping
                raise MeritDataError(f"{w['emp_id']}: unknown worker_type {w['worker_type']!r}")
            if w["worker_type"] != "employee" or w["status"] == "terminated":
                continue
            if w["status"] not in ("active", "on_leave"):
                raise MeritDataError(f"{w['emp_id']}: unexpected non-terminated status {w['status']!r}")
            if w["level"] not in _LEVELS:
                raise MeritDataError(f"{w['emp_id']}: unknown level {w['level']!r}")
            if w["rating"] not in _RATINGS:
                raise MeritDataError(f"{w['emp_id']}: unknown rating {w['rating']!r}")
            if w["promoted_this_period"] not in ("yes", "no"):
                raise MeritDataError(f"{w['emp_id']}: promoted_this_period must be yes/no "
                                     f"({w['promoted_this_period']!r})")
            if w["promotion_eligible"] not in ("yes", "no"):     # validate the vocab on every row, promoted or not
                raise MeritDataError(f"{w['emp_id']}: promotion_eligible must be yes/no "
                                     f"({w['promotion_eligible']!r})")
            # a promotion increase requires the worker to actually be promotion-eligible (no contradiction)
            if w["promoted_this_period"] == "yes" and w["promotion_eligible"] != "yes":
                raise MeritDataError(f"{w['emp_id']}: promoted_this_period=yes but promotion_eligible"
                                     f"={w['promotion_eligible']!r}")
            if w["band_id"] not in self.bands:
                raise MeritDataError(f"{w['emp_id']}: unknown band_id {w['band_id']!r}")
            band = self.bands[w["band_id"]]
            # the band must match the worker on level AND job family AND location — else compa-ratio (base ÷
            # range-mid) is computed against the wrong salary range
            if (band["level"], band["job_family"], band["location"]) != (w["level"], w["job_family"], w["location"]):
                raise MeritDataError(f"{w['emp_id']}: band {w['band_id']!r} "
                                     f"({band['level']}/{band['job_family']}/{band['location']}) does not match "
                                     f"worker ({w['level']}/{w['job_family']}/{w['location']})")
            _num(w["base_salary"], f"{w['emp_id']}.base_salary", positive=True)
            if w["is_people_manager"] not in ("yes", "no"):
                raise MeritDataError(f"{w['emp_id']}: is_people_manager must be yes/no ({w['is_people_manager']!r})")
            sch = _num(w["scheduled_hours"], f"{w['emp_id']}.scheduled_hours", positive=True)
            std = _num(w["standard_full_time_hours"], f"{w['emp_id']}.standard_full_time_hours", positive=True)
            if sch > std:
                raise MeritDataError(f"{w['emp_id']}: scheduled_hours {sch} exceeds full-time {std}")
            if w["status"] == "on_leave":
                self.on_leave += 1
            self.emps.append(w)
        if not self.emps:
            raise MeritDataError("no eligible (active employee) population for the merit cycle")

    def _participant_group(self, w):
        """The equity-ledger participant group for a refresher, preserving the holder's existing group; for an
        employee with no ledger history, mirror the generator's rule — 'management' only if a people manager at
        L5-L7, else 'staff' (an L5-L7 individual contributor is staff, not management)."""
        grp = self._ledger_group.get(w["emp_id"])
        if grp in _LEDGER_GROUPS:
            return grp
        return "management" if (w["is_people_manager"] == "yes" and w["level"] in ("L5", "L6", "L7")) else "staff"

    # -- per-employee planning --
    def _plan_one(self, w):
        base = _num(w["base_salary"], "base")                     # FTE base — compa-ratio is an FTE concept
        band = self.bands[w["band_id"]]
        compa = base / band["mid"]
        pen = (base - band["min"]) / (band["max"] - band["min"])
        rating, level = w["rating"], w["level"]
        fte = _num(w["scheduled_hours"], "sched") / _num(w["standard_full_time_hours"], "std")   # actual / full-time
        merit_pct = _MERIT["matrix"][rating][_compa_quartile(compa)]
        promo_pct = _MERIT["promo_increase_pct"] if w["promoted_this_period"] == "yes" else 0.0
        # the RATE increase applies to the FTE base -> the new FTE base drives compa + the over-band-max check
        new_base = base * (1.0 + merit_pct + promo_pct)
        new_compa = new_base / band["mid"]
        # SPEND is the actual cash cost: a % of ACTUAL (FTE-prorated) pay. Equity refreshers prorate to FTE too.
        actual_base = base * fte
        merit_amt = actual_base * merit_pct
        promo_amt = actual_base * promo_pct
        bonus_amt = (actual_base * _MERIT["bonus_target"][level] * _MERIT["bonus_rating_factor"][rating]
                     * _MERIT["company_attainment"])
        refresh_val = float(_MERIT["refresh_grid"][level][rating]) * fte
        return {"emp_id": w["emp_id"], "level": level, "rating": rating, "band_id": w["band_id"],
                "base_salary": base, "fte": fte, "actual_base": actual_base,
                "compa_ratio": compa, "range_penetration": pen,
                "merit_pct": merit_pct, "merit_amount": merit_amt, "promo_amount": promo_amt,
                "new_base_salary": new_base, "new_compa_ratio": new_compa,
                "bonus_amount": bonus_amt, "equity_refresh_value": refresh_val,
                "participant_group": self._participant_group(w),
                "promoted": w["promoted_this_period"] == "yes"}

    def plan(self):
        return [self._plan_one(w) for w in self.emps]

    def equity_refresh_grants(self, plan, grant_year=2026):
        """The equity refreshers as grant rows in the equity-ledger schema -- ready to append to
        equity_grants.csv for the next cycle (the merit arm feeds the equity arm; nothing is mutated here).
        Grant ids continue AFTER the ledger's highest existing id, so an appended row never collides."""
        px = _MERIT["refresh_price_usd"]
        gdate = f"{grant_year}-02-15"
        rows, gid = [], self._max_grant_num                       # start after the ledger's highest grant id
        for p in sorted((p for p in plan if p["equity_refresh_value"] > 0), key=lambda p: p["emp_id"]):
            gid += 1
            shares = max(1, round(p["equity_refresh_value"] / px))
            # the participant group is preserved from the holder's equity-ledger history (a CEO / Section 16
            # officer keeps that group), computed per-employee in _plan_one
            rows.append({
                "grant_id": f"G-{gid:06d}", "plan_id": "P-2022", "emp_id": p["emp_id"],
                "participant_group": p["participant_group"], "grant_type": "annual_refresh", "award_type": "rsu",
                "grant_date": gdate, "shares_granted": int(shares), "psu_max_multiplier": 1.0,
                "psu_share_basis": "", "stock_price_at_grant_usd": round(px, 2), "strike_price_usd": "",
                "grant_date_fv_per_share_usd": round(px, 4), "vest_start_date": gdate,
                "vest_months_total": _MERIT["refresh_vest_months"], "cliff_months": _MERIT["refresh_cliff_months"],
                "vest_frequency": "monthly", "performance_period_end": ""})
        return rows


def _agg_by(plan, key, fields):
    out = {}
    for p in plan:
        b = out.setdefault(p[key], {"n": 0, **{f: 0.0 for f in fields}})
        b["n"] += 1
        for f in fields:
            b[f] += p[f]
    return out


def compute(data_dir=_DATA):
    """The full comp-cycle plan the merit agent renders (it does no math of its own)."""
    cyc = MeritCycle(data_dir)
    plan = cyc.plan()
    n = len(plan)
    payroll = sum(p["actual_base"] for p in plan)            # ACTUAL (FTE-prorated) payroll — the real cash base
    merit_spend = sum(p["merit_amount"] for p in plan)
    promo_spend = sum(p["promo_amount"] for p in plan)
    bonus_pool = sum(p["bonus_amount"] for p in plan)
    refresh_value = sum(p["equity_refresh_value"] for p in plan)
    budget = payroll * _MERIT["merit_budget_pct"]
    refresh_grants = cyc.equity_refresh_grants(plan)
    # the refreshers grouped by the equity ledger's participant group (preserved from the holder's history) —
    # so the board dashboard's exec / management / staff allocation stays intact when these append
    by_group = {}
    for g in refresh_grants:
        b = by_group.setdefault(g["participant_group"], {"grants": 0, "shares": 0})
        b["grants"] += 1
        b["shares"] += g["shares_granted"]
    # guardrail signals (presented; the committee decides): budget conformance, out-of-range-after-merit,
    # and the merit-matrix discipline (a below-rating high-compa employee gets 0%)
    over_max_after = sum(1 for p in plan if p["new_base_salary"] > cyc.bands[p["band_id"]]["max"])
    return {
        "as_of": "FY2026 planning", "eligible_headcount": n,
        # disclosed like the People Analytics headcount (engine.py): eligible = active + protected-leave
        "active_count": n - cyc.on_leave, "on_leave_count": cyc.on_leave,
        "payroll": round(payroll, 0),
        "merit": {"budget": round(budget, 0), "spend": round(merit_spend, 0),
                  "spend_pct": round(merit_spend / payroll * 100, 2),
                  "budget_pct": round(_MERIT["merit_budget_pct"] * 100, 2),
                  "within_budget": merit_spend <= budget + 1.0,
                  "headroom": round(budget - merit_spend, 0)},
        "bonus_pool": round(bonus_pool, 0), "promo_spend": round(promo_spend, 0),
        "promotions": sum(1 for p in plan if p["promoted"]),
        "equity_refresh": {"total_value": round(refresh_value, 0), "grant_count": len(refresh_grants),
                           "total_shares": sum(g["shares_granted"] for g in refresh_grants),
                           "by_group": by_group, "grants": refresh_grants},
        "avg_compa_ratio": round(sum(p["compa_ratio"] for p in plan) / n, 3),
        "avg_new_compa_ratio": round(sum(p["new_compa_ratio"] for p in plan) / n, 3),
        "over_max_after_merit": over_max_after,
        "by_rating": _agg_by(plan, "rating", ("merit_amount", "bonus_amount", "actual_base")),
        "by_level": _agg_by(plan, "level", ("merit_amount", "bonus_amount", "equity_refresh_value", "actual_base")),
        "matrix": _MERIT["matrix"],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(compute(), indent=2, default=str))
