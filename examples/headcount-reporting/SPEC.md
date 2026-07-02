# SPEC — Headcount & Workforce reporting agent

## What it does

On a schedule (e.g., monthly), produces the workforce operating dashboard for People leadership:

1. **Compute** every metric in its registry-declared set via the shared
   [`MetricEngine`](../../foundation/compute/engine.py) — the agent does no math.
2. **Render** the computable metrics into a dark operating dashboard (KPIs, management-layer
   distribution, span-of-control detail, the headcount bridge, representation by level, leadership
   diversity), with a data-derived **"what needs attention"** narrator.
3. **List** the not-yet-instrumented metrics honestly in a **coverage** section (named source needs).
4. **Draft** a Day-1 digest; **stop at the publish gate**.

## The engine is the single source of math

The agent calls `engine.compute(metric_id)` and renders the result. It never re-implements a metric.
This is the design invariant: reporting agents are **presentation + governance** over one
registry-bound, read-only engine, so every report shows the same numbers and no agent can quietly
bend a definition.

## Metrics (registry `downstream = headcount-reporting`) — 14 total

**Computed now (9):** `headcount`, `fte`, `net_headcount_growth` (with a reconciling bridge),
`span_of_control` (mean/median/max), `span_outlier_rate`, `management_layers` (depth distribution),
`contingent_workforce_ratio`, `representation_by_level`, `leadership_diversity`.

**Data-pending (5)** — defined in the registry, source table not modeled yet; shown in the coverage
section, never estimated: `vacancy_rate` (approved-positions), `headcount_plan_attainment` (plan
table), `succession_coverage` + `successor_readiness` (succession table), `adverse_impact_ratio`
(selection-decision events).

## Data contract & fail-closed

The "input" is the engine. If the engine, registry, or dataset is unavailable — or any declared
metric returns `unknown_metric` (agent/registry drift) — the agent **fails closed**: writes no
report, renames any prior output `.stale`, prints a single `FAIL CLOSED: <reason>` line (no stack
trace), and exits non-zero.

## Outputs

- `output/report.sample.html` — the branded dashboard (a rendered screenshot is committed at
  `output/report.sample.png`).
- `output/day1-digest.sample.md` — the digest a human reviews.

Both are written **atomically** (temp + `os.replace`) so a crash never leaves a partial report.

## The publish gate (human-in-the-loop)

```bash
python3 run.py                                          # draft only — nothing sent
python3 run.py --publish                                # refused: needs a valid named approver
python3 run.py --publish --approved-by "People Analytics Lead"   # records the approval (PUBLISHED.json)
```

`--approved-by` is validated (rejects control characters / injection); the approval is recorded as
structured JSON (`PUBLISHED.json`), scope `publish.headcount_report`. This example uses a simple
**named-approver** gate; the full **role-scoped, ledger-backed** approval gate (entitled pools,
channel ACL, point-in-time registry version) is demonstrated in
[`examples/visible-handoff`](../visible-handoff/) and is wired into the cross-domain
[`operating-review`](../operating-review/).

## Where the model fits

None. The dashboard and narrator are pure arithmetic + templates (tier-0) — deterministic and
offline, with no API key and zero cost.
