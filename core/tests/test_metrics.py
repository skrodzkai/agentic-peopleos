#!/usr/bin/env python3
"""Evals for the metric registry. Run: python core/tests/test_metrics.py

Proves the registry is well-formed AND that it is a measurement-GOVERNANCE system:
no metric may grant a decisional/irreversible action.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core import metrics  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


reg = metrics.MetricRegistry.load()
data = {"schema_version": reg.schema_version, "metrics": reg.all()}

# Well-formed.
ok(metrics.validate(data) == [], "the shipped registry validates clean")
ok(len(reg.all()) >= 30, "registry has a substantive set of metrics")
ok(set(reg.domains()) <= metrics.DOMAINS and len(reg.domains()) >= 5, "metrics span the People domains")

# Governance teeth: no metric grants a dangerous action; every metric forbids one; allowed vocab only.
for m in reg.all():
    allowed = set(m["agent_allowed_actions"])
    ok(not (allowed & metrics.DANGEROUS_ACTIONS), f"{m['id']} grants no decisional action")
    ok(allowed <= metrics.ALLOWED_ACTIONS, f"{m['id']} uses only allowed-action vocabulary")
    ok(bool(m["agent_forbidden_actions"]), f"{m['id']} explicitly forbids at least one action")

# Targeted: the flagship comp metric can analyze but never touch pay.
cr = reg.get("compa_ratio")
ok(cr is not None and reg.may("compa_ratio", "calculate") and reg.may("compa_ratio", "flag_outliers"),
   "compa_ratio may be calculated and outliers flagged")
ok("change_salary" in cr["agent_forbidden_actions"] and "recommend_pay_change" in cr["agent_forbidden_actions"],
   "compa_ratio forbids changing OR recommending pay")

# Lookups agents rely on.
ok("midpoint" in reg.definition("compa_ratio"), "definition() returns the metric definition")
ok("metrics.registry.json#compa_ratio" in reg.citation("compa_ratio"), "citation() points back to the registry")
ok(reg.param("requisition_aging", "aging_threshold_days") == 90, "params are readable (aging threshold)")

# Negative: a metric that grants a dangerous action is rejected.
bad = {"schema_version": "1.0", "metrics": [dict(cr, id="evil", agent_allowed_actions=["calculate", "change_salary"])]}
ok(any("GRANTS a forbidden decisional action" in v for v in metrics.validate(bad)),
   "a metric granting change_salary is caught by the validator")

# Negative: missing field + duplicate id are caught.
ok(any("missing/empty required field" in v for v in metrics.validate({"schema_version": "1.0", "metrics": [{"id": "x"}]})),
   "a metric missing required fields is caught")
dup = {"schema_version": "1.0", "metrics": [cr, cr]}
ok(any("duplicate metric id" in v for v in metrics.validate(dup)), "duplicate metric ids are caught")

# Negative: a duplicate JSON key (which json.loads would silently collapse, hiding a dangerous
# action under a "safe" last value) is rejected at PARSE time, before validate() ever runs.
import tempfile  # noqa: E402
evil_json = ('{"schema_version":"1.0","metrics":[{"id":"x",'
             '"agent_allowed_actions":["change_salary"],'
             '"agent_allowed_actions":["calculate"]}]}')
p = Path(tempfile.mkdtemp()) / "dupkey.json"
p.write_text(evil_json, encoding="utf-8")
try:
    metrics._load_json(p)
    ok(False, "duplicate JSON key must be rejected at parse")
except metrics.RegistryError:
    ok(True, "duplicate JSON key is rejected at parse (can't hide a dangerous action)")

# Negative: malformed action fields don't crash the validator (fail closed, not traceback).
ok(any("must be a list" in v for v in metrics.validate(
    {"schema_version": "1.0", "metrics": [dict(cr, id="n", agent_allowed_actions=None)]})),
   "a non-list agent_allowed_actions is a violation, not a crash")
ok(any("must be a list" in v for v in metrics.validate(
    {"schema_version": "1.0", "metrics": [dict(cr, id="d", downstream="not-a-list")]})),
   "a non-list downstream is caught")

# Negative: the CLI fails closed (exit 1, no traceback) on unparseable input.
empty = Path(tempfile.mkdtemp()) / "empty.json"
empty.write_text("", encoding="utf-8")
ok(metrics._main(["validate", str(empty)]) == 1, "CLI exits 1 on empty/unparseable input")
ok(metrics._main(["validate", "/no/such/file.json"]) == 1, "CLI exits 1 on a missing file")

# Every metric is classed (Core KPI / Diagnostic / Operational alert) and carries a protocol note.
for m in reg.all():
    ok(m.get("metric_class") in metrics.METRIC_CLASSES, f"{m['id']} has a valid metric_class")
    ok(bool(m.get("protocol")), f"{m['id']} carries an implementation protocol")

# Audit fixes are locked in (external metric-panel review, 2026-06-20):
ok("workers_with_a_manager" in reg.get("span_of_control")["formula"],
   "span_of_control counts workers-with-a-manager / distinct managers (not ICs/managers)")
ok("distinct" in reg.get("internal_mobility_rate")["definition"].lower()
   or "distinct" in reg.get("internal_mobility_rate")["formula"].lower(),
   "internal_mobility_rate is a person-rate (distinct movers)")
ok("ending" in reg.get("net_headcount_growth")["formula"].lower(),
   "net_headcount_growth headline is ending - beginning")
ok("open_past_sla" in reg.get("sla_attainment")["formula"],
   "sla_attainment denominator includes open-and-breached cases")
ok("12 / months" in reg.get("voluntary_attrition")["formula"],
   "voluntary_attrition pins simple annualization x(12/months)")
ok("sum(base)" in (reg.get("compa_ratio").get("aggregate_formula") or ""),
   "compa_ratio publishes the group aggregate form")
ok("below_min_rate" in reg.get("out_of_band_rate")["formula"],
   "out_of_band_rate splits below-min vs above-max")
# Metrics referenced but previously undefined / newly added high-value ones now exist.
for mid in ("reopen_rate", "ceo_pay_ratio", "adjusted_pay_gap", "total_turnover_rate",
            "quality_of_hire", "cost_per_hire", "span_outlier_rate"):
    ok(reg.get(mid) is not None, f"{mid} exists in the registry")
# Pay-equity + adverse-impact metrics never expose an individual.
ok("identify_individual_publicly" in reg.get("adjusted_pay_gap")["agent_forbidden_actions"],
   "adjusted_pay_gap forbids identifying an individual")
ok("identify_individual_publicly" in reg.get("adverse_impact_ratio")["agent_forbidden_actions"],
   "adverse_impact_ratio forbids identifying an individual")

print(f"OK — {passed} metric-registry checks passed ({len(reg.all())} metrics, {len(reg.domains())} domains).")
