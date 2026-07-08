# SPEC — sbc-forecasting agent

## Purpose
Render the forward SBC-expense forecast for a Finance / Total-Rewards audience. Presentation + governance
only; the math lives in `foundation/compute/sbc_forecast.py`.

## Input
`sbc_forecast.compute()` — derived from the append-only grant ledger (`equity_grants.csv`) plus `workers.csv`
(term dates), `shares_outstanding.csv`, and `financials.csv`. No other input; no external calls. The forecast
anchors at the fiscal close (the last shares/financials `period_end`).

## Output (deterministic; atomic writes)
- `output/report.sample.html` — the dark SBC-forecast dashboard.
- `output/day1-digest.sample.md` — the one-page digest.
- `output/report.sample.png` — an illustrative render (not byte-gated).
- `output/PUBLISHED.json` — written only on `--publish --approved-by "<name>"`.

## What it shows
- **Locked-in SBC runoff** — the future recognition of grants already outstanding, straight-line by fiscal
  year, with an illustrative forfeiture-rate-adjusted column; ties exactly to the equity-spend backlog.
- **Total go-forward forecast** — locked-in plus an illustrative steady-state new-grant layer, in dollars and
  as a % of (flat) revenue.
- **Assumptions + reconciliation** — the illustrative forfeiture rate / run-rate / revenue basis, and the
  backlog reconciliation to the equity-spend arm.

## Invariants (fail closed on violation)
- Every rendered figure is finite; no fiscal year recognizes a negative expense.
- The gross fiscal-year runoff (+ any disclosed beyond-horizon tail) sums back to the backlog; the cumulative
  is monotonic; each forfeiture-adjusted figure is <= its gross figure.
- Each total = locked-in + new-grant overlay; the illustrative assumptions are in range.
- Publish requires a named approver matching a strict charset; a control-char approver is refused (rc 2).
- Two runs are byte-identical (determinism); a failed/refused run stales any prior published output.

## Explicitly out of scope (never)
Issuing guidance; sizing/recommending/authorizing a grant, pool, or accrual; presenting an illustrative
assumption as fact; naming an individual; any external send.
