#!/usr/bin/env python3
"""Deterministic synthetic "Acme Corp" data foundation for Agentic PeopleOS.

This is what turns the metric registry from *definitions* into a *running function*: a realistic
but entirely synthetic dataset that exercises the registry's metrics across every domain. It is
seeded, so the same dataset is produced on every machine (the arm agents and their evals depend
on that determinism). No real people, companies, or PII — ids are obviously synthetic (E-0001),
and the public PII scan (tools/pii_scan.py) runs clean over the output.

    python foundation/data/generate.py        # writes foundation/data/acme/*.csv

Tables (one row per entity):
  workers.csv            HRIS — the backbone (org, status, comp, rating, demographics, dates)
  comp_bands.csv         salary ranges + market p50 by level/family/location
  benefits_enrollment.csv per-employee benefit eligibility + election
  cases.csv              People Ops case management (SLA, reopen, FCR, CSAT)

All dates are ISO YYYY-MM-DD. AS_OF anchors the synthetic "today" so cohorts/tenure are stable.
"""
import csv
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "acme"
AS_OF = date(2026, 1, 31)              # synthetic "today"
SEED = 30414                           # fixed: deterministic output
LEVELS = ["L3", "L4", "L5", "L6", "L7"]
FAMILIES = ["Engineering", "Product", "Sales", "GA", "Support", "Marketing"]
LOCATIONS = [("US", 2080, 1.00), ("UK", 1950, 0.85), ("India", 2080, 0.32),
             ("Germany", 1880, 0.92), ("Canada", 1950, 0.88)]
RATINGS = ["below", "meets", "exceeds", "outstanding"]
GENDERS = ["A", "B"]                   # synthetic groups (no real demographics)
ETHNICITIES = ["grp1", "grp2", "grp3"]
BENEFITS = ["medical", "dental", "vision", "401k", "life"]
CASE_CATEGORIES = ["payroll", "benefits", "leave", "policy", "access", "other"]


def _d(d: date) -> str:
    return d.isoformat()


def _band(level, family, location):
    """A deterministic salary band by level/family/location (USD, location-adjusted)."""
    base_mid = {"L3": 95000, "L4": 135000, "L5": 185000, "L6": 250000, "L7": 340000}[level]
    fam_mult = {"Engineering": 1.12, "Product": 1.08, "Sales": 1.05, "Marketing": 1.0,
                "Support": 0.9, "GA": 0.95}[family]
    loc_mult = dict((c, m) for c, _h, m in LOCATIONS)[location]
    mid = round(base_mid * fam_mult * loc_mult / 1000) * 1000
    return int(mid * 0.80), mid, int(mid * 1.20)


def generate():
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- comp bands: one per (level, family, location) ----
    bands = {}
    for lvl in LEVELS:
        for fam in FAMILIES:
            for loc, _h, _m in LOCATIONS:
                lo, mid, hi = _band(lvl, fam, loc)
                bid = f"B-{lvl}-{fam[:3].upper()}-{loc[:2].upper()}"
                bands[bid] = {"band_id": bid, "level": lvl, "job_family": fam, "location": loc,
                              "range_min": lo, "range_mid": mid, "range_max": hi,
                              "market_p50": round(mid * rng.uniform(1.0, 1.08))}

    # ---- workers: the HRIS backbone ----
    workers = []
    N = 240
    # Build an org: a few L7 roots, then managers down the levels.
    for i in range(1, N + 1):
        eid = f"E-{i:04d}"
        # level skew: more juniors than seniors
        lvl = rng.choices(LEVELS, weights=[34, 30, 22, 10, 4])[0]
        fam = rng.choice(FAMILIES)
        loc, ft_hours, _m = rng.choice(LOCATIONS)
        bid = f"B-{lvl}-{fam[:3].upper()}-{loc[:2].upper()}"
        band = bands[bid]
        # tenure: hire 0-9 years ago
        hire = AS_OF - timedelta(days=rng.randint(30, 365 * 9))
        span_days = (AS_OF - hire).days
        # ~16% have left — only workers with enough tenure to hold a valid in-window term date, so a
        # "terminated" worker ALWAYS has hire < term_date < AS_OF (status-active count == date-active
        # count at the as-of snapshot; the headcount KPI and the net-growth bridge agree).
        terminated = span_days >= 31 and rng.random() < 0.16
        on_leave = (not terminated) and rng.random() < 0.05
        term_date = term_type = regrettable = ""
        status = "active"
        if terminated:
            status = "terminated"
            td = AS_OF - timedelta(days=rng.randint(1, min(365, span_days - 1)))
            term_date = _d(td)
            term_type = rng.choices(["voluntary", "involuntary"], weights=[72, 28])[0]
            if term_type == "voluntary":
                regrettable = rng.choices(["yes", "no"], weights=[45, 55])[0]
        elif on_leave:
            status = "on_leave"
        # comp: compa-ratio centered ~0.98 with spread; a few out of band
        compa = rng.gauss(0.98, 0.11)
        compa = max(0.62, min(1.45, compa))
        base = int(round(band["range_mid"] * compa / 100) * 100)
        part_time = rng.random() < 0.08
        scheduled = ft_hours if not part_time else int(ft_hours * rng.choice([0.5, 0.6, 0.8]))
        rating = rng.choices(RATINGS, weights=[8, 60, 25, 7])[0] if status != "terminated" else \
            rng.choices(RATINGS, weights=[20, 55, 20, 5])[0]
        level_entry = AS_OF - timedelta(days=rng.randint(60, 365 * 5))
        if level_entry < hire:
            level_entry = hire
        # Promotion-eligible: active, not top-of-track (L7), and >= 12 months in level (sub-tenure
        # excluded). Only eligible employees can be promoted — keeps the eligible-denominator clean.
        eligible = (status == "active" and lvl != "L7" and (AS_OF - level_entry).days >= 365)
        promoted = "yes" if (eligible and rng.random() < 0.14) else "no"
        workers.append({
            "emp_id": eid, "worker_type": "employee", "status": status,
            "hire_date": _d(hire), "term_date": term_date, "term_type": term_type,
            "regrettable": regrettable, "level": lvl, "job_family": fam, "location": loc,
            "manager_id": "", "is_people_manager": "no",
            "scheduled_hours": scheduled, "standard_full_time_hours": ft_hours,
            "base_salary": base, "band_id": bid,
            "rating": rating, "gender_group": rng.choice(GENDERS),
            "ethnicity_group": rng.choices(ETHNICITIES, weights=[55, 30, 15])[0],
            "promotion_eligible": "yes" if eligible else "no",
            "promoted_this_period": promoted, "level_entry_date": _d(level_entry),
        })

    # ---- contractors (counted separately; for contingent-workforce ratio) ----
    for j in range(1, 41):
        eid = f"C-{j:04d}"
        lvl = rng.choice(LEVELS); fam = rng.choice(FAMILIES)
        loc, ft_hours, _m = rng.choice(LOCATIONS)
        hire = AS_OF - timedelta(days=rng.randint(30, 365 * 2))
        workers.append({
            "emp_id": eid, "worker_type": "contractor", "status": "active",
            "hire_date": _d(hire), "term_date": "", "term_type": "", "regrettable": "",
            "level": lvl, "job_family": fam, "location": loc, "manager_id": "",
            "is_people_manager": "no", "scheduled_hours": ft_hours,
            "standard_full_time_hours": ft_hours, "base_salary": "", "band_id": "",
            "rating": "", "gender_group": "", "ethnicity_group": "",
            "promotion_eligible": "no", "promoted_this_period": "no", "level_entry_date": "",
        })

    # ---- org: build a realistic pyramid (target span ~6, true manager-of-manager layers) ----
    # Each level reports ONE level up, but only enough seniors become managers to hit the target
    # span. That yields realistic spans (not 1-2) and real depth (L3 -> L4 -> L5 -> L6 -> L7).
    employees = [w for w in workers if w["worker_type"] == "employee"]
    by_level = {lvl: [w for w in employees if w["level"] == lvl] for lvl in LEVELS}
    order = ["L7", "L6", "L5", "L4", "L3"]
    TARGET_SPAN = 6
    for idx in range(1, len(order)):
        reports = by_level[order[idx]]
        pool = by_level[order[idx - 1]] or by_level["L7"]
        if not reports or not pool:
            continue
        managers_used = max(1, -(-len(reports) // TARGET_SPAN))   # ceil
        managers_used = min(managers_used, len(pool))
        managers = rng.sample(pool, managers_used)
        rng.shuffle(reports)
        for k, w in enumerate(reports):
            mgr = managers[k % managers_used]
            w["manager_id"] = mgr["emp_id"]
            mgr["is_people_manager"] = "yes"

    # ---- comp bands rows ----
    _write("comp_bands.csv", list(bands.values()),
           ["band_id", "level", "job_family", "location", "range_min", "range_mid",
            "range_max", "market_p50"])

    # ---- workers rows ----
    _write("workers.csv", workers,
           ["emp_id", "worker_type", "status", "hire_date", "term_date", "term_type",
            "regrettable", "level", "job_family", "location", "manager_id",
            "is_people_manager", "scheduled_hours", "standard_full_time_hours",
            "base_salary", "band_id", "rating", "gender_group", "ethnicity_group",
            "promotion_eligible", "promoted_this_period", "level_entry_date"])

    # ---- benefits enrollment: per active employee per benefit ----
    enroll = []
    for w in employees:
        if w["status"] == "terminated":
            continue
        for ben in BENEFITS:
            eligible = "yes"
            # 401k auto-enroll default; others active election
            if ben == "401k":
                enrolled = rng.choices(["yes", "no"], weights=[78, 22])[0]
                via_default = "yes" if enrolled == "yes" and rng.random() < 0.4 else "no"
            else:
                enrolled = rng.choices(["yes", "no"], weights=[88, 12])[0]
                via_default = "no"
            enroll.append({"emp_id": w["emp_id"], "benefit": ben, "eligible": eligible,
                           "enrolled": enrolled, "via_default": via_default,
                           "employer_cost_annual": rng.choice([0, 0, 1200, 4800, 6200, 300, 180])})
    _write("benefits_enrollment.csv", enroll,
           ["emp_id", "benefit", "eligible", "enrolled", "via_default", "employer_cost_annual"])

    # ---- People Ops cases over the trailing 90 days ----
    # We store RAW FACTS only (open/resolve timestamps + the SLA target). SLA attainment, time to
    # resolution, and breach status are RECOMPUTED by the engine from these — never precomputed
    # flags that could drift from the facts.
    cases = []
    as_of_dt = datetime.combine(AS_OF, time(18, 0))
    for c in range(1, 521):
        opened = as_of_dt - timedelta(days=rng.randint(0, 90), hours=rng.randint(0, 23))
        cat = rng.choice(CASE_CATEGORIES)
        sla_hours = {"payroll": 24, "benefits": 48, "leave": 48, "policy": 72,
                     "access": 8, "other": 72}[cat]
        resolved_at = ""
        reopened = first_contact = "no"
        csat = ""
        if rng.random() < 0.86:
            ttr = abs(rng.gauss(sla_hours * 0.6, sla_hours * 0.7)) + 1   # right-skewed
            res_dt = opened + timedelta(hours=ttr)
            # As-of snapshot: a resolution that would land AFTER the as-of hasn't happened yet,
            # so the case is still open. We never record a future fact (resolved_at <= as_of only).
            if res_dt <= as_of_dt:
                resolved_at = res_dt.isoformat(timespec="seconds")
                reopened = rng.choices(["yes", "no"], weights=[9, 91])[0]
                first_contact = rng.choices(["yes", "no"], weights=[58, 42])[0]
                csat = rng.choices(["1", "2", "3", "4", "5"], weights=[4, 6, 14, 38, 38])[0]
        cases.append({"case_id": f"CASE-{c:04d}", "opened_at": opened.isoformat(timespec="seconds"),
                      "resolved_at": resolved_at, "category": cat, "sla_target_hours": sla_hours,
                      "reopened": reopened, "first_contact_resolution": first_contact, "csat": csat,
                      "channel": rng.choices(["human", "self_service"], weights=[64, 36])[0]})
    _write("cases.csv", cases,
           ["case_id", "opened_at", "resolved_at", "category", "sla_target_hours",
            "reopened", "first_contact_resolution", "csat", "channel"])

    print(f"generated Acme dataset -> {OUT}")
    print(f"  workers.csv: {len(workers)} ({len(employees)} employees, {len(workers) - len(employees)} contractors)")
    print(f"  comp_bands.csv: {len(bands)} | benefits_enrollment.csv: {len(enroll)} | cases.csv: {len(cases)}")
    print(f"  as_of: {AS_OF.isoformat()} | seed: {SEED}")


def _write(name, rows, fields):
    with open(OUT / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


if __name__ == "__main__":
    generate()
