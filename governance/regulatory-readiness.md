# Regulatory readiness

Employment AI is **high-risk** territory, not a normal SaaS workflow. This maps the major
obligations (as of mid-2026) to (a) what is **implemented as a working control in this
synthetic repo** and (b) what a **production deployment must add**. Being explicit about the
gap is the point — a portfolio that claims full compliance from a reference skeleton would be
the red flag. **Not legal advice** — confirm current obligations with counsel for any real
deployment.

| Regulation / framework | Scope | What it requires | Implemented here ✅ | Production must add ⛔ |
|---|---|---|---|---|
| **EU AI Act** — Annex III lists recruitment/selection & employment decisions as high-risk; obligations apply **2 Aug 2026** (Art. 113) | hiring, promotion, termination, performance | risk management, **human oversight**, logging/traceability, transparency | human-owned decisions ([HITL matrix](hitl-matrix.md)); a complete, replayable [decision ledger](event-log.md); cited [model & agent cards](model-and-agent-cards.md) | a **Fundamental Rights Impact Assessment**; registration; conformity assessment; post-market monitoring |
| **NYC Local Law 144** | Automated Employment Decision Tools used on NYC candidates | annual **independent bias audit**, candidate notice, results publication | agents flag/recommend but never decide hiring (decisions human + logged); a [bias-audit cadence](bias-audit-cadence.md) and the queryable trail an audit needs | the **independent third-party audit** itself + published results + candidate notices |
| **GDPR Art. 22** | solely-automated decisions with legal/significant effect | right to **human intervention** | consequential scopes are never agent-autonomous; an entitled human approves, recorded in the ledger | a documented data-subject intervention/appeal process and SLAs |
| **GDPR Art. 17 / CCPA** | personal data rights | access, deletion, minimization | identifying records stay in the HRIS/ATS; vault + ledger carry no direct identifiers (convention + heuristic backstops, [data classification](data-classification.md)); [retention & erasure](data-retention-and-erasure.md) design | the actual erasure pipeline against the systems of record + DPA records |
| **EEOC guidance (US)** | employment selection procedures | adverse-impact (4/5ths) analysis | decision events are structured + queryable, enabling disparate-impact analysis | the recurring statistical analysis + remediation workflow |
| **State pay-transparency laws** | job postings / bands | posting + band publication rules | comp scopes (`publish.comp_summary`) are gated + governed by Total Rewards | jurisdiction-specific posting logic + legal review |
| **NIST AI RMF 1.0** (voluntary framework) | any AI system | **Govern, Map, Measure, Manage** | *Govern:* SOUL guardrails, [HITL matrix](hitl-matrix.md), change control. *Map:* [agent cards](model-and-agent-cards.md). *Measure:* evals + the [decision ledger](event-log.md). *Manage:* fail-closed behavior (in code + evals) | independent measurement, a formal risk register, org-level sign-off, and the runtime **circuit breakers + retirement** (documented design, not runnable code in this repo) |

## How the controls line up to the frameworks

NIST AI RMF's four functions are a useful spine because the rest of the table maps onto them:

- **Govern** — who is accountable and what the rules are: each agent's immutable `SOUL.md`,
  the [approval registry](approval-registry.md), and [change control](change-request-template.md).
- **Map** — what each system is and where it can do harm: [model & agent cards](model-and-agent-cards.md).
- **Measure** — is it working and is it fair: example evals + the replayable
  [decision ledger](event-log.md) + the [bias-audit cadence](bias-audit-cadence.md).
- **Manage** — respond when it isn't: fail-closed behavior (implemented in the example agents
  and their evals); auditor circuit breakers and a [retirement](../docs/architecture.md) path
  are documented designs, not runnable code in this reference.

## The through-line

Three properties cover most of the above: **human ownership of consequential decisions**, a
**tamper-evident audit trail**, and **data minimization** (no PII outside the system of record).
Those are designed in, not bolted on. Everything in the ⛔ column is an organizational and legal
obligation that no code skeleton can satisfy on its own.

## Sources

Verified against primary sources on **2026-06-20** — full citations + quotes in
[`vault/00-foundation/regulatory-landscape.md`](../vault/00-foundation/regulatory-landscape.md):

- EU AI Act — official text **[Regulation (EU) 2024/1689, EUR-Lex OJ](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)**
  (Annex III Point 4 = employment high-risk; Art. 113 = high-risk obligations apply 2 Aug 2026). Plain-language
  cross-reference: [artificialintelligenceact.eu Annex III](https://artificialintelligenceact.eu/annex/3/).
- NYC Local Law 144 — DCWP [AEDT FAQ / guidance](https://www.nyc.gov/assets/dca/downloads/pdf/about/DCWP-AEDT-FAQ.pdf) (final rule adopted Apr 2023; DCWP enforcement from **5 Jul 2023**; annual independent bias audit + candidate notice)
- GDPR — official text **[Regulation (EU) 2016/679, EUR-Lex OJ](https://eur-lex.europa.eu/eli/reg/2016/679/oj)**,
  Art. 22 (solely-automated decisions; right to human intervention). Readable mirror: [gdpr-info.eu Art. 22](https://gdpr-info.eu/art-22-gdpr/).
- [NIST AI Risk Management Framework (AI RMF 1.0)](https://www.nist.gov/itl/ai-risk-management-framework) (Govern / Map / Measure / Manage)
