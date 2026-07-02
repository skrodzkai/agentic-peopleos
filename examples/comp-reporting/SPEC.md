# SPEC — Compensation Reporting agent

## What it does

On a schedule (e.g., monthly, or ahead of a comp cycle), the agent produces the Total
Rewards compensation report for the People leadership team:

1. **Read** a compensation snapshot (here: `data/comp_snapshot.sample.csv`).
2. **Compute** the report deterministically, **citing the canonical metric registry**:
   - a data-derived **"what needs attention"** insight (the Analytics Narrator)
   - headline KPIs (population, average compa-ratio, out-of-band count, out-of-band rate,
     documented exceptions, and the **out-of-band-without-an-exception** count — the
     governance gap)
   - the **compa-ratio distribution** across bands
   - a **by-level** breakdown (population, average compa, out-of-band count)
   - an **out-of-band flag table** (who, role, base, band, direction, whether a documented
     exception exists)
3. **Draft** a short Day-1 digest in plain language.
4. **Stop at the publish gate** — a human reviews and approves before anything is sent.

## Measurement governance (the point of this example)

Every metric is defined **once**, in
[`vault/90-people-analytics/metrics/metrics.registry.json`](../../vault/90-people-analytics/metrics/metrics.registry.json),
and this agent **cites** that definition rather than redefining it. The registry also
records, per metric, what an agent **may** do and what it **must not**:

| Metric | id | Agent may (from the registry) | Agent must NOT |
|---|---|---|---|
| Compa-ratio | `compa_ratio` | `calculate`, `flag_outliers`, `draft_summary` | `recommend_pay_change`, `change_salary` |
| Range penetration | `range_penetration` | `calculate`, `flag_outliers`, `draft_summary` | `recommend_pay_change`, `change_salary` |
| Out-of-band rate | `out_of_band_rate` | `calculate`, `flag_outliers`, `draft_summary` | `recommend_pay_change`, `change_salary` |
| Compensation exception rate | `comp_exception_rate` | `calculate`, `trend`, `flag_outliers`, `draft_summary` | `recommend_pay_change`, `change_salary` |

> These are the exact `agent_allowed_actions` / `agent_forbidden_actions` from the registry — the
> table is not a paraphrase. The agent calculates a single snapshot and flags; it does not trend
> a time series in this example.

So this is not just a glossary — it is a **measurement-governance** boundary the code
enforces. The repo's [`core/metrics.py`](../../core/metrics.py) validator independently
rejects any registry in which a metric grants a dangerous action, and this agent's eval
asserts the agent's output never recommends or changes pay.

## Inputs

`data/comp_snapshot.sample.csv` — one row per employee. Columns: `emp_id, level,
job_family, location, base_salary, range_min, range_mid, range_max, exception_flag`. All
data is synthetic (Acme Corp).

## Data contract

The agent validates every row before computing anything and **fails closed** on any
violation. The contract:

- **Required columns:** all 9 above must be present.
- **`base_salary`, `range_min`, `range_mid`, `range_max`:** positive integers.
- **Band ordering:** strict `range_min < range_mid < range_max` (rules out zero-width bands,
  which would make range penetration undefined).
- **`exception_flag` ∈** `{yes, no}`.
- **`emp_id`** must be unique and non-empty; text fields must be non-empty.

Violations are reported with the offending `emp_id` and field.

## Definitions (cited, not redefined)

- **Compa-ratio** = `base_salary / range_mid`. 1.00 means paid at midpoint.
- **Range penetration** = `(base_salary − range_min) / (range_max − range_min)`, as a
  percent of the band.
- **Out of band** = base below `range_min` or above `range_max`.
- **Unexcepted out-of-band** = out of band **and** `exception_flag = no` — the governance
  gap the report exists to surface.

## Outputs

- `output/report.sample.html` — the branded compensation report (open in a browser); a
  rendered screenshot is committed at `output/report.sample.png`.
- `output/day1-digest.sample.md` — the digest a human reviews.

## The publish gate (human-in-the-loop)

`run.py` produces a **draft** and stops. Distribution is a separate, explicit human step:

```bash
python3 run.py                                          # draft only — nothing is sent
python3 run.py --publish                                # refused: the gate requires a named approver
python3 run.py --publish --approved-by "Total Rewards Partner"   # records who approved
```

There is no email/Slack/send tool in `tools.yaml` by design — the human owns distribution.

> Note: this example uses a simple **named-approver** gate — `--approved-by` records *who*
> approved, not *whether they were entitled*. The full **role-scoped approval registry**
> (entitled pools, channel ACLs, and ledger re-verification) is demonstrated in
> [`examples/visible-handoff`](../visible-handoff/). The scope this report publishes under,
> `publish.comp_summary`, is defined there and routed to the `hr_approver` pool.

## Escalation & fail-closed behavior

- Snapshot missing, empty, or contract-violating → the agent **fails closed**: it writes no
  report, prints a single `FAIL CLOSED: <reason>` line (no stack trace), exits non-zero, and
  renames any prior output to `*.stale` so a failed run can't masquerade as current. It
  never emits a partial report.
- Out-of-band pay **without** a documented exception → highlighted at the top of the digest
  as the first thing for the human to triage. The agent still does not propose a number.

## Where the model fits

The report is pure arithmetic — no model needed (tier-0). In production a small, cheap model
writes the narrative digest from the computed numbers; this example uses a deterministic
template so it runs offline with no API key and zero cost.
