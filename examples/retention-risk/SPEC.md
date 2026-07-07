# SPEC — Retention Risk (Committee View)

## What it is
The retention-arm **marquee**: a single dark, one-page committee dashboard that renders the
**published glass-box hazard model** as a **segment-first** planning instrument, and stops at a human
publish gate. Its design thesis: every dashboard *claims* to be trustworthy; this one **performs** it.

## Inputs
- `foundation/compute/retention.py` — the engine. The agent reconstructs the *published* model from
  the pinned manifest via `model_from_manifest` (no re-fit) and reads:
  `evaluate` (out-of-time metrics), `segment_risk` + `reconciliation_summary` (two-way model-vs-observed
  segment risk), `company_risk` / `tier_counts` / `company_survival` (company rollups). The agent does
  **no model fitting and computes no source metric** — it formats these engine statistics and the plain
  display ratios between them (e.g. a lift or a below-vs-above band ratio).
- `foundation/compute/manifests/retention_model_manifest.json` — the pinned trained artifact
  (coefficients, standardizer, Platt calibration, band thresholds). CI re-fits and checks it reproduces.
- `foundation/render/charts.py` — the deterministic SVG chart toolkit.

## Outputs (drafts only, local)
- `output/report.sample.html` — the dashboard (self-contained, inline SVG, no JS, no CDN).
- `output/committee-digest.sample.md` — the one-page committee digest.
- `output/report.sample.png` — an **illustrative** snapshot of the HTML (browser-rendered; **not**
  part of the deterministic gate).
- `output/PUBLISHED.json` — written **only** on an approved publish, inside the same atomic
  transaction as the report (no false "approved" without a record).

## The dashboard (13 panels)
0. **Guardrail strip** — the five reading rails (segment-first / planning-only / synthetic /
   fairness-not-validated / voluntary-only), *before* any number.
1. **Insight ribbon** — a deterministic narrator (no model) leading with the diagnosis.
2. **Beacon** — the company aggregate on its own **segment spread** (a ~10% average hides a 2%–20%
   range); leads with the *observed* rate and wears the model's disagreement as a badge.
3. **KPI row** — 6-mo exit risk, base hazard, ROC-AUC (with the guard ceiling printed on the card),
   top-decile lift, segments — every card honestly `trend n/a` (a single artifact has no history).
4/5. **Segment forests** — comp-position & tenure; function & level — **solid = model bottom-up,
   ghost = observed KM, red = gap-flagged**. Disagreement is a *shape*, surfaced not averaged.
6. **Segment ledger** — all rendered segments across 6 dimensions, both estimates + the gap + the
   suppression floor (region is broad-only, never country).
7. **Glass-box drivers** — the top coefficients as additive log-odds per +1 SD; protective (green)
   vs risk-raising (red); decoy ranks noted. Exact, associational, not causal.
8. **Trust panel** — every metric **beside its no-skill baseline**, the realism guard ("too good
   fails the build"), and the out-of-time train/calibration/test split.
9. **Survival outlook** — company S(t) staircase, the planning-horizon bin highlighted (months beyond
   the observed window are a labeled frozen-hazard projection).
10. **Support tiers** — test-slice **person-months** per tier (thresholds set on calibration), never
    listed as people, never an adverse-action label.
11. **Planning levers** — comp review / manager support / career pathing, **each ending in a human
    gate**; recommendations, never actions.
12. **Fairness — NOT YET VALIDATED** — an amber tile with a literal `[x]`/`[ ]` checklist.

## Governance (non-negotiable)
- **Presentation + governance only** — no model fitting or source-metric computation; every statistic
  engine-computed, the agent formatting them and the plain display ratios between them.
- **Segment-first** — no per-person output anywhere; no leaderboard; regions broad-only; small
  segments suppressed (floor n ≥ 30, raisable never lowerable).
- **Not an adverse-action input** — never a termination / pay-cut / PM trigger. Model card is binding.
- **Voluntary exits only** — involuntary + retirement are competing-risk censored.
- **Fail closed** — panel/manifest/engine unavailable, a missing/non-finite headline or segment, or a
  **realism-guard trip** ⇒ no report, one clean line, non-zero exit (and any stale outputs invalidated).
- **Publish gate** — `--publish` requires `--approved-by "<name>"` matching a strict charset (control
  chars + trailing-newline rejected via `re.fullmatch`); the approval record is part of the
  all-or-nothing write transaction.
- **Deterministic + offline + stdlib-only** — same inputs ⇒ identical bytes (committed HTML/digest are
  byte-diffed in CI; the PNG is an illustrative snapshot, not gated).
- **Fairness scaffolding only** — the dashboard shows its unchecked fairness boxes; no fairness
  determination rests on this build.

## Run
```bash
cd examples/retention-risk
python3 run.py                                          # draft
python3 run.py --publish                                # refused: needs a valid approver
python3 run.py --publish --approved-by "People Analytics Lead"
python3 evals/test_retention_agent.py
```
