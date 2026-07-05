# Glass Lewis model & the ISS-vs-GL war room — methodology & provenance

The **glass-lewis-screen** agent renders a two-proxy-advisor say-on-pay view over synthetic Acme: an
illustrative reconstruction of **Glass Lewis's CURRENT (2026) pay-for-performance scorecard** beside the
illustrative **ISS** concern level, plus their reconciliation. This documents, component by component, what
is publicly-known method, what is a public outline, and what is an illustrative reconstruction — and why the
two advisors diverge on the shipped data.

> **Not affiliated with Glass, Lewis & Co. or ISS.** Nothing here is Glass Lewis or ISS output. The models
> are neutral reconstructions over a synthetic universe, for demonstration only, and are built **only from
> public methodology descriptions** — no Glass Lewis proprietary reports or licensed materials are used or
> ingested (Glass Lewis's terms restrict copying/redistribution and use of its materials to train AI systems).

## Current vs legacy Glass Lewis model (why there's no letter grade)
Glass Lewis **retired its A–F letter grade** with the 2026 model (previewed July 2025, effective for 2026
meetings). The current model is a **0–100 numerical scorecard** across five quantitative tests that maps to a
**concern level** — Negligible / Low / Medium / High / Severe — where **Severe and High** are the levels most
likely to draw a negative say-on-pay recommendation. (An earlier version of this arm modeled the legacy A–F
grade, where "C" was ideal and A/F both signalled misalignment; this arm now renders the current scorecard.)

## The honesty ledger (component by component)

| Component | Status | What that means |
|---|---|---|
| A 0–100 **scorecard** across five quantitative tests → a **concern level** (Negligible/Low/Medium/High/Severe) | **PUBLIC** | The output shape is publicly known. Our composite + level are a reconstruction. |
| The **five tests**: Granted CEO Pay vs TSR; Granted CEO Pay vs Financial Performance; CEO STI Payouts vs TSR; Total Granted NEO Pay vs Financial Performance; CEO Compensation-Actually-Paid vs TSR — plus a **qualitative downward modifier** | **PUBLIC** | The test roster is disclosed. |
| Pay basis is **granted CEO / NEO-team** pay; **TSR is a separate test** from the financial tests; the financial-performance metric set (non-financial sectors) is **revenue growth, EPS growth, OCF growth, ROE, ROA**; a **5-year weighted** window; GL builds its **own peer group** (~15 firms, min ~10 viable) | **PUBLIC-OUTLINE** | Inputs + structure are described publicly; the exact mix is proprietary. |
| The exact **test weights**, the score-band cutoffs, the qualitative point deductions, and the peer-ranking function | **ILLUSTRATIVE** | Glass Lewis expressly does NOT disclose the **weights**. GL *does* publish overall concern bands + test-specific rating ranges, but this repo intentionally applies **one uniform illustrative band** across all five tests (a labeled simplification, not GL's per-test tables). Everything in the `_GL` block of `glass_lewis_screen.py` is a defensible neutral reconstruction, labeled as such. |

## The illustrative reconstruction (the `_GL` constants)
- **Five tests, each 0–100** (higher = better alignment). Four are a pay-percentile-minus-performance-percentile
  alignment score (`85 − 1.6·gap`, clamped): CEO pay vs TSR, CEO pay vs financials, STI vs TSR, NEO-team pay
  vs financials. The fifth (CAP vs TSR) is a ratio test — 5-yr aggregate CEO CAP ÷ reported cumulative TSR,
  compared here to the **synthetic 15-name peer-group median** ratio. GL's public method frames CAP-vs-TSR in a
  **broader market-cap benchmark context**; using the ~15-name peer median is a labeled **illustrative
  simplification**. The **"no penalty at/below +50% above median" threshold is a disclosed GL rule**; the
  specific penalty *slope* below the threshold is an illustrative reconstruction.
- **STI vs TSR** — GL's public description is STI payout *as a percentage of target* vs TSR. The synthetic data
  now carries an **STI target** per company-year, so the test ranks **payout ÷ target** (generosity relative to
  opportunity, size-neutral) and scores it against the TSR percentile — matching GL's description. We rank it
  **within the ~15-name GL peer pool** as an **illustrative proxy** for GL's broader-market benchmarking, and
  the target *levels* are illustrative.
- **Financial-performance percentile** = the equal-weighted average of the five disclosed metric percentiles
  (revenue/EPS/OCF growth, ROE, ROA). **TSR is deliberately NOT blended in** — it is its own test.
- **Measurement window** — pay, TSR, STI, and CAP use a **5-year weighted** window (matching GL). The
  financial-*growth* tests use a **3-year window** here — a documented simplification driven by the synthetic
  data's history depth (GL's is 5-year); the metric *set* is the disclosed one.
- **Illustrative test weights** (GL does not disclose them): CEO pay vs TSR 0.22, CEO pay vs financials 0.22,
  STI vs TSR 0.14, NEO pay vs financials 0.24, CAP vs TSR 0.18.
- **Composite → concern**: Severe 0–20, High 21–40, Medium 41–60, Low 61–80, Negligible 81–100.
- **P4P qualitative downward modifier** (on the composite): the one pay-vs-performance flag derivable from
  the data — **STI paid above the peer median while TSR lagged** — plus **documentation that GL's full P4P
  checklist** (one-time awards, upward discretion, short LTI vesting, excessive LTIP potential, undisclosed
  goals) needs plan-design disclosure beyond this dataset. Partial and labeled, not faked.
- **Say-on-pay responsiveness** — a *disclosed* GL policy modeled as a **recommendation-level factor, kept
  SEPARATE from the quantitative P4P composite** (GL applies responsiveness at the say-on-pay recommendation
  level, not inside the P4P score — so we report it beside the score, never subtracted from it). GL engages a
  company whose prior say-on-pay support fell below **~80%**, and a weak board response then raises the
  recommendation concern. The data carries a prior support % and a responsiveness posture (robust / limited /
  none); the **~80% threshold is public**, the concern mapping (robust → low, limited → elevated, none → high)
  is illustrative. The committed subject sits at **93.7%** support — above the threshold — so the factor is
  wired and rendered but flags no concern.
- **Peer group** — a cap-banded (0.33×–3.0×) co-citation network, target **~15 firms, minimum 10 viable**.

## How GL differs from ISS (why we run both)
| | ISS (this repo's `iss_screen.py`) | Glass Lewis (`glass_lewis_screen.py`) |
|---|---|---|
| Pay basis | CEO-only | Granted CEO **and** NEO-team |
| Performance | 5-yr **relative TSR** (MOM / RDA / PTA cascade) | A five-test scorecard: pay vs TSR **and** vs financials + CAP |
| Peer group | revenue-centered screen | cap-banded co-citation network |
| Output | Low / Medium / High concern | 0–100 composite → Negligible … Severe concern |

Both output a concern level. They agree at the extremes and **diverge in the middle** — where a company's
stock lagged its financials — which is the entire reason a committee runs both.

## The reconciliation ("war room")
`advisor_synthesis(iss, gl)` maps each advisor to a concern ordinal (ISS Low/Med/High → 0/1/2; GL
Negligible/Low → 0, Medium → 1, High/Severe → 2) and returns one of five verdicts — **CLEAN SWEEP**,
**ISS-ONLY FLAG**, **GL-ONLY FLAG**, **DUAL WATCH**, **TWO-FRONT FIGHT** — plus the numbers behind the
(dis)agreement and a **directional** say-on-pay support band (wider/lower as concern stacks), **never a vote
forecast or probability**.

## Calibration — the shipped divergence (a deliberately-constructed teaching case, mechanism made transparent)
The committed data lands **GL = Low concern (composite 70/100)** and **ISS = Medium** → **ISS-ONLY FLAG**. The
mechanism is structural: Acme is a **disciplined-pay, lagging-stock** company —
- the granted CEO pay percentile is high (~93rd) against a weak 5-yr TSR (~27th), so the **Granted CEO Pay vs
  TSR** test scores Severe — exactly what ISS (CEO-only, TSR-centric) flags Medium on; but
- the NEO team is **lean**, the STI is **lean**, the CEO's equity is **underwater** (CAP below granted), and
  the **financials are solid** — so the other four tests read Negligible/Low, and the weighted composite lands
  in the **Low** band.
- The dashboard's **two-pole counterfactual** makes this auditable: a pay-vs-TSR-only read scores ~0 (Severe),
  a financials-only read ~87 (Negligible), and the blended composite sits between at 70 (Low). Glass Lewis's
  broader scorecard sees enough alignment to stay Low where ISS's narrow lens flags Medium.

## Guardrails
- **Synthetic only** — the universe is `ACMQ` + `S001…S060`; the engine fails closed on a real-ticker shape or
  a real issuer name (reusing the peer-universe public-safety guards), and on roster mismatch, duplicate
  tickers, a non-positive growth base / pay component, a non-numeric field, two subjects, or an undersized
  peer group.
- **No individual names**, no real issuer identity, no pay figure attached to a real company.
- **Presentation + governance only** — the agent never scores a program itself, never forecasts a vote, and
  never recommends a pay change; the Compensation Committee owns the response.
- **Deterministic, standard-library, offline** — two runs are byte-identical.
