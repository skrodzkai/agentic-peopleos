# SPEC — Executive Compensation Peer Group Builder

## What it is
The Executive Compensation arm's first agent: a dark, board-ready dashboard that builds a defensible
executive-comp **peer group** the way a Compensation Committee does — a hard screen for membership,
then a transparent fit-rank for ordering — and stops at a human approval gate.

## Inputs
- `foundation/compute/peers.py` — the shared screener: the per-criterion pass/fail **gate** and the
  size-fit **rank**. The agent does **no** screening or ranking math.
- `foundation/data/acme/peer_universe.csv` — a universe of REAL public companies (as-disclosed public financials, illustrative snapshot; provenance in `governance/real-peer-data.md`) with a synthetic subject (= the same
  Acme Corp the rest of the portfolio uses).
- `foundation/render/charts.py` — the deterministic SVG chart toolkit.

## Outputs (drafts only, local)
- `output/report.sample.html` — the committee dashboard (self-contained, inline SVG, no JS, no CDN).
- `output/day1-digest.sample.md` — the committee digest.
- `output/PUBLISHED.json` — written **only** on an approved publish, inside the same atomic transaction
  as the report (no false "approved" without a record).

## The two-step model (the way a committee works)
1. **Screen — the gate (defensible membership).** A hard, transparent per-criterion pass/fail:
   - revenue within **0.5–2.0×** of the subject,
   - market cap within **0.5–2.0×**,
   - membership in the documented **software/SaaS peer group** (a set of GICS sub-industries; GICS
     fragments SaaS across sectors, so an exact single-code match would drop real software peers).

   Membership is decided here and only here. Every inclusion and exclusion defends itself on one line.
   **Headcount is deliberately a *soft* factor**, not a hard gate — matching disclosed market practice,
   where revenue and market cap are the primary size anchors and headcount is a secondary screen. It
   shapes the fit-rank below, never membership.
2. **Fit-rank — the order (recommended core + watchlist).** Within the in-band group, peers are ranked
   by a pure **revenue-weighted size-closeness** score over revenue, market cap, **and headcount** (100 =
   identical size to the subject; 0 = at a band edge). The score **orders** the group into a recommended
   **core** and a substitution **watchlist**; it **never** changes who is in the group. No opaque
   qualitative weights.

## The dashboard
1. **Insight ribbon** — a deterministic narrator (no model) leading with the screen result.
2. **Subject beacon** — the subject company's facts + where its revenue sits within the peer group
   (a percentile instrument).
3. **Screen criteria & funnel** — the hard-gate criteria (revenue · market cap · sub-industry) as chips,
   plus a muted "headcount · soft" chip, and a waterfall (universe → industry → size → in-band).
4. **Peer size distribution** — the group's revenue spread, the subject's band highlighted.
5. **Recommended core peer group** — the fit-ranked core, each with its size-fit score.
6. **Watchlist** — in-band alternates the committee can substitute in.
7. **Defensible exclusions** — same-industry companies kept out on size, each with the criterion failed.
8. **Target-percentile policy** — the committee's chosen targets, carried forward to benchmarking
   **after** approval (the screen never recommends pay).

## Governance (non-negotiable)
- **Presentation + governance only** — no screening or ranking math; every PASS/FAIL and fit score
  comes from the shared screener.
- **Fail closed** — universe missing / no single subject / degenerate field / no peers ⇒ no report, one
  clean line, non-zero exit.
- **Read-only** — never writes to a system of record; never recommends pay.
- **Propose, don't decide** — the committee approves the final peer group; the fit score orders but
  never gates membership.
- **Publish gate** — `--publish` requires `--approved-by "<name>"` matching a strict charset (control
  chars + trailing-newline rejected via `re.fullmatch`); the approval record is part of the
  all-or-nothing write transaction.
- **Real peers, synthetic subject** — the candidate peers are real public companies with as-disclosed public financials (a dated, illustrative snapshot; provenance in `governance/real-peer-data.md`); the subject (Acme) is synthetic.
