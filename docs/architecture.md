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
   Auditor             - system of record + circuit breakers
   Performance coach   - weekly calibration / review
   Cost governor       - budgets + model-tier enforcement
```

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
`SOUL.md`, a `run` entrypoint, a `cost_tracker.json`, and a registry entry.

### 3. Headcount — the registry + manifest
A single registry file is the source of truth for who exists, what domain they belong
to, and who owns them. A manifest summarizes the whole org. If the filesystem and the
manifest disagree, that **drift** is surfaced before anything is reconciled.

### 4. Performance reviews — the coach
A dedicated "coach" agent reads frozen performance snapshots on a weekly cadence and
proposes changes as **experiments with a hypothesis and an evaluation window** — never
silent edits. It can recommend; it cannot unilaterally rewrite another agent's strategy.

### 5. Compensation — budgets + model tiers
Every agent is on a budget and uses the cheapest model tier that can do its job.
See [cost-governance.md](cost-governance.md).

### 6. Compliance — the auditor
A continuously running auditor is the system of record: it logs what every agent did,
reconciles state, and runs a **circuit breaker** that can automatically pause any agent
that violates a hard rule. Agents fail *closed* — when they can't confirm the world is
safe, they stop instead of acting blind.

### 7. Offboarding — retirement
Decommissioned agents aren't deleted; they're **retired**: execution is blocked via kill
switches, the code is archived, and the registry records the retirement. This keeps
history intact and makes re-enabling a single, auditable step.

## A note on change control

Every parameter change to a live agent is treated as a controlled experiment — baseline,
hypothesis, evaluation window, verdict — rather than an ad-hoc tweak. This is the single
biggest reason a fleet this size stays stable over months.
