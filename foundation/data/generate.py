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
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

# Single source of truth for the real-ticker deny-list lives with the screener (compute layer); import it
# so the generator (don't-mint) and the loader (don't-accept) can never drift. Side-effect-free import.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from foundation.compute.peers import REAL_TICKERS as _PEER_REAL_TICKERS  # noqa: E402

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

    # ---- potential rating (for the 9-box talent grid) ----
    # Drawn from an INDEPENDENT rng stream so adding this column does NOT perturb the rest of the
    # deterministic dataset (existing tables stay byte-identical). Loosely correlated with performance.
    rng_pot = random.Random(SEED + 7)
    _POT_W = {"outstanding": [5, 30, 65], "exceeds": [10, 45, 45],
              "meets": [25, 55, 20], "below": [55, 35, 10]}
    for w in employees:
        w["potential"] = rng_pot.choices(["Low", "Med", "High"], weights=_POT_W[w["rating"]])[0]

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
            "promotion_eligible", "promoted_this_period", "level_entry_date", "potential"])

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

    # ---- financials: quarterly revenue (the business-linkage / People<->Finance layer) ----
    # Independent rng stream (never perturbs the workforce tables). 12 calendar quarters ending at the
    # last COMPLETE quarter before AS_OF; revenue rises ~3.5%/qtr with a small seeded wobble. Synthetic.
    rng_fin = random.Random(SEED + 3)
    qm = ((AS_OF.month - 1) // 3) * 3 + 1                 # first month of AS_OF's quarter
    qe = date(AS_OF.year, qm, 1) - timedelta(days=1)      # end of the previous (last complete) quarter
    quarters = []
    cur = qe
    for _ in range(12):
        quarters.append(cur)
        pm, py = cur.month - 3, cur.year
        if pm <= 0:
            pm, py = pm + 12, py - 1
        nm_y, nm_m = (py + 1, 1) if pm == 12 else (py, pm + 1)
        cur = date(nm_y, nm_m, 1) - timedelta(days=1)
    quarters.reverse()                                    # oldest -> newest
    fin = []
    for i, q in enumerate(quarters):
        rev = 9_000_000 * (1.035 ** i) * rng_fin.uniform(0.985, 1.015)
        fin.append({"period_end": _d(q), "revenue_usd": int(round(rev / 1000) * 1000)})
    _write("financials.csv", fin, ["period_end", "revenue_usd"])

    # ---- peer universe: synthetic public companies for executive-comp peer-group screening ----
    # Independent rng stream. The SUBJECT is Acme (the same synthetic company); the candidate peers are
    # obviously-fictional issuers (made-up names + tickers) spanning sectors and sizes, clustered around
    # Acme's size so a revenue/market-cap/sub-industry screen (with headcount as a soft fit factor) yields
    # a realistic peer set. No real company, ticker, or proxy is represented.
    rng_peer = random.Random(SEED + 11)
    ttm = sum(f["revenue_usd"] for f in fin[-4:])               # Acme trailing-12-month revenue
    active_emp = sum(1 for w in employees if w["status"] in ("active", "on_leave"))
    PREFIX = ["North", "Vantage", "Cobalt", "Meridian", "Apex", "Lumen", "Quanta", "Sable", "Ridge",
              "Cinder", "Vertex", "Helio", "Onyx", "Brightwater", "Cedar", "Aster", "Polaris", "Nimbus",
              "Beacon", "Forge", "Harbor", "Summit", "Tiderock", "Granite", "Cypress", "Falcon", "Ironwood",
              "Slate", "Aurora", "Driftwood", "Kestrel", "Mistral", "Basalt", "Verdant", "Halcyon", "Crestline"]
    # (sector, [(subindustry, weight)...], rev/employee_$, market-cap/revenue, asset-intensity, [name suffixes]).
    # IT dominates and Application Software dominates IT, mirroring Acme's market — so a tight, same-
    # sub-industry screen still funnels to a credible peer set out of a deliberately broad starting universe.
    SECTORS = [
        ("Information Technology",
         [("Application Software", 52), ("Systems Software", 28), ("IT Services", 20)], 290_000, 9.0, 1.4,
         ["Systems", "Software", "Cloud", "Logic", "Technologies", "Analytics", "Platforms", "Digital"]),
        ("Communication Services",
         [("Interactive Media", 1), ("Internet Services", 1)], 380_000, 7.0, 1.6,
         ["Networks", "Media", "Wireless", "Interactive", "Connect"]),
        ("Health Care",
         [("Health Care Technology", 1), ("Life Sciences Tools", 1)], 240_000, 4.5, 1.9,
         ["Bio", "Health", "Therapeutics", "Diagnostics", "Sciences"]),
        ("Industrials",
         [("Electronic Equipment", 1), ("Research & Consulting", 1)], 210_000, 2.4, 2.2,
         ["Industries", "Dynamics", "Controls", "Manufacturing", "Works"]),
        ("Consumer Discretionary",
         [("Internet Retail", 1), ("Specialty Retail", 1)], 260_000, 2.0, 1.8,
         ["Retail", "Brands", "Commerce", "Goods", "Group"]),
        ("Financials",
         [("Financial Exchanges", 1), ("Capital Markets", 1)], 520_000, 5.5, 6.0,
         ["Capital", "Financial", "Holdings", "Partners", "Markets"]),
    ]
    SECTOR_W = [48, 12, 13, 13, 7, 7]                           # IT-heavy, mirroring Acme's market
    # Tickers we refuse to mint: a real, recognizable ticker would undermine the "obviously synthetic"
    # guarantee. Single source of truth — the same deny-list the screener's loader rejects on (so the
    # generator and the loader can never drift). The dedup loop skips any base that lands on one of these.
    REAL_TICKERS = set(_PEER_REAL_TICKERS)
    companies = [{"ticker": "ACMQ", "company_name": "Acme Corp", "gics_sector": "Information Technology",
                  "gics_subindustry": "Application Software", "revenue_usd": ttm,
                  "market_cap_usd": int(round(ttm * 8.4 / 1_000_000) * 1_000_000),
                  "employees": active_emp, "total_assets_usd": int(round(ttm * 1.6 / 1_000_000) * 1_000_000),
                  "is_subject": "yes"}]
    used_names, used_tk, n, guard = set(), {"ACMQ"} | REAL_TICKERS, 0, 0
    while n < 220:
        guard += 1
        if guard > 50_000:                                      # bounded: never spin forever on an exhausted pool
            raise RuntimeError("peer-universe generation exceeded its retry budget; widen the name/ticker pool")
        sector, subs, rev_per_emp, mc_mult, asset_int, suffixes = rng_peer.choices(SECTORS, weights=SECTOR_W)[0]
        subindustry = rng_peer.choices([s for s, _ in subs], weights=[w for _, w in subs])[0]
        name = f"{rng_peer.choice(PREFIX)} {rng_peer.choice(suffixes)}"
        if name in used_names:
            continue
        # derive a 4-char ticker; rotate ONLY the final letter (bounded to 26 tries, A after Z) to dodge
        # collisions with prior peers and the real-ticker deny-list. If a base is exhausted, drop the name.
        base = (name.split()[0][:3] + name.split()[1][:1]).upper()
        tk = base
        for _ in range(26):
            if tk not in used_tk:
                break
            last = tk[-1]
            tk = base[:3] + (chr(ord(last) + 1) if last < "Z" else "A")
        if tk in used_tk:
            continue
        used_names.add(name)
        used_tk.add(tk)
        # revenue: log-uniform $12M..$1.6B, beta-skewed toward small-cap so there's a real cluster near Acme
        lo, hi = 12_000_000, 1_600_000_000
        rev = lo * (hi / lo) ** (rng_peer.betavariate(2.0, 3.2))
        rev = int(round(rev / 1_000_000) * 1_000_000)
        mc = int(round(rev * mc_mult * rng_peer.uniform(0.7, 1.45) / 1_000_000) * 1_000_000)
        emp = max(20, int(round(rev / (rev_per_emp * rng_peer.uniform(0.8, 1.25)) / 10) * 10))
        assets = int(round(rev * asset_int * rng_peer.uniform(0.8, 1.3) / 1_000_000) * 1_000_000)
        companies.append({"ticker": tk, "company_name": name, "gics_sector": sector,
                          "gics_subindustry": subindustry, "revenue_usd": rev,
                          "market_cap_usd": mc, "employees": emp, "total_assets_usd": assets,
                          "is_subject": "no"})
        n += 1
    _write("peer_universe.csv", companies,
           ["ticker", "company_name", "gics_sector", "gics_subindustry", "revenue_usd",
            "market_cap_usd", "employees", "total_assets_usd", "is_subject"])

    # ---- exec pay + TSR + self-peers: inputs for the illustrative ISS pay-for-performance screen ----
    # Independent rng stream (zero churn to the tables above). Per company: a self-selected peer list
    # (same-sector nearest-by-revenue, the way issuers actually pick peers, which yields natural
    # reciprocity for size-clustered names), a 5-year CEO total-pay trajectory scaled to market cap, a
    # 5-year indexed TSR path ($100 invested), and an EVA-style financial-performance score for the FPA.
    # All synthetic; the ISS engine derives 1/3/5-yr aggregates + percentile ranks from these.
    rng_iss = random.Random(SEED + 17)
    same_sector = {}
    for c in companies:
        same_sector.setdefault(c["gics_sector"], []).append(c)
    exec_rows = []
    for c in companies:
        is_subj = c.get("is_subject") == "yes"
        pool = sorted((o for o in same_sector[c["gics_sector"]] if o["ticker"] != c["ticker"]),
                      key=lambda o: (abs(o["revenue_usd"] - c["revenue_usd"]), o["ticker"]))
        if is_subj:
            # The subject is deliberately positioned for a borderline/Medium ISS story (a clean pass would
            # not exercise the screen, an absurd fail isn't realistic): CEO pay ~2.2x the peer median with
            # a steady ~30% ramp, a SOFT TSR (+7% over 5y), and below-median financials. Explicit +
            # deterministic for control; clearly illustrative synthetic positioning.
            self_peers = [o["ticker"] for o in pool[:12]]
            med = c["market_cap_usd"] * 0.012        # ≈ peer-median annual CEO pay anchor
            pays = [int(round(med * f / 1000) * 1000) for f in (1.68, 1.80, 1.94, 2.08, 2.20)]
            tsr = [104.0, 109.0, 113.0, 110.0, 107.0]
            fe = 16.0                                # strong financials ≈ high pay -> FPA neutral, no escalation
        else:
            self_peers = [o["ticker"] for o in pool[:rng_iss.randint(10, 14)]]
            base = max(1_000_000, c["market_cap_usd"] * rng_iss.uniform(0.008, 0.016))   # CEO pay ~ size
            p = base / (1.07 ** 4)                    # back out year-1 so ~7%/yr growth lands near base at y5
            pays = []
            for _ in range(5):
                p *= 1.0 + rng_iss.uniform(-0.05, 0.18)
                pays.append(int(round(p / 1000) * 1000))
            val, tsr = 100.0, []                      # indexed TSR path: $100 invested, annual total return
            for _ in range(5):
                val = max(8.0, val * (1.0 + rng_iss.gauss(0.10, 0.28)))
                tsr.append(round(val, 2))
            fe = round(rng_iss.uniform(-8.0, 18.0), 2)
        exec_rows.append({                           # `fe` (not `fin`) so it never shadows the financials list
            "ticker": c["ticker"], "self_peers": ";".join(self_peers),
            "pay_y1": pays[0], "pay_y2": pays[1], "pay_y3": pays[2], "pay_y4": pays[3], "pay_y5": pays[4],
            "tsrval_y1": tsr[0], "tsrval_y2": tsr[1], "tsrval_y3": tsr[2], "tsrval_y4": tsr[3],
            "tsrval_y5": tsr[4], "fin_eva": round(fe, 2)})
    _write("exec_pay_tsr.csv", exec_rows,
           ["ticker", "self_peers", "pay_y1", "pay_y2", "pay_y3", "pay_y4", "pay_y5",
            "tsrval_y1", "tsrval_y2", "tsrval_y3", "tsrval_y4", "tsrval_y5", "fin_eva"])

    print(f"generated Acme dataset -> {OUT}")
    print(f"  workers.csv: {len(workers)} ({len(employees)} employees, {len(workers) - len(employees)} contractors)")
    print(f"  comp_bands.csv: {len(bands)} | benefits_enrollment.csv: {len(enroll)} | cases.csv: {len(cases)}")
    print(f"  financials.csv: {len(fin)} quarters ({fin[0]['period_end']} -> {fin[-1]['period_end']})")
    print(f"  peer_universe.csv: {len(companies)} companies (1 subject + {len(companies)-1} synthetic peers)")
    print(f"  as_of: {AS_OF.isoformat()} | seed: {SEED}")


def _write(name, rows, fields):
    with open(OUT / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")   # LF (clean git diff --check)
        w.writeheader()
        for r in rows:
            w.writerow(r)


if __name__ == "__main__":
    generate()
