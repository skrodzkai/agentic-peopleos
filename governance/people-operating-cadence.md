# People operating cadence

Agents do the work; the People function still has to be *run*. This is the operating rhythm a
People leader keeps on top of an agent fleet — the part a software-only build leaves out. It is
written to be owned, not admired: every loop has a DRI, a threshold, and an evidence artifact.

## Directly responsible individuals (DRIs)

| Area | DRI (role) | Owns |
|---|---|---|
| Talent acquisition | Head of TA | pipeline health, hiring decisions, the bar |
| Total Rewards | Director, Total Rewards | comp bands, pay equity, offer/spend approvals |
| People Ops | Head of People Ops | lifecycle execution, policy ownership, cases |
| People Analytics | People Analytics lead | metric definitions, report integrity |
| People Technology / agent governance | People Tech lead | agent behavior changes, the ledger, access |
| Compliance | with Legal | bias audits, EU AI Act / LL144 / GDPR posture |

## Weekly People business review (60 min)

| Segment | Source | Threshold that forces discussion |
|---|---|---|
| Pipeline & risk | `ta-reporting` output | any role with ≥3 at-risk reqs; any req 90+ days |
| Decisions made | ledger query (recommend → approve → act) | any gated action; any escalation unresolved >48h |
| Exceptions | escalation queue | any item past its SLA (below) |
| Cost | per-agent spend vs. budget | any agent >120% of weekly budget |
| Agent changes | change-control log | any behavior change shipped or proposed |

## Exception handling & escalation

| Trigger | Escalates to | SLA |
|---|---|---|
| No entitled approver responds | next pool member, then the function DRI | same business day |
| Requisition with 2+ risk flags | Head of TA | 24h |
| Policy gap / stale policy | the policy DRI | the review cycle |
| Agent produced a wrong/biased output | People Tech + the function DRI | 24h, with incident note |
| Any irreversible decision | the human decision-maker (never the agent) | n/a — not automatable |

## Policy & agent-behavior change control

Changing an agent's behavior is a control change, handled like one — never self-applied:

- **Gate:** hypothesis → baseline → eval window → **named approver** → rollback plan → event trail.
- **RACI:** Accountable = function DRI · Responsible = People Tech · Consulted = Legal/Security ·
  Informed = the team.
- **Record:** the change, its approver, and its result are rows in the ledger.

## Evidence cadence (audit readiness)

| Cadence | Artifact | Owner |
|---|---|---|
| Weekly | business-review notes + the decision-ledger export | People Analytics |
| Monthly | exception/SLA report; cost vs. budget | People Tech |
| Quarterly | access review (approver pools, channel ACLs); agent eval results | People Tech + Compliance |
| Quarterly | bias/adverse-impact review for any decisioning agent | Compliance + Legal |
| Annual | pay-equity analysis; EU AI Act FRIA review; AEDT bias audit (LL144) | Total Rewards / Compliance |

## Closed-loop action tracking

Every flagged risk and every exception is tracked to closure: **surfaced → owner assigned →
action taken → verified next cycle.** The weekly review opens with last week's open items; an item
isn't closed until the metric moves or the DRI signs it off. Nothing falls through because the
ledger is the backstop — you can always ask *what was decided, by whom, and did it get done?*

## Adoption path

Earn trust before granting autonomy: **functional agents first** (read, analyze, flag — low
risk), then human-approved actions, then — only where reversible and low-impact — autonomy.
Conversational/employee-facing agents come last, once the eval and audit discipline is proven.
