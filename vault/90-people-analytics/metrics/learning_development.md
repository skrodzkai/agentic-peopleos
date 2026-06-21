---
type: reference
owner: Talent Management
status: approved
last-reviewed: 2026-06-20
---

# Learning & Development metrics

Generated from `metrics.registry.json` — the single source of truth. Do not edit by hand.

Each metric is tagged **Core KPI**, **Diagnostic**, or **Operational alert**. The Formula cell also carries the group/aggregate form (where it differs) and the implementation protocol.

| Metric | Definition | Formula / group / protocol | Grain | Decision it supports | Agent may / must-not |
|---|---|---|---|---|---|
| **Training hours per FTE**<br><sub>Core KPI</sub> | Average formal learning hours per FTE in the period. | `total_training_hours / average_fte`<br>_protocol:_ Per FTE; pair with completion and capability/skill outcomes, not hours alone. | period | L&D investment and capability building (ISO 30414). | ✓ calculate, trend, segment, draft_summary<br>✗ alter_record |
| **Training completion rate**<br><sub>Diagnostic</sub> | Share of assigned learning that was completed. | `completed_assignments / assigned_assignments`<br>_protocol:_ Separate mandatory/compliance completion from development completion. | cohort | Compliance and capability follow-through. | ✓ calculate, trend, segment, draft_summary<br>✗ alter_record |
| **Critical-skill coverage**<br><sub>Core KPI</sub> | Share of critical-skill seat demand met by employees assessed at the required proficiency. | `employees_at_required_proficiency / required_critical_skill_seats`<br>_protocol:_ Denominator = required_critical_skill_seats (the sum of seat demand across critical skills), not a vague headcount; define the critical-skill taxonomy and the proficiency bar; avoid self-assessed proficiency inflating coverage. | cohort | Build-vs-buy and workforce-capability planning. | ✓ calculate, segment, flag_outliers, draft_summary<br>✗ alter_record |
