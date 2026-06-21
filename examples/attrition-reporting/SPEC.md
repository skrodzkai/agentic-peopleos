# SPEC — Attrition & Retention reporting agent

## What it does
Monthly, produces the attrition/retention operating dashboard for People leadership:
1. **Compute** every registry-declared attrition metric via the shared
   [`MetricEngine`](../../foundation/compute/engine.py) — the agent does no math.
2. **Render** annualized turnover (voluntary, regrettable, total, involuntary), first-year and
   90-day attrition, 12-month retention, and **voluntary-attrition segment hotspots** (by level and
   location, from `engine.segment(...)`), with a data-derived narrator.
3. **List** the not-yet-instrumented mobility metrics honestly. **Stop at the publish gate.**

## Metrics (registry `downstream = attrition-reporting`) — 9 total
**Computed now (7):** `voluntary_attrition`, `regrettable_attrition`, `total_turnover_rate`,
`involuntary_turnover_rate`, `new_hire_attrition`, `early_attrition_90d`, `twelve_month_retention`.
**Data-pending (2):** `internal_mobility_rate` (internal move events), `internal_fill_rate`
(vacancy fill events) — shown in the coverage section, never estimated.

## Annualization (stated on the dashboard)
All turnover is **annualized by simple ×(12/months)** over the trailing 12 months, with an
**average-headcount denominator** (mean of monthly actives) — no compounding. Stated explicitly so
the numbers are defensible; the engine enforces it.

## Data contract & fail-closed
The "input" is the engine. Engine/registry/dataset unavailable, or any declared id returning
`unknown_metric`, → **fail closed** (no report, prior output `.stale`, one clean line, non-zero exit).

## Outputs
`output/report.sample.html` (+ committed `report.sample.png`) and `output/day1-digest.sample.md`,
written atomically.

## Publish gate
`--publish --approved-by "Name"` (validated; rejects control chars), recorded as `PUBLISHED.json`,
scope `publish.attrition_report`. Named-approver demo; the full role-scoped ledger gate is in
[`visible-handoff`](../visible-handoff/) / [`operating-review`](../operating-review/).

## Where the model fits
None — deterministic arithmetic + templates (tier-0), offline, zero cost.
