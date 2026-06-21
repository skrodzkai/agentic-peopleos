# Human-in-the-loop (HITL) matrix

Which decisions an agent may take alone, and which require a human — on two axes:
**reversibility** × **impact on a person**. This is the most defensible public position on
AI-in-HR, and it maps directly to the scopes the [approval registry](approval-registry.md)
enforces.

| | Informational | Process | Decisional |
|---|---|---|---|
| **Easy to undo** | agent autonomous | agent acts, human-reviewed sample | agent recommends → human approves |
| **Hard to undo** | agent acts, human-reviewed | agent recommends → human approves | two-person approval; agent advisory |
| **Irreversible** | agent acts, human-audited | two-person approval; agent advisory | **never agent-autonomous** — human decides, agent informs only |

## Worked examples

| Decision | Cell | In this repo |
|---|---|---|
| Answer a policy question (cited) | informational / easy | `policy_lookup` answers or escalates |
| Surface an aging requisition | process / easy | `ta-reporting` flags; human owns the call |
| Publish the weekly report | process / hard | gated `publish.ta_report` approval (visible-handoff) |
| Comp band exception | decisional / hard | `publish.comp_summary` scope; HR approver |
| Performance rating, hire/no-hire, termination | decisional / irreversible | **never agent-autonomous** — not an automatable scope |

## Enforcement

Every "→ human approves" cell is a gated action: the agent posts a recommendation, an entitled
human approves (reaction), and the ledger binds action→approval→recommendation. Irreversible
decisional actions are simply not modeled as agent scopes — the agent can inform, never decide.
