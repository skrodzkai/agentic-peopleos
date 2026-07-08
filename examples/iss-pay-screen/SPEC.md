# SPEC — ISS Pay-for-Performance Screen

## What it is
The Executive Compensation arm's **board-anticipation** agent: a dark dashboard that shows how the ISS
quantitative pay-for-performance screen would likely read the subject, so a Compensation Committee can
anticipate the proxy-advisor result and prepare. Presentation + governance only; stops at a human gate.

## Policy year
The engine (`foundation/compute/iss_screen.py`) is **parameterized by policy year** (`ISS_POLICIES`,
default **2026**): MOM/RDA windows and the concern thresholds are keyed by season, so the screen tracks
live ISS policy and keeps the prior season for a before/after. The dashboard stamps the season + the
concrete 2026 delta, and reads every gauge threshold from the engine's bands (never hard-coded). The
verified public thresholds, the 2026 changes, and the honesty ledger (illustrative comparison group;
FPA as a single-score EVA proxy vs the real four-metric ISS FPA) are documented in
[`governance/iss-pay-screen-methodology.md`](../../governance/iss-pay-screen-methodology.md).

## Inputs
- `foundation/compute/iss_screen.py` — the ISS screen engine (comparison group + MOM/RDA/PTA/FPA →
  concern). The agent does **no** screening math.
- `foundation/compute/peers.py` — the committee's own peer group, read **only** to show the overlap
  between the two (different) peer objects.
- `foundation/render/charts.py` — shared color constants for the inline concern gauges.

## Outputs (drafts only, local)
- `output/report.sample.html` — the committee dashboard (self-contained, inline SVG, no JS, no CDN).
- `output/day1-digest.sample.md` — the committee digest.
- `output/PUBLISHED.json` — written **only** on an approved publish, inside the same atomic transaction.

## The dashboard
1. **Insight ribbon** — a deterministic narrator leading with the concern level + its driver.
2. **Concern beacon** — the overall Low/Medium/High concern, large, with the honest disclaimer.
3. **Quantitative measures** — MOM, RDA, PTA, each a value + band chip + a Low/Medium/High zone gauge with
   the threshold ticks; plus the FPA (financial-performance) modifier line.
4. **ISS-derived comparison group** — the self-peer-graph group + its **overlap** with the committee's own
   peer group (the "two peer objects" point: different memberships built by different rules).
5. **Qualitative review** — the second-stage ISS factors a Medium/High concern puts in scope.

## Governance + honesty (non-negotiable)
- **Presentation + governance only** — no screening math; every value engine-computed.
- **Fail closed** — ISS inputs missing/degenerate / comparison group not scorable ⇒ no report, one clean
  line, non-zero exit. (The overlap is optional: the screen renders standalone if the peer arm is absent.)
- **Read-only**; **anticipates, never decides**; **never recommends pay**.
- **Publish gate** — `--publish` requires `--approved-by "<name>"` matching a strict charset; the approval
  record is part of the all-or-nothing write transaction.
- **Honesty (immutable)** — the dashboard + digest always state this is an **illustrative** model of ISS's
  **published** methodology on **synthetic** data, that **ISS publishes its threshold table + WLS mechanics**
  (in its Pay-for-Performance Mechanics doc) while the **exact FPA threshold + qualitative outcome still need
  ISS/consultant review**, and that this is **NOT ISS's
  actual output**. No real issuer, ticker, or proxy is represented.
