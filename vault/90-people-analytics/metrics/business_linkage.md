---
type: reference
owner: People Analytics + Finance
status: approved
last-reviewed: 2026-06-20
---

# Business Linkage (People & Finance) metrics

Generated from `metrics.registry.json` — the single source of truth. Do not edit by hand.

Each metric is tagged **Core KPI**, **Diagnostic**, or **Operational alert**. The Formula cell also carries the group/aggregate form (where it differs) and the implementation protocol.

| Metric | Definition | Formula / group / protocol | Grain | Decision it supports | Agent may / must-not |
|---|---|---|---|---|---|
| **Revenue per FTE**<br><sub>Core KPI</sub> | Trailing-twelve-month revenue divided by current full-time-equivalent headcount. | `ttm_revenue / current_fte`<br>_protocol:_ Trailing-12-month revenue over current FTE; benchmark only against similar stage/GTM and the company's own multi-year trend (RPE is a lagging indicator). | period | Workforce-efficiency and headcount-investment decisions (the People&lt;-&gt;Finance bridge). | ✓ calculate, trend, segment, draft_summary, surface_to_human<br>✗ make_hiring_decision, recommend_termination, change_salary |
| **Operating leverage (revenue/FTE growth)**<br><sub>Diagnostic</sub> | Year-over-year change in revenue per FTE - whether each FTE produces more revenue over time. | `(revenue_per_fte_now - revenue_per_fte_year_ago) / revenue_per_fte_year_ago`<br>_protocol:_ Report alongside the revenue/FTE and headcount trend; pair with gross margin and retention so a high reading with thin margins isn't misread as health. | period | Whether the company is scaling efficiently (more output with proportionally fewer people). | ✓ calculate, trend, draft_summary, surface_to_human<br>✗ make_hiring_decision, recommend_termination, change_salary |
| **Workforce cost ratio (base-salary basis)**<br><sub>Diagnostic</sub> | Active-employee base salary as a share of trailing-twelve-month revenue. | `sum(active_base_salary) / ttm_revenue`<br>_protocol:_ State the basis explicitly (base salary only); the fully-loaded ratio is labor_cost_per_fte and stays data_pending until benefits/taxes/variable are modeled. | period | Workforce-cost-to-revenue governance (the largest SaaS P&L line). | ✓ calculate, trend, segment, draft_summary, surface_to_human<br>✗ recommend_pay_change, change_salary, make_hiring_decision |
