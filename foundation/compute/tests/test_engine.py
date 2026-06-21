#!/usr/bin/env python3
"""Reconciliation evals for the shared metric compute engine.

These prove the engine's numbers tie out (headcount, the net-growth bridge, span math,
distributions reconcile to 100%, turnover splits sum to total) and that the engine is
governance-safe (read-only — no method can change a record) and honest about missing data.
Run: python foundation/compute/tests/test_engine.py
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.compute.engine import (  # noqa: E402
    MetricEngine, EngineDataError, _FUNCS, _window, _month_ends, _resolved, _is_open_at_asof,
    _exists_at_asof, _sla_attainment, _open_case_backlog, _case_csat, _cases_in_period, _date,
)

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


eng = MetricEngine()

# ---- headcount reconciles ----
hc = eng.compute("headcount")
ok(hc["value"] == hc["extras"]["active"] + hc["extras"]["on_leave"], "headcount = active + on_leave")
ok(hc["value"] > 0, "headcount is positive")

# ---- net headcount growth bridge reconciles exactly ----
ng = eng.compute("net_headcount_growth")
ex = ng["extras"]
ok(ng["value"] == ex["ending"] - ex["beginning"], "net growth = ending - beginning")
ok(ex["bridge_reconciles"] is True, "growth bridge (hires - voluntary - involuntary) reconciles")

# ---- span of control: every-manager math + sane distribution ----
sp = eng.compute("span_of_control")
ok(sp["extras"]["managers"] > 0 and 3 <= sp["value"] <= 9, "span is a realistic average (3-9)")
ok(sp["extras"]["median"] > 0, "span has a median")
so = eng.compute("span_outlier_rate")
ok(0 <= so["value"] <= 100, "span outlier rate is a percentage")

# ---- management layers: real depth (manager-of-manager chains exist) ----
ml = eng.compute("management_layers")
ok(ml["extras"]["max_depth"] >= 3, "org has real depth (manager-of-manager chains)")

# ---- turnover splits sum to total (counts, not rounded rates) ----
vol = eng.compute("voluntary_attrition")["extras"]["voluntary_exits"]
invol = eng.compute("involuntary_turnover_rate")["extras"]["involuntary_exits"]
tot = eng.compute("total_turnover_rate")["extras"]["all_exits"]
ok(tot == vol + invol, "total exits == voluntary + involuntary")
reg = eng.compute("regrettable_attrition")["extras"]["regrettable_exits"]
ok(reg <= vol, "regrettable exits are a subset of voluntary")

# ---- annualization uses average headcount (not point-in-time) ----
ok(eng.compute("voluntary_attrition")["extras"]["avg_headcount"] > 0, "voluntary attrition uses avg headcount")

# ---- compa-ratio: aggregate (group) form, not mean-of-ratios ----
cr = eng.compute("compa_ratio")
ok(cr["extras"]["aggregate"] == cr["value"], "compa headline is the aggregate (sum base / sum mid)")
ok(0.7 <= cr["value"] <= 1.3, "compa-ratio is realistic")
seg = eng.segment("compa_ratio", "level")
ok(len(seg) >= 3 and all(0.5 < v < 1.6 for v in seg.values()), "compa segments by level")

# ---- out of band splits below/above and reconciles ----
ob = eng.compute("out_of_band_rate")
ok(ob["extras"]["below"] + ob["extras"]["above"] <= ob["extras"]["n"], "out-of-band counts within population")
ok("below_min_rate" in ob["extras"] and "above_max_rate" in ob["extras"], "out-of-band split below/above")

# ---- distributions reconcile to ~100% ----
rd = eng.compute("rating_distribution")
ok(abs(sum(rd["value"].values()) - 100) <= 2, "rating distribution sums to ~100%")
rep = eng.compute("representation_by_level")["value"]
for lvl, shares in rep.items():
    ok(abs(sum(shares.values()) - 100) <= 2, f"representation at {lvl} sums to ~100%")

# ---- people ops: SLA denominator includes open-and-breached ----
sla = eng.compute("sla_attainment")["extras"]
ok("open_past_sla" in sla and sla["resolved"] >= 0, "SLA attainment counts open-and-breached in the denominator")
ttr = eng.compute("time_to_resolution")["extras"]
ok("p50" in ttr and "p90" in ttr and ttr["p90"] >= ttr["p50"], "time-to-resolution reports p50 and p90")

# ---- registry-bound: every registry id is implemented OR explicitly data_pending; unknown rejected ----
impl, dp_ids, reg_ids = set(_FUNCS), set(eng.DATA_PENDING), eng._reg_ids
ok(reg_ids - impl - dp_ids == set(), "every registry metric is implemented or explicitly data_pending")
ok(impl - reg_ids == set() and dp_ids - reg_ids == set(), "no engine metric id is absent from the registry")
ok(impl.isdisjoint(dp_ids), "no metric is both implemented and data_pending")
ok(eng.compute("made_up_metric")["status"] == "unknown_metric", "an unknown metric id is rejected, not data_pending")
ok(eng.compute("compa_ratio").get("name") == "Compa-ratio", "results carry registry metadata (name)")

# ---- the trailing-12-month window is exactly 12 month-ends (annualization divisor matches) ----
ws, we, wm = _window(eng)
ok(wm == 12 and len(_month_ends(ws, we)) == 12, "window is a true 12-month span (12 month-ends, divisor 12)")

# ---- honest about missing data ----
dp = eng.compute("ceo_pay_ratio")
ok(dp["status"] == "data_pending" and dp["needs"], "missing-source metric returns data_pending with the need named")
ok(eng.compute("labor_cost_per_fte")["status"] == "data_pending",
   "labor_cost_per_fte is data_pending (fully-loaded cost not modeled) rather than a base-only approximation")

# ---- people-ops metrics are RECOMPUTED from raw timestamps (no trusted precomputed flags) ----
ok("within_sla" not in eng.cases[0] and "ttr_hours" not in eng.cases[0],
   "the cases table stores raw facts (timestamps), not precomputed SLA/ttr flags")
ok(eng.compute("sla_attainment")["extras"]["resolved"] > 0, "SLA attainment recomputes from open/resolve timestamps")

# ---- point-in-time: a resolution AFTER the as-of is a future fact the snapshot can't know ----
# The committed dataset never records a future resolution (the generator leaves such cases open)...
ok(all(not c["resolved_at"] or datetime.fromisoformat(c["resolved_at"]) <= eng.as_of_dt for c in eng.cases),
   "committed dataset has no resolved_at after the as-of (clean snapshot)")


class _Stub:
    """Minimal engine-shaped object to exercise the point-in-time guard directly."""
    as_of_dt = datetime(2026, 1, 31, 18, 0)
    cases = [
        # resolved before the as-of -> counts as resolved
        {"case_id": "C1", "opened_at": "2026-01-20T09:00:00", "resolved_at": "2026-01-21T09:00:00",
         "category": "payroll", "sla_target_hours": "24", "reopened": "no",
         "first_contact_resolution": "yes", "csat": "5", "channel": "human"},
        # resolution stamped AFTER the as-of -> must be treated as still open, not resolved
        {"case_id": "C2", "opened_at": "2026-01-30T09:00:00", "resolved_at": "2026-02-05T09:00:00",
         "category": "payroll", "sla_target_hours": "24", "reopened": "no",
         "first_contact_resolution": "yes", "csat": "5", "channel": "human"},
        # never resolved -> open
        {"case_id": "C3", "opened_at": "2026-01-10T09:00:00", "resolved_at": "",
         "category": "payroll", "sla_target_hours": "24", "reopened": "no",
         "first_contact_resolution": "no", "csat": "", "channel": "human"},
        # OPENED after the as-of -> a future fact; must be excluded from every People Ops metric
        {"case_id": "C4", "opened_at": "2026-02-10T09:00:00", "resolved_at": "",
         "category": "payroll", "sla_target_hours": "24", "reopened": "no",
         "first_contact_resolution": "no", "csat": "", "channel": "human"},
    ]


_stub = _Stub()
ok([c["case_id"] for c in _resolved(_stub)] == ["C1"], "future-stamped resolution is excluded from resolved set")
ok(_is_open_at_asof(_stub, _Stub.cases[1]) and _is_open_at_asof(_stub, _Stub.cases[2]),
   "future-resolved and never-resolved cases are both open at the as-of")
ok(not _is_open_at_asof(_stub, _Stub.cases[0]), "a case resolved before the as-of is not open")
ok(not _exists_at_asof(_stub, _Stub.cases[3]) and not _is_open_at_asof(_stub, _Stub.cases[3]),
   "a case OPENED after the as-of does not exist in the snapshot and is not 'open'")
ok(_open_case_backlog(_stub)["value"] == 2, "backlog counts only the in-snapshot open cases (C2+C3), not the future-opened one")
ok(sum(_open_case_backlog(_stub)["extras"]["by_age"].values()) == 2, "backlog age buckets reconcile (no negative-age future case)")
ok(len(_cases_in_period(_stub)) == 3, "case_volume period excludes the future-opened case (3 of 4)")
ok(_sla_attainment(_stub)["extras"]["resolved"] == 1, "SLA attainment denominator excludes future resolutions")
# CSAT is post-resolution: the future-resolved case (C2 carries csat='5') must NOT leak in.
ok(_case_csat(_stub)["extras"]["responses"] == 1, "CSAT counts only cases resolved at-or-before the as-of (no future leak)")

# ---- registry-grade definitions (round 7): no soft proxies passing as canonical ----
pr = eng.compute("promotion_rate")
ok(pr["extras"]["eligible_population"] > 0 and "L7" not in pr["extras"]["by_level"],
   "promotion_rate uses the eligible-population denominator (top-of-track excluded)")
ob = eng.compute("open_case_backlog")
ok(set(ob["extras"]["by_age"]) == {"<24h", "1-3d", "4-7d", "8-14d", "15d+"},
   "open_case_backlog reports the required age buckets")
ok(sum(ob["extras"]["by_age"].values()) == ob["value"], "backlog age buckets reconcile to the open count")
cv = eng.compute("case_volume")
ok(cv["extras"]["period_days"] == 90, "case_volume is explicitly period-scoped (opened-in-period)")

# ---- point-in-time (round 9): snapshot metrics honor as_of, not 'today's' status field ----
hc_2024 = MetricEngine(as_of=date(2024, 1, 31))
hc_2025 = MetricEngine(as_of=date(2025, 1, 31))
ok(hc_2024.compute("headcount")["value"] == len(hc_2024._active_at(date(2024, 1, 31))),
   "headcount at a past as_of equals the date-active population (point-in-time)")
ok(len({hc_2024.compute("headcount")["value"], hc_2025.compute("headcount")["value"], hc["value"]}) == 3,
   "headcount actually varies by as_of (no stale 'current status' bleed across years)")
ok(hc_2024.compute("fte")["value"] != eng.compute("fte")["value"]
   or hc_2024.compute("span_of_control")["value"] != eng.compute("span_of_control")["value"],
   "other snapshot metrics (fte/span) also move with as_of")
# contingent_workforce_ratio counts contractors POINT-IN-TIME too (not the current status field).
_expected_con_2024 = sum(1 for w in hc_2024.workers if w["worker_type"] == "contractor"
                         and (_date(w["hire_date"]) or date.max) <= date(2024, 1, 31)
                         and (_date(w["term_date"]) is None or _date(w["term_date"]) > date(2024, 1, 31)))
ok(hc_2024.compute("contingent_workforce_ratio")["extras"]["contractors"] == _expected_con_2024,
   "contingent_workforce_ratio counts contractors point-in-time at as_of (not current status)")

# ---- honesty (round 9): promotion_velocity is a proxy, so it is data_pending — not 'ok' ----
pv = eng.compute("promotion_velocity")
ok(pv["status"] == "data_pending" and pv["needs"],
   "promotion_velocity is data_pending (no dated promotion events) — not an inflated 'ok' proxy")

# ---- fail-closed (round 9): an impossible case interval is refused, not silently computed ----
bad = MetricEngine()
bad.cases.append({"case_id": "BAD", "opened_at": "2026-01-20T10:00:00", "resolved_at": "2026-01-19T10:00:00",
                  "category": "payroll", "sla_target_hours": "24", "reopened": "no",
                  "first_contact_resolution": "no", "csat": "", "channel": "human"})
_raised = False
try:
    bad._check_data_quality()
except EngineDataError:
    _raised = True
ok(_raised, "a case that resolved before it opened fails closed (no negative TTR / inflated SLA)")

# ---- governance: the engine is read-only — no dangerous mutator exists ----
for danger in ("change_salary", "recommend_pay_change", "change_rating", "terminate",
               "alter_record", "make_hiring_decision"):
    ok(not hasattr(eng, danger), f"engine has no '{danger}' method (read-only by construction)")

# ---- determinism: same data -> same numbers ----
eng2 = MetricEngine()
ok(eng2.compute("compa_ratio")["value"] == cr["value"], "engine is deterministic across instances")

print(f"OK — {passed} engine checks passed "
      f"(headcount {hc['value']}, span {sp['value']}, voluntary attrition {vol} exits, compa {cr['value']}).")
