# Architecture

Agentic PeopleOS organizes a fleet the way an org chart organizes a company. The unit of
work is an **agent**; the unit of governance is the **role it plays in the org**.

## The org

```
People lead       - routing, human interface, escalation
      │
   Function owner  - sets policy for a People function, reviews its agents
      ├── Advisor agents    - specialist guidance, recommend-only
      └── Specialist agents - recruiting, comp, onboarding, policy ... (do the work)

Cross-cutting (watch everyone):
   Auditor             - maintains the decision ledger + circuit breakers
   Performance coach   - weekly calibration / review
   Cost governor       - budgets + model-tier enforcement
```

## Three sources of record (used consistently across this repo)

The system deliberately keeps three records separate, each authoritative for one thing:

- **Decisions, actions, approvals → the decision ledger** ([event-log](../governance/event-log.md)),
  maintained by the auditor. Tamper-evident and replayable.
- **Employee & candidate data → the HRIS/ATS** (the data system of record). The only store that
  holds PII; the only one an erasure request mutates ([data-retention-and-erasure](../governance/data-retention-and-erasure.md)).
- **The conversation → the chat surface.** Human-readable, but never the authority for a
  decision — that lives in the ledger.

In the production pattern, a **fleet registry/manifest** is a fourth, narrower record — the source of
truth for *which* agents exist, as distinct from the ledger's record of what they decided. This public
reference repo demonstrates the per-agent primitives below; it does **not** ship a populated multi-agent
fleet registry (that is a deployment artifact, described here as a design pattern).

## The seven primitives

### 1. Identity — `SOUL.md`
Every agent has a `SOUL.md` with a fixed structure:
1. **Identity** — who it is, what it owns.
2. **Operating principles** — how it makes decisions.
3. **Immutable section** — explicitly marked guardrails that must never change.

This is the agent's job description. Reviews and changes touch principles, never the
immutable section.

### 2. Onboarding — the scaffolder
New agents are never hand-assembled. A scaffolder ([`../scaffold/newagent.sh`](../scaffold/newagent.sh))
stamps out the required files so every agent in the fleet is structurally identical:
`SOUL.md`, a `run` entrypoint, a `cost_tracker.json` (and, in a deployment, a fleet-registry entry).

### 3. Headcount — the registry + manifest  *(design pattern — not shipped populated here)*
In a deployment, a single registry file is the source of truth for *which agents exist*, what domain
they belong to, and who owns them (not for what they decided — that's the ledger); a manifest summarizes
the whole org, and any **drift** between the filesystem and the manifest is surfaced before reconciliation.
This public reference repo describes the pattern but does not ship a populated fleet registry/manifest.

### 4. Performance reviews — the coach
A dedicated "coach" agent reads frozen performance snapshots on a weekly cadence and
proposes changes as **experiments with a hypothesis and an evaluation window** — never
silent edits. It can recommend; it cannot unilaterally rewrite another agent's strategy.

### 5. Compensation — budgets + model tiers
Every agent is on a budget and uses the cheapest model tier that can do its job.
See [cost-governance.md](cost-governance.md).

### 6. Compliance — the auditor
A continuously running auditor maintains the **decision ledger** (the source of record for
decisions, actions, and approvals): it logs what every agent did and reconciles state. The
fail-closed behavior is implemented in the example agents and their evals. In production the
auditor also runs a **circuit breaker** that automatically pauses any agent that violates a hard
rule — that breaker is a documented design pattern here, not runnable code in this reference.

### 7. Offboarding — retirement
Decommissioned agents aren't deleted; they're **retired**: execution is blocked via kill
switches, the code is archived, and the registry records the retirement. This keeps history
intact and makes re-enabling a single, auditable step. *(Like the circuit breaker, the
retirement registry / kill switches are a documented design pattern — a production extension,
not runnable code in this reference repo.)*

## A note on change control

Every parameter change to a live agent is treated as a controlled experiment — baseline,
hypothesis, evaluation window, verdict — rather than an ad-hoc tweak. This is the single
biggest reason a fleet this size stays stable over months.
