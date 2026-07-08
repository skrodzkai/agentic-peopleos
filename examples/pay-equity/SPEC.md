# SPEC — pay-equity agent

## Purpose
Render the pay-equity / EU Pay Transparency assessment for a People / Total-Rewards leader. Presentation +
governance only; the math lives in `foundation/compute/pay_equity.py`.

## Input
`pay_equity.compute()` — derived entirely from `foundation/data/acme/workers.csv` (employee status, pay,
protected-class group, and the job-related controls). No other input; no external calls.

## Output (deterministic; atomic writes)
- `output/report.sample.html` — the dark pay-equity dashboard.
- `output/day1-digest.sample.md` — the one-page digest.
- `output/report.sample.png` — an illustrative render (not byte-gated).
- `output/PUBLISHED.json` — written only on `--publish --approved-by "<name>"`.

## What it shows
- **Raw gap** (mean + median) between protected-class groups — the numbers the EU Directive requires
  published (headline is the gender lens; ethnicity is a secondary lens).
- **Adjusted gap** — the like-for-like residual from an OLS of ln(FTE-hourly pay) on the protected-class
  indicator + controls (level, family, country, tenure, rating, management), shown as a forest plot: point
  estimate + 95% CI, with the raw gap as a ghost marker.
- **EU 5% joint-assessment screen** — per category of workers (job level), the mean and median gap vs the 5%
  threshold, flagging any category that triggers a joint pay assessment.

## Invariants (fail closed on violation)
- Every adjusted-gap statistic is finite and its 95% CI brackets the point estimate.
- Each dimension's group counts partition the analyzed population; R² ∈ [0,1].
- Each EU category's `exceeds_threshold` flag equals `mean_gap_pct >= 5%` (Article 10 is "at least 5%"); the
  `potential_joint_assessment` flag equals `n_flagged > 0` and is a **screen flag** (before objective-factor
  justification), never a legal determination.
- Publish requires a named approver matching a strict charset; a control-char approver is refused (rc 2).
- Two runs are byte-identical (determinism); a failed/refused run stales any prior published output.

## Explicitly out of scope (never)
Recommending or setting any pay change/raise/adjustment; asserting which real protected class a pseudonymised
label denotes; presenting the adjusted gap as a legal conclusion or the tool as legal advice; naming an
individual; any external send.
