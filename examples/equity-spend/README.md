# equity-spend — company-wide equity spend & burn (board view)

The Executive-Compensation arm's **board equity deliverable**: the quarterly view a VP of Total Rewards
takes to the CFO/CEO/board. It renders — from a company-wide grant ledger — **SBC as a share of revenue**,
gross/net burn and the current ISS Equity-Plan-Scorecard **Value-Adjusted Burn Rate** vs an illustrative
industry cap, **overhang / dilution**, **pool longevity** (when the next shareholder share-request lands),
the locked-in **SBC backlog**, and **where the equity goes** — executives through broad-based staff.

```bash
python3 run.py                                              # draft dashboard + digest (nothing sent)
python3 run.py --publish --approved-by "Compensation Committee Chair"
python3 evals/test_equity_spend_agent.py                    # agent evals
python3 ../../foundation/compute/tests/test_equity_spend.py # engine tests
```

**Why it matters.** This is the artifact where Total Rewards meets the board's language: is the equity spend
sustainable (SBC % of revenue, trend), and is the plan defensible to proxy advisors (3-yr burn vs the EPSC
cap, plan features, overhang) before we ask shareholders for more shares. The company-wide grant ledger is
also the contract a future merit-comp arm writes its annual refresh/promotion/new-hire grants into — so the
board metrics update for free.

**Honesty.** Benchmark caps, EPSC weights, and the SVT valuation are **illustrative** — representative of
published software practice, **not** ISS or Glass Lewis output. Plan-feature tests are scored exactly from
the plan facts. The pre-2023 volatility-multiplier burn is shown only as a labeled diagnostic (ISS retired
it for the Value-Adjusted Burn Rate in 2023). Synthetic company-wide data; presentation + governance only —
the agent never sizes a share request or recommends a grant. Provenance:
[`governance/equity-plan-methodology.md`](../../governance/equity-plan-methodology.md).

Part of the [Agentic PeopleOS](../../README.md) Executive Compensation arm.
