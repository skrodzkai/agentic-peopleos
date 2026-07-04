# Agentic PeopleOS

**Operating discipline for agent-run People functions.**

[![CI](https://github.com/skrodzkai/agentic-peopleos/actions/workflows/ci.yml/badge.svg)](https://github.com/skrodzkai/agentic-peopleos/actions/workflows/ci.yml)

Most of an HR back office is judgment applied to repeatable process — sourcing, screening,
onboarding, comp analysis, performance paperwork, policy questions, compliance. That's work a
well-governed fleet of AI agents can do. Agentic PeopleOS is the operating layer that makes it
trustworthy enough for a real company: every agent has a role, a budget, an audit trail, a
performance review, and hard limits — and a human owns every consequential decision.

This repo is the sanitized, reusable skeleton of that operating system — no secrets, no live
systems, no proprietary logic.

> Built by [@skrodzkai](https://github.com/skrodzkai) — a senior Total Rewards leader and
> hands-on AI systems engineer building the operating system for an agent-run People function.

---

## The People function as agent modules

Each HR function becomes one or more agents. The agent does the work; a human owns the call.

| Function | What the agent does | What the human owns |
|---|---|---|
| Recruiting & sourcing | finds, ranks, and reaches candidates | who advances |
| Screening & scheduling | screens, schedules, keeps the pipeline moving | the bar |
| Onboarding | runs the checklist, provisioning, day-one plan | the exceptions |
| Comp & benefits | benchmarks, models scenarios, flags outliers | offers & spend |
| Performance | drafts reviews, surfaces calibration gaps | ratings & promotions |
| Policy & employee Q&A | answers from policy, escalates edge cases | the policy itself |
| Compliance & audit | watches every action, keeps the record | the risk posture |
| People analytics & reporting | compiles operating reports, flags risks, ties metrics to action | the decision, and what's published |

---

## The governance spine — "what bad thing did this prevent?"

The hard part isn't the agents; it's running a fleet of them **safely and auditably**. Every
example is built on one spine, and each control answers a concrete threat — provable in code,
transcript, ledger, and evals:

- **A tamper-evident decision ledger** ([`core/event_log.py`](core/event_log.py)) — a
  hash-chained, replayable JSONL ledger that detects edits, gaps, duplicates, out-of-order or
  **forged** approvals, and **decision laundering** (an action with no genuine approval).
- **Approval registry** ([`core/approval_registry.py`](core/approval_registry.py)) — role-scoped, satisfied by
  a *pool*: any one entitled HR human can approve, so PTO/illness never blocks a decision.
  Entitlement is re-derived on replay; the logged flag is never trusted.
- **Injection-safe content** ([`core/content.py`](core/content.py)) — only provenance-trusted
  policy is authoritative; a note or channel message can never approve anything.
- **Human-in-the-loop by construction** — agents recommend; an entitled human approves with a
  reaction; only then does the gated action run.

```bash
# from the repo root
python3 examples/visible-handoff/run.py                      # request → recommendation → ✅ → publish
python3 examples/visible-handoff/evals/test_handoff.py       # spoofed/bot/duplicate/injected/tampered — all caught
python3 -m core.event_log validate examples/visible-handoff/output/events.jsonl \
    --registry examples/visible-handoff/approval_registry.json      # full integrity: chain + re-verified approvals
```

---

## Why it needs an operating system

A single HR agent is easy. A *fleet* running a People function fails the same ways a team does
without management — so Agentic PeopleOS borrows the management primitives directly:

- agents drift from their purpose → **give each one a job description** (`SOUL.md`)
- new agents are inconsistent → **onboard them with a scaffolder + conventions**
- costs spiral silently → **put every agent on a budget with tiered model routing**
- quality degrades unnoticed → **run weekly performance reviews**
- one bad actor breaks everything → **an auditor with circuit breakers**
- dead agents linger → **a real offboarding / retirement process**

| Operating primitive | How it works | Where it lives |
|---|---|---|
| Identity & guardrails | Immutable role + hard limits | [`templates/SOUL.template.md`](templates/SOUL.template.md) |
| Onboarding | Scaffolder + build conventions | [`scaffold/newagent.sh`](scaffold/newagent.sh) |
| Budget | Per-agent cost tracking + model tiers | [`docs/cost-governance.md`](docs/cost-governance.md) |
| Performance review | Calibration + experiment-driven tuning | [`docs/architecture.md`](docs/architecture.md) |
| Compliance & audit | Decision ledger (source of record for decisions) + circuit breakers *(design pattern)* | [`docs/architecture.md`](docs/architecture.md) |
| Quality gate | Verification checklist before "done" | [`docs/verification.md`](docs/verification.md) |
| Offboarding | Retirement registry + kill switches *(design pattern)* | [`docs/architecture.md`](docs/architecture.md) |

> **Runnable in this repo:** identity/guardrails, the scaffolder, budgets, the **decision
> ledger + approval registry**, the **metric registry + governance validator**, the example
> reporting agents, and verification. Performance reviews, **circuit breakers, and agent
> retirement are documented design patterns / production extensions** — not runnable code here.

---

## Quick start

```bash
# Onboard a new People-function agent with the standard structure
./scaffold/newagent.sh comp-benchmarker compensation
```

This creates the agent with a `SOUL.md` (identity), a `run` entrypoint, and a
`cost_tracker.json` (budget) — the three things every agent in the fleet must have.

See [`docs/architecture.md`](docs/architecture.md) for the full model.

---

## The Analytics arm — dashboards off one engine

The **People Analytics & reporting** arm is a set of agents that turn the metric registry into
governed operating dashboards. They share one design: a **shared compute engine**
([`foundation/compute/engine.py`](foundation/compute/engine.py)) is the single source of math over a
[synthetic data foundation](foundation/data/) (synthetic throughout, except the exec-comp peer universe — real public companies, see [real-peer-data](governance/real-peer-data.md)), a **shared dark renderer**
([`foundation/render/dashboard.py`](foundation/render/dashboard.py)) plus a deterministic
[SVG chart toolkit](foundation/render/charts.py) draw every dashboard, and each agent is
**presentation + governance only** — it does no metric math, cites the registry, shows
not-yet-instrumented metrics **honestly** as `data_pending`, fails closed, and stops at a publish gate.

- **[People Intelligence — Executive View](examples/people-intelligence/)** ⭐ *the marquee* — a
  one-page executive dashboard led by the **People↔Finance linkage** (Revenue/FTE on an *illustrative*
  SaaS benchmark, operating leverage), with KPI sparklines, a headcount bridge, a range-penetration pay
  distribution, **attrition by team** (retention hotspots), the **org-shape diamond** (managers vs ICs
  by level), and a 9-box talent grid — every number engine-computed, every trend point the same engine
  re-run at a past quarter-end.
- **[Headcount & Workforce](examples/headcount-reporting/)** — headcount, FTE, span of control,
  management layers, the headcount bridge, representation by level, leadership diversity.
- **[Attrition & Retention](examples/attrition-reporting/)** — annualized turnover (voluntary,
  regrettable, total, involuntary), first-year/90-day attrition, 12-month retention, segment hotspots,
  with the annualization method stated on the dashboard.
- **[People Ops Service Desk](examples/people-ops-reporting/)** — case volume, SLA attainment,
  time-to-resolution (p50/p90), reopen/FCR/CSAT, aging backlog — recomputed from raw case timestamps,
  not trusted flags.
- **[Monthly People Operating Review](examples/operating-review/)** — the cross-domain composer:
  headline KPIs from every domain + a "measured vs defined" coverage map, shipped behind the **full
  role-scoped, ledger-backed approval gate** (an entitled human's approval recorded in a hash-chained,
  re-verified ledger; a non-entitled actor is denied and escalated).

## The Executive Compensation arm — committee-style reference workflows

The **Executive Compensation** arm is built around the parts of Total Rewards that have to mirror
board-level scrutiny: peer group construction, proxy-backed benchmarking, target percentile policy,
relative-TSR PSU tracking, and human-owned committee decisions.

- **[Executive Comp Peer Group Builder](examples/executive-comp-peer-builder/)** — builds a defensible
  peer group the way a compensation committee does, screening a synthetic subject (Acme) against a universe
  of **real public companies** (as-disclosed public financials — a dated, illustrative snapshot; provenance
  in [real-peer-data](governance/real-peer-data.md)). A **hard, transparent screen** decides membership
  (membership in a documented **software/SaaS peer group** — a set of GICS sub-industries, since GICS
  fragments SaaS across sectors — plus revenue and market cap each within **0.5–2.0×** of the subject; headcount
  is a disclosed soft fit factor), then a **revenue-weighted size-fit rank** orders the in-band group into a recommended core
  + a substitution watchlist. It documents every same-industry size exclusion and carries the committee's
  target-percentile policy forward to benchmarking. It produces **only a peer set for a human to review and
  approve** — it never sets, recommends, or benchmarks pay itself. The fit score *orders* the group; the
  screen — not the score — *decides* who is in it.
- **[Executive Comp Benchmarking](examples/executive-comp-benchmarking/)** — once the peer group is
  approved, positions the synthetic subject's NEOs against the peer group's **real, SEC-disclosed** proxy
  pay (each figure from a company's latest DEF 14A Summary Compensation Table; provenance in
  [proxy-comp-data](governance/proxy-comp-data.md)). For each role (CEO/CFO/COO/CLO) and pay element
  (base, annual cash, total cash, LTI/equity, total direct comp) it shows the subject's **percentile** of
  the peer distribution versus the committee's target band, with peer **P25/median/P75** and a
  below/on-target/above call. It leads with the honest headline — **cash competitive, long-term equity
  below target** — and **suppresses** a thin role (CHRO, two peers) rather than invent a percentile. Peer
  figures are **actual as-disclosed pay, not target opportunity**; the agent runs no positioning math
  (all of it in the shared engine), never recommends pay, and stops at a human approval gate.
- **[Relative TSR PSU Valuation](examples/rtsr-psu-valuation/)** — tracks a synthetic software-company
  rTSR PSU against an index-style peer set, applies a public-style payout curve (25th=50%,
  55th=100%, 75th+=200%), and estimates an illustrative Monte Carlo fair value from supplied
  volatility, correlation, dividend, and risk-free-rate assumptions. The sample is deterministic,
  offline, synthetic-only, and explicitly not accounting/legal/investment advice.
- **[ISS Pay-for-Performance Screen](examples/iss-pay-screen/)** — a board-anticipation dashboard that
  shows how the **ISS quantitative pay-for-performance screen** would likely read the subject: the overall
  Low/Medium/High concern, the three measures (**MOM / RDA / PTA**) against ISS's *published* non-S&P-500
  thresholds and weighted-least-squares mechanics, the ISS-derived comparison group (and its overlap with
  the committee's own peer group), and the FPA modifier. It models the proxy-advisor screen a committee
  must navigate, on transparent public methodology over synthetic Acme data — anticipating the board read,
  never deciding pay, and never claiming to be ISS's actual output.

## Portable skills — point your agent at real SEC data

Two **portable, standard-library agent skills** ([`skills/`](skills/)) — copy them into any agent's skills
directory and it can work with **real, public SEC EDGAR data** (no login, no API key, no paid provider):

- **[`sec-edgar`](skills/sec-edgar/)** — the foundation. Resolve any ticker, list a company's filings, and
  **identify what each filing type is and how to read it** (proxy/comp, 10-K, 8-K exec changes, insider
  Form 4, activist 13D, IPO S-1, foreign 20-F, …) via a form-type knowledge map — with SEC fair-access
  (required contact User-Agent, throttle, retry) built in.
- **[`sec-comp-research`](skills/sec-comp-research/)** — builds on the foundation for the **proxy-season
  workflow**: find the DEF 14A, read the Summary Compensation Table, screen a size + industry peer group,
  and position pay at target percentiles. The real-data companion to the Executive Compensation arm above.

## Examples (reference patterns)

- **[Talent Acquisition reporting agent](examples/ta-reporting/)** — the recruiting-pipeline
  reporting agent: open requisitions, the weekly operating report **citing the canonical metric
  registry**, a Day-1 digest, and a **human publish gate**. Run it: `cd examples/ta-reporting && python3 run.py`.
- **[Compensation reporting agent](examples/comp-reporting/)** — the **Total Rewards** report and
  **measurement governance**: the registry forbids the comp metrics' `recommend_pay_change`/`change_salary`,
  so the agent flags out-of-band pay but **never recommends or changes a salary**.
  Run it: `cd examples/comp-reporting && python3 run.py`.
- **[Visible handoff](examples/visible-handoff/)** — the governance spine end to end: a cited
  recommendation in `#people-analytics`, an **entitled human approves with a ✅**, and only then does
  the gated publish run — every step a row in a hash-chained ledger.
  Run it: `cd examples/visible-handoff && python3 run.py`.

---

## Governance

The controls above are documented, each tied to the working code:

- [event-log](governance/event-log.md) — ledger data dictionary, invariants, integrity model
- [approval-registry](governance/approval-registry.md) — role-scoped, pool-based approvals
- [hitl-matrix](governance/hitl-matrix.md) — reversibility × impact; what's never agent-autonomous
- [model-and-agent-cards](governance/model-and-agent-cards.md) — per-agent transparency card (NIST AI RMF "Map")
- [retention-risk-model-card](governance/retention-risk-model-card.md) — the governed retention-risk model: purpose, **prohibited uses**, fairness, explanation limits, employee-facing boundaries
- [change-request-template](governance/change-request-template.md) — every behavior change is a controlled experiment
- [prompt-injection-threat-model](governance/prompt-injection-threat-model.md)
- [data-classification](governance/data-classification.md) — no real system-of-record PII in the vault or ledger (pseudonymous; synthetic names may appear in examples; heuristic backstop)
- [data-retention-and-erasure](governance/data-retention-and-erasure.md) — keep the proof, not the person (GDPR Art. 17)
- [bias-audit-cadence](governance/bias-audit-cadence.md) — staying audit-ready for NYC LL144 / EEOC
- [regulatory-readiness](governance/regulatory-readiness.md) — EU AI Act, NYC LL144, GDPR, NIST AI RMF (implemented vs. production-adds)
- [people-operating-cadence](governance/people-operating-cadence.md) — how a human still runs it

**Measurement governance.** Every number a reporting agent emits is defined *once* in a
canonical [metric registry](vault/90-people-analytics/metrics/metrics.registry.json) — **72
metrics across 12 People domains** (ISO 30414-aligned), each tagged Core KPI / Diagnostic /
Operational Alert with an implementation protocol and, where it differs, the correct group
formula. Each metric also declares what an agent **may** do (calculate, trend, flag) and what
it **must not** (e.g. change or recommend pay) — and [`core/metrics.py`](core/metrics.py)
independently rejects any registry where a metric grants a dangerous action. The human-readable
[metrics glossary](vault/90-people-analytics/metrics-glossary.md) is generated from it; reporting
agents cite it instead of redefining metrics. The catalog was hardened across several independent
audit passes (correctness, gameability, ISO 30414 / WorldatWork / SHRM alignment).

The registry runs on a shared platform: a deterministic, synthetic **data foundation**
([`foundation/data`](foundation/data)) — an Acme Corp HRIS/comp/benefits/cases dataset — and a
shared **compute engine** ([`foundation/compute/engine.py`](foundation/compute/engine.py)) that
computes each metric *once*, honoring its protocol (average-headcount denominators, simple
annualization, matured cohorts, every-manager span). The engine is read-only by construction — it
has no method that could change a salary, rating, or record — and returns `data_pending` (never a
fabricated number) for metrics whose source table isn't modeled yet. The People-function **arms**
(analytics, compensation, benefits) are reporting agents built on this engine.

The knowledge layer is an Obsidian/Git [`vault/`](vault/) — process-centric (no direct identifiers) and
frontmatter-linted (`tools/vault_lint.py`).

---

## Design principles

1. **Identity is immutable, behavior is not.** Every agent has a marked, unchangeable
   section in its `SOUL.md` that defines its non-negotiable guardrails.
2. **No agent is free.** Every agent declares a budget and the cheapest model tier that
   can do its job. Expensive models are opt-in, never default. See
   [cost governance](docs/cost-governance.md).
3. **Nothing ships unverified.** A change isn't "done" until it passes the
   [verification checklist](docs/verification.md) — no "probably works."
4. **Fail closed.** When an agent can't confirm the world is safe, it stops rather
   than acting blind.
5. **Everything is auditable.** The decision ledger is the source of record for what every
   agent decided (the HRIS/ATS stays the source of record for *data*, chat for the
   *conversation* — see [architecture](docs/architecture.md)). In production a circuit breaker
   pauses any agent that trips a hard rule; that breaker is a documented design pattern here,
   not runnable code.
6. **Humans stay in the loop.** Agents recommend, and act only within tight bounds; a
   person owns every decision that carries real-world consequences.
7. **The fleet improves under change control.** Performance is scored continuously; behavior
   changes are proposed as controlled experiments (hypothesis, baseline, eval window) with a
   named approver and a rollback — agents never silently change their own controls.

---

## What this repo is *not*

This is a set of conventions and templates, not a runtime or an SDK. Bring your
own LLM client and scheduler. The reference pattern is intentionally simple:
plain files, plain Python, explicit budgets, and visible audit trails.

The patterns here are proven: the same operating system runs a 30+ agent autonomous fleet
in production today.

## License

MIT — see [LICENSE](LICENSE).
