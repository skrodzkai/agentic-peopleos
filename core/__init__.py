"""Agentic PeopleOS — core runtime.

Reference implementation of the governance spine:

- event_log : an append-only, hash-chained, replayable decision ledger + validator
- approval_registry : role-scoped approval policy satisfied by a pool of entitled humans
- content   : content typing + injection-safe handling of untrusted notes/messages
- messaging : a Slack-first (swappable) messaging adapter that records to the ledger

Standard library only. Deterministic. Offline. The ledger — not Slack, not the vault —
is the source of truth for decisions, actions, and approvals.
"""
