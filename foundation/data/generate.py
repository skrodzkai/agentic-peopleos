#!/usr/bin/env python3
"""Deterministic synthetic "Acme Corp" data foundation for Agentic PeopleOS.

This is what turns the metric registry from *definitions* into a *running function*: a realistic
but entirely synthetic dataset that exercises the registry's metrics across every domain. It is
seeded, so the same dataset is produced on every machine (the arm agents and their evals depend
on that determinism). No real people, companies, or PII — ids are obviously synthetic (E-0001),
and the public PII scan (tools/pii_scan.py) runs clean over the output.

    python3 foundation/data/generate.py        # writes foundation/data/acme/*.csv

Tables (one row per entity):
  workers.csv            HRIS — the backbone (org, status, comp, rating, demographics, dates)
  comp_bands.csv         salary ranges + market p50 by level/family/location
  benefits_enrollment.csv per-employee benefit eligibility + election
  cases.csv              People Ops case management (SLA, reopen, FCR, CSAT)

All dates are ISO YYYY-MM-DD. AS_OF anchors the synthetic "today" so cohorts/tenure are stable.
"""
import csv
import math
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

# Single source of truth for the real-ticker deny-list lives with the screener (compute layer); import it
# so the generator (don't-mint) and the loader (don't-accept) can never drift. Side-effect-free import.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from foundation.compute.peers import REAL_TICKERS as _PEER_REAL_TICKERS  # noqa: E402
from foundation.compute.peers import REAL_COMPANY_NAMES as _PEER_REAL_NAMES  # noqa: E402

OUT = Path(__file__).resolve().parent / "acme"
AS_OF = date(2026, 1, 31)              # synthetic "today"
SEED = 30414                           # fixed: deterministic output
LEVELS = ["L3", "L4", "L5", "L6", "L7"]
FAMILIES = ["Engineering", "Product", "Sales", "GA", "Support", "Marketing"]
LOCATIONS = [("US", 2080, 1.00), ("Canada", 1950, 0.88), ("UK", 1950, 0.90),
             ("Ireland", 1950, 0.92), ("Germany", 1880, 0.95), ("France", 1820, 0.93),
             ("Netherlands", 1880, 0.94), ("Poland", 2000, 0.55), ("India", 2080, 0.34),
             ("Australia", 1976, 0.97), ("Singapore", 2080, 0.85), ("Brazil", 2080, 0.48)]
RATINGS = ["below", "meets", "exceeds", "outstanding"]
GENDERS = ["A", "B"]                   # synthetic groups (no real demographics)
ETHNICITIES = ["grp1", "grp2", "grp3"]
BENEFITS = ["medical", "dental", "vision", "401k", "life"]
CASE_CATEGORIES = ["payroll", "benefits", "leave", "policy", "access", "other"]


def _d(d: date) -> str:
    return d.isoformat()


def _sig(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))


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
    N = 2400
    # Build an org: a few L7 roots, then managers down the levels.
    for i in range(1, N + 1):
        eid = f"E-{i:04d}"
        # level skew: more juniors than seniors
        lvl = rng.choices(LEVELS, weights=[34, 30, 22, 10, 4])[0]
        fam = rng.choice(FAMILIES)
        # US-HQ global SaaS: US dominant, large India/Poland delivery, the rest spread across EMEA/APAC/Americas
        loc, ft_hours, _m = rng.choices(LOCATIONS, weights=[34, 6, 8, 4, 6, 5, 4, 8, 12, 3, 4, 6])[0]
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
    for j in range(1, 261):
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
    for c in range(1, 2601):                                # scaled to the ~2,400-employee company
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
        rev = 140_000_000 * (1.045 ** i) * rng_fin.uniform(0.985, 1.015)
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
    PREFIX = ["North", "Vantage", "Cobalt", "Meridian", "Apex", "Fairwind", "Quanta", "Sable", "Ridge",
              "Cinder", "Vertex", "Helio", "Onyx", "Brightwater", "Cedar", "Aster", "Polaris", "Nimbus",
              "Beacon", "Forge", "Harbor", "Summit", "Tiderock", "Granite", "Cypress", "Falcon", "Ironwood",
              "Slate", "Aurora", "Driftwood", "Kestrel", "Thornwood", "Basalt", "Verdant", "Halcyon", "Crestline"]
    # (sector, [(subindustry, weight)...], rev/employee_$, market-cap/revenue, asset-intensity, [name suffixes]).
    # IT dominates and Application Software dominates IT, mirroring Acme's market — so a tight, same-
    # sub-industry screen still funnels to a credible peer set out of a deliberately broad starting universe.
    SECTORS = [
        ("Information Technology",
         [("Application Software", 52), ("Systems Software", 28), ("IT Services", 20)], 290_000, 9.0, 1.4,
         ["Compute", "Stack", "Cloud", "Logic", "Labs", "Analytics", "Platforms", "Digital"]),
        ("Communication Services",
         [("Interactive Media", 1), ("Internet Services", 1)], 380_000, 7.0, 1.6,
         ["Streams", "Media", "Wireless", "Interactive", "Connect"]),
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
         ["Ledger", "Advisory", "Holdings", "Exchange", "Markets"]),
    ]
    SECTOR_W = [48, 12, 13, 13, 7, 7]                           # IT-heavy, mirroring Acme's market
    # Tickers we refuse to mint: a real, recognizable ticker would undermine the "obviously synthetic"
    # guarantee. Single source of truth — the same deny-list the screener's loader rejects on (so the
    # generator and the loader can never drift). The dedup loop skips any base that lands on one of these.
    REAL_TICKERS = set(_PEER_REAL_TICKERS)
    companies = [{"ticker": "ACMQ", "company_name": "Acme Corp", "gics_sector": "Information Technology",
                  "gics_subindustry": "Application Software", "revenue_usd": ttm,
                  "market_cap_usd": int(round(ttm * 7.5 / 1_000_000) * 1_000_000),
                  "employees": active_emp, "total_assets_usd": int(round(ttm * 1.6 / 1_000_000) * 1_000_000),
                  "is_subject": "yes"}]
    used_tk, n, guard = {"ACMQ"} | REAL_TICKERS, 0, 0
    while n < 220:
        guard += 1
        if guard > 50_000:                                      # bounded: never spin forever on an exhausted pool
            raise RuntimeError("peer-universe generation exceeded its retry budget; widen the ticker pool")
        sector, subs, rev_per_emp, mc_mult, asset_int, suffixes = rng_peer.choices(SECTORS, weights=SECTOR_W)[0]
        subindustry = rng_peer.choices([s for s, _ in subs], weights=[w for _, w in subs])[0]
        prefix = rng_peer.choice(PREFIX)
        # derive a 4-char ticker from the prefix; rotate ONLY the final letter (bounded to 26 tries, A after Z)
        # to dodge collisions with prior peers and the real-ticker deny-list. If a base is exhausted, drop it.
        base = prefix[:4].upper().ljust(4, "X")
        tk = base
        for _ in range(26):
            if tk not in used_tk:
                break
            last = tk[-1]
            tk = base[:3] + (chr(ord(last) + 1) if last < "Z" else "A")
        if tk in used_tk:
            continue
        # OBVIOUSLY-SYNTHETIC name: a numbered issuer label, so it can NEVER collide with a real company name
        # (no real company is "Cinder 042"). The coined prefix adds a little variety; the sequential number
        # guarantees fiction. This is stronger than a best-effort real-name deny-list, which two audits showed
        # can't keep up with a finite word pool. The deny-list is kept only as a cheap belt-and-suspenders.
        name = f"{prefix} {n + 1:03d}"
        if name.casefold() in _PEER_REAL_NAMES:                 # unreachable for a numbered name, but harmless
            continue
        used_tk.add(tk)
        # revenue: log-uniform $12M..$1.6B, beta-skewed toward small-cap so there's a real cluster near Acme
        lo, hi = 150_000_000, 5_000_000_000
        rev = lo * (hi / lo) ** (rng_peer.betavariate(2.3, 2.5))
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
        anchor = 9_000_000 * (c["market_cap_usd"] / 6_000_000_000.0) ** 0.40   # CEO pay ~ size, SUBLINEAR (no $90M tails)
        if is_subj:
            # The subject is deliberately positioned for a borderline/Medium ISS story (a clean pass would
            # not exercise the screen, an absurd fail — e.g. pay at the 100th percentile — isn't realistic):
            # CEO pay above the peer median with a steady ramp, a SOFT TSR (+7% over 5y), and below-median
            # financials, so PAY OUTRUNS TSR (a Medium RDA — the pay-for-performance misalignment measure)
            # while MOM stays modest. Explicit + deterministic for control; clearly illustrative synthetic
            # positioning (retuned when the synthetic peer universe changed the subject's self-peer group).
            self_peers = [o["ticker"] for o in pool[:12]]
            med = anchor                             # ≈ peer-median annual CEO pay anchor
            pays = [int(round(med * f / 1000) * 1000) for f in (1.25, 1.37, 1.49, 1.61, 1.73)]
            tsr = [104.0, 109.0, 113.0, 110.0, 107.0]
            fe = 16.0                                # strong financials ≈ high pay -> FPA neutral, no escalation
        else:
            self_peers = [o["ticker"] for o in pool[:rng_iss.randint(10, 14)]]
            base = max(1_000_000, anchor * rng_iss.uniform(0.75, 1.35))                  # CEO pay ~ size
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

    # ---- retention panel: a monthly person-period panel for the retention-risk model ----
    # Independent rng stream (SEED+23): appended, so every table above stays byte-identical. A 36-month
    # point-in-time panel over an independent synthetic workforce (R-0001…), with PLANTED discrete-time
    # hazard signal — so a real model can recover structure — AND deliberate realism (stochastic noise,
    # decoy features, missingness, a weak-signal cohort) so held-out performance lands in a believable band
    # rather than a cartoonish 0.99. Voluntary / involuntary / retirement are DISTINCT competing risks:
    # only `voluntary` is the model target; the others are censored (never coded as a positive). One row per
    # (employee × active month). Features are point-in-time as-of month-END m (all known then); `event_next`
    # is the leakage-free one-step-ahead competing-risks label = the outcome in the FOLLOWING month (the exit
    # month itself is not a row, so end-of-m features predicting an m+1 event carry no lookahead). No real people/PII.
    rng_ret = random.Random(SEED + 23)
    N_RET = 2400
    H = 36                                                 # observation horizon, months

    def _pme(d):                                           # previous month-end
        return date(d.year, d.month, 1) - timedelta(days=1)

    # The panel is FULLY HISTORICAL: the newest labeled month is AS_OF - 1 month, so every row's `event_next`
    # (the following-month outcome) references a month <= AS_OF — a realized, already-observed outcome as of
    # AS_OF. The AS_OF month itself is never a labeled row (its following month would be the future), so the
    # panel contains no future/unknowable labels.
    months = []
    cur = _pme(AS_OF)
    for _ in range(H):
        months.append(cur)
        cur = _pme(cur)
    months.reverse()                                       # oldest -> newest, length H (newest = AS_OF - 1 month)
    mi = {m: i for i, m in enumerate(months)}

    def _absm(d):                                          # absolute month index (year*12+month)
        return d.year * 12 + (d.month - 1)

    base_abs = _absm(months[0])
    SPAN_BACK = 120
    # shared synthetic market-index path (mild up-drift + a deterministic mid-window drawdown so some RSU
    # grants go underwater -> equity_moneyness has real spread). Anchored SPAN_BACK months before the window.
    price = [100.0]
    for k in range(1, SPAN_BACK + H + 6):
        shock = -0.035 if 72 <= k <= 84 else 0.0
        price.append(max(15.0, price[-1] * (1.0 + 0.006 + shock + rng_ret.gauss(0.0, 0.045))))

    def _price_at(d):
        return price[max(0, min(len(price) - 1, _absm(d) - (base_abs - SPAN_BACK)))]

    def _zn(x, c, s):                                      # rough standardization for the planted predictor
        return (x - c) / s

    REGIONS = ["Americas", "EMEA", "APAC"]
    RET_FUNCS = ["Engineering", "Product", "Sales", "Customer Success", "G&A", "Marketing"]
    EQW = {"L3": 0.22, "L4": 0.30, "L5": 0.40, "L6": 0.52, "L7": 0.60}

    panel = []
    vol_events = inv_events = ret_events = 0
    for i in range(1, N_RET + 1):
        eid = f"R-{i:04d}"
        func = rng_ret.choices(RET_FUNCS, weights=[30, 14, 20, 14, 12, 10])[0]
        level = rng_ret.choices(LEVELS, weights=[34, 30, 22, 10, 4])[0]
        region = rng_ret.choices(REGIONS, weights=[50, 34, 16])[0]
        audit_group = rng_ret.choice(["A", "B", "C"])      # synthetic, fairness-audit-only; independent of hazard
        span_band = rng_ret.choices(["IC", "1-3", "4-7", "8+"], weights=[64, 18, 12, 6])[0]
        weak = func == "Customer Success"                  # weak-signal cohort (effects damped)
        hire = AS_OF - timedelta(days=rng_ret.randint(31, 365 * 8))
        comp_ratio = max(0.62, min(1.45, rng_ret.gauss(0.99, 0.12)))
        eqw = EQW[level]
        grant_price = _price_at(hire)
        team_attr = min(0.45, max(0.02, rng_ret.gauss(0.14, 0.06)))
        is_high_perf = rng_ret.random() < 0.20
        is_low_perf = rng_ret.random() < 0.12
        eng_level = rng_ret.gauss(0.0, 1.0)
        eng_trend = rng_ret.gauss(-0.02, 0.06)
        last_raise_pct = round(rng_ret.uniform(0.02, 0.06), 3)
        mgr_change_m = (months[0] + timedelta(days=rng_ret.randint(-200, 760))) if rng_ret.random() < 0.35 else None
        noise_a_base = rng_ret.randint(20, 60)
        noise_b_static = round(rng_ret.uniform(0.1, 9.9), 2)   # harmless synthetic noise, no hazard effect
        # stagnation COUNTERS seeded at window entry, then incremented monthly (reset on raise/promo). Seeding
        # deep histories (up to ~5y in level / ~2.5y since raise) is what lets the stuck / tenure-danger /
        # high-perf-unrecognized flags actually fire instead of being constant-zero columns.
        msr = rng_ret.randint(0, 24)
        msp = rng_ret.randint(0, 42)
        mil = msp
        eng_vals = []
        for t, m in enumerate([mm for mm in months if mm >= hire]):
            if t > 0:
                msr += 1
                msp += 1
                mil += 1
            # --- monthly raise/promo dynamics (reset the stagnation clocks) ---
            if rng_ret.random() < 0.03:
                msr = 0
                last_raise_pct = round(rng_ret.uniform(0.01, 0.05), 3)
                comp_ratio = min(1.45, comp_ratio + rng_ret.uniform(0.01, 0.05))
            else:
                comp_ratio = max(0.62, comp_ratio - 0.0015)               # slow erosion -> stagnation signal
            if level != "L7" and mil >= 12 and rng_ret.random() < 0.012:
                msp = 0
                mil = 0
            # --- point-in-time features as of month-END m (all known at end of m) ---
            tenure_m = max(0, _absm(m) - _absm(hire))
            e_t = eng_level + eng_trend * t + rng_ret.gauss(0.0, 0.30)
            eng_vals.append(e_t)
            eng_missing = rng_ret.random() < 0.12
            slope = (eng_vals[-1] - eng_vals[-3]) / 2.0 if len(eng_vals) >= 3 else eng_trend
            stuck = 1 if (mil >= 36 and msp >= 24) else 0
            tdanger = 1 if (18 <= tenure_m <= 24 and msp >= 12 and msr >= 9) else 0
            hpu = 1 if (is_high_perf and msp > 24) else 0
            promo_vel = round((30 - msp) / 16.0 + rng_ret.gauss(0.0, 0.30), 3)
            pdelta_missing = rng_ret.random() < 0.08
            pdelta = round(rng_ret.gauss(0.0, 0.6), 2)
            mgr_chg = 1 if (mgr_change_m is not None and abs(_absm(m) - _absm(mgr_change_m)) <= 12) else 0
            mta = round(min(0.5, max(0.0, team_attr + rng_ret.gauss(0.0, 0.02))), 3)
            tdep = max(0, min(6, int(round(rng_ret.gauss(team_attr * 6.0, 1.2)))))
            msg = tenure_m                                                  # months since grant (grant at hire)
            vested = 0.0 if msg < 12 else min(1.0, 0.25 + 0.75 * (msg - 12) / 36.0)
            unvested_pct = round(eqw * max(0.10, 1.0 - vested), 4)          # floor: ongoing refreshers keep some unvested
            rem = msg % 12                                                  # annual vest cadence: cliff at 12, then yearly
            dtnv = (12 - msg if msg < 12 else (12 - rem if rem else 0)) * 30   # days to next vest, bounded [0, 360]
            pvw = 1 if (msg >= 12 and rem in (0, 1)) else 0                 # the month of / month after an annual vest
            underwater = round(max(0.0, min(1.0, 1.0 - _price_at(m) / grant_price)), 4)
            eq_heavy = 1 if eqw >= 0.45 else 0
            # --- planted monthly voluntary hazard. EVERY allowlist feature carries an intentional signed
            # effect (so a model recovers real structure) and ONLY the 3 named decoys are inert. ---
            es = 0.0 if eng_missing else slope
            pd_ = 0.0 if pdelta_missing else pdelta
            zfeat = (0.55 * _zn(msr, 12, 10) + 0.55 * _zn(msp, 24, 16) + 0.45 * stuck
                     - 0.70 * _zn(comp_ratio, 0.99, 0.12) - 0.60 * _zn(es, 0.0, 0.5) - 0.40 * pd_
                     + 0.50 * hpu + 0.55 * _zn(mta, 0.14, 0.06) + 0.30 * _zn(tdep, 1.2, 1.3)
                     + 0.45 * tdanger - 0.65 * _zn(unvested_pct, 0.18, 0.14) + 0.55 * pvw
                     + 0.60 * _zn(underwater, 0.10, 0.18)
                     + 0.25 * mgr_chg - 0.25 * _zn(last_raise_pct, 0.035, 0.015)
                     - 0.15 * _zn(promo_vel, 0.0, 1.0)
                     + 0.15 * _zn(dtnv, 180, 110) + 0.12 * eq_heavy)
            z = -6.65 + (0.4 if weak else 1.0) * zfeat + rng_ret.gauss(0.0, 0.85)
            lam_vol = max(0.0, min(0.30, _sig(z)))
            lam_inv = 0.0025 * (2.2 if is_low_perf else 1.0)
            lam_ret = 0.0009 * (3.0 if tenure_m > 300 else 1.0)
            u = rng_ret.random()
            if u < lam_vol:
                ev = "voluntary"
            elif u < lam_vol + lam_inv:
                ev = "involuntary"
            elif u < lam_vol + lam_inv + lam_ret:
                ev = "retirement"
            else:
                ev = "none"
            tb = ("<1y" if tenure_m < 12 else "1-2y" if tenure_m < 24 else "2-3y" if tenure_m < 36
                  else "3-5y" if tenure_m < 60 else "5y+")
            cpb = "below" if comp_ratio < 0.9 else "within" if comp_ratio <= 1.1 else "above"
            panel.append({
                "emp_id": eid, "month": _d(m), "month_index": mi[m],
                "function": func, "level": level, "region_band": region,
                "manager_span_band": span_band, "comp_position_band": cpb, "tenure_band": tb,
                "comp_ratio": round(comp_ratio, 4), "mths_since_last_raise": msr,
                "last_raise_pct": last_raise_pct, "mths_since_promo": msp,
                "promo_velocity_vs_peer": promo_vel, "stuck_in_level_flag": stuck,
                "perf_rating_delta_4q": ("" if pdelta_missing else pdelta),
                "high_perf_unrecognized": hpu,
                "engagement_slope_3p": ("" if eng_missing else round(slope, 3)),
                "mgr_changed_12m": mgr_chg, "mgr_team_attrition_ttm": mta,
                "team_departures_90d": tdep,
                "tenure_danger_18_24": tdanger, "unvested_equity_pct_comp": unvested_pct,
                "days_to_next_vest": dtnv, "post_vest_window_flag": pvw,
                "equity_moneyness": underwater, "comp_mix_equity_heavy": eq_heavy,
                "decoy_noise_a": max(0, noise_a_base + rng_ret.randint(-8, 8)),
                "decoy_noise_b": noise_b_static,
                "decoy_noise_c": rng_ret.randint(0, 12),
                "audit_group": audit_group, "event_next": ev,
            })
            if ev != "none":
                vol_events += ev == "voluntary"
                inv_events += ev == "involuntary"
                ret_events += ev == "retirement"
                break
    _write("retention_panel.csv", panel,
           ["emp_id", "month", "month_index", "function", "level", "region_band",
            "manager_span_band", "comp_position_band", "tenure_band", "comp_ratio",
            "mths_since_last_raise", "last_raise_pct", "mths_since_promo", "promo_velocity_vs_peer",
            "stuck_in_level_flag", "perf_rating_delta_4q", "high_perf_unrecognized",
            "engagement_slope_3p", "mgr_changed_12m", "mgr_team_attrition_ttm", "team_departures_90d",
            "tenure_danger_18_24", "unvested_equity_pct_comp", "days_to_next_vest",
            "post_vest_window_flag", "equity_moneyness", "comp_mix_equity_heavy", "decoy_noise_a",
            "decoy_noise_b", "decoy_noise_c", "audit_group", "event_next"])

    print(f"generated Acme dataset -> {OUT}")
    print(f"  workers.csv: {len(workers)} ({len(employees)} employees, {len(workers) - len(employees)} contractors)")
    print(f"  comp_bands.csv: {len(bands)} | benefits_enrollment.csv: {len(enroll)} | cases.csv: {len(cases)}")
    print(f"  financials.csv: {len(fin)} quarters ({fin[0]['period_end']} -> {fin[-1]['period_end']})")
    print(f"  peer_universe.csv: {len(companies)} companies (1 subject + {len(companies)-1} synthetic peers)")
    print(f"  retention_panel.csv: {len(panel)} person-months ({N_RET} employees; "
          f"{vol_events} vol / {inv_events} invol / {ret_events} ret exits)")
    print(f"  as_of: {AS_OF.isoformat()} | seed: {SEED}")


def _write(name, rows, fields):
    with open(OUT / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")   # LF (clean git diff --check)
        w.writeheader()
        for r in rows:
            w.writerow(r)


if __name__ == "__main__":
    generate()
