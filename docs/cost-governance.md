# Cost governance

The fastest way to kill an agent project is a surprise bill. Agentic PeopleOS treats model
spend like a compensation budget: every agent has one, and every agent uses the cheapest
tier that can do its job.

## Deterministic model routing

Pick the tier by the *kind of work*, not by habit. Route up only when a quality gate fails.

| Tier | Use for | Rule of thumb |
|---|---|---|
| **Tier 0 — free / local** | scraping, filtering, extraction | default for anything mechanical |
| **Tier 1 — small classifier** | classification, simple evals | cheap, fast, deterministic |
| **Tier 2 — mid model** | synthesis, report generation | the workhorse for real reasoning |
| **Tier 3 — frontier model** | only when explicitly needed | opt-in, never the default |

Two hard rules that prevent runaway cost:

1. **Never use a frontier model for scheduled background work.** Batch jobs run on the
   lowest viable tier and escalate only on a failure or quality gate.
2. **Always log the model choice rationale** for recurring jobs, so cost is auditable
   after the fact.

> Keep concrete model IDs in **one** registry file and reference it everywhere else.
> Hardcoding model names across a codebase guarantees they go stale.

## Per-agent budgets — `cost_tracker.json`

Every agent ships with a [`cost_tracker.json`](../templates/cost_tracker.template.json)
that records spend against a budget. The cost governor reads these to spot the agent
that suddenly costs 10x what it did last week — the same way you'd spot comp drift.

## Why this matters to a reviewer

This is the difference between a demo and a system. A demo calls the biggest model for
everything. A system knows what each task is worth and pays accordingly — and can prove it.
