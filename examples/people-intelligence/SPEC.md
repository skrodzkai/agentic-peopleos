# SPEC — People Intelligence (Executive View)

## What it is
The Analytics-arm **marquee**: a single dark, one-page executive dashboard that composes headline
People metrics across domains — led by the People↔Finance linkage — from the shared compute engine,
and stops at a human publish gate.

## Inputs
- `foundation/compute/engine.py` — every metric value and the point-in-time quarterly history
  (`engine.series_multi`). The agent does **no** metric math.
- `foundation/render/charts.py` — the deterministic SVG chart toolkit.
- `vault/90-people-analytics/metrics/metrics.registry.json` — cited definitions + coverage.

## Outputs (drafts only, local)
- `output/report.sample.html` — the dashboard (self-contained, inline SVG, no JS, no CDN).
- `output/day1-digest.sample.md` — the executive digest.
- `output/PUBLISHED.json` — written **only** on an approved publish, inside the same atomic
  transaction as the report (no false "approved" without a record).

## The dashboard
1. **Insight ribbon** — a deterministic narrator (no model) leading with the diagnosis.
2. **Signature: Revenue/FTE percentile instrument** — the engine's trailing-12-month Revenue/FTE
   placed on an **illustrative** SaaS benchmark axis (presentation positioning only; the metric value
   is the engine's; the benchmark anchors are representative ranges, not a specific published dataset).
3. **KPI strip** — Revenue/FTE, headcount, voluntary attrition, compa-ratio, out-of-band pay, each
   with an 8-quarter sparkline (the same engine re-run at each quarter-end; a missing quarter is shown
   as "trend n/a", never silently dropped).
4. **Charts** — headcount bridge (waterfall) · operating leverage (dual-axis) · pay positioning
   (range-penetration histogram) · attrition by team (hotspots) · org shape (managers vs ICs by level,
   the "diamond") · 9-box talent grid · a Total Rewards & retention strip.
5. **Footer** — instrumentation coverage (measured vs defined), aggregation floor, ISO 30414.

## Governance (non-negotiable)
- **Presentation + governance only** — no metric math; every number engine-computed and cited.
- **Fail closed** — engine/registry/dataset unavailable, or a required headline not computable ⇒ no
  report, one clean line, non-zero exit.
- **Read-only** — never writes to a system of record; no decisional action (registry-enforced).
- **Publish gate** — `--publish` requires `--approved-by "<name>"` matching a strict charset
  (control chars + trailing-newline rejected via `re.fullmatch`); the approval record is part of the
  all-or-nothing write transaction.
- **Deterministic + offline + stdlib-only** — same inputs ⇒ identical bytes (committed HTML/digest are
  byte-diffed in CI; the PNG is an illustrative snapshot, not gated). The chart `<defs>` ids are
  content-derived so no two charts collide.
- **Aggregate only** — no per-person output anywhere (the 9-box and attrition-by-team are group-level).
- **Business-operations only on the flagship** — this executive view carries financial, talent-risk,
  and comp-health metrics; other reporting domains the registry defines (and their governance) live in
  their own agents and docs, not on this dashboard.

## Run
```bash
cd examples/people-intelligence
python run.py                                          # draft
python run.py --publish --approved-by "People Analytics Lead"
python evals/test_people_intelligence.py
```
