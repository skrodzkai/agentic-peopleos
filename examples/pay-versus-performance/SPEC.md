# SPEC — Pay versus Performance (Compensation Actually Paid, Item 402(v))

## Purpose

Reconstruct the mandatory SEC Pay-versus-Performance disclosure (Reg. S-K Item 402(v)) with
deterministic math, transparent assumptions, and a human approval gate: the five-year table of
Compensation Actually Paid (CAP) versus Total Shareholder Return, peer-group TSR, net income, and a
company-selected measure — anchored by the Summary-Compensation-Table-to-CAP reconciliation bridge.

Registrants subject to Item 402(v) must publish this table (emerging growth companies are exempt;
smaller reporting companies show three covered years, other filers five after transition). CAP is not
a number a company can look up; it is a per-executive equity fair-value roll-forward, which is why it
is usually outsourced to a valuation provider. This example shows the reconstruction working end to
end on synthetic data.

## What CAP Is (Reg. S-K 402(v)(2)(iii))

For each named executive officer and covered fiscal year, Compensation Actually Paid starts from the
Summary Compensation Table (SCT) Total and applies the prescribed equity roll-forward:

- **subtract** the SCT grant-date fair value of stock and option awards reported for the year;
- **add** the year-end fair value of awards granted in the year that are unvested at year-end;
- **add** the change in fair value (year-end vs prior year-end) of prior-year awards unvested at year-end;
- **add** the fair value at vesting of awards granted in the year that vested during the year;
- **add** the change in fair value (vesting date vs prior year-end) of prior-year awards that vested;
- **subtract** the prior-year-end fair value of prior-year awards that were forfeited during the year;
- **add** dividends/dividend-equivalents paid on unvested awards not otherwise reflected;
- **add/subtract** pension adjustments (not applicable to this synthetic issuer — no defined-benefit plan).

The table then pairs PEO CAP and the average of the non-PEO NEOs' CAP with company TSR (a fixed $100
indexed investment), peer-group TSR, net income, and the company-selected measure.

## Fair-Value Re-measurement

[`foundation/compute/pvp.py`](../../foundation/compute/pvp.py) **re-measures** fair values rather than
trusting supplied figures, so the reconciliation is legible:

- Restricted stock units at the subject share price on the measurement date.
- Stock options by Black-Scholes-Merton (`bs_call`) over the **contractual remaining term** at each
  measurement date — an expected-term/exercise-behavior model (the GAAP-typical refinement) is a
  disclosed simplification, not silently implied.
- Relative-TSR market-condition PSUs by the same deterministic Monte Carlo estimator the rTSR PSU arm
  ships (`foundation.compute.rtsr.monte_carlo_valuation`), re-run for the remaining performance period.
  **Once the performance period has closed, the engine requires the committee-certified
  `earned_payout_pct`** and values the award at price × target shares × earned percent — it fails
  closed rather than silently assume a 100%-of-target payout.

Dividends on unvested awards are **year-specific** inputs (`dividends_paid_unvested_by_fy`): each
amount is added exactly once, in the covered year it was paid — including the year an award forfeits,
for dividends paid before the forfeiture. A tranche-level scalar is refused at load.

One committed synthetic stock-price path drives both the executives' equity fair values and the company
TSR column, so the pay side and the performance side of the table reconcile to a single price series.

Important approximation: a real filer's award fair values are produced by its valuation provider under
audited assumptions (expected-term option models, full daily-path averaging, settlement timing,
plan-specific dividend equivalents, performance-to-date locking in the PSU model). The Monte Carlo PSU
re-measurement here values the remaining performance period from the current share price; it is an
illustrative reconstruction of the methodology, not the filed figure.

Scope limitations, enforced rather than fudged: the engine models **one continuous PEO** and refuses a
multi-PEO input (the rule requires separate columns for each person who served as PEO in a covered
year); pension adjustments take the rule's three buckets (service cost, prior service cost, change in
actuarial present value — never a pre-netted number) and are not applicable to this synthetic issuer.

## Inputs

- [`data/awards.sample.json`](data/awards.sample.json) — subject, covered years, the stock-price path,
  the market/rTSR context, and each NEO's SCT rows and equity-award tranches.
- [`data/pvp_financials.sample.json`](data/pvp_financials.sample.json) — per-year peer TSR, net income,
  and the company-selected measure.

All inputs are synthetic.

## Outputs

- `output/report.sample.html`
- `output/day1-digest.sample.md`
- `output/PUBLISHED.json` only after a valid `--publish --approved-by ...` run

## Failure Contract

The agent exits `0` for a successful draft or approved publish, `1` for malformed or unavailable input
(current-looking outputs are marked `.stale`, never left in place), and `2` for a demo publish attempt
without a valid named reviewer label. The reconciliation bridge is self-checked: if the itemized 402(v)
line items do not tie to the reported CAP to the cent, the build fails closed.

## Public-Safety Boundary

The sample contains no real issuer, ticker, stock price, proxy, award, grant, employee, employer, or
vendor report data. The subject is the synthetic Acme Corp (ACMQ) used across the executive-comp arm,
with Q-marked fake tickers; the engine rejects real-ticker collisions through the shared peer-universe
deny-list.

The output is not accounting advice, legal advice, tax advice, investment advice, an auditor-approved
ASC 718 valuation, or the company's filed 402(v) disclosure. The `--approved-by` flag is a demo
named-reviewer marker; production approval would use the role-scoped approval registry and hash-chained
decision ledger demonstrated elsewhere in this repository.
