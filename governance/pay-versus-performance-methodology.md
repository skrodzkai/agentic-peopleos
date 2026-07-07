# Pay-versus-Performance / Compensation Actually Paid — methodology & provenance

The **pay-versus-performance** agent renders the SEC Item 402(v) disclosure over synthetic Acme: the
Compensation Actually Paid (CAP) reconciliation, the five-year Pay-versus-Performance table, and the
required CAP-versus-performance relationship views. Unlike the proxy-advisor arms, the **methodology here
is entirely public** — it is the text of a federal regulation — so this note documents what is faithful
to the rule, what is a modeling simplification, and where the synthetic data stands in for a filer's
audited figures.

> **Not accounting, legal, tax, or investment advice, and not a filed disclosure.** Nothing here is any
> company's 402(v) disclosure or an auditor-approved ASC 718 valuation. It is a neutral reconstruction of
> the disclosure methodology over a synthetic universe, for demonstration only.

## The rule (public)

SEC Reg. S-K Item 402(v), effective for proxy statements covering fiscal years ending on or after
December 16, 2022, requires every registrant (other than emerging growth companies) to publish a
**Pay-versus-Performance table**. A smaller reporting company shows three covered years; other filers
phase in to **five**. The columns are: the Principal Executive Officer's Summary Compensation Table (SCT)
total and **Compensation Actually Paid (CAP)**; the average SCT total and average CAP of the remaining
named executive officers; the value of a fixed **$100 invested measured by Total Shareholder Return**;
the same for a **peer-group TSR**; **net income**; and a **company-selected measure** (the financial
measure the registrant judges most linked to CAP). The filer also lists three-to-seven "most important"
financial measures and describes the **relationships** between CAP and each of company TSR, net income,
and the company-selected measure. (See the SEC final rule, Release 34-95607, and the SEC small-entity
compliance guide.)

## Compensation Actually Paid — the roll-forward (public)

CAP is defined in Reg. S-K 402(v)(2)(iii). Starting from the SCT total for a named executive officer and
covered year, the equity adjustment is:

| Step | 402(v)(2)(iii) requirement | Sign |
|---|---|---|
| Grant-date fair value of stock + option awards **reported in the SCT** for the year | remove reported grant-date value | **−** |
| Year-end fair value of awards **granted in the year** and unvested at year-end | add | **+** |
| **Change** in fair value (year-end vs prior year-end) of **prior-year awards unvested** at year-end | add (signed) | **±** |
| Fair value at vesting of awards **granted in the year that vested** during the year | add | **+** |
| **Change** in fair value (vesting date vs prior year-end) of **prior-year awards that vested** | add (signed) | **±** |
| Prior-year-end fair value of **prior-year awards forfeited** during the year | subtract | **−** |
| Dividends / dividend-equivalents paid on unvested awards not otherwise reflected | add | **+** |
| Pension: subtract reported change in actuarial present value; add service cost + prior-service cost | add (signed) | **±** |

`foundation/compute/pvp.py` implements exactly these buckets in `cap_for_neo_year`, and the itemized
bridge is **self-checked to tie to the reported CAP** or the build fails closed. The
`foundation/compute/tests/test_pvp.py` suite verifies every bucket against hand-computed values on a
restricted-stock-only panel (where fair value is price × shares, so the arithmetic is exact and the
implementation is not its own oracle).

## The honesty ledger (component by component)

| Component | Status | What that means |
|---|---|---|
| The **table columns**, the **CAP roll-forward** line items, the three **relationship** disclosures, and the **$100-indexed TSR** convention | **PUBLIC** | These are the regulation. The reconstruction follows the rule's structure line for line. |
| **Award fair values** at each measurement date | **ILLUSTRATIVE** | A filer obtains these from its valuation provider under audited assumptions. This engine **re-measures** them from transparent inputs: RSUs at the share price; options by Black-Scholes-Merton re-struck to the remaining term; relative-TSR market-condition PSUs by the shared Monte Carlo estimator, valued over the **remaining performance period from the current share price**. That PSU approach does not lock in path-to-date relative performance the way a full valuation model would — a labeled simplification, not the filed figure. |
| The subject **stock-price path**, **peer-group TSR**, **net income**, and **company-selected measure** | **ILLUSTRATIVE / SYNTHETIC** | All synthetic. The subject price path is committed and drives **both** the executives' equity fair values and the company TSR column, so the pay and performance sides reconcile to one price series. |
| **Pension adjustments** | **NOT APPLICABLE** | The synthetic issuer has no defined-benefit plan, so the pension term is zero. The engine supports a per-NEO pension adjustment for completeness. |
| The **directional "pay-for-performance aligned" read** | **ILLUSTRATIVE** | A legibility signal comparing the first-to-last direction of PEO CAP and company TSR. It is **never** a say-on-pay vote forecast or a proxy-advisor concern level. |

## Why this matters as a control

CAP is the single most error-prone, highest-value figure in the executive-pay disclosure, and it is the
number a compensation committee cannot reconstruct without specialist help. Doing it in transparent,
deterministic, self-checking code — where the bridge must tie to the cent or the build fails — turns an
opaque, outsourced calculation into an auditable one. It reads on the **same synthetic subject and price
path** as the [rTSR PSU valuation arm](../examples/rtsr-psu-valuation/), and it reuses that arm's Monte
Carlo estimator, so the two together cover the two hardest quantitative pieces of a modern proxy: the
market-condition PSU fair value and the CAP reconciliation.

## Regulatory note

This reflects Item 402(v) as in force in mid-2026. The SEC's May 2026 rule proposal contemplates scaling
back several executive-pay disclosures for smaller registrants while leaving them in place for large
accelerated filers; confirm the current requirement with counsel before relying on any of this. Not legal
advice.
