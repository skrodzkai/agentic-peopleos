# SPEC — rTSR PSU Valuation

## Purpose

Demonstrate how Agentic PeopleOS can support executive compensation workflows with deterministic
math, transparent assumptions, and human approval gates.

## Public-Style Plan Mechanics

The sample mirrors common public software-company rTSR PSU mechanics:

- Performance period: three fiscal years.
- Comparator rule: companies in a software/services-style index at both the beginning and end of the
  performance period.
- TSR formula: `(ending 30-day simple average closing price + dividends paid during period) /
  beginning 30-day simple average closing price`.
- Payout curve: 25th percentile = 50%, 55th percentile = 100%, 75th percentile and above = 200%.
- Interpolation: linear between stated points.
- rTSR percentile precision: reported to two decimals.
- Committee tracker views: synthetic payout history against the 100% target line, and a ranked peer
  TSR distribution with the issuer highlighted.

Public source pattern: SEC-filed proxy disclosures for software companies that use relative-TSR
market-condition PSUs. This repository does not embed or depend on any issuer-specific filing,
vendor report, market feed, or licensed index constituent history.

## Valuation Method

[`foundation/compute/rtsr.py`](../../foundation/compute/rtsr.py) estimates fair value with a
deterministic Monte Carlo model:

- Risk-neutral geometric Brownian motion.
- Caller-supplied spot prices, volatilities, dividend yields, risk-free rate, correlations, path
  count, seed, and performance period.
- Correlated terminal prices via Cholesky decomposition.
- Per-path rTSR percentile, payout percentage, and stock-settled payoff.
- Discounted expected payoff per target share.
- Monte Carlo standard error and a 95% MC confidence interval for the fair-value estimate.

This implementation is educational decision support. Accounting classification, award terms,
dividend equivalents, service conditions, forfeitures, market-data source, and auditor-approved
assumptions remain outside the example.

Important approximations:

- The actual performance tracker uses beginning/end 30-day average price observations supplied in
  `prices.sample.json`.
- The Monte Carlo estimator ranks total return using terminal prices plus a continuous dividend-yield
  approximation. It does not simulate every daily price needed to value a future 30-day averaging
  window.
- The estimator does not model vesting after the performance period, settlement lag, award-specific
  dividend equivalents, forfeitures, or accounting-policy elections.

## Inputs

- [`data/plan_terms.sample.json`](data/plan_terms.sample.json)
- [`data/companies.sample.json`](data/companies.sample.json)
- [`data/prices.sample.json`](data/prices.sample.json)
- [`data/payout_history.sample.json`](data/payout_history.sample.json)
- [`data/valuation_assumptions.sample.json`](data/valuation_assumptions.sample.json)

All inputs are synthetic.

## Outputs

- `output/report.sample.html`
- `output/day1-digest.sample.md`
- `output/PUBLISHED.json` only after a valid `--publish --approved-by ...` run

## Failure Contract

The agent exits:

- `0` for a successful draft or approved publish.
- `1` for malformed or unavailable input; current-looking outputs are not left in place.
- `2` for demo publish attempts without a valid named reviewer label.

## Public-Safety Boundary

The sample contains no real issuer, ticker, peer set, index constituent snapshot, award, grant,
stock price, volatility, correlation matrix, employee, employer, or vendor report data. The sample
uses the same synthetic Acme Corp subject as the peer-group builder and Q-marked fake tickers; the
engine rejects known real-ticker collisions through the shared peer-universe deny-list.

The output is not accounting advice, legal advice, tax advice, investment advice, or an
auditor-approved ASC 718 valuation.

The `--approved-by` flag is a demo named-reviewer marker. Production approval would use the
role-scoped approval registry and hash-chained decision ledger demonstrated elsewhere in the repo.
