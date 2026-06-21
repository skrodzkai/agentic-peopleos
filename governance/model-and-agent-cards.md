# Model & agent cards

Every agent in the fleet ships a short, standard **card**: what it does, what it must not do,
what it runs on, and how its risk is managed. This is the "Map" function of the
[NIST AI RMF](regulatory-readiness.md) and the per-system transparency the
[EU AI Act](regulatory-readiness.md) expects for high-risk employment tools.

A card is not a new artifact to maintain by hand — it is a **view assembled from things the
agent already has**: its [`SOUL.md`](../docs/architecture.md) (identity + immutable guardrails),
its [`tools.yaml`](../examples/ta-reporting/tools.yaml) (granted capabilities), its
[`cost_tracker.json`](../examples/ta-reporting/cost_tracker.json) (model tier + budget), and the
[metric registry](../vault/90-people-analytics/metrics/metrics.registry.json) (what it may and
must not do with each number).

## Card template

```markdown
# Agent card — <agent-name>

- **Domain / owner:** <function> · <human owner role>
- **Purpose:** <one sentence — what decision it supports>
- **Inputs:** <data it reads, and the system of record it comes from>
- **Outputs:** <what it produces; who consumes it>
- **Human-in-the-loop:** <which scope(s) it gates on; who is entitled to approve>
- **May / must-not:** <allowed actions> / <forbidden actions> (cite the metric registry where relevant)
- **Model & cost:** <default tier> · <budget> · <escalation rule>
- **Failure mode:** fails closed — <what it does on bad input / uncertainty>
- **Known limitations & risks:** <bias surface, data gaps, what it is NOT validated for>
- **Provenance:** SOUL.md · tools.yaml · cost_tracker.json · evals
```

## Worked example — `comp-reporting`

- **Domain / owner:** Total Rewards · Total Rewards Partner (human)
- **Purpose:** turn a comp snapshot into a pay-equity-and-range report and surface out-of-band pay.
- **Inputs:** synthetic comp snapshot (in production: the HRIS / comp system, read-only).
- **Outputs:** a draft HTML report + a Day-1 digest, for a human to review and publish.
- **Human-in-the-loop:** stops at a publish gate. *This example uses a named-approver gate*
  (`--approved-by`) for the `publish.comp_summary` scope; the **full role-scoped registry gate**
  (entitled `hr_approver` pool, channel ACL, ledger re-verification) is demonstrated end-to-end
  in [`visible-handoff`](../examples/visible-handoff/), which defines and routes that scope.
- **May / must-not:** per the registry, `calculate` / `flag_outliers` / `draft_summary` (and
  `trend` for the exception rate) — **must not** `recommend_pay_change` or `change_salary`.
  Enforced by the metric registry and asserted in
  [`evals/test_comp.py`](../examples/comp-reporting/evals/test_comp.py).
- **Model & cost:** tier-0 (the report is deterministic; no model needed) · monthly budget · escalation opt-in.
- **Failure mode:** fails closed — bad/missing snapshot ⇒ no report, one clean error line, non-zero exit.
- **Known limitations & risks:** reports band position only; it does **not** assess whether the
  bands themselves are equitable, and it does not infer protected attributes. Pay-equity
  *decisions* require the human analysis in the [bias-audit cadence](bias-audit-cadence.md).
- **Provenance:** [SOUL.md](../examples/comp-reporting/SOUL.md) · [tools.yaml](../examples/comp-reporting/tools.yaml) · [cost_tracker.json](../examples/comp-reporting/cost_tracker.json) · evals.

## On model cards specifically

When an agent uses an LLM, the card also records the **model family and tier** (from
`cost_tracker.json`), what the model is and isn't used for (e.g. "writes the narrative digest
from already-computed numbers; never computes a metric or decides an outcome"), and the
escalation rule. Exact model IDs are environment configuration, not baked into docs — the card
points at the config, the config names the model.
