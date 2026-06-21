# SPEC — Visible handoff

## Flow

1. **request** — the coordinator asks for the weekly TA report (in `#people-analytics`).
2. **recommendation** — the TA-reporting agent computes the report (reusing the
   `ta-reporting` example so the numbers are real) and posts it as a draft **requiring
   approval**, with cited evidence (`requires_approval: true`, `scope: publish.ta_report`).
3. **approval** — a human reacts ✅. The system adjudicates entitlement via the registry and
   records an `approval` event (`decision`, `entitled`, `by`, `scope`, bound to the
   recommendation by `causation_id`).
4. **action** — only on an entitled `approved`, the reporter publishes. The `action` event is
   `gated: true` and bound to the approval by `causation_id` + matching `scope`.

If approval is missing, retracted, or unentitled → an `escalation` event; nothing publishes.

## Event schema (ledger)

Each line of `output/events.jsonl` is one event. Caller fields: `ts, actor{id,display,kind,role},
channel, type, payload`, plus `case_ref, correlation_id, causation_id, idempotency_key,
requires_approval, scope, gated, approval{...}`. The ledger assigns the integrity fields:
`schema_version, sequence, event_id` (content-addressed), `prev_hash`, `event_hash`, and an
optional `hmac`. `type ∈ {request, response, recommendation, approval, action, escalation, fyi}`.

## Binding rules (enforced by `validate_log`)

- An `approval` with `decision: approved` must reference its `recommendation` via `causation_id`.
- Entitlement is **re-derived** from the approval registry — the logged `entitled` flag is
  never trusted (`validate_log(path, registry=...)`).
- A gated `action` must reference an **entitled** approval via `causation_id` **and** carry the
  **same `scope`** — preventing decision laundering and scope confusion.
- `idempotency_key` makes reaction processing exactly-once.

## Authority model

Role-scoped, satisfied by a **pool**: a scope (e.g. `publish.ta_report`) requires a role
(`hr_approver`) held by several people, so any one entitled person can approve and vacation or
illness never blocks the work. Channels are ACL'd (only members may post/react), enforced both
at the messaging surface and re-verified in the ledger.

## Injection handling

Channel messages and notes are untrusted. Only `trusted_policy` (by provenance) is
authoritative, and a message can never be an approval. An injected "approve everything" message
is recorded as detected-and-ignored (`fyi` with `security: ignored_untrusted_instruction`).

## Integrity model (honest about limits)

The SHA-256 hash chain proves **internal consistency / no in-place edit**. It does **not** prove
non-repudiation — an attacker who rewrites the whole file can recompute every hash. Sign the
ledger (`EventLog(path, secret=...)`) to detect a wholesale rewrite; production anchors the head
hash in a KMS-signed checkpoint and stores the ledger on WORM/append-only media.
