# Agentic PeopleOS

**Operating discipline for agent-run People functions.**

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
| Compliance & audit | System-of-record + circuit breakers | [`docs/architecture.md`](docs/architecture.md) |
| Quality gate | Verification checklist before "done" | [`docs/verification.md`](docs/verification.md) |
| Offboarding | Retirement registry + kill switches | [`docs/architecture.md`](docs/architecture.md) |

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

## Examples

- **[Talent Acquisition reporting agent](examples/ta-reporting/)** — a complete, runnable
  agent (synthetic data, standard library only). It reads open requisitions, computes the
  weekly operating report, drafts a Day-1 digest, and **stops at a human publish gate**.
  Run it: `cd examples/ta-reporting && python run.py`.

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
5. **Everything is auditable.** A system-of-record records what every agent did, and a
   circuit breaker can pause any agent automatically.
6. **Humans stay in the loop.** Agents recommend, and act only within tight bounds; a
   person owns every decision that carries real-world consequences.
7. **The fleet improves itself.** Performance is scored continuously; changes are
   proposed as controlled experiments — hypothesis, baseline, evaluation window — and a
   human approves what ships.

---

## What this repo is *not*

This is a set of conventions and templates, not a runtime or an SDK. Bring your
own LLM client and scheduler. The reference pattern is intentionally simple:
plain files, plain Python, explicit budgets, and visible audit trails.

The patterns here are proven: the same operating system runs a 30+ agent autonomous fleet,
including an always-on trading floor, in production today.

## License

MIT — see [LICENSE](LICENSE).
