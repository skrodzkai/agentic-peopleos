# Model card — retention-risk (Workforce Planning & Retention Risk)

A governed, **segment-first** retention-risk model for workforce planning. This card documents the published
trained artifact at
[`foundation/compute/manifests/retention_model_manifest.json`](../foundation/compute/manifests/retention_model_manifest.json)
and the shared compute in [`foundation/compute/retention.py`](../foundation/compute/retention.py). It follows
the [card template](model-and-agent-cards.md) — the "Map" function of the
[NIST AI RMF](regulatory-readiness.md) and the per-system transparency the
[EU AI Act](regulatory-readiness.md) expects for a high-risk employment tool.

Everything here runs on **synthetic Acme data**. The numbers demonstrate the *mechanics and governance* of
such a model; they are **not** a claim of external predictive validity, and nothing here was fit on, or is to
be used on, real people.

## What it is

- **Domain / owner:** People Analytics · HR Business Partner + People Analytics lead (human owners).
- **Purpose:** help a People team **plan** — surface where voluntary-attrition pressure is concentrated across
  the workforce, so leaders can invest in retention (comp review, manager support, career pathing) *earlier
  and more fairly*. It supports a planning conversation; it does not make a decision.
- **Model:** a **glass-box discrete-time monthly hazard** — a pure-Python, L2-regularized logistic regression
  (fit by IRLS) over a 36-month person-month panel, layered into P(exit ≤ 6/12 mo), a survival curve, a
  median-time-to-exit, and low/elevated/high **support tiers**. Every coefficient is inspectable; explanations
  are the **exact** additive per-feature contributions (contributions + intercept = the logit), not a post-hoc
  approximation. Probabilities are out-of-sample **Platt-calibrated**. Standard library only, deterministic,
  offline, fail-closed.
- **Target — voluntary only, competing risks censored:** the label is a **voluntary** exit in the following
  month. Involuntary terminations and retirements are **cause-specific censored** (coded 0, never a positive)
  so the model never learns to predict — or is credited for predicting — a company-initiated exit.
- **Out-of-time evaluation:** train (months ≤ 23) / calibration (24–29) / **test (30–35)**, a genuine
  forward split. Governed on **PR-AUC / precision@k / calibration / survival-concordance under class
  imbalance**, never on accuracy. A **realism guard** fails the build if the synthetic model looks
  implausibly perfect (ROC-AUC > 0.90, PR-AUC > 0.60, a perfect precision@k, or a decoy feature in the top-3
  by |coefficient|) — a tell for leakage or an overfit demo.

## Inputs & the feature-governance line

- **Inputs (synthetic):** a point-in-time monthly panel of job/comp/tenure/engagement-slope/team-context
  features, built **row-locally** so a row's features are known strictly as-of that month (leakage-free by
  construction).
- **Excluded from the model, kept only for the fairness audit:** protected/sensitive attributes are **never**
  model inputs. A neutral `audit_group` stratum is retained *only* to measure fairness, and is independent of
  the hazard.
- **Quarantined (require legal/privacy approval before use):** surveillance-adjacent signals
  (commute, PTO/leave, after-hours activity, work-model) are **not** in the allowlist. This model does not use
  them.
- **Raw tenure is excluded** as an age/cohort proxy; tenure enters only through coarse bands and a documented
  danger-zone flag.

## Outputs & who consumes them

- **Primary output is segment-level:** 6-month voluntary-exit risk **by function / level / manager-span band /
  tenure band / comp-position band / region band**, reconciled two ways (a bottom-up aggregate of the model's
  calibrated hazards vs a top-down empirical Kaplan-Meier) with the **gap surfaced, not averaged away**.
  Segments below a small-n floor are **suppressed** (no estimate, only a coarse size band) — the floor can be
  raised but never lowered.
- **Consumers:** HR Business Partners and People Analytics, for planning. Region is **broad-only** (never
  country-level).

## Human-in-the-loop & the ledger

- The model **recommends context for a planning conversation**; a human owns every consequential decision.
- Any per-employee scoring (a **future increment**, not built here) **will be** synthetic-IDs-only,
  approval-gated, and routed through People Analytics / HRBP — never a manager-facing "who will quit" board.
- The decision-ledger **design** logs **hashes / IDs / score / band / disposition / outcome — never raw
  sensitive features** (the ledger primitive is built in `core/event_log.py`; wiring the retention scores
  through it is a future increment).

## Prohibited uses (must-not)

- **Must not** be used to select, rank, discipline, deny opportunity to, or take any adverse action against a
  named individual. It is a **planning** signal, not an adverse-action tool.
- **Must not** be surfaced to line managers as an individual flight-risk score/leaderboard.
- **Must not** ingest the quarantined surveillance-adjacent signals, or any protected attribute, as a model
  input.
- **Must not** be represented as validated on real employees, or as a hiring/firing/promotion decision system.
- **Must not** drive a comp change directly — a `comp review suggested` flag routes to the separate
  comp-governance process; it never changes pay.

## Fairness — NOT YET SHIPPED (do not rely on this build for a fairness determination)

The **thick fairness card** is the intended governance surround but is **future-state** — it does **not** exist
in this build and this model must **not** be represented as fairness-validated. What ships today is the
*scaffolding* for it: the segment reconciliation, small-n suppression, protected attributes excluded from
inputs, and a retained neutral audit stratum. What is **NOT built yet** (the fairness-audit increment): group
calibration, FPR/FNR and equal-opportunity gaps, subgroup precision@k, confidence intervals, and documented
remediation. Four-fifths (adverse-impact ratio) alone is treated as **too thin**. Until the fairness layer
exists, treat any group-level use as unvalidated.

## Employee-facing & privacy boundaries

For a real deployment (this is a synthetic demo), the model card carries the operational boundaries — not just
the governance docs:
- **Notice** — employees in scope are informed the model exists, what it is for (planning, not adverse action),
  and what data classes feed it; it is never a covert score.
- **Human review & appeal** — a person owns every consequential decision; an affected employee can request a
  human review of any planning action taken in reference to a score. No automated adverse action.
- **Data-subject rights** — in a real deployment, access / correction / erasure requests (GDPR Art. 15-17)
  are served against the **features and any retained score** in the systems of record; see
  [data-retention-and-erasure](data-retention-and-erasure.md). Erasure does **not** rewrite the append-only
  decision ledger (that would break its integrity) — by design the ledger holds only
  hashes/IDs/score/band/disposition, **never raw sensitive features**, so a person's raw data is erasable
  while the audit proof survives. (This build is a synthetic demo; no real person's data is processed.)
- **Score retention** — scores are retained only as long as the planning cycle needs them, then expired on the
  documented retention schedule; a stale score is never carried forward as a standing label.

## Failure mode

Fails **closed** everywhere: a malformed panel, a non-canonical temporal slice, a non-finite number, a
singular calibration, an implausibly-perfect model, or a drifted/forged manifest each raise a controlled error
and stop — never a silent number. The manifest re-fits and reproduces within tolerance in CI, with
version / increment / row-count / feature-set provenance pinned.

## Known limitations & risks

- **Synthetic-validation only** — the reported metrics demonstrate mechanics on planted signal + realism
  noise; they are **not** an external-accuracy claim and must not be quoted as one.
- **Intervention paradox** — acting on scores corrupts future labels (self-defeating and self-fulfilling
  loops). A randomized/stepped-wedge **holdout** is required to keep an unbiased read; it is a first-class part
  of the design (holdout increment).
- **Explanation limits** — the additive contributions are exact *for this linear model*; they are associational
  (this model's decomposition), **not causal**, and must not be read as "the reason" an individual would leave.
- **Not a challenger yet** — a boosted-stumps challenger and the production-upgrade path
  (LightGBM/SHAP/lifelines/MLflow) are named but **not** built here; this is the governed reference skeleton.

## Provenance

[retention.py](../foundation/compute/retention.py) · [manifest](../foundation/compute/manifests/retention_model_manifest.json)
· [tests](../foundation/compute/tests/test_retention.py) (glass-box hazard + out-of-time eval + realism guard
+ segment reconciliation; re-fit + reproduced in CI) · this card. Model IDs / tiers are environment config,
not baked into docs.
