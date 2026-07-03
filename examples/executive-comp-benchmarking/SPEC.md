# SPEC — Executive Compensation Benchmarking

## What it is
The Executive Compensation arm's second agent: a dark, board-ready dashboard that positions the
subject's NEOs' pay against the approved peer group's **real, SEC-disclosed** proxy pay — per role, per
pay element, as a percentile versus the committee's target-percentile policy — and stops at a human
approval gate.

## Inputs
- `foundation/compute/benchmarking.py` — the shared benchmarking engine: per-role incumbent
  distribution, mid-rank percentile, linear-interpolated quartiles, target-band status, thin-role
  suppression, component-sum-to-Total reconciliation. The agent does **no** positioning math.
- `foundation/data/acme/proxy_comp.csv` — the committed real peer proxy dataset (role-based; each figure
  from a company's latest DEF 14A Summary Compensation Table; provenance in
  `governance/proxy-comp-data.md`) plus the synthetic subject (the same Acme Corp the portfolio uses).
- `foundation/render/charts.py` — the deterministic SVG chart toolkit.

## Outputs (drafts only, local)
- `output/report.sample.html` — the committee dashboard (self-contained, inline SVG, no JS, no CDN).
- `output/day1-digest.sample.md` — the committee digest.
- `output/PUBLISHED.json` — written **only** on an approved publish, inside the same atomic transaction
  as the report (no false "approved" without a record).

## What the peer figures are (honesty contract)
- **Actual / as-disclosed SCT pay** (equity at grant-date fair value), **not** target opportunity.
  Positioning actual pay against a target-percentile policy is the standard proxy read; the dashboard
  labels it and never implies the peer numbers are targets.
- **One incumbent per company per role** (a transition-year outgoing officer is dropped), **medians and
  percentiles** (never a mean, so a founder mega-grant can't distort the read).
- For the like-for-like comparison to hold, the **subject's own inputs must be SCT-basis actuals** too
  (the committed Acme rows are synthetic SCT-shaped actuals for this reason).

## The dashboard
1. **Insight ribbon** — a deterministic narrator (no model) leading with the honest headline (cash
   competitive; equity below target), and stating peer pay is actual-not-target.
2. **Headline beacon** — the CEO total-direct-comp percentile on a 0–100 track with the target band and
   the peer median, plus roles/positions/below-target counts.
3. **Positioning at a glance** — a role × element status matrix (below / on-target / above).
4. **Gap to target** — the below-band positions, widest shortfall first.
5. **Per-role positioning tables** — each element: subject value, a 0–100 position bar with the target
   band shaded, peer P25 / median / P75, target band, and the call.
6. **Suppressed roles** — thin-disclosure roles, shown, never given a spurious percentile.

## Governance (non-negotiable)
- **Presentation + governance only** — no positioning math; every percentile, quartile and status comes
  from the shared engine.
- **Fail closed** — proxy dataset missing / schema or reconciliation failure / no positions ⇒ no report,
  one clean line, non-zero exit.
- **Read-only** — never writes to a system of record; never recommends or sets pay.
- **Position, don't decide** — the Compensation Committee sets pay; the agent only shows where the
  subject sits versus the market.
- **Publish gate** — `--publish` requires `--approved-by "<name>"` matching a strict charset (control
  chars + trailing-newline rejected via `re.fullmatch`); the approval record is part of the
  all-or-nothing write transaction.
- **Real peer pay, synthetic subject** — peer figures are real public-company as-disclosed SCT pay (a
  dated illustrative snapshot; provenance in `governance/proxy-comp-data.md`); the subject (Acme) is
  synthetic, and no individual executive name is stored.
