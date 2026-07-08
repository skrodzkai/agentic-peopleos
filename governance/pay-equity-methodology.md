# Pay-Equity & EU Pay Transparency — methodology

The pay-equity arm (`foundation/compute/pay_equity.py` + `examples/pay-equity/`) reconstructs, on synthetic
data, the two measurements a Total-Rewards function owes the board and — from 2026-27 — regulators under the
**EU Pay Transparency Directive (Directive (EU) 2023/970)**: the pay gap it must publish, and the like-for-like
analysis an equal-pay audit rests on. This note states exactly what is methodology-faithful and what is
illustrative, so nothing here is mistaken for legal output.

## 1. Source of truth
Everything is derived from one committed table, `foundation/data/acme/workers.csv`:
- **Population** — employees (`worker_type == employee`) in `active` or `on_leave` status. Contractors and
  terminated workers are out of scope and counted in an explicit exclusion ledger.
- **Pay** — `base_salary`, normalised to a comparable **FTE hourly rate** = FTE-annual base ÷
  `standard_full_time_hours`. In this data `base_salary` is already a full-time-equivalent figure (a
  part-timer records the same annual base as a full-timer at the same level), so dividing by the role's
  standard full-time hours yields the Directive's hourly basis without part-time distortion. Base pay only —
  no bonus, equity, or benefits.
- **Protected class** — `gender_group` (A/B) and `ethnicity_group` (grp1-3), both **pseudonymised** in the
  synthetic data.
- **Legitimate controls** — job `level`, `job_family`, `location`, tenure (from `hire_date` to the fixed
  as-of date), performance `rating`, and `is_people_manager`.

The engine is stdlib-only, deterministic, offline, and fails closed on any malformed row.

## 2. Methodology-faithful
- **Mean and median gaps, both reported.** The Directive (Articles 9-10) requires the gender pay gap on
  **both** a mean and a median basis; the engine reports both, per group, against the highest-paid group as
  reference.
- **Raw vs adjusted, kept distinct.** The **raw** gap mixes pay-setting with workforce composition. The
  **adjusted** gap is the coefficient on the protected-class indicator in an OLS of `ln(FTE-hourly)` on the
  indicator plus the controls above (`foundation/compute/regression.py`, homoskedastic standard errors), i.e.
  the like-for-like residual — reported as a percentage with a **95% confidence interval**. A gap is called
  material only when its whole CI stays off zero.
- **The 5% joint-pay-assessment trigger.** Article 10 obliges an employer to run a **joint pay assessment**
  with worker representatives when pay reporting shows a difference in **average** pay of **at least 5%** in
  any **category of workers** that is not justified by objective, gender-neutral criteria and is not remedied
  within six months. The engine computes, per category, the within-category gap between the advantaged and
  disadvantaged group on **both** the mean (the Article 10 trigger) and the median (also mandated for
  reporting), flags a category whose mean gap **reaches or exceeds 5%** (Article 10 is "at least 5%", so the
  comparison is `>=`), and surfaces a mean-clean/median-≥5% category as a watch rather than a trigger. Gaps
  are shown **before** objective-factor justification — the point at which the trigger is evaluated. The
  resulting flag is a **screen flag**, not a legal determination.

## 3. Illustrative (labeled, never claimed as legal output)
- **Pseudonymised groups.** The tool reports gaps between groups and **never asserts** which real protected
  class a label (A/B, grp1-3) denotes.
- **"Category of workers" = job level.** A stand-in for the Directive's "equal work or work of equal value"
  grouping, which in production is defined by a **gender-neutral job-evaluation scheme**, not a single level
  field.
- **Base pay only, observable controls only.** Real pay-equity work covers total remuneration and accounts
  for factors not in this table (prior pay, negotiated offers, role scope). A surviving adjusted gap here is a
  **flag for a privileged, cohort-level equal-pay review**, not a legal conclusion.

## 4. Calibration (synthetic Acme, as of 31 Jan 2026)
- 2,023 employees analysed (2,660 workers less 260 contractors and 377 terminated).
- Gender: raw median gap ≈ **3.1%** (group B vs A), raw mean ≈ 2.7%; **adjusted ≈ +0.4%**, CI spans zero
  (not statistically distinguishable from zero); model R² ≈ 0.96. The raw gap is overwhelmingly composition.
- EU screen: one category (**L7**) crosses the 5% mean trigger (≈5.8%), with L6 a near-miss (≈5.0%) — so a
  joint pay assessment is indicated at L7. The disadvantaged group flips by level, which is why the
  company-wide adjusted gap is near zero while a specific level still trips the category trigger.

## 5. Guardrails (what the arm never does)
- Never recommends, sets, or authorises a pay change, raise, or adjustment for any group or individual.
- Never de-anonymises or asserts the real identity of a protected-class group.
- Never presents the adjusted gap as a legal conclusion, or the tool as legal advice.
- Never emits an individual's name or a direct identifier; the analysis is group-level on synthetic ids.
- Never distributes without a named human approver (the publish gate).

## References (public)
- Directive (EU) 2023/970 of 10 May 2023 (EU Pay Transparency Directive) — Articles 9 (reporting: mean and
  median gaps, by category of workers) and 10 (joint pay assessment; the 5% trigger).
