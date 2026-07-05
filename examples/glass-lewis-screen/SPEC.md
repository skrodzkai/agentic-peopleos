# SPEC — glass-lewis-screen agent

## Purpose
Render the two-proxy-advisor (ISS vs Glass Lewis) say-on-pay view for a Compensation Committee. Presentation
+ governance only; the scoring lives in `foundation/compute/glass_lewis_screen.py` (which reuses the ISS
engine, `foundation/compute/iss_screen.py`).

## Input
`glass_lewis_screen.compute()` — runs the GL screen and the ISS screen over the SAME synthetic universe
(`foundation/data/acme/{iss_universe,exec_pay_tsr,gl_financials}.csv`) and reconciles them. No other input;
no external calls.

## Output (deterministic; atomic writes)
- `output/report.sample.html` — the dark two-advisor war-room dashboard.
- `output/day1-digest.sample.md` — the one-page committee digest.
- `output/report.sample.png` — an illustrative render (not byte-gated).
- `output/PUBLISHED.json` — written only on `--publish --approved-by "<name>"`.

## What it shows
Reconciliation verdict banner + committee considerations; KPI band (GL concern + composite, ISS concern, verdict,
say-on-pay support band, GL peer-group size); the two-advisor "war room" cards (each lens' inputs side by
side); the **Glass Lewis 5-test scorecard** (each test scored 0–100 + banded, weighted to the composite —
STI measured as payout **relative to target**, not raw dollars) with the **say-on-pay responsiveness** factor
(a disclosed GL policy: below ~80% prior support invites engagement scrutiny, kept separate from the P4P
composite); and a **two-pole counterfactual** where a pay-vs-TSR-only read and a financials-only read
**bracket** the composite, with pay-vs-performance percentile strips per lens.

## Divergence (the calibrated teaching case)
The committed data lands **GL = Low concern (composite 70/100)** and **ISS = Medium** → **ISS-ONLY FLAG**. The
mechanism is honest and structural: Acme is a disciplined-pay, lagging-stock company — the granted CEO pay
percentile is high against a weak 5-yr TSR, so GL's *Granted CEO Pay vs TSR* test scores Severe (exactly what
ISS flags Medium on); but the NEO team is lean, the STI is lean, the CEO's equity is underwater (CAP below
granted), and the financials are solid — so the other four tests read Negligible/Low and the weighted
composite lands Low. The two-pole counterfactual makes this auditable: pay-vs-TSR-only ≈ Severe,
financials-only ≈ Negligible, blended composite = Low.

## Invariants (fail closed on violation)
- Both advisors scored one issuer; the GL concern matches its composite band; the five tests are present,
  scored 0–100, and weighted to sum 1; every rendered percentile ∈ [0,100]; the peer group meets the scorable
  minimum; the verdict is a known one; the support band is an ordered range in [0,100] labeled "not a vote
  forecast".
- Publish requires a named approver matching a strict charset; a control-char approver is refused (rc 2).
- Two runs are byte-identical (determinism); a failed/refused run stales any prior published output.

## Explicitly out of scope (never)
Scoring a program itself; forecasting a say-on-pay vote or emitting a probability; recommending or changing
executive pay; naming an individual or a real issuer; claiming an actual ISS/Glass Lewis grade; any external
send.
