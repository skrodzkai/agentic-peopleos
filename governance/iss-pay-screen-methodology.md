# ISS pay-for-performance screen — methodology & provenance

The **iss-pay-screen** agent renders an illustrative reconstruction of the **ISS quantitative
pay-for-performance (P4P) screen** over synthetic Acme, so a compensation committee can anticipate how
the proxy advisor's quantitative concern would read before the vote. It is built **only from ISS's
publicly described methodology** and runs on synthetic data — it is **not** ISS software, ingests no ISS
proprietary reports, and produces **no ISS recommendation**.

> **Not affiliated with ISS.** Nothing here is ISS output. The model is a neutral reconstruction over a
> synthetic universe, for demonstration only, from public methodology descriptions.

## Parameterized by policy year

ISS revises the quantitative screen each proxy season. `foundation/compute/iss_screen.py` keys the
thresholds and measurement windows by **policy year** (`ISS_POLICIES`, default `2026`), so the screen
tracks live policy and keeps the prior season for a legible before/after. `screen(policy_year=…)` fails
closed on an unmodeled year.

| Measure | 2025 | 2026 | 2026 change |
|---|---|---|---|
| **MOM** (Multiple of Median CEO pay) | 1-year only | 50/50 blend of 1-year and 3-year | window blended |
| **RDA** (Relative Degree of Alignment) | 3-year | 5-year | horizon 3→5yr |
| **PTA** (Pay-TSR Alignment) | 5-year WLS | 5-year WLS | unchanged |
| **FPA** (Financial Performance Assessment) | EVA-style, 3-year (real ISS) | EVA-style, 5-year (real ISS) | horizon 3→5yr |

### Published non-S&P-500 (Russell 3000) concern thresholds

| Measure | 2025 (eligible / medium / high) | 2026 (eligible / medium / high) |
|---|---|---|
| MOM | 1.84 / 2.33 / 3.33 | 1.89 / 2.33 / 3.40 |
| RDA | −38 / −50 / −60 | −41 / −54 / −64 |
| PTA | −25 / −30 / −45 | −28 / −30 / −45 |

S&P-500 issuers use a **different** published MOM table (2026: 1.73 / 2.04 / 2.99); this illustration is
non-S&P-500 throughout (the subject Acme is a mid-cap Russell-3000 filer), so only the non-S&P-500 tables
operate — the S&P-500 case is out of scope, not silently mis-scored.

## The honesty ledger (component by component)

| Component | Status | What that means |
|---|---|---|
| The **three measures** (MOM / RDA / PTA), their **signs**, the concern-level **aggregation** rules, and the **non-S&P-500 threshold tables** per season | **PUBLIC** | ISS publishes these in its "Pay-for-Performance Mechanics" document and season policy updates; the reconstruction follows them. |
| The **PTA weighted-least-squares** trend mechanics (decay 0.85, the TSR/pay fence-post weight vectors, the normalized-slope formula) | **PUBLIC** | Taken from the Mechanics doc; implemented in `_wls_norm_slope`. |
| The **comparison group** | **ILLUSTRATIVE** | A self-peer graph + peer-of-peer walk + size/GICS screen selecting ~14-24 names — an illustration of ISS's published peer logic, **not** ISS's exact peer engine (which adds GICS 8/6/4 precision caps, market-cap buckets, a ~20%-of-median centering test, and prior-year-ISS-peer priority). Documented, not claimed as replication. |
| The **FPA** | **ILLUSTRATIVE PROXY** | The real 2026 FPA is a **5-year, four-EVA-metric** screen (EVA Margin, EVA Spread, EVA Momentum vs Sales, EVA Momentum vs Capital). This arm models FPA as a **single-score EVA-style proxy** (`fin_eva`), horizon-agnostic — explicitly **not** the four-metric ISS FPA. Extending the synthetic data to the four-metric shape is documented future work. |
| The exact **FPA cut points** and the **qualitative-evaluation outcome** | **NOT MODELED** | ISS does not publish the exact FPA threshold, and the second-stage qualitative outcome requires ISS/consultant review. The screen surfaces the qualitative factors as a checklist, never a verdict. |

## Determinism

Every measured value is rounded (`_RANK_ROUND = 9`) before percentile-ranking, so a near-tie cannot flip
a rank on last-ULP float drift across platforms — matching the Glass Lewis arm's guard. The committed
dashboard is byte-identical across Python 3.9 and 3.14.

## Sources (public)

- ISS U.S. voting-policy gateway: https://www.iss-stoxx.com/stewardship/voting-policy-gateway/proxy-voting-policies/
- ISS U.S. Pay-for-Performance Mechanics (current): https://www.iss-stoxx.com/file/policy/current/americas/pay-for-performance-mechanics.pdf
- ISS U.S. compensation-policies FAQ + 2026 benchmark policy updates (same gateway).
- CAP / Harvard corpgov 2026 policy-update summary (corroborates the year-over-year threshold moves):
  https://corpgov.law.harvard.edu/2026/01/20/iss-and-glass-lewis-2026-policy-updates/

Not legal, accounting, or proxy-advisory advice. Confirm current ISS policy with counsel/ISS before
relying on any of this.
