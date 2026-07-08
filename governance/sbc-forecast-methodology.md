# SBC expense forecast — methodology

The sbc-forecasting arm (`foundation/compute/sbc_forecast.py` + `examples/sbc-forecasting/`) projects, on
synthetic data, the forward stock-based-compensation (SBC) expense a company must guide on. It is deliberately
built to separate the **certain** (amortization of grants already made) from the **assumed** (future grants,
future forfeitures, revenue). This note states exactly which is which.

## 1. Source of truth
Everything is derived from the committed data:
- **Grant ledger** (`equity_grants.csv`) — the append-only record of every grant: shares, grant-date fair
  value per share, vest-start, vesting months, cliff. This is the single source of the SBC to amortize.
- **Term dates** (`workers.csv`) — for service-condition forfeitures (a holder terminated before the anchor
  has already forfeited unvested cost).
- **Shares / financials** (`shares_outstanding.csv`, `financials.csv`) — the fiscal-close anchor date, the
  market-cap context, and the trailing-twelve-months revenue used for the % context.

The forecast **anchors at the fiscal close** — the last shares/financials `period_end` — so every forecast
fiscal year is a full year and the period-0 backlog is measured on the same books-close date the equity-spend
arm uses.

## 2. Methodology-faithful (assumption-free)
- **Locked-in runoff.** Each outstanding grant's remaining cost = grant-date fair value × (remaining service
  months ÷ total vesting months), recognized **straight-line** over the requisite service period — the same
  amortization convention as `foundation/compute/equity_spend.py`, down to the whole-month day-of-month
  convention. Split across future fiscal years, the gross runoff **sums back exactly to the backlog**.
- **Backlog reconciliation.** The period-0 locked-in backlog equals `equity_spend.compute()["unamortized_sbc"]`
  **to the cent**, and the weighted-average remaining vesting years match. A drift is a bug and fails closed
  (enforced in the engine test and the agent's `build_report`).
- **Forecast-time knowledge.** The gross runoff assumes full vesting — a forecast made at the close cannot
  know a future termination, so it does not net one out. (56 grants in the synthetic book belong to holders
  who terminate just after the close; the gross forecast correctly ignores those unknowable events, and the
  forfeiture-rate overlay is where estimated future forfeitures live.)

## 3. Illustrative (labeled, never guidance)
- **Forfeiture rate.** An estimated annual forfeiture rate (6%, a constant) haircuts each future fiscal year's
  gross expense by `(1 - rate)^k`. GAAP (ASU 2016-09) permits estimating forfeitures rather than accounting
  for them as they occur; a forward forecast has no future actuals.
- **New-grant overlay.** A steady-state assumption that the company keeps granting at its trailing-twelve-
  months grant-date-fair-value run-rate, each vintage amortized straight-line over 48 months. This is the
  speculative layer that turns a declining runoff into a total run-rate.
- **Revenue basis.** The % -of-revenue context holds revenue flat at the last trailing-twelve-months figure —
  it is a scale reference, not a revenue forecast.

## 4. Calibration (synthetic Acme, fiscal close 2025-12-31)
- Locked-in backlog ≈ **$179.8M**, weighted-average remaining vesting ≈ **2.4 years**, reconciling exactly to
  the equity-spend arm.
- Runoff: FY2026 ≈ $85.9M → FY2027 ≈ $58.3M → FY2028 ≈ $30.8M → FY2029 ≈ $4.8M, complete by **FY2029**.
- Total forecast (locked-in + illustrative $127.2M/yr run-rate, 6% forfeiture): ≈ $117.7M in FY2026 rising
  toward ≈ $131M by FY2029 as the new-grant layer ramps — roughly flat as a share of (flat) revenue (~14-15%).

## 5. Guardrails (what the arm never does)
- Never issues financial guidance or presents the forecast as a filed/approved number.
- Never presents the forfeiture rate, new-grant run-rate, or flat-revenue basis as fact.
- Never sizes, recommends, or authorises a grant, pool, or accrual.
- Never emits an individual's name or a per-grant id; the forecast is company-wide on synthetic ids.
- Never distributes without a named human approver (the publish gate).

## References
- ASC 718 (share-based payment) — grant-date-fair-value measurement and straight-line attribution over the
  requisite service period.
- ASU 2016-09 — the accounting-policy election to estimate forfeitures or recognise them as they occur.
