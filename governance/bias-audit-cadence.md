# Bias-audit cadence

NYC Local Law 144 requires an **annual independent bias audit** of an Automated Employment
Decision Tool, plus candidate notice. The EEOC expects **adverse-impact** analysis of selection
procedures. This document is the operating cadence that keeps the system *audit-ready* — it does
**not** replace the independent audit itself, which is performed by a qualified third party (see
the ⛔ column in [regulatory-readiness](regulatory-readiness.md)).

The enabling design choice is upstream: **agents flag and recommend; humans decide.** Because
every consequential decision is a human action recorded in the [decision ledger](event-log.md),
the population an auditor needs — who was advanced, by whom, against what recommendation — is
already structured and queryable. The audit measures *outcomes*, not chat logs.

## Cadence

| Cadence | Activity | Owner | Output |
|---|---|---|---|
| **Per decision** | the recommending agent records inputs, the recommendation, and the human decision as ledger events | agent + entitled human | the audit population, accumulating continuously |
| **Monthly** | internal adverse-impact check (4/5ths rule) on selection rates by group, from ledger queries | People Analytics | a flag if any group's selection rate < 80% of the top group's |
| **Quarterly** | review agent cards + metric definitions for drift; confirm no agent acquired a decisional action | Function owner + governance | sign-off or a [change request](change-request-template.md) |
| **Annual** | **independent third-party bias audit**; publish required results; refresh candidate notices | external auditor + Legal | the LL144 audit + public summary |

## What gets measured

- **Selection rate by stage and group** — to compute adverse impact (4/5ths). Groups and the
  protected-attribute join live in the **system of record**, never in the vault or ledger
  ([data-classification](data-classification.md)); the analysis joins them at query time under
  access control.
- **Recommendation→decision concordance** — how often humans follow vs. override an agent. A
  very high follow rate on a consequential scope is itself a risk signal (rubber-stamping) and a
  reason to revisit the [HITL matrix](hitl-matrix.md).
- **Metric integrity** — that reporting agents still cite the canonical
  [metric registry](../vault/90-people-analytics/metrics/metrics.registry.json) and that no
  metric grants a decisional action (the `core/metrics.py` validator runs in CI).

## What an agent is *not* allowed to do

No agent infers, stores, or reports protected attributes, and no agent makes a hiring,
promotion, or termination decision — the [metric registry](../vault/90-people-analytics/metrics/metrics.registry.json)
marks `make_hiring_decision`, `recommend_termination`, and `identify_individual_publicly` as
forbidden, and the validator rejects any registry that grants them. The bias audit checks the
**human** decisions the agents informed, which is exactly where the legal obligation sits.
