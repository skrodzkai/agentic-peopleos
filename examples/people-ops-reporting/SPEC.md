# SPEC — People Ops Service Desk reporting agent

## What it does
Weekly/monthly, produces the People Ops service-desk dashboard:
1. **Compute** every routed metric via the shared [`MetricEngine`](../../foundation/compute/engine.py)
   (the agent does no math). SLA attainment, time-to-resolution, breach, and backlog are
   **recomputed by the engine from raw case timestamps** — never trusted precomputed flags.
2. **Render** the service-desk metrics (volume, SLA, TTR p50/p90, reopen, FCR, CSAT, backlog by age,
   category mix) with a data-derived narrator.
3. **List** the broader routed domains honestly as **per-domain coverage** (not yet instrumented).
4. **Stop at the publish gate.**

## Metrics (registry `downstream = people-ops-reporting`) — 17 routed
**Computed now (7, the service desk):** `case_volume` (period-scoped, per-100-FTE, by category),
`sla_attainment` (denominator includes open-and-breached), `time_to_resolution` (median + p90,
wall-clock), `reopen_rate`, `first_contact_resolution`, `case_csat`, `open_case_backlog` (age buckets).
**Data-pending (10), grouped by domain in the coverage section:** `self_service_deflection`
(People Ops — session logs); `recordable_incident_rate`, `lost_time_injury_rate`, `absence_rate`
(Health & Safety — EHS/absence tables); `grievance_rate`, `disciplinary_action_rate`,
`ethics_hotline_cases` (Compliance & Ethics — ER/hotline tables); `training_hours_per_fte`,
`training_completion_rate`, `critical_skill_coverage` (Learning & Development — LMS tables).

The dashboard is **titled "People Ops Service Desk"** so the 7 instrumented metrics read as a
complete, focused report; the 10 routed-but-pending metrics show the intended ISO 30414 breadth as
honest instrumentation coverage, and light up when their source tables are added in a later increment.

## Data contract & fail-closed
Input is the engine. Engine/registry/dataset unavailable, or any declared id returning
`unknown_metric`, → **fail closed** (no report, prior output `.stale`, one clean line, non-zero exit).

## Outputs
`output/report.sample.html` (+ committed `report.sample.png`) and `output/day1-digest.sample.md`,
written atomically.

## Publish gate
`--publish --approved-by "Name"` (validated; rejects control chars) → `PUBLISHED.json`, scope
`publish.people_ops_report`. Named-approver demo; the full role-scoped ledger gate is in
[`visible-handoff`](../visible-handoff/) / [`operating-review`](../operating-review/).

## Where the model fits
None — deterministic arithmetic + templates (tier-0), offline, zero cost.
