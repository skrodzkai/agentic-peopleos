# Approval registry

A reaction is not an approval until **identity and authority** are modeled. Implemented in
[`core/approval_registry.py`](../core/approval_registry.py); re-verified in the ledger.

## Model: role-scoped, satisfied by a pool

- A **decision class (scope)** — e.g. `publish.ta_report` — requires a **role** (`hr_approver`).
- A role is held by a **pool** of several people. **Any one** entitled person can approve, so
  vacation, illness, or turnover never blocks a decision — there is no single point of failure.
- **Channels are ACL'd:** only members may post or react (enforced at the messaging surface and
  re-verified in the ledger).

## What's recorded for every approval

`actor.id`, `actor.role`, `scope`, `decision`, the `entitled` adjudication, a human-readable
`reason`, a `policy_ref`, and `causation_id` binding it to the recommendation. The ledger
**re-derives** entitlement from the registry on replay — the logged flag is never trusted, so a
forged approval is detectable (`core/tests/test_event_log.py`).

## Rejections the model enforces

- a non-HR human, a bot/agent, or an unknown actor cannot approve;
- a non-member cannot post or react;
- an approval for one scope cannot authorize an action under another (no scope confusion);
- a replayed reaction cannot double-approve; a retracted reaction authorizes nothing.

## Production path

Project the pool onto an IdP/SCIM group; reactions resolve to verified workforce identities;
high-impact/irreversible decisions can require a two-person rule (see
[hitl-matrix](hitl-matrix.md)). The reference registry is `ApprovalRegistry.ACME`.
