---
type: reference
owner: People Operations
status: approved
last-reviewed: 2026-06-20
---

# Compliance & Ethics metrics

Generated from `metrics.registry.json` — the single source of truth. Do not edit by hand.

Each metric is tagged **Core KPI**, **Diagnostic**, or **Operational alert**. The Formula cell also carries the group/aggregate form (where it differs) and the implementation protocol.

| Metric | Definition | Formula / group / protocol | Grain | Decision it supports | Agent may / must-not |
|---|---|---|---|---|---|
| **Grievance rate**<br><sub>Core KPI</sub> | Formal employee grievances per 100 FTE in the period. | `(formal_grievances * 100) / average_fte`<br>_protocol:_ Per 100 FTE; segment by type; pair with resolution time and substantiation rate. | period | ER risk and culture signal (ISO 30414 compliance & ethics). | ✓ calculate, trend, draft_summary<br>✗ identify_individual_publicly, alter_record |
| **Disciplinary action rate**<br><sub>Diagnostic</sub> | Formal disciplinary actions per 100 FTE in the period. | `(disciplinary_actions * 100) / average_fte`<br>_protocol:_ Per 100 FTE; segment by severity and outcome; watch for inconsistency across managers/groups. | period | Workforce-conduct and consistency oversight. | ✓ calculate, trend, draft_summary<br>✗ identify_individual_publicly, alter_record |
| **Ethics-hotline cases**<br><sub>Diagnostic</sub> | Ethics/whistleblower reports per 100 FTE, with substantiation rate. | `(hotline_reports * 100) / average_fte`<br>_protocol:_ Per 100 FTE; report substantiation rate and time-to-close; protect reporter anonymity (never identify). | period | Speak-up culture and compliance risk. | ✓ calculate, trend, draft_summary<br>✗ identify_individual_publicly, alter_record |
