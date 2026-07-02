# Change request template

Agents never silently change their own controls. Every change to a live agent's behavior — a
new prompt, a tuned threshold, a granted tool, a model-tier change, a metric redefinition — is
proposed as a **controlled experiment** with a named approver and a rollback. This is the
"performance review" primitive in the [architecture](../docs/architecture.md) and the **Govern**
function of the [NIST AI RMF](regulatory-readiness.md).

The same discipline the [production fleet](../README.md) runs on: baseline, hypothesis, evaluation
window, verdict — not an ad-hoc tweak.

## Template

```markdown
# Change request — <agent> — <short title>

- **CR id:** CR-<yyyy>-<nnn>
- **Author / date:** <name> · <yyyy-mm-dd>
- **Agent(s) affected:** <agent-name(s)> · domain
- **Type:** prompt | threshold | tool grant | model tier | metric definition | guardrail
- **Touches the immutable SOUL section?** no  (if yes → this is not a CR; it requires a new agent + review)

## What & why
- **Change:** <precisely what changes — file, field, old value → new value>
- **Hypothesis:** <expected effect, stated so it can be falsified>
- **Baseline:** <current metric / behavior being improved on>

## Evidence & safety
- **Evaluation window:** <dates / sample size before a verdict>
- **Success metric & threshold:** <how we'll know it worked>
- **Blast radius:** <what else this could affect — e.g. shared modules, downstream agents>
- **Rollback:** <exact command / revert; restores the baseline>
- **Verification:** <which evals + the verification checklist must pass>

## Approval
- **Recommended by:** <agent / analyst>     (recommend-only)
- **Approved by:** <entitled human + role>   (scope; recorded in the decision ledger)
- **Verdict (post-window):** keep | revert | iterate — <date, by whom>
```

## Rules

1. **Recommend ≠ apply.** An agent (or analyst) may *author* a CR; only an **entitled human**
   approves it, and the approval is an event in the [decision ledger](event-log.md) like any
   other gated action.
2. **The immutable section is off-limits.** A CR may change operating *principles* and
   parameters, never an agent's marked guardrails. Changing those means retiring the agent and
   onboarding a new one through review.
3. **No verdict, no permanence.** Every CR has an evaluation window and a recorded verdict
   (keep / revert / iterate). A change with no verdict is reverted by default.
4. **Always reversible.** No CR is approved without a written rollback that restores the
   baseline, consistent with the [verification checklist](../docs/verification.md).
5. **Metric changes are governed too.** Editing the
   [metric registry](../vault/90-people-analytics/metrics/metrics.registry.json) is a CR — the
   `core/metrics.py` validator must still pass, and the glossary is regenerated and re-committed.
