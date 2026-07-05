# merit-comp-planning — annual merit / comp-cycle plan (committee view)

The Total-Rewards arm's **comp-cycle deliverable**: the plan a VP of Total Rewards takes into the annual
planning committee. It renders — from the company-wide workforce + comp bands — the **merit-increase budget**
allocated through a performance × compa-ratio matrix, the **bonus pool** (target × attainment × individual
factor), **promotion** increases, and **equity refreshers** emitted as append-valid rows in the equity-ledger
schema (written to `output/equity_refresh_grants.sample.csv`). Every number comes from
`foundation/compute/merit_comp.py`; the agent renders and governs — it does no math and it authorizes no pay.

```bash
python3 run.py                                                  # draft dashboard + digest (nothing sent)
python3 run.py --publish --approved-by "Compensation Committee Chair"
python3 evals/test_merit_comp_agent.py                          # agent evals
python3 ../../foundation/compute/tests/test_merit_comp.py       # engine + equity-handoff tests
```

**Why it matters.** The comp cycle is where Total Rewards meets the budget: can we fund a competitive merit
increase, differentiate it toward strong performers, keep people moving toward market, and stay inside the
pool — all while the bonus and equity commitments hold? The dashboard shows the **merit matrix** (the artifact
every comp leader argues over), the spend differentiation across ratings, budget conformance, and the
employees who'd exceed band-max after merit. And it closes the loop with the exec-comp arm: the **equity
refreshers are emitted as append-valid grant rows in the equity ledger's schema** — preserving each holder's
existing participant group (a CEO's refresher stays `ceo`, not `management`) and written as a real
appendable CSV. As FY2026 grants they carry into the **next** period's board burn / SBC / overhang; the ledger
schema is the contract between the two arms, and the engine test proves the rows append + validate against the
live equity engine.

**Honesty.** The merit matrix, bonus targets, company attainment, refresh grid, and the merit budget are
**illustrative** placeholders — a real cycle calibrates them to the plan. Synthetic company-wide workforce;
presentation + governance only — the agent never sets an increase, sizes a bonus, or approves a promotion.

Part of the [Agentic PeopleOS](../../README.md) Total Rewards arm.
