#!/usr/bin/env python3
"""Pay-equity / EU Pay Transparency compute over the synthetic Acme workforce.

Two questions a Total-Rewards leader has to answer for the board and, from 2026-27, for regulators under
the EU Pay Transparency Directive (2023/970): "what is our pay gap?" and "how much of it survives once we
account for legitimate, job-related factors?" This engine answers both from `workers.csv` — the single
source of truth — with no hand-maintained derived file to drift.

Two numbers, deliberately distinct:
- UNADJUSTED gap — the raw mean and median difference in pay between protected-class groups. This is the
  number the Directive requires an employer to publish; it reflects WORKFORCE COMPOSITION (who sits in which
  level/role/country) as much as pay-setting.
- ADJUSTED gap — the difference that REMAINS after a regression controls for legitimate factors (job level,
  job family, country, tenure, performance rating, people-management). This is the "like-for-like" residual;
  it is the closest observable proxy for unequal pay for equal work, and it is what an equal-pay audit
  investigates. The engine reports it with a confidence interval (via foundation/compute/regression.ols),
  never as a bare point estimate.

METHODOLOGY-FAITHFUL vs ILLUSTRATIVE (the honesty line, stated like iss_screen.py / equity_spend.py):
- STRUCTURES are methodology-faithful: separate mean AND median gaps (the Directive requires both); pay
  measured on a comparable HOURLY basis (FTE hourly = FTE-annual base / standard full-time hours, so
  part-time status does not distort it); a regression-ADJUSTED residual gap with standard errors; and the
  Directive's 5% JOINT-PAY-ASSESSMENT trigger evaluated per category of workers.
- ILLUSTRATIVE (labeled, never claimed as legal output): protected-class groups are PSEUDONYMISED in the
  synthetic data (gender_group A/B, ethnicity_group grp1-3) — the engine reports gaps between groups and
  never asserts which real class a label denotes. "Category of workers" is operationalised as JOB LEVEL, a
  stand-in for the Directive's "equal work or work of equal value" grouping, which in production comes from a
  gender-neutral job-evaluation scheme. Pay here is BASE only (no bonus/equity/benefits). The adjusted gap
  uses OBSERVABLE controls only; unobserved factors (prior pay, negotiation, scope) are not captured, so a
  surviving gap is a flag for a privileged, cohort-level equal-pay review — not a legal conclusion.

Standard library only. Deterministic. Fail-closed. Presentation layers render it; they never decide.
"""
from __future__ import annotations

import csv
import math
from datetime import date, datetime
from pathlib import Path

from foundation.compute.regression import ols, SingularMatrixError

_DATA = Path(__file__).resolve().parents[1] / "data" / "acme"

# The engine reads only the columns it needs, but pins the FULL header so an upstream schema change to
# workers.csv fails closed here instead of silently shifting a column.
_WORKER_COLS = ("emp_id", "worker_type", "status", "hire_date", "term_date", "term_type", "regrettable",
                "level", "job_family", "location", "manager_id", "is_people_manager", "scheduled_hours",
                "standard_full_time_hours", "base_salary", "band_id", "rating", "gender_group",
                "ethnicity_group", "promotion_eligible", "promoted_this_period", "level_entry_date",
                "potential")

AS_OF = date(2026, 1, 31)                 # the synthetic "today" (matches foundation/data/generate.py)
INCLUDED_STATUSES = ("active", "on_leave")   # employed population; terminated workers are out of scope
_LEVELS = ("L3", "L4", "L5", "L6", "L7")     # the ordered career ladder — also the EU "category of workers"
_RATINGS = ("below", "meets", "exceeds", "outstanding")
_EU_THRESHOLD_PCT = 5.0                    # EU Pay Transparency Directive (2023/970) joint-assessment trigger

# Which protected-class dimensions the engine screens. gender_group is the Directive's primary lens; the
# ethnicity lens is an additional, voluntary equity screen with the same machinery.
_DIMENSIONS = (
    {"key": "gender_group", "label": "Gender group", "eu_scope": True,
     "note": "Pseudonymised gender categories (A / B) — the EU Pay Transparency Directive's primary lens."},
    {"key": "ethnicity_group", "label": "Ethnicity group", "eu_scope": False,
     "note": "Pseudonymised ethnicity categories (grp1-3) — a voluntary equity screen, same methodology."},
)

_ADJ_DP = 6                                # rounding for a deterministic, byte-stable report


class PayEquityDataError(ValueError):
    """A workforce-data defect the engine refuses to compute past (fail closed, never a silent estimate)."""


# ---------------------------------------------------------------- loading + validation
def _rows(path, cols):
    if not path.exists():
        raise PayEquityDataError(f"missing data file: {path.name}")
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames is None or list(rd.fieldnames) != list(cols):
            raise PayEquityDataError(f"{path.name}: header {rd.fieldnames} != expected {list(cols)}")
        out = [dict(r) for r in rd]
    if not out:
        raise PayEquityDataError(f"{path.name}: no rows")
    return out


def _num(v, ctx, positive=False):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise PayEquityDataError(f"{ctx}: not a number ({v!r})")
    if not math.isfinite(f):
        raise PayEquityDataError(f"{ctx}: not finite ({v!r})")
    if positive and f <= 0:
        raise PayEquityDataError(f"{ctx}: must be > 0 ({v!r})")
    return f


def _pdate(v, ctx):
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise PayEquityDataError(f"{ctx}: bad date {v!r}")


def _load_population(data_dir):
    """Return the in-scope employee records with a derived comparable FTE-hourly pay, plus an exclusion
    ledger (so the report can be honest about who is NOT in the analysis and why)."""
    raw = _rows(data_dir / "workers.csv", _WORKER_COLS)
    # The excluded ledger records only legitimate SCOPE decisions (who the analysis is not about). A row that
    # IS in scope — an employed employee with a recorded protected class — but carries malformed pay or a
    # malformed control is a DATA DEFECT, not a scope decision, and fails closed (it must never silently
    # shrink the analyzed population).
    kept, excluded = [], {"not_employee": 0, "not_employed_status": 0, "missing_all_protected_class": 0}
    for r in raw:
        if r["worker_type"] != "employee":
            excluded["not_employee"] += 1
            continue
        if r["status"] not in INCLUDED_STATUSES:
            excluded["not_employed_status"] += 1
            continue
        # Validate pay + controls FIRST, for EVERY in-scope employee — a malformed value here is a data defect
        # that must fail closed whether or not the row also happens to be missing a protected-class label
        # (never let a bad salary hide behind a "missing class" exclusion).
        ctx = f"workers.csv {r['emp_id']}"
        base = _num(r["base_salary"], f"{ctx} base_salary", positive=True)
        std_ft = _num(r["standard_full_time_hours"], f"{ctx} standard_full_time_hours", positive=True)
        if r["level"] not in _LEVELS:
            raise PayEquityDataError(f"{ctx}: in-scope employee has an unknown level {r['level']!r}")
        if r["rating"] not in _RATINGS:
            raise PayEquityDataError(f"{ctx}: in-scope employee has an unknown rating {r['rating']!r}")
        if r["is_people_manager"] not in ("yes", "no"):
            raise PayEquityDataError(f"{ctx}: in-scope employee has a malformed is_people_manager "
                                     f"{r['is_people_manager']!r}")
        # job_family + location are regression controls too; a blank one on an in-scope employee would
        # silently become its own dummy category and distort the adjusted gap — fail closed.
        if not r["job_family"].strip():
            raise PayEquityDataError(f"{ctx}: in-scope employee has a blank job_family")
        if not r["location"].strip():
            raise PayEquityDataError(f"{ctx}: in-scope employee has a blank location")
        hire = _pdate(r["hire_date"], f"{ctx} hire_date")
        tenure_years = (AS_OF - hire).days / 365.25
        if tenure_years < 0:
            raise PayEquityDataError(f"{ctx}: hire_date {hire} is after the as-of date {AS_OF}")
        # Protected class is PER-LENS: an employee with a gender feeds the gender (+ EU) lens; one with an
        # ethnicity feeds the ethnicity lens. Only an employee missing BOTH has no lens and is out of scope.
        gender = r["gender_group"].strip() or None
        ethnicity = r["ethnicity_group"].strip() or None
        if gender is None and ethnicity is None:
            excluded["missing_all_protected_class"] += 1
            continue
        kept.append({
            "emp_id": r["emp_id"], "gender_group": gender, "ethnicity_group": ethnicity,
            "level": r["level"], "job_family": r["job_family"], "location": r["location"],
            "rating": r["rating"], "is_people_manager": 1.0 if r["is_people_manager"] == "yes" else 0.0,
            "tenure_years": tenure_years,
            # FTE hourly: base_salary is already an FTE-annual figure in this data (a part-timer earns the same
            # annual base as a full-timer at the same level), so the comparable hourly rate divides by the
            # role's STANDARD full-time hours, not the individual's scheduled hours. This is the Directive's
            # hourly basis and it is not distorted by part-time status.
            "hourly": base / std_ft,
        })
    if not kept:
        raise PayEquityDataError("no in-scope employees after filtering — cannot compute a pay gap")
    return kept, excluded


# ---------------------------------------------------------------- descriptive (unadjusted) gaps
def _median(xs):
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _groups_in(pop, key):
    """Distinct group labels for a dimension, ordered by DESCENDING mean hourly so the reference group (index
    0) is the highest-paid — every other group's gap is then reported as a shortfall against it."""
    labels = sorted({r[key] for r in pop})
    return sorted(labels, key=lambda g: -sum(r["hourly"] for r in pop if r[key] == g) / max(1, sum(1 for r in pop if r[key] == g)))


def _unadjusted(pop, key):
    order = _groups_in(pop, key)
    ref = order[0]
    ref_mean = sum(r["hourly"] for r in pop if r[key] == ref) / sum(1 for r in pop if r[key] == ref)
    ref_median = _median([r["hourly"] for r in pop if r[key] == ref])
    groups = []
    for g in order:
        h = [r["hourly"] for r in pop if r[key] == g]
        gmean, gmed = sum(h) / len(h), _median(h)
        groups.append({
            "group": g, "n": len(h),
            "mean_hourly": round(gmean, _ADJ_DP), "median_hourly": round(gmed, _ADJ_DP),
            # gap vs reference, positive == this group earns LESS than the reference group
            "mean_gap_pct": round((1.0 - gmean / ref_mean) * 100.0, _ADJ_DP),
            "median_gap_pct": round((1.0 - gmed / ref_median) * 100.0, _ADJ_DP),
            "is_reference": g == ref,
        })
    return {"reference_group": ref, "groups": groups}


# ---------------------------------------------------------------- adjusted (regression) gap
def _design(pop, key, ref_group):
    """Build (X, y, group_cols) for OLS of ln(hourly) on a protected-class indicator set + legitimate
    controls. The reference group and one reference level of every categorical control are OMITTED (absorbed
    into the intercept) so the design is full rank; group_cols maps each modelled group to its column index."""
    groups = [g for g in _groups_in(pop, key) if g != ref_group]           # every non-reference group
    families = sorted({r["job_family"] for r in pop})[1:]                  # drop first (reference) category
    locations = sorted({r["location"] for r in pop})[1:]
    levels = [lv for lv in _LEVELS if lv in {r["level"] for r in pop}][1:]
    ratings = [rt for rt in _RATINGS if rt in {r["rating"] for r in pop}][1:]

    cols = ["intercept"] + [f"grp::{g}" for g in groups] + [f"lvl::{lv}" for lv in levels] \
        + [f"fam::{f}" for f in families] + [f"loc::{lc}" for lc in locations] \
        + [f"rat::{rt}" for rt in ratings] + ["mgr", "tenure_years"]
    idx = {c: i for i, c in enumerate(cols)}

    X, y = [], []
    for r in pop:
        row = [0.0] * len(cols)
        row[idx["intercept"]] = 1.0
        if r[key] in groups:
            row[idx[f"grp::{r[key]}"]] = 1.0
        if r["level"] in levels:
            row[idx[f"lvl::{r['level']}"]] = 1.0
        if r["job_family"] in families:
            row[idx[f"fam::{r['job_family']}"]] = 1.0
        if r["location"] in locations:
            row[idx[f"loc::{r['location']}"]] = 1.0
        if r["rating"] in ratings:
            row[idx[f"rat::{r['rating']}"]] = 1.0
        row[idx["mgr"]] = r["is_people_manager"]
        row[idx["tenure_years"]] = r["tenure_years"]
        X.append(row)
        y.append(math.log(r["hourly"]))
    group_cols = {g: idx[f"grp::{g}"] for g in groups}
    return X, y, group_cols, cols


def _adjusted(pop, key, ref_group, z=1.96):
    X, y, group_cols, cols = _design(pop, key, ref_group)
    try:
        beta, se, info = ols(X, y)
    except SingularMatrixError as exc:
        raise PayEquityDataError(f"adjusted {key} gap: singular design ({exc}) — a control has no variation")
    groups = []
    for g, ci in group_cols.items():
        # coefficient is the log-pay difference of group g vs the reference, holding controls fixed.
        # adjusted gap %, positive == group g earns LESS than reference for like-for-like work.
        coef, s = beta[ci], se[ci]
        gap_pct = (1.0 - math.exp(coef)) * 100.0
        lo_pct = (1.0 - math.exp(coef + z * s)) * 100.0     # upper log bound -> lower gap bound
        hi_pct = (1.0 - math.exp(coef - z * s)) * 100.0
        groups.append({
            "group": g, "coef_logpay": round(coef, _ADJ_DP), "se": round(s, _ADJ_DP),
            "adjusted_gap_pct": round(gap_pct, _ADJ_DP),
            "ci_lo_pct": round(min(lo_pct, hi_pct), _ADJ_DP), "ci_hi_pct": round(max(lo_pct, hi_pct), _ADJ_DP),
            # a gap is "material" only if its whole CI stays on one side of zero (gap != 0 is defensible)
            "significant": (coef + z * s < 0.0) or (coef - z * s > 0.0),
        })
    return {"reference_group": ref_group, "groups": groups, "z": z,
            "controls": [c for c in cols if c not in ("intercept",) and not c.startswith("grp::")],
            "n": info["n"], "n_params": info["p"], "r2": round(info["r2"], _ADJ_DP)}


# ---------------------------------------------------------------- EU Pay Transparency 5% trigger
def _eu_assessment(pop, key):
    """The Directive's joint-pay-assessment mechanism, per CATEGORY of workers (here: job level). Within each
    category we measure the pay gap BETWEEN the groups directly — the disadvantaged (lowest-mean) group vs the
    advantaged (highest-mean) group — as a magnitude, and report it on BOTH bases the Directive requires:
    the MEAN gap (Article 10's "average pay level", which is the formal >=5% joint-assessment trigger) and the
    MEDIAN gap (also mandated for reporting under Article 9). A category over 5% on the mean, unjustified by
    objective gender-neutral factors and not remedied within 6 months, obliges a joint pay assessment with
    worker representatives; a category clean on the mean but over 5% on the median is surfaced as a watch, not
    a trigger. This is the raw within-category gap, BEFORE objective-factor justification."""
    groups = _groups_in(pop, key)
    categories, n_flagged, n_median_watch = [], 0, 0
    for lv in _LEVELS:
        cell = [r for r in pop if r["level"] == lv]
        stats = []
        for g in groups:
            gh = [r["hourly"] for r in cell if r[key] == g]
            if gh:
                stats.append({"group": g, "n": len(gh), "mean_hourly": sum(gh) / len(gh), "median_hourly": _median(gh)})
        if len(stats) < 2:            # 0 or 1 group present -> no in-category gap to assess; surfaced, never dropped
            categories.append({"category": lv, "n": len(cell), "assessable": False,
                               "groups": [{"group": s["group"], "n": s["n"]} for s in stats]})
            continue
        hi_mean = max(stats, key=lambda s: s["mean_hourly"])
        lo_mean = min(stats, key=lambda s: s["mean_hourly"])
        hi_med = max(stats, key=lambda s: s["median_hourly"])
        lo_med = min(stats, key=lambda s: s["median_hourly"])
        mean_gap = (1.0 - lo_mean["mean_hourly"] / hi_mean["mean_hourly"]) * 100.0
        median_gap = (1.0 - lo_med["median_hourly"] / hi_med["median_hourly"]) * 100.0
        # Article 10(1) is "at least 5%" — an exactly-5.00% gap TRIGGERS, so the comparison is >=, not >.
        exceeds = mean_gap >= _EU_THRESHOLD_PCT
        median_watch = (not exceeds) and median_gap >= _EU_THRESHOLD_PCT
        if exceeds:
            n_flagged += 1
        if median_watch:
            n_median_watch += 1
        categories.append({
            "category": lv, "n": len(cell), "assessable": True,
            "advantaged_group": hi_mean["group"], "disadvantaged_group": lo_mean["group"],
            "mean_gap_pct": round(mean_gap, _ADJ_DP), "median_gap_pct": round(median_gap, _ADJ_DP),
            "exceeds_threshold": exceeds, "median_watch": median_watch,
            "groups": [{"group": s["group"], "n": s["n"], "mean_hourly": round(s["mean_hourly"], _ADJ_DP),
                        "median_hourly": round(s["median_hourly"], _ADJ_DP)} for s in stats],
        })
    return {"category_dimension": "job_level", "threshold_pct": _EU_THRESHOLD_PCT,
            "categories": categories, "n_categories": len(categories),
            "n_flagged": n_flagged, "n_median_watch": n_median_watch,
            # a SCREEN flag, not a legal determination: Article 10 also depends on objective-factor
            # justification and a six-month remediation window, which this data does not model.
            "potential_joint_assessment": n_flagged > 0}


# ---------------------------------------------------------------- public entrypoint
def compute(data_dir=None):
    """Return the fully-validated pay-equity view. Reads workers.csv from `data_dir` (default: the committed
    foundation/data/acme), no other I/O, deterministic. Raises PayEquityDataError (fail closed) on any data
    defect."""
    pop, excluded = _load_population(Path(data_dir) if data_dir is not None else _DATA)
    n = len(pop)

    dimensions = []
    for d in _DIMENSIONS:
        key = d["key"]
        # PER-LENS population: only employees who carry THIS protected-class label. An employee missing (say)
        # ethnicity still contributes to the gender lens, and vice-versa — a missing label narrows one lens,
        # never the whole analysis. A lens with fewer than two groups is degenerate-but-valid (it just shows no
        # gap); an EMPTY secondary lens is skipped, but an empty PRIMARY (gender) lens is a fail-closed error.
        pop_d = [r for r in pop if r[key] is not None]
        if not pop_d:
            if key == "gender_group":
                raise PayEquityDataError("no employee carries a gender-group label — cannot compute the "
                                         "primary lens or the EU screen")
            continue
        unadj = _unadjusted(pop_d, key)
        adj = _adjusted(pop_d, key, unadj["reference_group"])
        entry = {"key": key, "label": d["label"], "note": d["note"], "eu_scope": d["eu_scope"],
                 "n_in_lens": len(pop_d), "unadjusted": unadj, "adjusted": adj}
        if d["eu_scope"]:
            entry["eu_pay_transparency"] = _eu_assessment(pop_d, key)
        dimensions.append(entry)

    # a headline for the primary (gender) lens: the raw vs the like-for-like gap, side by side
    primary = next(dd for dd in dimensions if dd["key"] == "gender_group")
    lagging = [g for g in primary["unadjusted"]["groups"] if not g["is_reference"]]
    prim_adj = primary["adjusted"]["groups"][0] if primary["adjusted"]["groups"] else None
    headline = {
        "dimension": "gender_group",
        "reference_group": primary["unadjusted"]["reference_group"],
        "focus_group": lagging[0]["group"] if lagging else None,
        "unadjusted_median_gap_pct": lagging[0]["median_gap_pct"] if lagging else None,
        "unadjusted_mean_gap_pct": lagging[0]["mean_gap_pct"] if lagging else None,
        "adjusted_gap_pct": prim_adj["adjusted_gap_pct"] if prim_adj else None,
        "adjusted_significant": prim_adj["significant"] if prim_adj else None,
        "eu_potential_joint_assessment": primary["eu_pay_transparency"]["potential_joint_assessment"],
        "eu_flagged_categories": primary["eu_pay_transparency"]["n_flagged"],
    }

    return {
        "company": "Acme Corp (ACMQ)", "as_of": AS_OF.isoformat(),
        "population": {"n_analyzed": n, "excluded": excluded,
                       "note": "Employees in active/on-leave status carrying at least one protected-class "
                               "label, with positive FTE base pay and the controls the adjusted model needs. "
                               "Each lens uses only the employees who carry ITS label (see n_in_lens per "
                               "dimension). Contractors and terminated workers are out of scope; a malformed "
                               "pay/control value on an in-scope employee fails closed.",
                       "reference_denominator": "gaps are vs the highest-paid PSEUDONYMISED group (A/B, "
                               "grp1-3); the tool never maps a label to a real statutory class"},
        "pay_measure": "FTE hourly = FTE-annual base salary / standard full-time hours (base only; "
                       "no bonus/equity/benefits)",
        "headline": headline,
        "dimensions": dimensions,
        "disclaimer": "Illustrative reconstruction of the EU Pay Transparency Directive's reporting + "
                      "joint-assessment mechanics on synthetic, pseudonymised data. The adjusted gap uses "
                      "observable controls only; a surviving gap is a flag for a privileged equal-pay review, "
                      "not a legal finding. Not legal advice.",
    }


if __name__ == "__main__":
    import json
    print(json.dumps(compute(), indent=2))
