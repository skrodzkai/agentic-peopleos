# SPEC — equity-spend board agent

## Purpose
Render the company-wide equity-spend / burn-rate view for a Compensation Committee. Presentation +
governance only; the math lives in `foundation/compute/equity_spend.py`.

## Input
`equity_spend.compute()` — derived entirely from the append-only grant ledger
(`foundation/data/acme/equity_grants.csv`) plus plan facts, shares outstanding, financials, and an
illustrative benchmark file. No other input; no external calls.

## Output (deterministic; atomic writes)
- `output/report.sample.html` — the dark board dashboard.
- `output/day1-digest.sample.md` — the one-page board digest.
- `output/report.sample.png` — an illustrative render (not byte-gated).
- `output/PUBLISHED.json` — written only on `--publish --approved-by "<name>"`.

## What it shows
Board headline + verdict; KPI band (SBC % rev TTM, 3-yr VABR, overhang, pool longevity, SBC backlog);
SBC $ vs % dual-axis trend; ISS-EPSC readiness (Grant-Practices burn-vs-cap, Plan-Features exact ticks,
Plan-Cost overhang proxy, NOT a value-adjusted SVT); burn table by FY (gross / net / VABR / retired-legacy diagnostic); grant value
by participant group (CEO → broad-based staff).

## Invariants (fail closed on violation)
- Every headline figure is finite; the market-cap identity (CSO × price) holds; the EPSC pillar's VABR
  matches the headline VABR; overhang ∈ (0,100); longevity > 0; the CEO group is present.
- The benchmark `source_note` must contain "illustrative" (defense-in-depth against a mislabeled cap).
- Publish requires a named approver matching a strict charset; a control-char approver is refused (rc 2).
- Two runs are byte-identical (determinism); a failed/refused run stales any prior published output.

## Explicitly out of scope (never)
Sizing a share request; recommending or changing grants/vesting; authorizing issuance; naming an individual;
claiming an actual ISS/Glass Lewis score; any external send.
