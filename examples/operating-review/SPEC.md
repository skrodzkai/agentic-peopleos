# SPEC — Monthly People Operating Review composer

## What it does
Monthly, composes the cross-domain People Operating Review for People leadership:
1. **Compose** headline KPIs from every domain via the shared
   [`MetricEngine`](../../foundation/compute/engine.py) — no math, no agent re-implementation.
2. Add a **consolidated "what needs attention"** (top signals across workforce, attrition, People Ops)
   and an honest **instrumentation-coverage map** (computed vs defined metrics per domain).
3. **Publish only behind the full role-scoped, ledger-backed approval gate.**

> **Curated, not registry-routed.** `operating-review` has **no** `downstream` metrics in the registry.
> The headline set is an explicit module constant `OPERATING_REVIEW_METRICS` (composer-selected
> executive view), all of which are computable today.

## Headline metrics (curated)
Workforce: `headcount`, `fte`, `net_headcount_growth`, `span_of_control`, `contingent_workforce_ratio`.
Attrition: `voluntary_attrition`, `regrettable_attrition`, `total_turnover_rate`, `twelve_month_retention`.
People Ops: `case_volume`, `sla_attainment`, `time_to_resolution`, `open_case_backlog`.
Diversity: `leadership_diversity`. Plus a coverage map over **all 11 domains**.

## The publish gate — FULL registry/ledger (the showpiece)
Unlike the leaf reporting agents (named-approver demo), this agent wires the real gate:

```bash
python3 run.py                                               # draft only — nothing sent
python3 run.py --publish --approved-by hr.business-partner   # entitled human → approved + published
python3 run.py --publish --approved-by obs.engineering       # NOT entitled → denied + escalation (refused)
```

- `--approved-by` is an **actor id**, adjudicated by the [`ApprovalRegistry`](../../core/approval_registry.py).
- The decision is recorded in a **hash-chained ledger** ([`core/event_log.py`](../../core/event_log.py))
  as recommendation → approval → action, then **re-verified** (`validate_log(..., registry=…)`):
  entitlement to scope `publish.operating_review`, channel ACL, and the point-in-time `registry_version`.
- An entitled approver (the `hr_approver` pool) → the gated action is recorded and the review is
  published (decision ledger at `output/decision.sample.events.jsonl`). A non-entitled actor (or an
  unknown actor) → **denied + escalation, exit 2, nothing distributed**. The ledger **validates in both
  outcomes** — it just records an escalation instead of an action.
- If the ledger fails verification for any reason, the agent **fails closed** and refuses to publish.

## Data contract & fail-closed
Input is the engine. Engine/registry/dataset unavailable, or any headline metric not `ok`, → **fail
closed** (no report, one clean line, non-zero exit). Writes are atomic.

## Outputs
`output/report.sample.html` (+ committed `report.sample.png`), `output/day1-digest.sample.md`, and —
when published — `output/decision.sample.events.jsonl` (the ledger-verified approval).

## Where the model fits
None — deterministic composition + templates (tier-0), offline, zero cost.
