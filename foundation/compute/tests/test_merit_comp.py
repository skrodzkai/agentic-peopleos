#!/usr/bin/env python3
"""Tests for the merit / comp-cycle engine: the board-plan invariants, the merit-matrix discipline, the
equity-refresher -> equity-ledger schema handshake, and a fail-closed inventory. Fail-closed cases copy the
committed CSVs to a tmp dir and corrupt exactly one thing."""
import csv
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.compute import merit_comp as M  # noqa: E402
from foundation.compute import equity_spend as E  # noqa: E402

_ACME = Path(__file__).resolve().parents[3] / "foundation" / "data" / "acme"
passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


def raises(fn, label):
    global passed
    try:
        fn()
        assert False, f"FAILED (no raise): {label}"
    except M.MeritDataError:
        passed += 1


def _tmp_with(mutate):
    d = Path(tempfile.mkdtemp())
    for name in ("workers.csv", "comp_bands.csv"):
        shutil.copy(_ACME / name, d / name)
    mutate(d)
    return d


def _rewrite(path, fn):
    rows = list(csv.DictReader(open(path)))
    fields = list(rows[0].keys())
    fn(rows)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# ---- happy path: the committed workforce yields a coherent comp-cycle plan --------------------------------
r = M.compute()
ok(r["eligible_headcount"] > 1500, "the eligible active-employee population is loaded")
m = r["merit"]
ok(m["within_budget"] and m["spend"] <= m["budget"] + 1.0, "merit spend is within the merit budget")
ok(2.5 <= m["spend_pct"] <= m["budget_pct"] + 0.01, "merit spend % is a believable share of payroll, <= budget %")
ok(r["bonus_pool"] > 0 and r["promo_spend"] > 0 and r["promotions"] > 0, "bonus pool + promotions are planned")
ok(r["equity_refresh"]["total_value"] > 0 and r["equity_refresh"]["grant_count"] > 0, "equity refreshers planned")
# merit moves the population TOWARD market (compa-ratio rises after the cycle)
ok(r["avg_new_compa_ratio"] > r["avg_compa_ratio"], "average compa-ratio rises after merit (toward market)")

# on-leave disclosure: eligible = active + protected-leave, disclosed (matches the People Analytics headcount)
ok(r["on_leave_count"] > 0 and r["active_count"] > 0, "the active / on-leave split is surfaced")
ok(r["active_count"] + r["on_leave_count"] == r["eligible_headcount"],
   "eligible headcount reconciles to active + on-leave (no silent folding of leave into 'active')")

# merit-matrix discipline: a high performer BELOW market gets the most; a low performer above market gets 0
mtx = r["matrix"]
ok(mtx["outstanding"]["q1"] > mtx["outstanding"]["q4"] > 0, "outstanding: below-market merit > above-market > 0")
ok(mtx["outstanding"]["q1"] > mtx["meets"]["q1"] > mtx["below"]["q1"], "at a fixed compa, higher rating pays more")
ok(mtx["below"]["q3"] == 0.0 and mtx["below"]["q4"] == 0.0, "a low performer already at/above market gets 0% merit")
for rating in ("below", "meets", "exceeds", "outstanding"):
    ok(mtx[rating]["q1"] >= mtx[rating]["q2"] >= mtx[rating]["q3"] >= mtx[rating]["q4"],
       f"{rating}: merit % is monotone non-increasing as compa-ratio rises")

# ---- the equity-refresher -> equity-ledger handshake (the two arms share the ledger schema) ---------------
cyc = M.MeritCycle()
grants = cyc.equity_refresh_grants(cyc.plan())
ok(len(grants) > 0, "refresher grant rows are produced")
ok(all(tuple(g.keys()) == E._GRANT_COLS for g in grants),
   "every refresher grant row matches the equity-ledger schema exactly (append-ready)")
ok(all(g["grant_type"] == "annual_refresh" and g["award_type"] == "rsu" and g["psu_share_basis"] == ""
       and float(g["shares_granted"]) > 0 for g in grants), "refresher grants are well-formed RSU annual refreshes")
# participant_group is PRESERVED from each holder's equity-ledger history (an exec's refresher stays exec —
# not silently reclassified to 'management', which would distort the board dashboard's exec-vs-staff split)
_ledger = {g["emp_id"]: g["participant_group"] for g in csv.DictReader(open(_ACME / "equity_grants.csv"))}
_rg = {g["emp_id"]: g["participant_group"] for g in grants}
_exec_recips = [e for e in _rg if _ledger.get(e) in ("ceo", "section16")]
ok(len(_exec_recips) > 0 and all(_rg[e] == _ledger[e] for e in _exec_recips),
   "CEO / Section 16 refresher recipients keep their ledger group (not flattened to management)")
ok(all(_rg[e] == _ledger[e] for e in _rg if e in _ledger and _ledger[e] in ("ceo", "section16", "management", "staff")),
   "every refresher recipient with ledger history keeps that exact participant group")
ok(all(g["participant_group"] in ("ceo", "section16", "management", "staff") for g in grants),
   "no refresher carries a 'director' or unknown participant group")
bg = r["equity_refresh"]["by_group"]
ok(sum(v["grants"] for v in bg.values()) == r["equity_refresh"]["grant_count"]
   and "ceo" in bg and "section16" in bg, "the by-group split covers all grants incl. the exec groups")
# participant-group fallback mirrors the generator: 'management' only for a people manager at L5-L7 (an L5-L7
# individual contributor with no ledger history is STAFF, not management)
ok(cyc._participant_group({"emp_id": "X-none", "is_people_manager": "no", "level": "L6"}) == "staff",
   "an L5-L7 non-people-manager with no ledger history is staff, not management")
ok(cyc._participant_group({"emp_id": "X-none", "is_people_manager": "yes", "level": "L6"}) == "management",
   "an L5-L7 people manager with no ledger history is management")
ok(cyc._participant_group({"emp_id": "X-none", "is_people_manager": "yes", "level": "L3"}) == "staff",
   "a people manager below L5 is staff (level gate)")
# grant ids continue AFTER the ledger's highest existing id -> no collision with committed grants
_max_led = max(int(g["grant_id"][2:]) for g in csv.DictReader(open(_ACME / "equity_grants.csv")))
ok(all(int(g["grant_id"][2:]) > _max_led for g in grants),
   "refresher grant ids continue past the ledger's highest id (no collision)")
# FTE proration: a part-timer's actual (paid) base is below their FTE base, and the actual payroll is below
# the sum of FTE bases (part-time hours are not billed as full-time cost)
_plan = cyc.plan()
_pt = [p for p in _plan if p["fte"] < 1.0]
ok(len(_pt) > 0 and all(0 < p["fte"] <= 1.0 for p in _plan), "FTE is in (0,1]; part-timers exist")
ok(all(abs(p["actual_base"] - p["base_salary"] * p["fte"]) < 1e-6 for p in _plan),
   "actual_base = FTE base x FTE fraction (cash cost is prorated)")
ok(r["payroll"] < sum(p["base_salary"] for p in _plan) - 1e6,
   "actual payroll is materially below the sum of FTE bases (part-time hours not costed as full-time)")
ok(all(abs(p["compa_ratio"] - p["base_salary"] / cyc.bands[p["band_id"]]["mid"]) < 1e-9 for p in _plan),
   "compa-ratio stays on the FTE base (NOT prorated) — a part-timer is not shown as underpaid")
# REAL handshake (not just column keys): append the refreshers to a copy of the live ledger and run the
# equity engine over it. This turns the equity arm's whole VALUE-validation surface (grant_type /
# participant_group vocab, plan_id existence, plan active window, grant_id uniqueness, emp existence) into a
# drift tripwire — a column-key check alone would stay green while a value-level equity tightening broke the
# real append.
_ehc = Path(tempfile.mkdtemp()) / "acme"
shutil.copytree(_ACME, _ehc)
_base_grants = len(E.EquityPlan(_ehc).grants)
with open(_ehc / "equity_grants.csv", "a", newline="") as _fh:
    _w = csv.DictWriter(_fh, fieldnames=list(E._GRANT_COLS), lineterminator="\n")
    _w.writerows(grants)
_after = E.EquityPlan(_ehc)                       # re-validates the whole ledger incl. the appended refreshers
ok(len(_after.grants) == _base_grants + len(grants),
   "refreshers append to the live equity ledger and the equity engine validates them (grant count rises exactly)")
E.compute(_ehc)                                   # full equity compute over the augmented ledger must not raise
ok(True, "equity_spend.compute() succeeds over the ledger augmented with the merit refreshers (real handshake)")

# ---- determinism ----------------------------------------------------------------------------------------
ok(json.dumps(M.compute(), sort_keys=True, default=str) == json.dumps(r, sort_keys=True, default=str),
   "compute() is deterministic")

# ---- FAIL-CLOSED inventory -------------------------------------------------------------------------------
raises(lambda: M.MeritCycle(Path(tempfile.mkdtemp())), "missing data files fail closed")
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda rows: next(x for x in rows if x["worker_type"] == "employee" and x["status"] != "terminated")
       .__setitem__("rating", "amazing")))), "an unknown performance rating fails closed")
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda rows: next(x for x in rows if x["status"] != "terminated").__setitem__("band_id", "B-NOPE")))),
       "a worker referencing an unknown comp band fails closed")
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda rows: next(x for x in rows if x["status"] != "terminated").__setitem__("base_salary", "0")))),
       "a non-positive base salary fails closed")
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "comp_bands.csv",
       lambda rows: rows.append(dict(rows[0]))))), "a duplicate band_id fails closed")
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "comp_bands.csv",
       lambda rows: rows[0].__setitem__("range_max", rows[0]["range_min"])))),
       "a comp band whose max <= min fails closed")
# a worker on a band whose level != the worker's level corrupts compa-ratio math -> must fail closed
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda rows: next(x for x in rows if x["status"] != "terminated" and x["level"] == "L3")
       .__setitem__("band_id", next(b["band_id"] for b in csv.DictReader(open(_ACME / "comp_bands.csv"))
                                    if b["level"] == "L7"))))),
       "a worker whose level != their comp band's level fails closed")
# promoted_this_period must be an explicit yes/no — a stray value must NOT silently read as 'not promoted'
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda rows: next(x for x in rows if x["status"] != "terminated")
       .__setitem__("promoted_this_period", "TRUE")))),
       "an out-of-vocabulary promoted_this_period value fails closed")
# a non-terminated worker with an unexpected status (neither active nor on_leave) fails closed
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda rows: next(x for x in rows if x["worker_type"] == "employee" and x["status"] != "terminated")
       .__setitem__("status", "sabbatical")))),
       "an unexpected non-terminated employee status fails closed")
# a typo'd worker_type must fail closed, NOT silently drop the employee from the cycle
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda rows: next(x for x in rows if x["worker_type"] == "employee")
       .__setitem__("worker_type", "employe")))),
       "a typo'd worker_type fails closed (not a silent drop)")
# the band must match the worker on job FAMILY + LOCATION, not just level (else compa-ratio uses the wrong range)
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda rows: next(x for x in rows if x["status"] != "terminated")
       .__setitem__("job_family", "Nonexistent")))),
       "a worker whose job family doesn't match their comp band fails closed")
# a promotion increase requires the worker to be promotion-eligible — a contradiction fails closed
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda rows: next(x for x in rows if x["status"] != "terminated" and x["promoted_this_period"] == "yes")
       .__setitem__("promotion_eligible", "no")))),
       "promoted_this_period=yes with promotion_eligible=no fails closed")
# scheduled hours above full-time is incoherent (FTE > 1) -> fail closed
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda rows: next(x for x in rows if x["status"] != "terminated")
       .__setitem__("scheduled_hours", "999999")))),
       "scheduled_hours above full-time fails closed")
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda rows: next(x for x in rows if x["status"] != "terminated")
       .__setitem__("scheduled_hours", "0")))),
       "non-positive scheduled_hours fails closed")
raises(lambda: M.compute(_tmp_with(lambda d: _rewrite(d / "workers.csv",
       lambda rows: next(x for x in rows if x["status"] != "terminated")
       .__setitem__("is_people_manager", "maybe")))),
       "an out-of-vocabulary is_people_manager value fails closed")

print(f"OK — {passed} merit / comp-cycle checks passed "
      f"({r['eligible_headcount']} eligible; merit {m['spend_pct']}% of ${r['payroll']/1e6:.0f}M vs {m['budget_pct']}% budget).")
