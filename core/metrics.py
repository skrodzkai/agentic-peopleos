#!/usr/bin/env python3
"""People Analytics metric registry — loader, lookup, and governance validator.

The registry (`vault/90-people-analytics/metrics/metrics.registry.json`) is the single
source of truth for metric definitions. Reporting agents READ it and CITE it; they never
redefine metrics. Every metric also carries `agent_allowed_actions` /
`agent_forbidden_actions`, which makes this a measurement-GOVERNANCE system: an agent may
calculate compa-ratio and flag outliers, but it is structurally forbidden from
recommending or changing pay. The validator enforces that no metric can ever grant a
decisional/irreversible action.

CLI:  python3 -m core.metrics validate <registry.json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "vault/90-people-analytics/metrics/metrics.registry.json"

DOMAINS = {"talent_acquisition", "headcount", "total_rewards", "attrition", "people_ops",
           "performance", "health_safety", "compliance_ethics", "learning_development",
           "succession", "diversity", "business_linkage"}
# Every metric is classed so Core KPIs aren't confused with diagnostics or workflow alerts.
METRIC_CLASSES = {"core_kpi", "diagnostic", "operational_alert"}

# Actions an agent may be granted (analysis only).
ALLOWED_ACTIONS = {"calculate", "trend", "segment", "flag_outliers", "rank", "draft_summary",
                   "forecast", "surface_to_human"}
# Decisional / irreversible actions an agent may NEVER be granted for any metric.
DANGEROUS_ACTIONS = {"change_salary", "recommend_pay_change", "change_pay", "change_rating",
                     "recommend_termination", "make_hiring_decision", "terminate",
                     "alter_record", "identify_individual_publicly"}

REQUIRED_FIELDS = ("id", "name", "domain", "definition", "formula", "grain", "unit",
                   "source_system", "owner", "refresh_cadence", "exclusions",
                   "common_misuse", "decision_supported", "downstream", "metric_class",
                   "protocol", "agent_allowed_actions", "agent_forbidden_actions")
# Fields that must be a JSON list (not a scalar) when present.
LIST_FIELDS = ("downstream", "agent_allowed_actions", "agent_forbidden_actions")


class RegistryError(ValueError):
    """Raised when the registry file cannot be parsed safely (fail closed)."""


def _no_dup_keys(pairs):
    """object_pairs_hook that rejects duplicate JSON keys.

    Without this, json.loads silently keeps the LAST value for a repeated key, so a file with
    `"agent_allowed_actions":["change_salary"], "agent_allowed_actions":["calculate"]` would
    sail past the dangerous-action gate. We refuse to parse it at all.
    """
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise RegistryError(f"duplicate JSON key '{k}'")
        seen[k] = v
    return seen


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_no_dup_keys)


class MetricRegistry:
    def __init__(self, data: dict):
        self.schema_version = data.get("schema_version")
        self.metrics = data.get("metrics", [])
        self._by_id = {m.get("id"): m for m in self.metrics}

    @classmethod
    def load(cls, path=DEFAULT_PATH):
        return cls(_load_json(path))

    def all(self):
        return list(self.metrics)

    def get(self, metric_id):
        return self._by_id.get(metric_id)

    def by_domain(self, domain):
        return [m for m in self.metrics if m.get("domain") == domain]

    def domains(self):
        return sorted({m.get("domain") for m in self.metrics})

    def param(self, metric_id, key, default=None):
        return (self.get(metric_id) or {}).get("params", {}).get(key, default)

    def definition(self, metric_id):
        return (self.get(metric_id) or {}).get("definition")

    def citation(self, metric_id):
        """A one-line citation an agent can put in a report."""
        m = self.get(metric_id)
        if not m:
            return f"[unknown metric: {metric_id}]"
        return f"{m['name']} — {m['definition']} (source: metrics.registry.json#{metric_id})"

    def may(self, metric_id, action):
        return action in (self.get(metric_id) or {}).get("agent_allowed_actions", [])


def validate(data) -> list:
    """Return governance violations ([] == valid). `data` is the parsed registry dict."""
    violations = []
    if data.get("schema_version") != "1.0":
        violations.append("schema_version is not '1.0'")
    metrics = data.get("metrics", [])
    if not metrics:
        return ["registry has no metrics"]

    if not isinstance(metrics, list):
        return ["'metrics' must be a list"]

    seen = set()
    for m in metrics:
        if not isinstance(m, dict):
            violations.append(f"metric entry is not an object: {m!r}")
            continue
        mid = m.get("id", "<no id>")
        for f in REQUIRED_FIELDS:
            if f not in m or m[f] in (None, "", []):
                violations.append(f"{mid}: missing/empty required field '{f}'")
        for f in LIST_FIELDS:
            if f in m and not isinstance(m[f], list):
                violations.append(f"{mid}: field '{f}' must be a list, got {type(m[f]).__name__}")
        if m.get("id") in seen:
            violations.append(f"{mid}: duplicate metric id")
        seen.add(m.get("id"))
        if m.get("domain") not in DOMAINS:
            violations.append(f"{mid}: invalid domain '{m.get('domain')}'")
        if m.get("metric_class") not in METRIC_CLASSES:
            violations.append(f"{mid}: invalid metric_class '{m.get('metric_class')}'")

        # Tolerate malformed action fields without crashing — a non-list is a violation, not a set().
        raw_allowed = m.get("agent_allowed_actions", [])
        raw_forbidden = m.get("agent_forbidden_actions", [])
        allowed = set(raw_allowed) if isinstance(raw_allowed, list) else set()
        forbidden = set(raw_forbidden) if isinstance(raw_forbidden, list) else set()
        bad_allowed = allowed - ALLOWED_ACTIONS
        if bad_allowed:
            violations.append(f"{mid}: allowed actions not in vocabulary: {sorted(bad_allowed)}")
        bad_forbidden = forbidden - DANGEROUS_ACTIONS
        if bad_forbidden:
            violations.append(f"{mid}: forbidden actions not in the dangerous-action vocabulary: {sorted(bad_forbidden)}")
        # The teeth: no metric may GRANT a decisional/irreversible action.
        granted_dangerous = allowed & DANGEROUS_ACTIONS
        if granted_dangerous:
            violations.append(f"{mid}: GRANTS a forbidden decisional action: {sorted(granted_dangerous)}")
        if allowed & forbidden:
            violations.append(f"{mid}: an action is both allowed and forbidden: {sorted(allowed & forbidden)}")
        if not forbidden:
            violations.append(f"{mid}: must explicitly forbid at least one decisional action")
    return violations


def _main(argv) -> int:
    if len(argv) != 2 or argv[0] != "validate":
        print("usage: python3 -m core.metrics validate <registry.json>", file=sys.stderr)
        return 2
    try:
        data = _load_json(argv[1])
        if not isinstance(data, dict):
            raise RegistryError("top-level JSON must be an object")
    except FileNotFoundError:
        print(f"METRIC REGISTRY INVALID — file not found: {argv[1]}", file=sys.stderr)
        return 1
    except (RegistryError, ValueError) as exc:  # ValueError covers JSONDecodeError
        print(f"METRIC REGISTRY INVALID — cannot parse: {exc}", file=sys.stderr)
        return 1
    v = validate(data)
    if v:
        print(f"METRIC REGISTRY INVALID — {len(v)} violation(s):", file=sys.stderr)
        for x in v:
            print(f"  - {x}", file=sys.stderr)
        return 1
    print(f"METRIC REGISTRY OK — {len(data.get('metrics', []))} metrics; no metric grants a decisional action.")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
