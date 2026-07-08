# Pay versus Performance — Compensation Actually Paid (Item 402(v))

The mandatory SEC Pay-versus-Performance disclosure, reconstructed on synthetic data: the five-year
table of **Compensation Actually Paid (CAP)** versus Total Shareholder Return, peer-group TSR, net
income, and a company-selected measure — led by the **Summary-Compensation-Table-to-CAP reconciliation
bridge** that turns reported pay into the value actually delivered.

CAP is the number a company can't just look up: it is a per-executive equity fair-value roll-forward
prescribed by Reg. S-K 402(v)(2)(iii), which is why filers usually outsource it to a valuation provider.
This arm computes it end to end and shows every line.

```bash
cd examples/pay-versus-performance && python3 run.py
python3 run.py --publish --approved-by "Compensation Committee Chair"
```

## What it shows

- **CAP reconciliation bridge** — SCT Total → CAP for the PEO, every 402(v)(2)(iii) line item, with the
  full itemized ledger beside the waterfall. The build fails closed if the bridge does not tie to CAP.
- **The 402(v) table** — five covered fiscal years: PEO SCT + CAP, average non-PEO SCT + CAP, company
  TSR ($100 indexed), peer TSR, net income, and the company-selected measure.
- **The required CAP relationship views** — both CAP columns the rule requires (PEO CAP and the
  average non-PEO CAP) against company TSR, against net income, and against the company-selected
  measure; the company-versus-peer-TSR comparison the rule also calls for is carried in the table's
  two indexed TSR columns.

## How it works

- All valuation math lives in [`foundation/compute/pvp.py`](../../foundation/compute/pvp.py); the agent
  reads from the engine, renders, and governs. It authorizes no pay and recommends nothing.
- Fair values are **re-measured**, not assumed: restricted stock at the share price, options by
  contractual-remaining-term Black-Scholes (a disclosed simplification), and relative-TSR PSUs by the
  same deterministic Monte Carlo estimator the [rTSR PSU arm](../rtsr-psu-valuation/) ships. A PSU
  whose performance period has closed **requires the committee-certified earned payout percent** —
  the engine refuses to assume a target payout.
- One committed synthetic stock-price path drives **both** the executives' equity fair values and the
  company TSR column — the pay side and the performance side reconcile to a single price series.
- Deterministic and offline: same inputs → identical bytes, so the committed dashboard is byte-diffed in
  CI. Synthetic Acme (ACMQ) subject; no real issuer, award, price, employee, or employer data.

See [`SPEC.md`](SPEC.md) for the full methodology and the
[Pay-versus-Performance methodology note](../../governance/pay-versus-performance-methodology.md) for the
public-vs-illustrative boundaries. This is an illustrative reconstruction of the disclosure methodology —
not accounting/legal/investment advice, an auditor-approved ASC 718 valuation, or a company's filed
402(v) disclosure.
