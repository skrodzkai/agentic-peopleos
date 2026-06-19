# SPEC — TA Reporting agent

## What it does

On a schedule (e.g., every Monday), the agent produces the Talent Acquisition operating
report for the People leadership team:

1. **Read** open requisitions from the ATS (here: `data/requisitions.sample.csv`).
2. **Compute** the report deterministically:
   - a data-derived **"what needs attention"** insight that ties the metrics to a business
     question (the Analytics Narrator)
   - headline KPIs (open reqs, on-hold, avg days open, reqs at risk, median pipeline)
   - pipeline stage mix, requisition age bands, department mix, country distribution
   - a per-recruiter scorecard (load, avg days open, at-risk count, pipeline depth)
   - **risk flags** and an **update-recency watchlist**
3. **Draft** a short Day-1 digest in plain language.
4. **Stop at the publish gate** — a human reviews and approves before anything is sent.

## Inputs

`data/requisitions.sample.csv` — one row per requisition. Columns: `req_id, title,
department, location, country, opened_date, stage, recruiter, hiring_manager, pipeline,
last_update, priority, status`. All data is synthetic (Acme Corp).

## Data contract

The agent validates every row before computing anything and **fails closed** on any
violation (see below). The contract:

- **Required columns:** all 13 above must be present.
- **`pipeline`:** a non-negative integer.
- **`opened_date`, `last_update`:** `YYYY-MM-DD`; `last_update` not before `opened_date`.
- **`status` ∈** `{open, on-hold, filled, closed}`.
- **`priority` ∈** `{P1, P2, P3}`.
- **`stage` ∈** `{Sourcing, Screen, Onsite, Offer}`.
- **Text fields** must be non-empty.

Violations are reported with the offending `req_id` and field.

## Outputs

- `output/report.sample.html` — the branded operating report (open in a browser);
  a rendered screenshot is committed at `output/report.sample.png`.
- `output/day1-digest.sample.md` — the digest a human reviews.

## Risk-flag rules (operating policy)

| Flag | Condition |
|---|---|
| `AGING` | open more than **90** days |
| `STALE` | not updated in more than **14** days |
| `THIN_PIPELINE` | priority `P1` with fewer than **3** candidates |

On-hold reqs are excluded from flags and KPIs.

## The publish gate (human-in-the-loop)

`run.py` produces a **draft** and stops. Distribution is a separate, explicit human step:

```bash
python run.py                              # draft only — nothing is sent
python run.py --publish                    # refused: the gate requires a named approver
python run.py --publish --approved-by "Dana Lopez"   # records who approved
```

There is no email/Slack/send tool in `tools.yaml` by design — the human owns distribution.

## Escalation & fail-closed behavior

- Source data missing, empty, or contract-violating → the agent **fails closed**: it writes
  no report, prints a single `FAIL CLOSED: <reason>` line (no stack trace), exits non-zero,
  and renames any prior output to `*.stale` so a failed run can't masquerade as current. It
  never emits a partial report.
- A requisition trips two or more risk flags → highlighted at the top of the digest for the
  human to triage.

## Where the model fits

The report is pure arithmetic — no model needed (tier-0). In production a small, cheap
model writes the narrative digest from the computed numbers; this example uses a
deterministic template so it runs offline with no API key and zero cost.
