# Agent layer

The agents that operate this vault. Each is a functional agent with a job description
(`SOUL.md`), scoped tools, a budget, and an eval — and each works through the governance
spine (recommend → entitled human approval → gated action), recorded in the event ledger.

| Agent | Function | Human owns | Lives in |
|---|---|---|---|
| `ta-reporting` | People analytics & reporting (recruiting) | the decision + what's published | [`../../examples/ta-reporting`](../../examples/ta-reporting) |
| `coordinator` | routes requests, opens cases | escalations | [`../../examples/visible-handoff`](../../examples/visible-handoff) |

Runtime: [`../../core`](../../core) — event ledger, approval registry, content typing, messaging.
Governance: [`../../governance`](../../governance).

> Roadmap (from the research): `comp-band-auditor`, `attrition-watch`, `policy-q-and-a` —
> each a different point on the human-in-the-loop matrix.
