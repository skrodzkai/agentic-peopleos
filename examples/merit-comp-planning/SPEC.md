# SPEC — merit-comp-planning agent

## Purpose
Render the annual merit / comp-cycle plan for a Compensation Committee. Presentation + governance only; the
allocation math lives in `foundation/compute/merit_comp.py`.

## Input
`merit_comp.compute()` — derived from `foundation/data/acme/workers.csv` (active employees: base_salary,
band_id, level, rating, promoted_this_period) + `comp_bands.csv` (range min/mid/max). "Active" **includes
protected leave** — matching the People Analytics headcount definition (`foundation/compute/engine.py`) — and
the active/on-leave split is disclosed. No other input; no external calls.

## Output (deterministic; atomic writes)
- `output/report.sample.html` — the dark committee dashboard.
- `output/day1-digest.sample.md` — the one-page cycle digest.
- `output/equity_refresh_grants.sample.csv` — the appendable equity-ledger grant delta (the real handoff).
- `output/report.sample.png` — an illustrative render (not byte-gated).
- `output/PUBLISHED.json` — written only on `--publish --approved-by "<name>"`.

## What it shows
Cycle headline + verdict; KPI band (merit spend %, bonus pool, promotions, equity-refresher value,
compa-ratio shift); the **merit matrix** (increase % by rating × compa-ratio quartile, heat-mapped); merit
spend by rating (differentiation); and a guardrails-and-handoff tile (budget conformance, employees above
band-max after merit, and the refresher-shares count emitted in the equity-ledger schema).

## The equity handoff
Equity refreshers are emitted by the engine as grant rows in the **same schema as `equity_grants.csv`**
(`grant_type=annual_refresh`, RSU), preserving each holder's existing participant group (a CEO/Section 16
officer keeps that group, so the board dashboard's exec-vs-management split stays intact). The agent writes
them to `output/equity_refresh_grants.sample.csv` — a real appendable delta. As FY2026 grants they carry into
the **next** period's board equity metrics (burn, SBC, overhang), not the current close. The engine test does
the REAL handshake: it appends the rows to a copy of the live ledger and runs `equity_spend.compute` over it,
so a value-level schema change in the equity arm trips the test — not just a column-name check.

## Invariants (fail closed on violation)
- Every headline figure is finite; merit spend ≤ the merit pool and reconciles to the reported headroom;
  merit spend % ≤ budget %; the average compa-ratio *rises* after merit (moves toward market); the merit
  matrix carries all four rating tiers, each with all four quartiles, every cell finite in [0, 0.5), and is
  monotone non-increasing as compa-ratio rises.
- Publish requires a named approver matching a strict charset; a control-char approver is refused (rc 2). The
  publish gate writes a **local** `PUBLISHED.json` marker — a named-approver acknowledgment, NOT the
  registry-backed approval the decision-ledger agents enforce (no governance claim beyond "marked published locally").
- Two runs are byte-identical (determinism); a failed/refused run stales any prior published output.

## Explicitly out of scope (never)
Setting or approving an individual increase / bonus / promotion / band placement; authorizing pay; naming an
individual; claiming the illustrative matrix/budget is company policy; any external send.
