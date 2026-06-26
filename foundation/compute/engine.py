#!/usr/bin/env python3
"""Shared metric compute engine for Agentic PeopleOS.

One place that computes registry metrics from the Acme data foundation, so every reporting
agent reads the SAME numbers and no agent reimplements (or quietly bends) a definition. The
engine honors each metric's protocol — average headcount is the mean of monthly actives, turnover
annualizes by simple x(12/months), cohorts must be matured, span counts every manager, etc.

Governance by construction: the engine only ever READS and AGGREGATES. It has no method that
changes a record, a salary, a rating, or makes a decision — the dangerous actions the registry
forbids are not implementable here. Metrics whose source table isn't in the foundation yet
return status='data_pending' with the missing input named (honest, not faked).

    from foundation.compute.engine import MetricEngine
    eng = MetricEngine()                      # loads foundation/data/acme + the registry
    eng.compute("span_of_control")            # -> {"status":"ok","value":...,"extras":{...}}
    eng.segment("compa_ratio", "level")       # -> {level: value, ...}
"""
from __future__ import annotations

import csv
import math
import statistics
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA = REPO / "foundation" / "data" / "acme"
AS_OF = date(2026, 1, 31)               # matches the data foundation's anchor

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from core.metrics import MetricRegistry  # noqa: E402  (the registry the engine is bound to)


def _date(s):
    return date.fromisoformat(s) if s else None


def _load(name):
    with open(DATA / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _month_ends(start: date, end: date):
    """Month-end dates within [start, end] (inclusive of end's month)."""
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        nm_y, nm_m = (y + 1, 1) if m == 12 else (y, m + 1)
        out.append(date(nm_y, nm_m, 1) - timedelta(days=1))
        y, m = nm_y, nm_m
    return out


class EngineDataError(ValueError):
    """Raised when the foundation contains an impossible fact the engine refuses to compute on
    (fail closed rather than emit a silently-wrong number)."""


class MetricEngine:
    # Metrics whose source table isn't generated yet — named honestly, not faked.
    DATA_PENDING = {
        "vacancy_rate": "approved-positions / establishment table",
        "headcount_plan_attainment": "headcount plan table",
        "internal_mobility_rate": "internal move events",
        # time-in-level-at-promotion needs dated promotion events; the snapshot only models current
        # level entry (a tenure-in-level proxy would be a different, weaker metric — kept honest):
        "promotion_velocity": "dated promotion events (promotion date + prior level-entry date)",
        "internal_fill_rate": "vacancy fill events (internal vs external)",
        "merit_increase": "merit-cycle compensation events",
        "comp_exception_rate": "comp-action + exception-approval events",
        "ceo_pay_ratio": "executive compensation",
        "total_cash_comp": "target bonus / commission data",
        "bonus_target_attainment": "bonus plan + payout data",
        "self_service_deflection": "self-service session logs with a resolution signal",
        "adverse_impact_ratio": "structured selection-decision events",
        "succession_coverage": "succession / critical-role table",
        "successor_readiness": "succession readiness table",
        "recordable_incident_rate": "EHS incident log",
        "lost_time_injury_rate": "EHS incident log",
        "absence_rate": "absence / scheduled-time table",
        "grievance_rate": "employee-relations case table",
        "disciplinary_action_rate": "employee-relations case table",
        "ethics_hotline_cases": "ethics hotline table",
        "training_hours_per_fte": "LMS training records",
        "training_completion_rate": "LMS assignment records",
        "critical_skill_coverage": "skills assessment table",
        # TA metrics live in the standalone ta-reporting example for now:
        "open_reqs": "ATS in the foundation (see examples/ta-reporting)",
        "time_to_fill": "ATS in the foundation (see examples/ta-reporting)",
        "time_to_hire": "ATS in the foundation (see examples/ta-reporting)",
        "requisition_aging": "ATS in the foundation (see examples/ta-reporting)",
        "requisition_stale": "ATS in the foundation (see examples/ta-reporting)",
        "thin_pipeline": "ATS in the foundation (see examples/ta-reporting)",
        "offer_acceptance_rate": "ATS in the foundation",
        "pass_through_rate": "ATS in the foundation",
        "quality_of_hire": "ATS + post-hire performance linkage",
        "cost_per_hire": "recruiting cost ledger",
        "source_channel_effectiveness": "ATS source attribution",
        "recruiter_capacity": "ATS recruiter assignment",
        # fully-loaded labor cost needs more than base salary:
        "labor_cost_per_fte": "variable pay + employer taxes/benefits (fully-loaded labor cost)",
    }

    def __init__(self, data_dir=DATA, as_of=AS_OF):
        global DATA
        DATA = Path(data_dir)
        self._data_dir = Path(data_dir)
        self.as_of = as_of
        self.as_of_dt = datetime.combine(as_of, time(18, 0))   # matches the generator's clock
        self.workers = _load("workers.csv")
        self.bands = {b["band_id"]: b for b in _load("comp_bands.csv")}
        self.benefits = _load("benefits_enrollment.csv")
        self.cases = _load("cases.csv")
        self.financials = _load("financials.csv")            # quarterly revenue (business-linkage)
        for f in self.financials:
            f["revenue_usd"] = int(f["revenue_usd"]) if f.get("revenue_usd") not in ("", None) else None
        self.financials.sort(key=lambda f: f["period_end"])
        for w in self.workers:
            for k in ("scheduled_hours", "standard_full_time_hours", "base_salary"):
                w[k] = int(w[k]) if w.get(k) not in ("", None) else None
        self.employees = [w for w in self.workers if w["worker_type"] == "employee"]
        # Fail closed on impossible source facts BEFORE any metric is computed on them.
        self._check_data_quality()
        # Bind to the registry: only registry ids are computable; everything else is rejected.
        self.registry = MetricRegistry.load()
        self._reg_by_id = {m["id"]: m for m in self.registry.all()}
        self._reg_ids = set(self._reg_by_id)

    def _check_data_quality(self):
        """Refuse impossible facts the engine would otherwise compute on silently. Today: a case
        cannot resolve before it opened (that would yield a negative TTR and flatter SLA/backlog)."""
        for c in self.cases:
            op, rs = c.get("opened_at"), c.get("resolved_at")
            if not op:
                raise EngineDataError(f"case {c.get('case_id')} has no opened_at")
            try:
                opened = datetime.fromisoformat(op)
                resolved = datetime.fromisoformat(rs) if rs else None
            except ValueError as exc:
                raise EngineDataError(f"case {c.get('case_id')} has an unparseable timestamp: {exc}")
            if resolved is not None and resolved < opened:
                raise EngineDataError(
                    f"case {c.get('case_id')} resolved_at ({rs}) precedes opened_at ({op}) — impossible interval")
        # Categorical integrity: a non-empty rating/potential must be a known value, so downstream
        # buckets (e.g. the 9-box) always reconcile rather than silently dropping an unknown code.
        for w in self.employees:
            if w.get("rating") and w["rating"] not in _VALID_RATINGS:
                raise EngineDataError(f"worker {w.get('emp_id')} has unknown rating '{w['rating']}'")
            if w.get("potential") and w["potential"] not in _VALID_POTENTIAL:
                raise EngineDataError(f"worker {w.get('emp_id')} has unknown potential '{w['potential']}'")

    # ---- population helpers ----
    def _active_at(self, d: date):
        """Employees active (incl. protected leave) at date d — by dates, for time series."""
        out = []
        for w in self.employees:
            h = _date(w["hire_date"])
            t = _date(w["term_date"])
            if h and h <= d and (t is None or t > d):
                out.append(w)
        return out

    def _active_asof(self):
        """Employees active at the engine's as_of — POINT-IN-TIME (by hire/term dates), so every
        snapshot metric (headcount/FTE/span/representation/...) reflects the as_of, not 'today's'
        status field. At the default as_of this equals the status-based population; at any other
        as_of it correctly differs (a worker terminated since then is excluded)."""
        return self._active_at(self.as_of)

    def _avg_headcount(self, start: date, end: date):
        counts = [len(self._active_at(me)) for me in _month_ends(start, end)]
        return statistics.mean(counts) if counts else 0

    # ---- point-in-time history (for sparklines / trend charts) ----
    def quarter_ends(self, n=8):
        """The n most recent COMPLETE calendar quarter-ends at or before as_of (oldest -> newest)."""
        qm = ((self.as_of.month - 1) // 3) * 3 + 1
        cur = date(self.as_of.year, qm, 1) - timedelta(days=1)
        out = []
        for _ in range(n):
            out.append(cur)
            pm, py = cur.month - 3, cur.year
            if pm <= 0:
                pm, py = pm + 12, py - 1
            nm_y, nm_m = (py + 1, 1) if pm == 12 else (py, pm + 1)
            cur = date(nm_y, nm_m, 1) - timedelta(days=1)
        return list(reversed(out))

    def series(self, metric_id, n=8):
        """Evaluate one metric at each of the n most recent quarter-ends — each point is the SAME
        engine re-run at that point in time (so the trend reuses the as_of point-in-time discipline).
        Returns [{period_end, value|None}]."""
        out = []
        for qe in self.quarter_ends(n):
            r = MetricEngine(self._data_dir, as_of=qe).compute(metric_id)
            out.append({"period_end": qe.isoformat(), "value": r.get("value") if r.get("status") == "ok" else None})
        return out

    def series_multi(self, metric_ids, n=8):
        """Like series() but evaluates several metrics per quarter-end, sharing each sub-engine (one
        construction per quarter instead of one per metric). Returns (quarter_labels, {id: [values]})."""
        qes = self.quarter_ends(n)
        out = {mid: [] for mid in metric_ids}
        for qe in qes:
            sub = MetricEngine(self._data_dir, as_of=qe)
            for mid in metric_ids:
                r = sub.compute(mid)
                out[mid].append(r.get("value") if r.get("status") == "ok" else None)
        return [qe.isoformat() for qe in qes], out

    # ---- dispatch ----
    def compute(self, metric_id, **opts):
        if metric_id not in self._reg_ids:
            # Registry-bound: an id that isn't in the canonical registry is an error, not a metric.
            return {"metric_id": metric_id, "status": "unknown_metric",
                    "error": "not defined in metrics.registry.json"}
        meta = self._meta(metric_id)
        fn = _FUNCS.get(metric_id)
        if fn is None:
            needs = self.DATA_PENDING.get(metric_id, "additional source data")
            return {"metric_id": metric_id, "status": "data_pending", "needs": needs, **meta}
        res = fn(self, **opts)
        res.update({"metric_id": metric_id, "status": "ok", **meta})
        return res

    def segment(self, metric_id, by, **opts):
        if metric_id not in self._reg_ids:
            return {"metric_id": metric_id, "status": "unknown_metric"}
        fn = _SEG.get(metric_id)
        if fn is None:
            return {"metric_id": metric_id, "status": "no_segment"}
        return fn(self, by, **opts)

    def _meta(self, metric_id):
        m = self._reg_by_id.get(metric_id, {})
        return {"name": m.get("name"), "metric_class": m.get("metric_class"),
                "registry_unit": m.get("unit")}


# =====================================================================================
#  Metric implementations. Each returns {"value": ..., "unit": ..., "extras": {...}}.
#  Keep them pure: read + aggregate only.
# =====================================================================================

def _window(eng):
    """A true trailing-12-month window: exactly 12 month-ends ending in as_of's month
    (e.g. Feb 2025 .. Jan 2026 for as_of 2026-01-31), so the annualization divisor (12)
    matches the number of monthly snapshots averaged."""
    idx = eng.as_of.year * 12 + (eng.as_of.month - 1) - 11
    sy, sm = divmod(idx, 12)
    start = date(sy, sm + 1, 1)
    return start, eng.as_of, 12


# ---- HEADCOUNT ----
def _headcount(eng):
    pop = eng._active_asof()
    return {"value": len(pop), "unit": "count",
            "extras": {"active": sum(1 for w in pop if w["status"] == "active"),
                       "on_leave": sum(1 for w in pop if w["status"] == "on_leave")}}


def _fte_of(pop):
    """Sum of capped FTE fractions for a population (scheduled / standard full-time hours)."""
    total = 0.0
    for w in pop:
        std = w["standard_full_time_hours"] or 0
        if std:
            total += min(1.0, (w["scheduled_hours"] or 0) / std)
    return total


def _fte(eng):
    return {"value": round(_fte_of(eng._active_asof()), 1), "unit": "fte"}


def _net_headcount_growth(eng):
    start, end, _ = _window(eng)
    begin = len(eng._active_at(start))
    ending = len(eng._active_at(end))
    hires = sum(1 for w in eng.employees if start < (_date(w["hire_date"]) or date.min) <= end)
    vol = sum(1 for w in eng.employees if w["term_type"] == "voluntary"
              and start < (_date(w["term_date"]) or date.min) <= end)
    invol = sum(1 for w in eng.employees if w["term_type"] == "involuntary"
                and start < (_date(w["term_date"]) or date.min) <= end)
    return {"value": ending - begin, "unit": "count",
            "extras": {"beginning": begin, "ending": ending, "hires": hires,
                       "voluntary_exits": vol, "involuntary_exits": invol, "total_exits": vol + invol,
                       "bridge_reconciles": ending - begin == hires - vol - invol}}


def _span_of_control(eng):
    active = eng._active_asof()
    ids = {w["emp_id"] for w in active}
    reports = {}
    for w in active:
        mid = w["manager_id"]
        if mid and mid in ids:
            reports.setdefault(mid, 0)
            reports[mid] += 1
    with_mgr = sum(1 for w in active if w["manager_id"] and w["manager_id"] in ids)
    spans = list(reports.values())
    bands = [("1-2", 1, 2), ("3", 3, 3), ("4-6", 4, 6), ("7-9", 7, 9), ("10-12", 10, 12), ("13+", 13, 1 << 30)]
    by_span = {lbl: sum(1 for s in spans if lo <= s <= hi) for lbl, lo, hi in bands}
    # Org-shape: managers vs ICs per level (the population-pyramid / "diamond" view).
    by_level = {}
    for lvl in sorted({w["level"] for w in active}):
        lp = [w for w in active if w["level"] == lvl]
        m = sum(1 for w in lp if w["is_people_manager"] == "yes")
        by_level[lvl] = {"managers": m, "ics": len(lp) - m, "total": len(lp)}
    return {"value": round(with_mgr / len(reports), 2) if reports else 0, "unit": "ratio",
            "extras": {"managers": len(reports), "mean": round(statistics.mean(spans), 2) if spans else 0,
                       "median": statistics.median(spans) if spans else 0,
                       "max": max(spans) if spans else 0, "by_span": by_span, "by_level": by_level}}


def _span_outlier_rate(eng, low=3, high=10):
    active = eng._active_asof()
    ids = {w["emp_id"] for w in active}
    reports = {}
    for w in active:
        mid = w["manager_id"]
        if mid and mid in ids:
            reports[mid] = reports.get(mid, 0) + 1
    if not reports:
        return {"value": 0, "unit": "percent", "extras": {}}
    sub = sum(1 for s in reports.values() if s < low)
    over = sum(1 for s in reports.values() if s > high)
    n = len(reports)
    return {"value": round(100 * (sub + over) / n), "unit": "percent",
            "extras": {"sub_scale_pct": round(100 * sub / n), "overloaded_pct": round(100 * over / n),
                       "managers": n}}


def _management_layers(eng):
    active = {w["emp_id"]: w for w in eng._active_asof()}
    depths = []
    for eid, w in active.items():
        d, cur, guard = 0, w, 0
        while cur["manager_id"] and cur["manager_id"] in active and guard < 50:
            d += 1
            cur = active[cur["manager_id"]]
            guard += 1
        depths.append(d)
    return {"value": statistics.median(depths) if depths else 0, "unit": "count",
            "extras": {"max_depth": max(depths) if depths else 0,
                       "distribution": {str(k): depths.count(k) for k in sorted(set(depths))}}}


def _contingent_workforce_ratio(eng):
    emp = len(eng._active_asof())
    # Contractors counted POINT-IN-TIME (by hire/term dates at as_of), same discipline as employees —
    # not the current 'status' field, which would make the ratio insensitive to as_of.
    con = sum(1 for w in eng.workers if w["worker_type"] == "contractor"
              and (_date(w["hire_date"]) or date.max) <= eng.as_of
              and (_date(w["term_date"]) is None or _date(w["term_date"]) > eng.as_of))
    tot = emp + con
    return {"value": round(100 * con / tot) if tot else 0, "unit": "percent",
            "extras": {"contractors": con, "employees": emp}}


# ---- ATTRITION ----
def _turnover(eng, predicate):
    start, end, months = _window(eng)
    exits = sum(1 for w in eng.employees if predicate(w)
                and start < (_date(w["term_date"]) or date.min) <= end)
    avg_hc = eng._avg_headcount(start, end)
    rate = (exits / avg_hc) * (12 / months) if avg_hc else 0
    return exits, avg_hc, round(100 * rate, 1)


def _voluntary_attrition(eng):
    ex, hc, r = _turnover(eng, lambda w: w["term_type"] == "voluntary")
    return {"value": r, "unit": "percent", "extras": {"voluntary_exits": ex, "avg_headcount": round(hc, 1)}}


def _regrettable_attrition(eng):
    ex, hc, r = _turnover(eng, lambda w: w["regrettable"] == "yes")
    vol = sum(1 for w in eng.employees if w["term_type"] == "voluntary")
    share = round(100 * ex / vol) if vol else 0
    return {"value": r, "unit": "percent",
            "extras": {"regrettable_exits": ex, "regrettable_share_of_voluntary_pct": share}}


def _total_turnover_rate(eng):
    ex, hc, r = _turnover(eng, lambda w: w["term_type"] in ("voluntary", "involuntary"))
    return {"value": r, "unit": "percent", "extras": {"all_exits": ex, "avg_headcount": round(hc, 1)}}


def _involuntary_turnover_rate(eng):
    ex, hc, r = _turnover(eng, lambda w: w["term_type"] == "involuntary")
    return {"value": r, "unit": "percent", "extras": {"involuntary_exits": ex}}


def _matured_hires(eng, days):
    """Hires whose cohort has had `days` to be observed (hired <= as_of - days)."""
    cutoff = eng.as_of - timedelta(days=days)
    return [w for w in eng.employees if (_date(w["hire_date"]) or date.max) <= cutoff]


def _new_hire_attrition(eng):
    cohort = _matured_hires(eng, 365)
    leavers = [w for w in cohort if w["term_date"]
               and (_date(w["term_date"]) - _date(w["hire_date"])).days < 365]
    vol = sum(1 for w in leavers if w["term_type"] == "voluntary")
    invol = sum(1 for w in leavers if w["term_type"] == "involuntary")
    # The companion split matters: high *involuntary* new-hire attrition reads as a hiring-quality
    # problem; high *voluntary* reads as an onboarding/expectations problem (different actions).
    return {"value": round(100 * len(leavers) / len(cohort)) if cohort else 0, "unit": "percent",
            "extras": {"cohort": len(cohort), "left_within_12mo": len(leavers),
                       "voluntary": vol, "involuntary": invol}}


def _early_attrition_90d(eng):
    cohort = _matured_hires(eng, 90)
    left = sum(1 for w in cohort if w["term_date"]
               and (_date(w["term_date"]) - _date(w["hire_date"])).days < 90)
    return {"value": round(100 * left / len(cohort)) if cohort else 0, "unit": "percent",
            "extras": {"cohort": len(cohort), "left_within_90d": left}}


def _twelve_month_retention(eng):
    cohort = _matured_hires(eng, 365)
    stayed = sum(1 for w in cohort if not (w["term_date"]
                 and (_date(w["term_date"]) - _date(w["hire_date"])).days < 365))
    return {"value": round(100 * stayed / len(cohort)) if cohort else 0, "unit": "percent",
            "extras": {"cohort": len(cohort), "active_at_12mo": stayed}}


# ---- TOTAL REWARDS ----
def _banded_active(eng):
    out = []
    for w in eng._active_asof():
        b = eng.bands.get(w["band_id"])
        if b and w["base_salary"]:
            out.append((w, b))
    return out


def _compa_ratio(eng):
    pairs = _banded_active(eng)
    sb = sum(w["base_salary"] for w, _ in pairs)
    sm = sum(int(b["range_mid"]) for _, b in pairs)
    ratios = [w["base_salary"] / int(b["range_mid"]) for w, b in pairs]
    return {"value": round(sb / sm, 3) if sm else 0, "unit": "ratio",
            "extras": {"aggregate": round(sb / sm, 3) if sm else 0,
                       "mean_of_ratios": round(statistics.mean(ratios), 3) if ratios else 0,
                       "n": len(pairs)}}


_PEN_LABELS = ["<min", "0-10", "-20", "-30", "-40", "-50", "-60", "-70", "-80", "-90", "-100", ">max"]


def _range_penetration(eng):
    pairs = _banded_active(eng)
    num = sum(w["base_salary"] - int(b["range_min"]) for w, b in pairs)
    den = sum(int(b["range_max"]) - int(b["range_min"]) for w, b in pairs)
    # Distribution of where people sit within their band (the pay-positioning histogram). The <min /
    # >max tails are the out-of-band employees; the shape in between is the in-band spread.
    hist = {lbl: 0 for lbl in _PEN_LABELS}
    for w, b in pairs:
        lo, hi = int(b["range_min"]), int(b["range_max"])
        if hi <= lo:
            continue
        p = 100 * (w["base_salary"] - lo) / (hi - lo)
        if p < 0:
            hist["<min"] += 1
        elif p > 100:
            hist[">max"] += 1
        else:
            hist[_PEN_LABELS[min(9, int(p // 10)) + 1]] += 1
    return {"value": round(100 * num / den) if den else 0, "unit": "percent",
            "extras": {"n": len(pairs), "by_penetration": hist}}


def _out_of_band_rate(eng):
    pairs = _banded_active(eng)
    below = sum(1 for w, b in pairs if w["base_salary"] < int(b["range_min"]))
    above = sum(1 for w, b in pairs if w["base_salary"] > int(b["range_max"]))
    n = len(pairs)
    in_band = n - below - above
    return {"value": round(100 * (below + above) / n) if n else 0, "unit": "percent",
            "extras": {"below_min_rate": round(100 * below / n) if n else 0,
                       "above_max_rate": round(100 * above / n) if n else 0,
                       "in_band_rate": round(100 * in_band / n) if n else 0,
                       "below": below, "above": above, "in_band": in_band, "n": n}}


def _raw_pay_gap(eng, group_field="gender_group"):
    pairs = _banded_active(eng)
    groups = {}
    for w, _b in pairs:
        groups.setdefault(w[group_field], []).append(w["base_salary"])
    meds = {g: statistics.median(v) for g, v in groups.items() if len(v) >= 5}
    if len(meds) < 2:
        return {"value": None, "unit": "percent", "extras": {"note": "insufficient group sizes"}}
    hi = max(meds.values())
    gaps = {g: round(100 * (m / hi - 1), 1) for g, m in meds.items()}
    return {"value": min(gaps.values()), "unit": "percent",
            "extras": {"by_group": gaps, "reference": "highest-median group"}}


def _adjusted_pay_gap(eng):
    """Regression-ADJUSTED pay gap: the residual pay difference for a group after controlling for
    level, job family, location, and tenure (OLS on log base salary), reported with a 95% CI.
    Contrast with the RAW gap so the reader sees what the controls explain. The honest version of a
    pay-equity number — a gap whose CI clears 0 is the one that needs action.
    """
    from foundation.compute.regression import ols, SingularMatrixError
    pop = [w for w in eng._active_asof()
           if w["base_salary"] and w["gender_group"] and w["ethnicity_group"]]
    if len(pop) < 30:
        return {"value": None, "unit": "percent", "extras": {"note": "population too small to adjust"}}

    # Control design (dummies drop a reference category; keep only categories with >=2 members to
    # avoid a rank-deficient column). Deterministic ordering via sorted categories.
    def _cats(field):
        seen = sorted({w[field] for w in pop})
        return [c for c in seen if sum(1 for w in pop if w[field] == c) >= 2]
    levels, fams, locs = _cats("level"), _cats("job_family"), _cats("location")
    lvl_d, fam_d, loc_d = levels[1:], fams[1:], locs[1:]   # drop reference (first) category

    def controls(w):
        ten = (eng.as_of - _date(w["hire_date"])).days / 365.25 if w["hire_date"] else 0.0
        return ([1.0 if w["level"] == c else 0.0 for c in lvl_d]
                + [1.0 if w["job_family"] == c else 0.0 for c in fam_d]
                + [1.0 if w["location"] == c else 0.0 for c in loc_d]
                + [ten])

    MIN_CELL = 10                                     # suppress a contrast unless both sides are big
    contrasts = {}                                    # enough for a stable estimate (privacy + stats)
    focals = {"gender": lambda w: 1.0 if w["gender_group"] == "B" else 0.0,
              "ethnicity": lambda w: 1.0 if w["ethnicity_group"] in ("grp2", "grp3") else 0.0}
    for key, focal in focals.items():
        focal_n = sum(1 for w in pop if focal(w) == 1.0)
        if not (MIN_CELL <= focal_n <= len(pop) - MIN_CELL):
            continue                                  # too small on one side -> suppressed, not noisy
        X = [[1.0, focal(w)] + controls(w) for w in pop]
        y = [math.log(w["base_salary"]) for w in pop]
        try:
            beta, se, info = ols(X, y)
        except SingularMatrixError as exc:
            raise EngineDataError(f"adjusted_pay_gap[{key}]: controls are collinear ({exc})")
        b, s = beta[1], se[1]
        pct = lambda lp: round(100 * (math.exp(lp) - 1), 1)   # log-points -> percent effect
        g1 = [math.log(w["base_salary"]) for w in pop if focal(w) == 1.0]
        g0 = [math.log(w["base_salary"]) for w in pop if focal(w) == 0.0]
        contrasts[key] = {"adj": pct(b), "ci_lo": pct(b - 1.96 * s), "ci_hi": pct(b + 1.96 * s),
                          "raw": pct(statistics.mean(g1) - statistics.mean(g0)),
                          "significant": (b - 1.96 * s) > 0 or (b + 1.96 * s) < 0,
                          "n": info["n"], "r2": round(info["r2"], 3)}
    headline = contrasts.get("gender", {}).get("adj")
    return {"value": headline, "unit": "percent",
            "extras": {"contrasts": contrasts, "controls": ["level", "job_family", "location", "tenure_years"],
                       "model": "OLS on log(base salary); 95% CI; reference = group A / grp1"}}


def _benefits_enrollment_rate(eng):
    by_ben = {}
    for r in eng.benefits:
        d = by_ben.setdefault(r["benefit"], {"elig": 0, "enr": 0})
        if r["eligible"] == "yes":
            d["elig"] += 1
            if r["enrolled"] == "yes":
                d["enr"] += 1
    rates = {b: round(100 * d["enr"] / d["elig"]) if d["elig"] else 0 for b, d in by_ben.items()}
    overall = round(100 * sum(d["enr"] for d in by_ben.values()) /
                    sum(d["elig"] for d in by_ben.values())) if by_ben else 0
    return {"value": overall, "unit": "percent", "extras": {"by_benefit": rates}}


def _benefits_cost_per_employee(eng):
    cost = sum(int(r["employer_cost_annual"]) for r in eng.benefits if r["enrolled"] == "yes")
    start, end, _ = _window(eng)
    avg = eng._avg_headcount(start, end)
    return {"value": round(cost / avg) if avg else 0, "unit": "currency",
            "extras": {"total_employer_cost": cost, "avg_headcount": round(avg, 1)}}


# ---- PEOPLE OPS (SLA / TTR / breach RECOMPUTED from raw timestamps; point-in-time: a case is
#      only "resolved" if it resolved AT OR BEFORE the as-of — future outcomes aren't knowable) ----
def _exists_at_asof(eng, c):
    """A case is part of the as-of snapshot only once it has been opened (opened_at <= as_of).
    A case opened after the snapshot is a future fact and is excluded from every People Ops metric."""
    return datetime.fromisoformat(c["opened_at"]) <= eng.as_of_dt


def _resolved(eng):
    """Cases resolved AT OR BEFORE the as-of. A resolution stamped after the snapshot is not yet
    knowable, so it never counts as resolved (the case is still open at the as-of)."""
    return [c for c in eng.cases if _exists_at_asof(eng, c)
            and c["resolved_at"] and datetime.fromisoformat(c["resolved_at"]) <= eng.as_of_dt]


def _is_open_at_asof(eng, c):
    """Open at the as-of snapshot: the case has been opened (opened_at <= as_of) AND is either
    never resolved or resolved only after the as-of timestamp. A not-yet-opened case is not open."""
    if not _exists_at_asof(eng, c):
        return False
    return (not c["resolved_at"]) or datetime.fromisoformat(c["resolved_at"]) > eng.as_of_dt


def _ttr_hours(c):
    """Resolution time in hours, computed from the raw open/resolve timestamps."""
    if not c["resolved_at"]:
        return None
    return (datetime.fromisoformat(c["resolved_at"]) - datetime.fromisoformat(c["opened_at"])).total_seconds() / 3600


def _open_age_hours(eng, c):
    return (eng.as_of_dt - datetime.fromisoformat(c["opened_at"])).total_seconds() / 3600


CASE_PERIOD_DAYS = 90  # People Ops reporting window


def _cases_in_period(eng):
    """Cases OPENED within the trailing reporting window AND existing at the as-of (a case opened
    after the snapshot is a future fact and is never counted)."""
    cutoff = eng.as_of_dt - timedelta(days=CASE_PERIOD_DAYS)
    return [c for c in eng.cases
            if cutoff < datetime.fromisoformat(c["opened_at"]) <= eng.as_of_dt]


def _case_volume(eng):
    period = _cases_in_period(eng)
    n = len(period)
    fte = _fte(eng)["value"]
    return {"value": n, "unit": "count",
            "extras": {"period_days": CASE_PERIOD_DAYS, "per_100_fte": round(100 * n / fte, 1) if fte else 0,
                       "by_category": _count_by(period, "category")}}


def _sla_attainment(eng):
    within = sum(1 for c in _resolved(eng) if _ttr_hours(c) <= float(c["sla_target_hours"]))
    resolved = len(_resolved(eng))
    open_breached = sum(1 for c in eng.cases
                        if _is_open_at_asof(eng, c) and _open_age_hours(eng, c) > float(c["sla_target_hours"]))
    den = resolved + open_breached
    return {"value": round(100 * within / den) if den else 0, "unit": "percent",
            "extras": {"within_sla": within, "resolved": resolved, "open_past_sla": open_breached}}


def _time_to_resolution(eng):
    ttr = sorted(_ttr_hours(c) for c in _resolved(eng))
    if not ttr:
        return {"value": 0, "unit": "hours", "extras": {}}
    p90 = ttr[min(len(ttr) - 1, int(round(0.9 * (len(ttr) - 1))))]
    return {"value": round(statistics.median(ttr), 1), "unit": "hours",
            "extras": {"p50": round(statistics.median(ttr), 1), "p90": round(p90, 1), "n": len(ttr)}}


def _reopen_rate(eng):
    resolved = _resolved(eng)
    re = sum(1 for c in resolved if c["reopened"] == "yes")
    return {"value": round(100 * re / len(resolved)) if resolved else 0, "unit": "percent",
            "extras": {"reopened": re, "resolved": len(resolved)}}


def _first_contact_resolution(eng):
    resolved = _resolved(eng)
    fcr = sum(1 for c in resolved if c["first_contact_resolution"] == "yes")
    return {"value": round(100 * fcr / len(resolved)) if resolved else 0, "unit": "percent",
            "extras": {"fcr": fcr, "resolved": len(resolved)}}


def _case_csat(eng):
    # CSAT is collected when a case is resolved, so it is scoped to cases resolved AT OR BEFORE the
    # as-of (consistent with reopen/FCR) — a survey on a future-resolved case isn't knowable yet.
    resolved = _resolved(eng)
    scored = [int(c["csat"]) for c in resolved if c["csat"]]
    sat = sum(1 for s in scored if s >= 4)
    # Response rate is the honesty companion: a 90% CSAT on a 5% response rate is not the same claim
    # as 90% on 70% (low response => selection bias). Always report it alongside the score.
    return {"value": round(100 * sat / len(scored)) if scored else 0, "unit": "percent",
            "extras": {"responses": len(scored), "satisfied_4_5": sat,
                       "response_rate_pct": round(100 * len(scored) / len(resolved)) if resolved else 0}}


_AGE_BANDS = [("<24h", 0, 24), ("1-3d", 24, 72), ("4-7d", 72, 168), ("8-14d", 168, 336), ("15d+", 336, 1e9)]


def _open_case_backlog(eng):
    openc = [c for c in eng.cases if _is_open_at_asof(eng, c)]
    breached = sum(1 for c in openc if _open_age_hours(eng, c) > float(c["sla_target_hours"]))
    by_age = {label: 0 for label, _lo, _hi in _AGE_BANDS}
    for c in openc:
        age = _open_age_hours(eng, c)
        for label, lo, hi in _AGE_BANDS:
            if lo <= age < hi:
                by_age[label] += 1
                break
    return {"value": len(openc), "unit": "count",
            "extras": {"breached_open": breached, "by_age": by_age,
                       "by_category": _count_by(openc, "category")}}


# ---- DIVERSITY ----
def _representation_by_level(eng, group_field="gender_group"):
    out = {}
    for lvl in sorted({w["level"] for w in eng._active_asof()}):
        pop = [w for w in eng._active_asof() if w["level"] == lvl and w[group_field]]
        n = len(pop)
        out[lvl] = {g: round(100 * sum(1 for w in pop if w[group_field] == g) / n) if n else 0
                    for g in sorted({w[group_field] for w in pop})}
    return {"value": out, "unit": "percent", "extras": {"group_field": group_field}}


def _leadership_diversity(eng, group_field="gender_group"):
    leaders = [w for w in eng._active_asof() if w["is_people_manager"] == "yes" and w[group_field]]
    n = len(leaders)
    share = {g: round(100 * sum(1 for w in leaders if w[group_field] == g) / n) if n else 0
             for g in sorted({w[group_field] for w in leaders})}
    return {"value": share, "unit": "percent", "extras": {"leaders": n, "group_field": group_field}}


# ---- PERFORMANCE ----
def _rating_distribution(eng):
    rated = [w for w in eng._active_asof() if w["rating"]]
    n = len(rated)
    dist = {r: round(100 * sum(1 for w in rated if w["rating"] == r) / n) if n else 0
            for r in ["below", "meets", "exceeds", "outstanding"]}
    unrated = sum(1 for w in eng._active_asof() if not w["rating"])
    return {"value": dist, "unit": "percent",
            "extras": {"rated": n, "unrated_share_pct": round(100 * unrated / (n + unrated)) if (n + unrated) else 0}}


def _promotion_rate(eng):
    # Registry definition: promoted / promotion-eligible population, cut by level (the fairness rate).
    # Eligible = active, not top-of-track, >= 12 months in level (see the data foundation).
    eligible = [w for w in eng._active_asof() if w["promotion_eligible"] == "yes"]
    promoted = sum(1 for w in eligible if w["promoted_this_period"] == "yes")
    by_level = {}
    for lvl in sorted({w["level"] for w in eligible}):
        lp = [w for w in eligible if w["level"] == lvl]
        by_level[lvl] = round(100 * sum(1 for w in lp if w["promoted_this_period"] == "yes") / len(lp)) if lp else 0
    # Enterprise rate (promoted / avg headcount) reported alongside for context.
    start, end, _ = _window(eng)
    avg = eng._avg_headcount(start, end)
    return {"value": round(100 * promoted / len(eligible)) if eligible else 0, "unit": "percent",
            "extras": {"promoted": promoted, "eligible_population": len(eligible), "by_level": by_level,
                       "enterprise_rate_pct": round(100 * promoted / avg) if avg else 0}}


# ---- BUSINESS LINKAGE (People <-> Finance) ----
def _ttm_revenue(eng, as_of=None):
    """Trailing-twelve-month revenue = sum of the 4 most recent quarters ending at/before as_of.
    Returns None when fewer than 4 quarters are available (honest, not a partial-year number)."""
    iso = (as_of or eng.as_of).isoformat()
    past = [f for f in eng.financials if f["period_end"] <= iso and f["revenue_usd"] is not None]
    return sum(f["revenue_usd"] for f in past[-4:]) if len(past) >= 4 else None


def _revenue_per_fte(eng):
    ttm, fte = _ttm_revenue(eng), _fte_of(eng._active_asof())
    quarters = [f["period_end"] for f in eng.financials if f["period_end"] <= eng.as_of.isoformat()][-4:]
    return {"value": round(ttm / fte) if (ttm and fte) else 0, "unit": "currency",
            "extras": {"ttm_revenue": ttm, "fte": round(fte, 1), "ttm_quarters": quarters}}


def _operating_leverage(eng):
    now_ttm, now_fte = _ttm_revenue(eng), _fte_of(eng._active_asof())
    ya = date(eng.as_of.year - 1, eng.as_of.month, eng.as_of.day)
    ya_ttm, ya_fte = _ttm_revenue(eng, ya), _fte_of(eng._active_at(ya))
    if not (now_ttm and now_fte and ya_ttm and ya_fte):
        return {"value": 0, "unit": "percent", "extras": {"note": "insufficient revenue history"}}
    rpf_now, rpf_ya = now_ttm / now_fte, ya_ttm / ya_fte
    hc_now, hc_ya = len(eng._active_asof()), len(eng._active_at(ya))
    return {"value": round(100 * (rpf_now - rpf_ya) / rpf_ya, 1), "unit": "percent",
            "extras": {"revenue_per_fte_now": round(rpf_now), "revenue_per_fte_year_ago": round(rpf_ya),
                       "revenue_growth_pct": round(100 * (now_ttm - ya_ttm) / ya_ttm, 1),
                       "headcount_growth_pct": round(100 * (hc_now - hc_ya) / hc_ya, 1) if hc_ya else 0}}


def _workforce_cost_ratio(eng):
    ttm = _ttm_revenue(eng)
    base = sum(w["base_salary"] or 0 for w in eng._active_asof())
    return {"value": round(100 * base / ttm, 1) if ttm else 0, "unit": "percent",
            "extras": {"base_salary_total": base, "ttm_revenue": ttm,
                       "basis": "base salary only (not fully-loaded; see labor_cost_per_fte)"}}


# ---- 9-BOX TALENT GRID (performance x potential) ----
_PERF_BAND = {"below": "Low", "meets": "Med", "exceeds": "High", "outstanding": "High"}
_VALID_RATINGS = frozenset(_PERF_BAND)                 # the known rating codes (load-time integrity)
_VALID_POTENTIAL = frozenset(("Low", "Med", "High"))


def _nine_box(eng):
    """Distribution of the rated active population across the performance x potential grid. The
    4-point rating scale is mapped to 3 performance bands (below->Low, meets->Med, exceeds/
    outstanding->High). A development/calibration view — never a pay or termination trigger."""
    pop = [w for w in eng._active_asof() if w["rating"] and w.get("potential")]
    grid = {pot: {perf: 0 for perf in ("Low", "Med", "High")} for pot in ("High", "Med", "Low")}
    for w in pop:
        perf = _PERF_BAND.get(w["rating"])
        if perf is None or w["potential"] not in grid:
            # Fail closed: an included row that can't be bucketed would break cell-sum == n.
            raise EngineDataError(
                f"nine_box: unrecognized rating/potential ('{w['rating']}'/'{w['potential']}') — cannot bucket")
        grid[w["potential"]][perf] += 1
    n = len(pop)   # every included row was bucketed above, so the 9 cells reconcile to n exactly
    pct = {pot: {perf: (round(100 * grid[pot][perf] / n) if n else 0) for perf in grid[pot]} for pot in grid}
    return {"value": grid, "unit": "count",
            "extras": {"n": n, "stars": grid["High"]["High"], "core": grid["Med"]["Med"],
                       "at_risk": grid["Low"]["Low"], "pct": pct}}


def _count_by(rows, field):
    out = {}
    for r in rows:
        out[r[field]] = out.get(r[field], 0) + 1
    return dict(sorted(out.items()))


_FUNCS = {
    "headcount": _headcount, "fte": _fte, "net_headcount_growth": _net_headcount_growth,
    "span_of_control": _span_of_control, "span_outlier_rate": _span_outlier_rate,
    "management_layers": _management_layers, "contingent_workforce_ratio": _contingent_workforce_ratio,
    "voluntary_attrition": _voluntary_attrition, "regrettable_attrition": _regrettable_attrition,
    "total_turnover_rate": _total_turnover_rate, "involuntary_turnover_rate": _involuntary_turnover_rate,
    "new_hire_attrition": _new_hire_attrition, "early_attrition_90d": _early_attrition_90d,
    "twelve_month_retention": _twelve_month_retention,
    "compa_ratio": _compa_ratio, "range_penetration": _range_penetration,
    "out_of_band_rate": _out_of_band_rate, "raw_pay_gap": _raw_pay_gap,
    "adjusted_pay_gap": _adjusted_pay_gap,
    "benefits_enrollment_rate": _benefits_enrollment_rate,
    "benefits_cost_per_employee": _benefits_cost_per_employee,
    "case_volume": _case_volume, "sla_attainment": _sla_attainment,
    "time_to_resolution": _time_to_resolution, "reopen_rate": _reopen_rate,
    "first_contact_resolution": _first_contact_resolution, "case_csat": _case_csat,
    "open_case_backlog": _open_case_backlog,
    "representation_by_level": _representation_by_level, "leadership_diversity": _leadership_diversity,
    "rating_distribution": _rating_distribution, "promotion_rate": _promotion_rate,
    "nine_box": _nine_box,
    "revenue_per_fte": _revenue_per_fte, "operating_leverage": _operating_leverage,
    "workforce_cost_ratio": _workforce_cost_ratio,
}

_SEG = {
    "compa_ratio": lambda eng, by, **o: _segment_compa(eng, by),
    "voluntary_attrition": lambda eng, by, **o: _segment_vol(eng, by),
}


def _segment_compa(eng, by):
    pairs = _banded_active(eng)
    groups = {}
    for w, b in pairs:
        groups.setdefault(w[by], []).append((w["base_salary"], int(b["range_mid"])))
    return {g: round(sum(x for x, _ in v) / sum(m for _, m in v), 3) for g, v in sorted(groups.items())}


def _segment_vol(eng, by):
    start, end, months = _window(eng)
    out = {}
    for g in sorted({w[by] for w in eng.employees if w[by]}):
        ex = sum(1 for w in eng.employees if w[by] == g and w["term_type"] == "voluntary"
                 and start < (_date(w["term_date"]) or date.min) <= end)
        hc = statistics.mean([sum(1 for w in eng._active_at(me) if w[by] == g)
                              for me in _month_ends(start, end)]) or 1
        out[g] = round(100 * (ex / hc), 1)
    return out


if __name__ == "__main__":
    eng = MetricEngine()
    for mid in _FUNCS:
        r = eng.compute(mid)
        print(f"{mid:28} {str(r.get('value')):>10}  {r.get('unit','')}")
