# Equity-plan methodology & provenance

The **equity-spend** agent renders the company-wide equity-spend / burn-rate view over synthetic Acme. This
documents what is methodology-faithful, what is illustrative, and how the synthetic data is built.

## Source of truth: one append-only grant ledger
Everything is derived from `foundation/data/acme/equity_grants.csv` — a company-wide, append-only ledger of
5,527 grants (2 plans; the 2019 legacy plan + the 2022 omnibus) with, per grant: participant group
(CEO / Section 16 / management / staff / director), grant type (new-hire / annual refresh / promotion /
exec / director), award type (RSU / option / PSU), a `psu_share_basis` (PSUs are recorded at **target**),
grant-date price and fair value, and the vesting schedule. The engine **fails closed** on any ledger defect —
a grant dated after its holder's termination, a fractional share count, an overdrawn pool, a duplicate
worker or benchmark row, or a non-chronological reporting spine — so a board figure never rests on bad data.

- **SBC expense and the pool roll-forward are DERIVED, not stored** — the engine amortizes each grant
  straight-line over its service period and applies **service-condition forfeitures** off `workers.csv`
  termination dates (unvested cost reverses on termination). There is no hand-maintained derived file that
  could drift out of sync.
- **Forward-compatible with merit-comp.** The grant schema already carries grant_type / award_type /
  participant_group / vesting, so a future merit-comp arm's annual cycle appends refresh / promotion /
  new-hire rows and every board metric updates for free — the ledger is the contract.

## Methodology-faithful (formulas + structures)
- **SBC % of revenue** — GAAP stock-based-comp expense ÷ revenue, quarterly and TTM.
- **Gross / net burn** — shares granted (PSUs at target) ÷ basic weighted-average shares; net removes
  forfeited shares returned to the pool.
- **Value-Adjusted Burn Rate (VABR)** — the **current** ISS Equity-Plan-Scorecard VABR *structure*: options at
  Black-Scholes value, full-value awards at price, over WASO × price; reported as a **3-year average**. This is
  an **illustrative reconstruction**, not the exact ISS number — the price input here is the grant-date /
  period-end close, whereas ISS's current convention uses a stock-price hierarchy led by a **~200-day average**
  (QDD). The structure is faithful; the price input is simplified, so a pass/fail vs the illustrative cap is
  directional, not an ISS determination.
- **Overhang** — (awards outstanding + shares still available to grant) ÷ common shares outstanding — the
  fully-loaded figure. **Dilution** — awards outstanding **only** ÷ common shares outstanding — the narrower
  standard number. The two are reported **distinctly** so a deck can't conflate the larger overhang for the
  smaller dilution.
- **Pool longevity** — shares available ÷ 3-year average net annual grants.
- **EPSC three pillars** — Plan Cost (SVT), Plan Features, Grant Practices (3-yr burn vs a cap).

## Illustrative (labeled; NEVER claimed as ISS or Glass Lewis output)
- **Benchmark burn caps + EPSC pass threshold** (`burn_benchmarks.csv`) — representative of published
  software-industry practice. The engine **refuses to load** any benchmark whose `source_note` does not
  declare itself illustrative (a structural honesty guard).
- **SVT (shareholder value transfer)** — ISS's is a proprietary binomial with company-specific caps; we
  model award value with the same Black-Scholes machinery and render it a **directional** gauge only.
- **The legacy volatility-multiplier burn** — the pre-2023 ISS convention, **retired in the 2023 policy
  year**. Shown only as a diagnostic (labeled "retired 2023") because older board decks still quote it; the
  current metric is the VABR above.

## Calibration (synthetic, believable SaaS ranges)
On the shipped data: SBC ≈ 13% of revenue (maturing off its peak), 3-yr VABR ≈ 2.35% against a ~2.75%
illustrative cap (passes with ~0.4pt headroom), overhang ≈ 14%, pool longevity ≈ 2.8 years (a shareholder
share-request around the 2028 annual meeting), ~$180M unamortized SBC backlog, 6/6 scoreable plan-feature
tests. The market-cap identity (75.0M shares × $85.20 = $6.39B) matches the peer-universe cap for Acme.

## Guardrails
- **No individual names** — the plan is company-wide, keyed to synthetic employee/director ids.
- **Presentation + governance only** — the agent never sizes a share request, recommends a grant, or
  authorizes issuance; the Compensation Committee decides.
- **Deterministic, standard-library, offline** — two runs are byte-identical.
