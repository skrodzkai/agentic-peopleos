---
type: reference
owner: People Analytics
status: approved
last-reviewed: 2026-06-20
---

# Diversity metrics

Generated from `metrics.registry.json` — the single source of truth. Do not edit by hand.

Each metric is tagged **Core KPI**, **Diagnostic**, or **Operational alert**. The Formula cell also carries the group/aggregate form (where it differs) and the implementation protocol.

| Metric | Definition | Formula / group / protocol | Grain | Decision it supports | Agent may / must-not |
|---|---|---|---|---|---|
| **Representation by level**<br><sub>Core KPI</sub> | Demographic-group share of the population at each level. | `count(group at level) / total_at_level`<br>_protocol:_ Always cut by level; enforce minimum cell sizes; never expose individuals. | cohort | DEI strategy, board reporting, regulatory context. | ✓ calculate, trend, segment, draft_summary<br>✗ identify_individual_publicly, make_hiring_decision |
| **Leadership diversity**<br><sub>Core KPI</sub> | Demographic-group share among people-leaders / senior levels. | `count(group in leadership) / total_in_leadership`<br>_protocol:_ Define the leadership scope (people-managers vs senior band); minimum cell sizes; pair with promotion_rate equity cuts. | cohort | Advancement equity and board credibility. | ✓ calculate, trend, segment, draft_summary<br>✗ identify_individual_publicly, make_hiring_decision |
| **Adverse-impact ratio (4/5ths)**<br><sub>Core KPI</sub> | Selection rate of a group relative to the highest-selected reference group (EEOC four-fifths rule). | `selection_rate(group) / selection_rate(reference_group)`<br>_protocol:_ Flag when the ratio &lt; 0.80; minimum cell sizes; this is a flag for investigation, not a legal conclusion. | cohort | Selection-fairness monitoring, especially with automated screening. | ✓ calculate, segment, flag_outliers, draft_summary<br>✗ make_hiring_decision, identify_individual_publicly |
