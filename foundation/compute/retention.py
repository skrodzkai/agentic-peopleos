#!/usr/bin/env python3
"""retention.py — data contract + glass-box hazard model for the `retention-risk` agent.

The shared compute for the governed retention-risk model, built up increment by increment (each behind
an adversarial review). Present through Increment 2:
  * DATA CONTRACT (Inc 0): `load_panel()` fail-closed load + panel-level temporal invariants;
    `feature_snapshot_hash()` (FEATURES ONLY — `model_version` logged separately); `panel_data_hash()`.
  * FEATURE BUILDER (Inc 1): `build_design()` — the pure, row-local, leakage-free panel -> design matrix
    (voluntary-hazard label, missing-indicators; competing risks censored).
  * GLASS-BOX MODEL (Inc 2): out-of-time `temporal_slices()`; `fit_hazard()` — pure-Python L2 IRLS
    logistic discrete-time hazard on the TRAIN slice; survival / horizon / median-or-"not reached";
    exact additive `explain()`; `platt_calibrate()` on the calibration slice; `rank_auc`/`brier_score`.
  * MANIFEST: `build_manifest()` writes the TRAINED artifact; `validate_manifest()` (strict schema) +
    `check_reproducible()` (tolerance sync gate — fitted floats aren't byte-diffed).

Governance properties enforced here:
  * QUARANTINED / PROTECTED columns may **never** appear in the panel (defense-in-depth allowlist).
  * everything fails closed — bad labels, non-finite numerics, singular systems, single-class or empty
    slices, non-convergence, dimension mismatches, and manifest/artifact drift all raise domain errors.
  * the feature-snapshot hash is an INTEGRITY POINTER, not anonymization; the real-data posture
    (documented, not built) uses a keyed HMAC / governed digest service.

stdlib only; deterministic; offline; fail-closed. NOTE: the accuracy CHALLENGER is Increment 5 and the
full test-slice evaluation + realism guard is Increment 3.

CLI:
    python3 foundation/compute/retention.py validate         # panel + manifest schema + model reproducibility
    python3 foundation/compute/retention.py build-manifest   # (re)train the model + emit the canonical manifest
"""
import csv
import hashlib
import json
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
FEATURE_VERSION = "1.0.0"
MODEL_VERSION = "1.0.0"                   # Increment 2: the trained glass-box hazard model
_SCAFFOLD_VERSION = "0.0.0-scaffold"      # sentinel used before a model is trained (Increment 0)
TRAINED_INCREMENT = 3                     # the increment that produced the current trained manifest (pinned both
                                          # in build_manifest and validate_manifest so provenance can't be faked)

ROOT = Path(__file__).resolve().parents[2]
PANEL_PATH = ROOT / "foundation" / "data" / "acme" / "retention_panel.csv"
MANIFEST_PATH = ROOT / "foundation" / "compute" / "manifests" / "retention_model_manifest.json"

ID_COL, TIME_COL, INDEX_COL = "emp_id", "month", "month_index"
LABEL_COL = "event_next"
STRATA_COL = "audit_group"
TARGET_EVENT = "voluntary"
EVENT_VALUES = ("none", "voluntary", "involuntary", "retirement")
AUDIT_GROUPS = ("A", "B", "C")

SEGMENT_COLS = ["function", "level", "region_band", "manager_span_band",
                "comp_position_band", "tenure_band"]

# The model feature set is an EXPLICIT allowlist (numeric, point-in-time). Nothing outside this list
# (segment dims, ids, strata, label, decoys-as-signal) is ever fed to the model as a real predictor.
ALLOWLIST_FEATURES = [
    "comp_ratio", "mths_since_last_raise", "last_raise_pct", "mths_since_promo",
    "promo_velocity_vs_peer", "stuck_in_level_flag", "perf_rating_delta_4q",
    "high_perf_unrecognized", "engagement_slope_3p", "mgr_changed_12m",
    "mgr_team_attrition_ttm", "team_departures_90d",
    "tenure_danger_18_24", "unvested_equity_pct_comp", "days_to_next_vest",
    "post_vest_window_flag", "equity_moneyness", "comp_mix_equity_heavy",
]
# NOTE: raw `tenure_months` is deliberately NOT a model input (it can proxy age/cohort); tenure enters only
# via `tenure_band` (a segment dim) and the `tenure_danger_18_24` flag, per the v3.2 safety spec.
# Named decoys: carried INTO the model's candidate features on purpose, so the realism guard (a later
# increment) can assert they never rank top-3 by importance. Harmless synthetic noise, no true effect.
DECOY_FEATURES = ["decoy_noise_a", "decoy_noise_b", "decoy_noise_c"]
MODEL_FEATURES = ALLOWLIST_FEATURES + DECOY_FEATURES
MISSABLE = {"perf_rating_delta_4q", "engagement_slope_3p"}     # may be blank -> None (genuine missingness)

# Hard deny-list: surveillance-adjacent proxies (quarantined, require legal/privacy approval) and direct
# protected attributes must NEVER appear as columns in the panel at all.
QUARANTINED = {"internal_app_activity", "internal_applications", "commute_change", "pto_anomaly",
               "overtime_trend", "work_model", "rto", "location_fine", "office_distance"}
PROTECTED = {"race", "ethnicity", "gender", "age", "disability", "national_origin",
             "marital_status", "family_status", "pregnancy", "religion"}

REQUIRED_COLS = ([ID_COL, TIME_COL, INDEX_COL] + SEGMENT_COLS + MODEL_FEATURES
                 + [STRATA_COL, LABEL_COL])

_ID_RE = re.compile(r"^R-\d{4}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FLOAT_DP = 6                                                   # decimal quantization for the canonical hash


class PanelError(ValueError):
    """Raised on any panel contract violation — the loader fails closed."""


class ManifestError(ValueError):
    """Raised on any model-manifest schema violation."""


# --------------------------------------------------------------------------- panel load + validate

def load_panel(path: Path = PANEL_PATH):
    """Load + fail-closed-validate the retention panel. Returns a list of typed row dicts:
        {emp_id, month, month_index, segments{}, audit_group, event_next, features{col->float|None}}
    Raises PanelError on the first contract violation (missing/extra/forbidden columns, malformed id
    or date, unknown event or audit group, unparseable feature, duplicate (emp_id, month))."""
    path = Path(path)
    if not path.exists():
        raise PanelError(f"retention panel not found: {path}")
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, restkey="__extra__", restval="__MISSING__")
        header = reader.fieldnames or []
        _validate_header(header)
        rows, seen = [], set()
        for n, raw in enumerate(reader, start=2):             # line 1 is the header
            if raw.get("__extra__"):
                raise PanelError(f"line {n}: too many fields (over-wide row)")
            if any(v == "__MISSING__" for v in raw.values() if isinstance(v, str)):
                raise PanelError(f"line {n}: short row (missing trailing fields)")
            emp = (raw.get(ID_COL) or "").strip()
            month = (raw.get(TIME_COL) or "").strip()
            if not _ID_RE.match(emp):
                raise PanelError(f"line {n}: malformed {ID_COL} {emp!r} (expect R-####)")
            if not _DATE_RE.match(month):
                raise PanelError(f"line {n}: malformed {TIME_COL} {month!r} (expect YYYY-MM-DD)")
            try:
                date.fromisoformat(month)                     # reject impossible calendar dates (e.g. month 13)
            except ValueError:
                raise PanelError(f"line {n}: impossible date {month!r}")
            key = (emp, month)
            if key in seen:
                raise PanelError(f"line {n}: duplicate (emp_id, month) {key}")
            seen.add(key)
            try:
                idx = int(raw[INDEX_COL])
            except (TypeError, ValueError):
                raise PanelError(f"line {n}: non-integer {INDEX_COL} {raw.get(INDEX_COL)!r}")
            if idx < 0:
                raise PanelError(f"line {n}: negative {INDEX_COL} {idx}")
            ev = (raw.get(LABEL_COL) or "").strip()
            if ev not in EVENT_VALUES:
                raise PanelError(f"line {n}: unknown {LABEL_COL} {ev!r} (allowed {EVENT_VALUES})")
            grp = (raw.get(STRATA_COL) or "").strip()
            if grp not in AUDIT_GROUPS:
                raise PanelError(f"line {n}: unknown {STRATA_COL} {grp!r} (allowed {AUDIT_GROUPS})")
            segments = {}
            for c in SEGMENT_COLS:
                v = (raw.get(c) or "").strip()
                if not v:
                    raise PanelError(f"line {n}: empty segment column {c!r}")
                segments[c] = v
            features = {}
            for c in MODEL_FEATURES:
                v = raw.get(c)
                v = v.strip() if isinstance(v, str) else v
                if v in (None, ""):
                    if c in MISSABLE:
                        features[c] = None
                        continue
                    raise PanelError(f"line {n}: missing required feature {c!r}")
                try:
                    fv = float(v)
                except ValueError:
                    raise PanelError(f"line {n}: non-numeric feature {c}={v!r}")
                if not math.isfinite(fv):
                    raise PanelError(f"line {n}: non-finite feature {c}={v!r}")
                features[c] = fv
            rows.append({ID_COL: emp, TIME_COL: month, INDEX_COL: idx,
                         "segments": segments, STRATA_COL: grp, LABEL_COL: ev,
                         "features": features})
    if not rows:
        raise PanelError("retention panel is empty")
    _validate_panel_invariants(rows)
    return rows


def _validate_panel_invariants(rows):
    """Panel-level discrete-time-hazard contract (not just per-row). Fails closed so a malformed alternate
    panel can't smuggle bad temporal structure that later train/calibration/test splits would trust:
      * the GLOBAL distinct months must be actual, strictly CONSECUTIVE month-ends (no skipped calendar
        month, no mid-month dates), and `month_index` must equal the row's position in that sequence;
      * each employee's consecutive rows must increment `month_index` by EXACTLY 1 (active months are
        contiguous; the first row may start mid-window when the employee was hired after window start);
      * AT MOST ONE terminal (non-'none') event per employee, and it MUST be their final observed row."""
    gmonths = sorted({r[TIME_COL] for r in rows})
    parsed = [date.fromisoformat(m) for m in gmonths]
    for d in parsed:
        if (d + timedelta(days=1)).month == d.month:        # next day still same month -> not a month-end
            raise PanelError(f"panel month {d.isoformat()} is not a month-end")
    for prev, cur in zip(parsed, parsed[1:]):
        first_next = prev + timedelta(days=1)               # first day of prev's next month (prev is a month-end)
        after = date(first_next.year + (first_next.month == 12), (first_next.month % 12) + 1, 1)
        if cur != after - timedelta(days=1):                # the month-end immediately following prev
            raise PanelError(f"panel months are not consecutive month-ends: {prev.isoformat()} -> {cur.isoformat()}")
    gidx = {m: i for i, m in enumerate(gmonths)}
    for r in rows:
        if r[INDEX_COL] != gidx[r[TIME_COL]]:
            raise PanelError(f"{r[ID_COL]} {r[TIME_COL]}: month_index {r[INDEX_COL]} "
                             f"!= global month position {gidx[r[TIME_COL]]}")
    by_emp = {}
    for r in rows:
        by_emp.setdefault(r[ID_COL], []).append(r)
    for emp, ers in by_emp.items():
        idxs = [r[INDEX_COL] for r in ers]
        for a, b in zip(idxs, idxs[1:]):
            if b != a + 1:
                raise PanelError(f"{emp}: non-contiguous month_index ({a} -> {b}); active months must be consecutive")
        terminal = [i for i, r in enumerate(ers) if r[LABEL_COL] != "none"]
        if len(terminal) > 1:
            raise PanelError(f"{emp}: more than one terminal event")
        if terminal and terminal[0] != len(ers) - 1:
            raise PanelError(f"{emp}: a terminal event must be the employee's final row")


def _validate_header(header):
    cols = list(header)
    dupes = {c for c in cols if cols.count(c) > 1}
    if dupes:
        raise PanelError(f"duplicate columns: {sorted(dupes)}")
    have = set(cols)
    # The strict allowlist below (exact REQUIRED_COLS) is the PRIMARY gate — any unlisted column fails
    # closed regardless. This deny-list is belt-and-suspenders for named surveillance/protected concepts
    # (clearer errors) and is case-folded so a capitalized "Gender"/"Age" can't slip a relaxed loader.
    forbidden = {c for c in have if c.casefold() in (QUARANTINED | PROTECTED)}
    if forbidden:
        raise PanelError(f"forbidden columns present (quarantined/protected): {sorted(forbidden)}")
    want = set(REQUIRED_COLS)
    missing, extra = want - have, have - want
    if missing:
        raise PanelError(f"missing required columns: {sorted(missing)}")
    if extra:
        raise PanelError(f"unexpected extra columns: {sorted(extra)}")


# --------------------------------------------------------------------------- canonical hashing

def _canon_value(v):
    """Canonicalize a feature value: None -> null; numerics -> a fixed-precision decimal STRING so the
    serialization is identical across Python versions / float repr (no binary-float drift in the hash).
    Strict even in direct API use — a bool (True != 1.0), a numeric STRING, or a NaN is a caller error,
    never silently coerced into the hash."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"feature value must be a real number or None, got {v!r}")
    f = float(v)
    if not math.isfinite(f):
        raise ValueError(f"non-finite feature value: {v!r}")
    if f == 0.0:
        f = 0.0                                # normalize -0.0 -> 0.0 so sign-of-zero never changes the hash
    return f"{f:.{FLOAT_DP}f}"


def feature_snapshot_hash(features: dict, *, schema_version: str = SCHEMA_VERSION,
                          feature_version: str = FEATURE_VERSION, as_of: str) -> str:
    """Stable sha256 over a scoring input's FEATURES ONLY (model_version is logged separately, never
    folded into this hash). Canonical form: UTF-8, sorted keys, compact separators, no NaN/Inf,
    decimal-quantized floats, ISO `as_of`. Integrity pointer — NOT anonymization."""
    if not _DATE_RE.match(str(as_of)):
        raise ValueError(f"as_of must be ISO YYYY-MM-DD, got {as_of!r}")
    try:
        date.fromisoformat(str(as_of))                    # reject ISO-shaped-but-impossible dates (e.g. 2025-13-01)
    except ValueError:
        raise ValueError(f"as_of is not a real calendar date: {as_of!r}")
    unknown = set(features) - set(MODEL_FEATURES)
    if unknown:
        raise ValueError(f"feature_snapshot_hash got non-feature keys: {sorted(unknown)}")
    missing = set(MODEL_FEATURES) - set(features)
    if missing:                               # absent != None: callers must pass explicit None for a missing feature
        raise ValueError(f"feature_snapshot_hash missing required features: {sorted(missing)}")
    payload = {
        "schema_version": schema_version,
        "feature_version": feature_version,
        "as_of": str(as_of),
        "features": {k: _canon_value(features.get(k)) for k in MODEL_FEATURES},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def panel_data_hash(path: Path = PANEL_PATH) -> str:
    """sha256 of the committed panel file bytes — provenance pin for the manifest."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------- feature builder (Increment 1)

MISSING_SUFFIX = "__missing"
# The model's design columns: every model feature, then an explicit missing-indicator for each MISSABLE
# feature (so the model can learn from missingness instead of a 0.0 impute masquerading as a real value).
DESIGN_FEATURES = list(MODEL_FEATURES) + [f"{c}{MISSING_SUFFIX}" for c in sorted(MISSABLE)]


def _feature_value(v, col):
    """A design-matrix slot value: None -> 0.0 (paired with its missing-indicator column); otherwise a strict
    finite real number. Strict even in direct API use — a bool (True != 1.0), a numeric STRING, or a NaN is a
    caller error, never silently coerced into the model input."""
    if v is None:
        return 0.0
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise PanelError(f"feature {col!r} must be a finite real number or None, got {v!r}")
    return float(v)


def build_design(rows):
    """Increment 1: assemble the point-in-time design matrix for the discrete-time VOLUNTARY-exit hazard.

    Pure, **row-local**, deterministic — each design vector is a function ONLY of its own panel row (no
    cross-row and no future information), so the construction itself cannot introduce lookahead leakage.
    Missing values in the two MISSABLE features are deterministically imputed to 0.0 and flagged with an
    explicit `<feat>__missing` indicator. Competing risks are handled by construction: an involuntary /
    retirement terminal row is a y=0 row in the risk set (the person was at risk that month and did NOT
    voluntarily exit) which then simply has no further rows — i.e. censored for the voluntary cause,
    never coded as a positive.

    Returns a dict: feature_names, X (list[list[float]]), y (1 iff the following-month outcome is a
    voluntary exit), emp, month_index, month — the middle three for the out-of-time temporal splits (by
    calendar month, NOT employee-grouped: the same person legitimately appears in earlier slices) below —
    and event (the raw competing-risks outcome per row, so the survival concordance can tell a competing-risk
    exit apart from an end-of-window survivor; NOT a model input).
    """
    miss = sorted(MISSABLE)
    names = list(MODEL_FEATURES) + [f"{c}{MISSING_SUFFIX}" for c in miss]
    X, y, emp, midx, month, event = [], [], [], [], [], []
    for r in rows:
        ev = r.get(LABEL_COL)
        if ev not in EVENT_VALUES:                        # fail closed — never silently coerce a bad label to y=0
            raise PanelError(f"build_design got an invalid {LABEL_COL}: {ev!r}")
        f = r["features"]
        absent = [c for c in MODEL_FEATURES if c not in f]
        if absent:                                        # a domain error, not a raw KeyError, on a malformed row
            raise PanelError(f"build_design row is missing model features: {absent}")
        vec = [_feature_value(f[c], c) for c in MODEL_FEATURES]
        vec += [1.0 if f[c] is None else 0.0 for c in miss]
        X.append(vec)
        y.append(1 if ev == TARGET_EVENT else 0)
        emp.append(r[ID_COL])
        midx.append(r[INDEX_COL])
        month.append(r[TIME_COL])
        event.append(ev)                                  # raw competing-risks outcome (voluntary/involuntary/retirement/none)
    return {"feature_names": names, "X": X, "y": y, "emp": emp, "month_index": midx, "month": month, "event": event}


# --------------------------------------------------------------- glass-box hazard model (Increment 2)

# Out-of-time temporal slices by month_index on the 36-month window (0..35): train 0-23 (24 mo),
# calibration 24-29 (6 mo), test 30-35 (6 mo). Disjoint by construction — the model is fit on train,
# calibrated on calibration, and (Increment 3) evaluated on test, so nothing downstream leaks backward.
SLICE_T1, SLICE_T2 = 23, 29
L2_LAMBDA = 1.0            # ridge penalty on the standardized coefficients (not the intercept)
IRLS_ITERS = 15
HORIZONS = (6, 12)        # months for the headline exit probabilities
COEF_DP = 8               # coefficient rounding for a deterministic, byte-stable manifest


class ModelError(ValueError):
    """Raised on a degenerate model fit (e.g. an empty or singular training problem)."""


def temporal_slices(design, t1=SLICE_T1, t2=SLICE_T2):
    """Disjoint out-of-time row-index sets by month_index: train (<=t1) / calibration (t1<..<=t2) / test (>t2)."""
    out = {"train": [], "calibration": [], "test": []}
    for i, mi in enumerate(design["month_index"]):
        out["train" if mi <= t1 else "calibration" if mi <= t2 else "test"].append(i)
    return out


def _standardizer(X, idx):
    """Per-feature mean/std over the given (train) rows. A (near-)constant feature gets std=1 (no scaling)."""
    if not idx:
        raise ModelError("standardizer got no rows")
    d = len(X[0])
    n = len(idx)
    mean = [0.0] * d
    for i in idx:
        row = X[i]
        for j in range(d):
            mean[j] += row[j]
    mean = [m / n for m in mean]
    var = [0.0] * d
    for i in idx:
        row = X[i]
        for j in range(d):
            dv = row[j] - mean[j]
            var[j] += dv * dv
    std = [(v / n) ** 0.5 for v in var]
    std = [s if s > 1e-9 else 1.0 for s in std]
    return mean, std


def _apply_std(row, mean, std):
    if len(row) != len(mean) or len(std) != len(mean):
        raise ModelError(f"design-row width {len(row)} does not match the model width {len(mean)}")
    if any(s <= 0.0 for s in std):                       # a zero/negative std would divide-by-zero -> fail closed
        raise ModelError("standardizer std has a non-positive entry")
    return [(row[j] - mean[j]) / std[j] for j in range(len(row))]


def _sigmoid(z):
    if not math.isfinite(z):                              # fail closed at the source — a NaN/inf logit is never a probability
        raise ModelError("non-finite logit")
    if z <= -60.0:
        return 0.0
    if z >= 60.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def _solve(A, b):
    """Solve A x = b (A square) by Gauss-Jordan with partial pivoting. Raises ModelError if singular."""
    n = len(A)
    aug = [list(A[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-12:
            raise ModelError(f"singular normal system at column {col}")
        aug[col], aug[piv] = aug[piv], aug[col]
        pv = aug[col][col]
        aug[col] = [v / pv for v in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0.0:
                f = aug[r][col]
                aug[r] = [a - f * c for a, c in zip(aug[r], aug[col])]
    return [aug[i][n] for i in range(n)]


def fit_hazard(design, slices=None, l2=L2_LAMBDA, iters=IRLS_ITERS):
    """Fit the glass-box discrete-time VOLUNTARY-exit hazard: an L2-regularized logistic regression on the
    standardized design matrix by IRLS (Newton), class-weighted for imbalance. Fit on the TRAIN slice ONLY
    (the standardizer too), so calibration/test are never seen. Returns a model dict:
    {intercept, coef{feature->weight (standardized units)}, mean, std, feature_names, pos_weight}."""
    if not (isinstance(iters, int) and not isinstance(iters, bool) and iters >= 1):
        raise ModelError(f"iters must be a positive int (got {iters!r})")
    X, y, names = design["X"], design["y"], design["feature_names"]
    sl = slices or temporal_slices(design)
    # the artifact-creation path must fit on the CANONICAL train slice — a caller-forged slice (e.g. the test
    # rows) would leak; reject anything but the out-of-time partition (same guard the eval path uses)
    _validate_slices(design, sl)
    tr = sl["train"]
    if not tr:
        raise ModelError("no rows in the train slice")
    mean, std = _standardizer(X, tr)
    Xs = [[1.0] + _apply_std(X[i], mean, std) for i in tr]     # leading intercept column
    yt = [float(y[i]) for i in tr]
    n, p = len(Xs), len(Xs[0])
    npos = sum(yt)
    if npos == 0 or npos == n:
        raise ModelError("train slice has a single class")
    pos_w = (n - npos) / npos                                  # up-weight the rare positive (voluntary) rows
    w = [pos_w if v == 1.0 else 1.0 for v in yt]
    beta = [0.0] * p
    last_step = 0.0
    for _ in range(iters):
        H = [[0.0] * p for _ in range(p)]
        g = [0.0] * p
        for k in range(n):
            row = Xs[k]
            z = sum(beta[j] * row[j] for j in range(p))
            pk = _sigmoid(z)
            r = w[k] * (yt[k] - pk)
            s = w[k] * pk * (1.0 - pk)
            for a in range(p):
                g[a] += r * row[a]
                sa = s * row[a]
                Ha = H[a]
                for b_ in range(a, p):
                    Ha[b_] += sa * row[b_]
        for a in range(p):
            for b_ in range(a + 1, p):
                H[b_][a] = H[a][b_]
            if a > 0:                                          # ridge on coefficients, never the intercept
                H[a][a] += l2
                g[a] -= l2 * beta[a]
        step = _solve(H, g)
        beta = [beta[a] + step[a] for a in range(p)]
        last_step = max(abs(v) for v in step)
        # NB: a FIXED iteration count (no early stop) keeps the fit reproducible across platforms — the
        # iteration count can't diverge on a convergence threshold that lands differently on mac vs CI.
    if not all(math.isfinite(v) for v in beta):
        raise ModelError("non-finite coefficients after IRLS")
    if last_step > 1e-3:            # the last Newton step must be tiny; a large one means we didn't converge
        raise ModelError(f"IRLS did not converge in {iters} iterations (last step {last_step:.2e})")
    return {"intercept": beta[0], "coef": {names[j]: beta[j + 1] for j in range(len(names))},
            "mean": mean, "std": std, "feature_names": list(names), "pos_weight": pos_w}


def _validate_model(model):
    """Fail closed unless `model` is a STRUCTURALLY SOUND fitted artifact — not just the right vector width.
    A corrupted/forged model (reversed feature order, zero standardizer std, a bool coefficient, a missing
    key) must raise a controlled ModelError before it is ever scored, never silently return numbers. Pins
    feature_names to the canonical DESIGN_FEATURES order (a reordering is a different, wrong model)."""
    if not isinstance(model, dict):
        raise ModelError("model must be a dict")
    missing = {"intercept", "coef", "mean", "std", "feature_names", "pos_weight"} - set(model)
    if missing:
        raise ModelError(f"model missing keys: {sorted(missing)}")
    fn = model["feature_names"]
    if list(fn) != list(DESIGN_FEATURES):
        raise ModelError("model feature_names drifted from the canonical design order (reordered/renamed model)")
    n = len(fn)
    for k in ("mean", "std"):
        v = model[k]
        if not (isinstance(v, list) and len(v) == n and all(_finite_num(x) for x in v)):
            raise ModelError(f"model {k} must be {n} finite numbers")
    if any(s <= 0.0 for s in model["std"]):
        raise ModelError("model std has a non-positive entry (degenerate standardizer — would divide by zero)")
    coef = model["coef"]
    if not (isinstance(coef, dict) and set(coef) == set(fn) and all(_finite_num(v) for v in coef.values())):
        raise ModelError("model coef must map each design feature to a finite number")
    if not _finite_num(model["intercept"]):
        raise ModelError("model intercept must be a finite number")
    if not (_finite_num(model["pos_weight"]) and model["pos_weight"] > 0):
        raise ModelError("model pos_weight must be a finite positive number")


def _validate_calibration(cal):
    """Fail closed unless `cal` is a sound Platt artifact — a dict with finite a/b (the runtime artifact is
    `{a, b}`; the manifest additionally tags method='platt'). A bool/str/missing param must raise ModelError,
    never be scored as a number."""
    if not isinstance(cal, dict):
        raise ModelError("calibration must be a dict")
    if not (_finite_num(cal.get("a")) and _finite_num(cal.get("b"))):
        raise ModelError("calibration params a/b must be finite numbers")


def _logit(model, x):
    if len(x) != len(model["feature_names"]):
        raise ModelError(f"scoring row width {len(x)} does not match the model's {len(model['feature_names'])} features")
    xs = _apply_std(x, model["mean"], model["std"])
    coef, names = model["coef"], model["feature_names"]
    return model["intercept"] + sum(coef[names[j]] * xs[j] for j in range(len(names)))


def predict_hazard(model, X, idx=None):
    """Uncalibrated monthly score for each design row. NOTE: these are pos_weight-inflated (the fit
    up-weights the rare positive rows), so they are NOT on the true-probability scale — for an absolute
    monthly exit risk use calibrated_probability(). Raw scores are still rank-correct for AUC/ranking."""
    _validate_model(model)                               # validate the artifact ONCE before the scoring loop
    idx = range(len(X)) if idx is None else idx
    return [_sigmoid(_logit(model, X[i])) for i in idx]


def _check_hazards(hazards):
    for h in hazards:
        if not _finite_num(h) or not (0.0 <= h <= 1.0):   # _finite_num rejects bool/str/None (no True==1, no raw TypeError)
            raise ModelError(f"hazard {h!r} outside [0,1]")


def survival_curve(hazards):
    """S[t] = P(survive through month t) = prod_{k<=t}(1 - lambda_k), for an ordered per-employee hazard run.
    Fails closed on any hazard outside [0,1] (which would give a nonsensical survival curve)."""
    _check_hazards(hazards)
    s, out = 1.0, []
    for h in hazards:
        s *= (1.0 - h)
        out.append(s)
    return out


def horizon_probability(hazards, h_months):
    """P(voluntary exit within the next h_months) = 1 - prod(1 - lambda) over that horizon. Fails closed if
    fewer than h_months hazards are supplied — silently truncating would return a shorter-horizon probability
    mislabeled as the requested one (e.g. a 6-month number returned for P(exit <= 12mo))."""
    if not isinstance(h_months, int) or isinstance(h_months, bool) or h_months < 0:
        raise ModelError("horizon must be a non-negative int")
    if len(hazards) < h_months:
        raise ModelError(f"horizon_probability: need >= {h_months} hazards, got {len(hazards)}")
    window = hazards[:h_months]
    _check_hazards(window)
    s = 1.0
    for h in window:
        s *= (1.0 - h)
    return 1.0 - s


def median_months_to_exit(hazards, max_h=18):
    """Smallest month where survival drops below 0.5, else None (rendered 'not reached'). This is a
    frozen-snapshot (time-homogeneous) 'what-if' median over the supplied hazard run — not a
    path-integrated forecast; None conflates 'survives past max_h' with a genuinely low hazard."""
    _check_hazards(hazards[:max_h])
    s = 1.0
    for t, h in enumerate(hazards[:max_h], start=1):
        s *= (1.0 - h)
        if s < 0.5:
            return t
    return None


def explain(model, x, top=5):
    """Exact additive per-feature contributions to the log-odds (coef * standardized value), largest first.
    Exact for a linear/additive model — NOT an approximation, and NOT SHAP. Validates the model artifact
    first so a corrupted/reversed model can't produce a plausible-looking explanation."""
    _validate_model(model)
    xs = _apply_std(x, model["mean"], model["std"])
    coef, names = model["coef"], model["feature_names"]
    contribs = [(names[j], coef[names[j]] * xs[j]) for j in range(len(names))]
    contribs.sort(key=lambda t: -abs(t[1]))
    return contribs[:top]


def platt_calibrate(model, design, calib_idx, iters=60):
    """Fit Platt scaling p = sigmoid(a*logit + b) on the CALIBRATION slice by 2-parameter Newton, so the
    surfaced probabilities are honestly calibrated (fit on a slice disjoint from train and test)."""
    if not (isinstance(iters, int) and not isinstance(iters, bool) and iters >= 1):
        raise ModelError(f"iters must be a positive int (got {iters!r})")
    _validate_model(model)
    if not calib_idx:
        raise ModelError("no rows in the calibration slice")
    # calibrate ONLY on the canonical calibration slice — a caller-forged index set (e.g. train or test rows)
    # would corrupt the Platt fit or leak; reject anything but the out-of-time calibration partition
    if list(calib_idx) != temporal_slices(design)["calibration"]:
        raise ModelError("platt_calibrate: calib_idx is not the canonical calibration slice")
    L = [_logit(model, design["X"][i]) for i in calib_idx]
    yt = [float(design["y"][i]) for i in calib_idx]
    npos = sum(yt)
    if npos == 0 or npos == len(yt):
        raise ModelError("calibration slice has a single class — Platt scaling needs both")
    a, b = 1.0, 0.0
    stepped = False
    for _ in range(iters):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for li, yi in zip(L, yt):
            pi = _sigmoid(a * li + b)
            r = yi - pi
            s = pi * (1.0 - pi)
            g0 += r * li
            g1 += r
            h00 += s * li * li
            h01 += s * li
            h11 += s
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            # A singular 2-param system means the calibration logits are degenerate (e.g. ~constant): the
            # slope is unidentifiable. Fail CLOSED rather than silently return the unfitted identity {a:1,b:0}
            # (which would leave probabilities uncalibrated). If Newton already stepped, keep the fitted a,b.
            if not stepped:
                raise ModelError("platt_calibrate: singular calibration system (degenerate logits) — cannot fit")
            break
        stepped = True
        da = (h11 * g0 - h01 * g1) / det
        db = (-h01 * g0 + h00 * g1) / det
        a += da
        b += db
        # Safe to early-stop here (unlike fit_hazard): 2-param Newton converges quadratically, so a +/-1
        # iteration difference from libm ULP noise moves a,b by << the manifest rounding/tolerance.
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    # NB: base rate drifts UP across the window (train ~1.07% -> test ~1.83%); Platt is anchored to the
    # calibration-slice prevalence, so out-of-time surfaced probabilities track calibration, not test.
    if not (math.isfinite(a) and math.isfinite(b)):
        raise ModelError("non-finite Platt calibration parameters")
    return {"a": a, "b": b}


def calibrated_probability(model, calibration, x):
    """The calibrated monthly hazard for a single design row. Validates the model + calibration artifact
    before scoring (a corrupted model/calibration fails closed, never returns a silent number)."""
    _validate_model(model)
    _validate_calibration(calibration)
    return _sigmoid(calibration["a"] * _logit(model, x) + calibration["b"])


def _binary_labels(y):
    # exact int 0/1 — a bool (True == 1) or a float 1.0 is a caller error, not a silent positive
    if any(isinstance(v, bool) or type(v) is not int or v not in (0, 1) for v in y):
        raise ModelError("labels must be binary (0/1)")


def brier_score(probs, y):
    """Mean squared error of probabilistic predictions — lower is better-calibrated + sharper. Fails closed
    on a length mismatch, non-binary labels, or a probability outside [0,1]/non-finite."""
    if not y or len(probs) != len(y):
        raise ModelError("brier_score: empty or length mismatch")
    _binary_labels(y)
    for p in probs:
        if not _finite_num(p) or not (0.0 <= p <= 1.0):   # _finite_num rejects bool/str/None (no raw TypeError)
            raise ModelError(f"brier_score: probability {p!r} outside [0,1]")
    return sum((p - yi) ** 2 for p, yi in zip(probs, y)) / len(y)


def rank_auc(scores, y):
    """ROC-AUC via the Mann-Whitney U rank statistic (ties = 0.5). Pure stdlib; used by the eval + realism
    guard in Increment 3. Rank-equivalent inputs (logits or calibrated probabilities) give the same AUC.
    Fails closed on a length mismatch, non-binary labels, or a single-class input."""
    if len(scores) != len(y):
        raise ModelError("rank_auc: length mismatch")
    if any(not _finite_num(s) for s in scores):           # rejects bool/str/None before any sort/compare
        raise ModelError("rank_auc: score must be a finite number")
    _binary_labels(y)
    npos = sum(1 for v in y if v == 1)
    nneg = len(y) - npos
    if npos == 0 or nneg == 0:
        raise ModelError("rank_auc needs both classes present")
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0                          # average 1-based rank across a tie block
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    rsum = sum(ranks[k] for k in range(len(scores)) if y[k] == 1)
    return (rsum - npos * (npos + 1) / 2.0) / (npos * nneg)


def train_model(panel_path=PANEL_PATH):
    """End-to-end: load panel -> design -> fit hazard on train -> Platt-calibrate on calibration.
    Returns (model, calibration, design, slices). The single source of the trained artifacts."""
    design = build_design(load_panel(panel_path))
    slices = temporal_slices(design)
    model = fit_hazard(design, slices)
    calibration = platt_calibrate(model, design, slices["calibration"])
    return model, calibration, design, slices


def model_from_manifest(manifest=None, manifest_path: Path = MANIFEST_PATH, panel_path: Path = PANEL_PATH):
    """Load the (model, calibration, bands) triple from the PINNED, version-controlled manifest — the published
    trained artifact — WITHOUT re-fitting (no IRLS), so a renderer scores the published model fast and
    deterministically.

    This is a TRUSTED-manifest loader for the committed artifact. It fails closed on exactly two things and NOT a
    third:
      (1) SCHEMA + GOVERNANCE CONTRACT (via validate_manifest) — exact key set, every pinned field vs the module
          source of truth, status/increment/version, and a strict numeric shape for the fitted primary model
          (finite coefficients/standardizer, std > 0).
      (2) PANEL PROVENANCE — the manifest's panel_data_hash must equal the hash of the panel on disk, so a
          manifest built from a DIFFERENT panel is rejected (pass panel_path=None to skip, e.g. a hand-built
          manifest in a unit test).
      (NOT) It does NOT re-verify that the coefficients reproduce a fresh fit — that is the separate, expensive
          check_reproducible() / `retention.py validate` CI gate. A schema-valid manifest carrying
          tampered-but-finite coefficients would still load here, so a caller must pass only the committed
          manifest (or one it already trusts). The reconstructed model scores identically to the published
          artifact (coefficients are the manifest's, rounded exactly as published)."""
    m = manifest if manifest is not None else json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    validate_manifest(m)                                          # schema + governance-contract gate (fails closed)
    if panel_path is not None and m.get("panel_data_hash") != panel_data_hash(panel_path):
        raise ModelError("model_from_manifest: manifest panel_data_hash does not match the panel on disk — "
                         "refusing to score a manifest built from a different panel (panel drift)")
    pc = m["primary_coefficients"]
    for k in ("features", "intercept", "pos_weight", "standardizer_mean", "standardizer_std"):
        if k not in pc:
            raise ModelError(f"manifest primary_coefficients missing {k!r}")
    names = list(m["design_features"])                            # the canonical column order
    coef, mean_d, std_d = pc["features"], pc["standardizer_mean"], pc["standardizer_std"]
    if not (set(coef) == set(mean_d) == set(std_d) == set(names)):
        raise ModelError("manifest coefficient / standardizer / design-feature name sets disagree")
    model = {"intercept": pc["intercept"], "coef": {n: coef[n] for n in names},
             "mean": [mean_d[n] for n in names], "std": [std_d[n] for n in names],
             "feature_names": names, "pos_weight": pc["pos_weight"]}
    _validate_model(model)                                        # structural soundness gate (fails closed)
    cal = m["primary_calibration"]
    if not _finite_num(cal.get("a")) or not _finite_num(cal.get("b")):
        raise ModelError("manifest primary_calibration a/b must be finite")
    bands = m["risk_band_thresholds"]
    if not (set(bands) == {"elevated", "high"} and _finite_num(bands["elevated"]) and _finite_num(bands["high"])):
        raise ModelError("manifest risk_band_thresholds must be finite {elevated, high}")
    return model, {"a": cal["a"], "b": cal["b"]}, {"elevated": bands["elevated"], "high": bands["high"]}


# --------------------------------------------------------------- evaluation + realism guard (Increment 3)

REALISM_AUC_MAX = 0.90            # a synthetic model above this is implausibly perfect -> fail closed
REALISM_PR_AUC_MAX = 0.60        # ditto for PR-AUC on this heavily-imbalanced target (honest ~0.10)
PRECISION_K_FRAC = 0.10          # top-decile
PRECISION_MIN_DENOM = 50         # need >=50 pooled flags or we report 'insufficient denominator'
BAND_ELEVATED_PCTILE = 0.85
BAND_HIGH_PCTILE = 0.97
SYNTHETIC_VALIDATION = "synthetic-only — demonstrates mechanics, not external predictive validity"


def pr_auc(scores, y):
    """Average precision (area under the precision-recall curve) — the headline discrimination metric under
    heavy class imbalance. TIE-AWARE / order-independent: rows with an equal score form a block, and every
    positive in the block is credited the precision at the block's LAST rank (pessimistic — a positive tied
    with negatives gets no rank credit for merely appearing first by index). So the AP depends only on the
    scores, never on input row order (all-tied scores => the base rate, not a fluke 1.0)."""
    if len(scores) != len(y):
        raise ModelError("pr_auc: length mismatch")
    if any(not _finite_num(s) for s in scores):           # rejects bool/str/None before any sort/compare
        raise ModelError("pr_auc: score must be a finite number")
    _binary_labels(y)
    P = sum(y)
    if P == 0 or P == len(y):
        raise ModelError("pr_auc needs both classes present")
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    ap = 0.0
    tp = 0                                                 # positives seen through the end of the current block
    rank = 0                                               # rows processed through the end of the current block
    i, n = 0, len(order)
    while i < n:
        j = i
        while j + 1 < n and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        block = order[i:j + 1]
        g_pos = sum(1 for k in block if y[k] == 1)
        tp += g_pos
        rank += len(block)
        if g_pos:
            ap += g_pos * (tp / rank)                      # every positive in the block shares the block-end precision
        i = j + 1
    return ap / P


def precision_at_k(scores, y, groups, k_frac=PRECISION_K_FRAC, min_denom=PRECISION_MIN_DENOM):
    """Top-decile precision, computed PER window (calendar month) then POOLED, so a tiny single window can't
    trip or hide it. Returns {precision|None, n_flagged, status} — 'insufficient_denominator' if the pooled
    flags never reach min_denom (Acme's active pop can be small)."""
    if not (len(scores) == len(y) == len(groups)):
        raise ModelError("precision_at_k: length mismatch")
    if any(not _finite_num(s) for s in scores):           # rejects bool/str/None before any sort/compare
        raise ModelError("precision_at_k: score must be a finite number")
    if not (0.0 < k_frac <= 1.0):
        raise ModelError(f"precision_at_k: k_frac {k_frac!r} must be in (0,1]")
    if not (isinstance(min_denom, int) and not isinstance(min_denom, bool) and min_denom >= 1):
        raise ModelError(f"precision_at_k: min_denom {min_denom!r} must be a positive int")
    _binary_labels(y)
    by_g = {}
    for i in range(len(scores)):
        by_g.setdefault(groups[i], []).append(i)
    flagged = []
    for g, idxs in by_g.items():
        idxs.sort(key=lambda i: (-scores[i], i))
        k = max(1, int(len(idxs) * k_frac))
        flagged.extend(idxs[:k])
    n = len(flagged)
    if n < min_denom:
        return {"precision": None, "n_flagged": n, "status": "insufficient_denominator"}
    return {"precision": sum(y[i] for i in flagged) / n, "n_flagged": n, "status": "ok"}


def _test_outcomes(model, calibration, design, slices):
    """Per employee active in the TEST slice, the inputs to the survival concordance: a risk score (max
    calibrated monthly hazard over their test rows), the month their observation TERMINATES in the window,
    and whether that termination is the VOLUNTARY event (True) or a censoring (False). Their last test row's
    raw event decides it: a voluntary exit is the event; an involuntary/retirement exit is a COMPETING-RISK
    censoring at that month (not event-free); an at-window-edge 'none' is administrative censoring. Treating
    a competing-risk exit as event-free would wrongly count them as a survivor in pairs — the bug this fixes."""
    by_emp = {}
    for i in slices["test"]:
        by_emp.setdefault(design["emp"][i], []).append(i)
    risk, term_month, is_event = [], [], []
    for e, idxs in by_emp.items():
        idxs.sort(key=lambda i: design["month_index"][i])
        risk.append(max(calibrated_probability(model, calibration, design["X"][i]) for i in idxs))
        last = idxs[-1]                                    # terminating test row (highest month index for this employee)
        term_month.append(design["month_index"][last])
        is_event.append(design["event"][last] == TARGET_EVENT)   # voluntary = event; competing-risk / admin edge = censored
    return risk, term_month, is_event


def horizon_concordance(risk, term_month, is_event):
    """Competing-risks-aware survival C-index (Harrell). A pair is COMPARABLE only when the one who
    terminates earlier does so via the voluntary EVENT (not a censoring) — so a competing-risk exit or an
    end-of-window survivor is never treated as an observed 'stayed longer' when it precedes a voluntary exit.
    Concordant when the earlier voluntary exiter carries the higher risk score (equal scores score 0.5).
    Equal termination months are not orderable and are skipped."""
    if not (len(risk) == len(term_month) == len(is_event)):
        raise ModelError("horizon_concordance: length mismatch")
    if any(not _finite_num(r) for r in risk):             # rejects bool/str/None before any compare
        raise ModelError("horizon_concordance: risk score must be a finite number")
    if not all(isinstance(t, int) and not isinstance(t, bool) for t in term_month):
        raise ModelError("horizon_concordance: term_month values must be integers")
    if not all(isinstance(e, bool) for e in is_event):
        raise ModelError("horizon_concordance: is_event values must be booleans")
    n = len(risk)
    conc = comp = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            ti, tj = term_month[i], term_month[j]
            if ti == tj:
                continue                                   # same month -> cannot order
            earlier, later = (i, j) if ti < tj else (j, i)
            if not is_event[earlier]:                      # earlier terminator was CENSORED -> pair not usable
                continue
            comp += 1
            if risk[earlier] > risk[later]:
                conc += 1.0
            elif risk[earlier] == risk[later]:
                conc += 0.5
    if comp == 0:
        raise ModelError("no comparable pairs for concordance")
    return conc / comp


def _validate_slices(design, slices):
    """Fail closed unless train/calibration/test are EXACTLY the canonical out-of-time partition
    `temporal_slices(design)` — every row assigned, none dropped, none reordered across bands. Boundary +
    disjointness checks alone are not enough: a caller could pass a cherry-picked in-band SUBSET of the test
    rows and still get normal-looking (but selectively favorable) metrics back. Requiring exact equality to
    the canonical partition closes that, and also catches an empty/overlapping/out-of-range slice."""
    if set(slices) != {"train", "calibration", "test"}:       # EXACTLY these three keys — no missing, no extra
        raise ModelError("_validate_slices: slices must be exactly {train, calibration, test}")
    canon = temporal_slices(design)
    for name in ("train", "calibration", "test"):
        if list(slices[name]) != canon[name]:
            raise ModelError(f"_validate_slices: {name!r} slice is not the canonical temporal partition "
                             f"(cherry-picked/reordered/empty slices are rejected)")


def evaluate(model=None, calibration=None, design=None, slices=None, panel_path=PANEL_PATH):
    """Out-of-time TEST-slice evaluation. Every number here is SYNTHETIC-VALIDATION only (mechanics, not
    external accuracy). Discrimination metrics use the raw logit (rank-equivalent); Brier uses calibrated
    probabilities. Fails closed unless the slices are the canonical out-of-time partition."""
    # all-or-nothing bundle: either evaluate the canonical trained model (everything defaulted) or supply a
    # COMPLETE, mutually-consistent (model, calibration, design, slices). A PARTIAL bundle — e.g. a model with
    # someone else's slices, or a model with calibration=None — would silently mix artifacts or fail deep in
    # _validate_slices with a confusing error; reject it up front.
    bundle = (model, calibration, design, slices)
    if all(v is None for v in bundle):
        model, calibration, design, slices = train_model(panel_path)
    elif any(v is None for v in bundle):
        raise ModelError("evaluate: pass NO bundle (train from panel_path) or a COMPLETE bundle "
                         "(model, calibration, design, slices) — a partial bundle is rejected")
    _validate_model(model)                               # a supplied bundle's model/calibration must be sound
    _validate_calibration(calibration)
    _validate_slices(design, slices)
    test = slices["test"]
    scores = [_logit(model, design["X"][i]) for i in test]
    cprob = [calibrated_probability(model, calibration, design["X"][i]) for i in test]
    ytest = [design["y"][i] for i in test]
    groups = [design["month_index"][i] for i in test]
    risk, term_month, is_event = _test_outcomes(model, calibration, design, slices)
    return {
        "roc_auc": rank_auc(scores, ytest),
        "pr_auc": pr_auc(scores, ytest),
        "precision_at_k": precision_at_k(scores, ytest, groups),
        "brier": brier_score(cprob, ytest),
        "horizon_concordance": horizon_concordance(risk, term_month, is_event),
        "base_rate": sum(ytest) / len(ytest),
        "n_test_rows": len(test),
        "n_test_employees": len(risk),
        "validation": SYNTHETIC_VALIDATION,
    }


def realism_guard(model, metrics):
    """Fail closed if the synthetic model looks implausibly perfect (a tell for leakage or an overfit demo):
    ROC-AUC > 0.90, PR-AUC > 0.60, a perfect top-decile precision@k, or any decoy in the top-3 features by
    |coefficient|. This is a plausibility CEILING that complements — it does not replace — the temporal-slice
    design + leakage tests that prevent leakage in the first place; a model in the honest ~0.84-0.90 band is
    plausible, so the guard is a tripwire against cartoonish performance, not a fine-grained leakage detector."""
    for k in ("roc_auc", "pr_auc", "precision_at_k"):
        if k not in metrics:
            raise ModelError(f"realism_guard: metrics missing {k!r}")
    for k in ("roc_auc", "pr_auc"):                       # a bool/NaN/inf metric must not slip a comparison
        if not (_finite_num(metrics[k]) and 0.0 <= metrics[k] <= 1.0):   # _finite_num rejects True/False too
            raise ModelError(f"realism_guard: metric {k!r} is not a finite value in [0,1] ({metrics[k]!r})")
    pk = metrics["precision_at_k"]
    if not (isinstance(pk, dict) and pk.get("status") in ("ok", "insufficient_denominator")):
        raise ModelError("realism_guard: malformed precision_at_k")
    if pk["status"] == "ok" and not (_finite_num(pk.get("precision")) and 0.0 <= pk["precision"] <= 1.0):
        raise ModelError("realism_guard: precision_at_k precision is not a finite value in [0,1]")
    v = []
    if metrics["roc_auc"] > REALISM_AUC_MAX:
        v.append(f"ROC-AUC {metrics['roc_auc']:.3f} > {REALISM_AUC_MAX}")
    if metrics["pr_auc"] > REALISM_PR_AUC_MAX:
        v.append(f"PR-AUC {metrics['pr_auc']:.3f} > {REALISM_PR_AUC_MAX}")
    if pk["status"] == "ok" and pk["precision"] == 1.0:
        v.append("precision@k is a perfect 1.0")
    dec = set(DECOY_FEATURES) & {k for k, _ in sorted(model["coef"].items(), key=lambda kv: -abs(kv[1]))[:3]}
    if dec:
        v.append(f"decoy(s) in the top-3 by |coef|: {sorted(dec)}")
    if v:
        raise ModelError("realism guard tripped: " + "; ".join(v))
    return True


def _percentile(sorted_vals, q):
    if not sorted_vals:
        raise ModelError("percentile of an empty sequence")
    return sorted_vals[min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))]


def risk_bands(probs):
    """Calibrated-probability cutoffs for the low / elevated / high tiers (elevated = 85th pctile, high =
    97th). Computed on the CALIBRATION slice so no test-slice information leaks into the committed thresholds.
    Fails closed on an empty set or any probability outside [0,1]/non-finite (a poisoned input can't set a
    band)."""
    if not probs:
        raise ModelError("risk_bands: empty probability set")
    for p in probs:
        if not _finite_num(p) or not (0.0 <= p <= 1.0):   # _finite_num rejects bool/str/None (no raw TypeError)
            raise ModelError(f"risk_bands: probability {p!r} outside [0,1]")
    s = sorted(probs)
    return {"elevated": round(_percentile(s, BAND_ELEVATED_PCTILE), COEF_DP),
            "high": round(_percentile(s, BAND_HIGH_PCTILE), COEF_DP)}


def risk_tier(prob, bands):
    """Map a calibrated probability to a support tier (never an adverse-action label). Fails closed on a
    probability that is not a finite real number (bool/str/None -> ModelError, never a raw TypeError, and
    True/False are NOT treated as 1/0), and on malformed bands — bands must be exactly {elevated, high} with
    finite thresholds satisfying 0 <= elevated <= high <= 1 (a string/negative/>1 band is a caller error
    surfaced as ModelError, never a silent 'low')."""
    if not _finite_num(prob):                              # rejects bool/str/None BEFORE any comparison
        raise ModelError(f"risk_tier: probability must be a finite number (got {prob!r})")
    if not (0.0 <= prob <= 1.0):
        raise ModelError(f"risk_tier: probability {prob!r} outside [0,1]")
    if not (isinstance(bands, dict) and set(bands) == {"elevated", "high"}):
        raise ModelError("risk_tier: bands must be exactly {elevated, high}")
    lo, hi = bands["elevated"], bands["high"]
    if not (_finite_num(lo) and _finite_num(hi)):
        raise ModelError("risk_tier: band thresholds must be finite numbers")
    if not (0.0 <= lo <= hi <= 1.0):
        raise ModelError("risk_tier: bands must satisfy 0 <= elevated <= high <= 1")
    return "high" if prob >= hi else "elevated" if prob >= lo else "low"


# --------------------------------------------------------------- segment layer / Layer 2 (Increment 4)

SEG_MIN_N = 30                    # suppress a segment with fewer than this many distinct employees (small-n)
RECONCILE_GAP = 0.02             # flag a segment where |bottom-up - top-down| 6-mo risk exceeds this
SEG_REID_CUTOFF = 10             # below this, even the coarse size band collapses to "<10" (re-identification-safe)


def _size_band(n_emp, floor):
    """A COARSE size bucket for a suppressed segment — never the exact tiny count. A suppressed group is by
    definition below the floor; reporting exactly '2' or '3' is a residual re-identification signal, so we
    expose only '<10' or '10-<floor>'. Rendered (non-suppressed) segments are >= the floor and report their
    exact size, which is not a re-identification risk."""
    return f"<{SEG_REID_CUTOFF}" if n_emp < SEG_REID_CUTOFF else f"{SEG_REID_CUTOFF}-{floor - 1}"


def _km_exit_probability(idxs, rows, value_of, horizon_months):
    """Discrete-time survival S = prod_t (1 - rate_t) over the segment's observation months, where rate_t is
    the mean of `value_of(i)` across the person-months observed in month t; returns the exit probability
    1 - S over up to `horizon_months`. Used BOTH ways so bottom-up and top-down are structurally identical
    and differ only in the monthly rate: `value_of` = the 0/1 voluntary label gives the empirical
    Kaplan-Meier estimate; `value_of` = the calibrated monthly hazard gives the model's aggregate. Each is a
    per-month rate in [0,1], so the running product stays a valid survival probability."""
    by_month = {}
    for i in idxs:
        cell = by_month.setdefault(rows[i][INDEX_COL], [0.0, 0])
        cell[0] += value_of(i)
        cell[1] += 1
    surv = 1.0
    for mi in sorted(by_month)[:horizon_months]:
        total, at_risk = by_month[mi]
        if at_risk:
            surv *= (1.0 - total / at_risk)
    return 1.0 - surv


def segment_risk(rows, model, calibration, design, slices, dims=None, min_n=SEG_MIN_N, horizon=6):
    """Layer-2 segment risk on the out-of-time TEST slice, computed TWO ways per segment and reconciled:
      * BOTTOM-UP: aggregate the model's calibrated Layer-1 monthly hazards into a survival curve.
      * TOP-DOWN: the segment's empirical Kaplan-Meier survival over the same window.
    Both use the identical per-month survival structure, so the reconciliation gap is a pure
    predicted-vs-observed comparison, not a formula artifact. Segments with fewer than `min_n` distinct
    employees are SUPPRESSED (small-n): they carry NO estimate and only a COARSE size_band (never an exact
    re-identifiable count) — the suppression floor can be RAISED but never lowered below SEG_MIN_N, so a
    caller can never turn the privacy floor off. Each rendered segment carries its
    reconciliation gap (bottom-up minus top-down) and a flag when it exceeds RECONCILE_GAP — the
    disagreement is surfaced, never averaged away. `rows` must be the panel `design` was built from, aligned
    index-for-identity (enforced below, not merely by length). The exit window is `horizon` months and is
    reported on each entry as `horizon_months`; the `*_6mo` keys are the default-horizon labels."""
    # fail closed on the governance-critical arguments (mirrors peers.py: `type() is not int` rejects bool)
    if type(min_n) is not int or min_n < SEG_MIN_N:
        raise ModelError(f"segment_risk: min_n must be an int >= {SEG_MIN_N} (the privacy floor; got {min_n!r})")
    if type(horizon) is not int or horizon <= 0:
        raise ModelError(f"segment_risk: horizon must be a positive integer (got {horizon!r})")
    if len(rows) != len(design["X"]):
        raise ModelError("segment_risk: rows are not aligned with the design matrix")
    if any(rows[i][ID_COL] != design["emp"][i] or rows[i][INDEX_COL] != design["month_index"][i]
           for i in range(len(rows))):
        raise ModelError("segment_risk: rows are not aligned index-for-identity with the design matrix")
    dims = dims or list(SEGMENT_COLS)
    unknown = [d for d in dims if d not in SEGMENT_COLS]
    if unknown:
        raise ModelError(f"segment_risk: unknown segment dimension(s) {unknown}")
    _validate_slices(design, slices)                       # canonical out-of-time slices only (no train-as-test, no empty)
    test = slices["test"]
    out = {}
    for dim in dims:
        groups = {}
        for i in test:
            groups.setdefault(rows[i]["segments"][dim], []).append(i)
        segs = []
        for val, idxs in sorted(groups.items()):
            n_emp = len({rows[i][ID_COL] for i in idxs})
            if n_emp < min_n:                              # small-n: suppress the estimate AND coarsen the size
                segs.append({"value": val, "suppressed": True, "size_band": _size_band(n_emp, min_n)})
                continue
            entry = {"value": val, "n_employees": n_emp, "n_rows": len(idxs)}
            haz = {i: calibrated_probability(model, calibration, design["X"][i]) for i in idxs}
            bottom_up = round(_km_exit_probability(idxs, rows, lambda i: haz[i], horizon), 6)
            top_down = round(_km_exit_probability(idxs, rows, lambda i: design["y"][i], horizon), 6)
            gap = round(bottom_up - top_down, 6)           # gap is exactly the two displayed numbers' difference
            entry.update({"suppressed": False, "horizon_months": horizon,
                          "bottom_up_6mo": bottom_up, "top_down_6mo": top_down,
                          "reconciliation_gap": gap, "gap_flagged": abs(gap) > RECONCILE_GAP})
            segs.append(entry)
        out[dim] = segs
    return out


def reconciliation_summary(segments):
    """Roll up the segment reconciliation: how many rendered segments disagree (bottom-up vs top-down beyond
    RECONCILE_GAP) and the largest absolute gap — surfaced, not averaged away."""
    rendered = [s for segs in segments.values() for s in segs if not s.get("suppressed")]
    suppressed = sum(1 for segs in segments.values() for s in segs if s.get("suppressed"))
    return {"n_segments": len(rendered), "n_suppressed": suppressed,
            "n_flagged": sum(1 for s in rendered if s.get("gap_flagged")),
            "max_abs_gap": round(max((abs(s["reconciliation_gap"]) for s in rendered), default=0.0), 6)}


# --------------------------------------------------- company-level committee rollups (Increment 4 helpers)
# These keep the ANALYTICS in the engine (deterministic, fail-closed, tested) so a renderer does NO math — it
# only formats what these return. Each reuses the EXACT segment/band machinery above, so a company number is
# never a hand-average of segment rows.

def company_risk(rows, model, calibration, design, slices, horizon=6):
    """The whole out-of-time TEST slice as one 'company' pseudo-segment: model BOTTOM-UP vs empirical
    TOP-DOWN `horizon`-month voluntary-exit risk, via the identical `_km_exit_probability` machinery
    `segment_risk` uses over the same window (never a hand-average of segment rows). Fails closed on
    unaligned rows / a non-canonical slice, exactly like `segment_risk`. Returns
    {bottom_up, top_down, gap, n_employees, n_rows, horizon_months}."""
    if type(horizon) is not int or horizon <= 0:
        raise ModelError(f"company_risk: horizon must be a positive integer (got {horizon!r})")
    if len(rows) != len(design["X"]):
        raise ModelError("company_risk: rows are not aligned with the design matrix")
    if any(rows[i][ID_COL] != design["emp"][i] or rows[i][INDEX_COL] != design["month_index"][i]
           for i in range(len(rows))):
        raise ModelError("company_risk: rows are not aligned index-for-identity with the design matrix")
    _validate_model(model)
    _validate_calibration(calibration)
    _validate_slices(design, slices)
    test = slices["test"]
    months_observed = len({rows[i][INDEX_COL] for i in test})
    if horizon > months_observed:                          # never silently truncate + mislabel the horizon
        raise ModelError(f"company_risk: horizon {horizon} exceeds the {months_observed} observed test months — "
                         "the empirical top-down number would be truncated yet reported at the larger horizon")
    haz = {i: calibrated_probability(model, calibration, design["X"][i]) for i in test}
    bottom_up = round(_km_exit_probability(test, rows, lambda i: haz[i], horizon), 6)
    top_down = round(_km_exit_probability(test, rows, lambda i: design["y"][i], horizon), 6)
    return {"bottom_up": bottom_up, "top_down": top_down, "gap": round(bottom_up - top_down, 6),
            "n_employees": len({rows[i][ID_COL] for i in test}), "n_rows": len(test),
            "horizon_months": horizon, "months_observed": months_observed}


def tier_counts(model, calibration, design, slices):
    """Support-tier sizing: set the low/elevated/high thresholds on the CALIBRATION slice (no test-slice
    leakage), then bucket every TEST-slice PERSON-MONTH (never a person) into a tier. Returns
    {counts:{low,elevated,high}, thresholds:{elevated,high}, n_rows}. Fails closed on a non-canonical slice."""
    _validate_model(model)
    _validate_calibration(calibration)
    _validate_slices(design, slices)
    bands = risk_bands([calibrated_probability(model, calibration, design["X"][i]) for i in slices["calibration"]])
    counts = {"low": 0, "elevated": 0, "high": 0}
    for i in slices["test"]:
        counts[risk_tier(calibrated_probability(model, calibration, design["X"][i]), bands)] += 1
    return {"counts": counts, "thresholds": bands, "n_rows": len(slices["test"])}


def company_survival(model, calibration, design, slices, max_h=12, median_h=18):
    """Company-level survival S(t) under the calibrated model. The per-month company hazard is the MEAN
    calibrated hazard across TEST person-months in each observed test month; because the observed window is
    only the test slice long, months beyond it are PROJECTED forward at the mean observed monthly hazard — a
    frozen-hazard 'what-if at the current rate', NOT a path forecast (the renderer labels it so). By
    construction S at the observed-window end equals `company_risk`'s bottom-up, so the two panels agree.
    Returns {survival:[S1..S_max_h], p_exit:[1-S1..], months_observed, median_months (sentinel-aware over
    median_h; None => 'not reached'), median_horizon, max_h}."""
    if type(max_h) is not int or max_h <= 0:
        raise ModelError(f"company_survival: max_h must be a positive integer (got {max_h!r})")
    if type(median_h) is not int or median_h <= 0:
        raise ModelError(f"company_survival: median_h must be a positive integer (got {median_h!r})")
    _validate_model(model)
    _validate_calibration(calibration)
    _validate_slices(design, slices)
    by_month = {}
    for i in slices["test"]:
        cell = by_month.setdefault(design["month_index"][i], [0.0, 0])
        cell[0] += calibrated_probability(model, calibration, design["X"][i])
        cell[1] += 1
    observed = [by_month[mi][0] / by_month[mi][1] for mi in sorted(by_month) if by_month[mi][1]]
    if not observed:
        raise ModelError("company_survival: no test-slice person-months to form a hazard path")
    proj = sum(observed) / len(observed)                          # mean observed monthly hazard for the projection tail

    def _path(n):
        p = list(observed[:n])
        while len(p) < n:
            p.append(proj)                                        # frozen-hazard projection beyond the observed window
        return p

    surv = survival_curve(_path(max_h))                           # fails closed on any rate outside [0,1]
    return {"survival": [round(s, 6) for s in surv],
            "p_exit": [round(1.0 - s, 6) for s in surv],
            "months_observed": min(len(observed), max_h),
            "median_months": median_months_to_exit(_path(median_h), median_h),
            "median_horizon": median_h, "max_h": max_h}


# --------------------------------------------------------------------------- model manifest (scaffold)

_MANIFEST_KEYS = {
    "schema_version", "feature_version", "model_version", "status", "increment",
    "target_event", "label_column", "event_values", "audit_strata_column",
    "id_column", "time_column", "segment_dims", "model_features", "allowlist_features",
    "decoy_features", "missable_features", "design_features", "missing_indicator_suffix",
    "primary_coefficients", "primary_calibration",
    "challenger_calibration", "risk_band_thresholds", "training_window",
    "panel_rows", "panel_data_hash",
}


def build_manifest(path: Path = MANIFEST_PATH, panel_path: Path = PANEL_PATH) -> dict:
    """(Re)build the canonical model manifest by TRAINING the glass-box hazard: fit on the train slice,
    Platt-calibrate on the calibration slice, and write the fitted artifacts (coefficients in STANDARDIZED
    units, the standardizer, the Platt params, the temporal window) rounded for a readable file. Because
    fitted floats are cross-platform-noisy, CI does NOT byte-diff this file — it re-fits and checks
    reproducibility within a tolerance (see check_reproducible). Same-platform it is exactly deterministic."""
    model, calibration, design, slices = train_model(panel_path)
    realism_guard(model, evaluate(model, calibration, design, slices))   # fail closed — never ship an implausibly-perfect model
    bands = risk_bands([calibrated_probability(model, calibration, design["X"][i]) for i in slices["calibration"]])

    def rnd(v):
        return round(float(v), COEF_DP)

    dnames = list(DESIGN_FEATURES)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "model_version": MODEL_VERSION,
        "status": "trained",
        "increment": TRAINED_INCREMENT,
        "target_event": TARGET_EVENT,
        "label_column": LABEL_COL,
        "event_values": list(EVENT_VALUES),
        "audit_strata_column": STRATA_COL,
        "id_column": ID_COL,
        "time_column": TIME_COL,
        "segment_dims": list(SEGMENT_COLS),
        "model_features": list(MODEL_FEATURES),
        "allowlist_features": list(ALLOWLIST_FEATURES),
        "decoy_features": list(DECOY_FEATURES),
        "missable_features": sorted(MISSABLE),
        "design_features": dnames,
        "missing_indicator_suffix": MISSING_SUFFIX,
        "primary_coefficients": {
            "intercept": rnd(model["intercept"]),
            "features": {k: rnd(v) for k, v in model["coef"].items()},
            "standardizer_mean": {dnames[j]: rnd(model["mean"][j]) for j in range(len(dnames))},
            "standardizer_std": {dnames[j]: rnd(model["std"][j]) for j in range(len(dnames))},
            "pos_weight": rnd(model["pos_weight"]),
        },
        "primary_calibration": {"method": "platt", "a": rnd(calibration["a"]), "b": rnd(calibration["b"])},
        "challenger_calibration": {},    # filled at Increment 5
        "risk_band_thresholds": bands,   # low/elevated/high cutoffs on calibrated probability (calibration slice)
        "training_window": {
            "train_max_month_index": SLICE_T1,
            "calibration_max_month_index": SLICE_T2,
            "test_min_month_index": SLICE_T2 + 1,
            "l2": L2_LAMBDA, "irls_iters": IRLS_ITERS, "horizons_months": list(HORIZONS),
            "n_train": len(slices["train"]), "n_calibration": len(slices["calibration"]),
            "n_test": len(slices["test"]),
        },
        "panel_rows": len(design["X"]),
        "panel_data_hash": panel_data_hash(panel_path),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    return manifest


def check_reproducible(manifest: dict = None, panel_path: Path = PANEL_PATH, tol: float = 1e-5) -> bool:
    """Re-fit the model and confirm the committed manifest's coefficients + calibration reproduce within
    `tol`. Fitted floats carry cross-platform ULP noise, so this tolerance check — NOT a byte-diff — is the
    manifest sync gate for the trained fields; it still catches real drift (a changed panel or model config)."""
    m = manifest or load_manifest()
    validate_manifest(m)                                   # fail closed on a malformed manifest — this is a
    #                                                      # sync-gate API, so it must defend its own shape
    #                                                      # (raw KeyError/TypeError -> controlled ManifestError)
    if m.get("status") != "trained":
        return True                                        # nothing fitted to reproduce
    model, calibration, _design, slices = train_model(panel_path)
    # provenance must be REAL, not decorative: a manifest that re-fits but declares the wrong version /
    # increment / row-count is false release metadata and must fail the sync gate (not just the coefficients).
    if m.get("model_version") != MODEL_VERSION:
        raise ManifestError(f"manifest model_version {m.get('model_version')!r} != {MODEL_VERSION!r}")
    if m.get("increment") != TRAINED_INCREMENT:
        raise ManifestError(f"manifest increment {m.get('increment')!r} != trained increment {TRAINED_INCREMENT}")
    if m.get("panel_rows") != len(_design["X"]):
        raise ManifestError(f"manifest panel_rows {m.get('panel_rows')!r} != actual design rows {len(_design['X'])}")
    # the panel fingerprint must be REAL, not decorative: a model can re-fit and reproduce coefficients while
    # the manifest declares a zeroed/forged panel_data_hash. Compare the declared hash to the actual panel hash
    # so a fabricated provenance line fails the sync gate. (validate_manifest checks only the 64-hex SHAPE;
    # this is where it is bound to the bytes on disk.)
    actual_hash = panel_data_hash(panel_path)
    if m.get("panel_data_hash") != actual_hash:
        raise ManifestError(
            f"manifest panel_data_hash {str(m.get('panel_data_hash'))[:12]}… != actual {actual_hash[:12]}… "
            "(stale or forged panel provenance)")
    pc = m["primary_coefficients"]
    dn = list(DESIGN_FEATURES)

    def _close(mapping, key, value):
        if key not in mapping or abs(mapping[key] - value) > tol:
            raise ManifestError(f"manifest value {key!r} not reproduced within {tol:g}")

    # intercept + every coefficient (exact key set, values within tolerance)
    if set(pc.get("features", {})) != set(model["coef"]):
        raise ManifestError("manifest coefficient key set drifted from the model")
    _close(pc, "intercept", model["intercept"])
    for k, v in model["coef"].items():
        _close(pc["features"], k, v)
    # the FULL scoring artifact: standardizer (keys + values) + class weight — a corrupted standardizer
    # must not pass CI (this was the Increment-2 sync-gate gap)
    for field, vec in (("standardizer_mean", model["mean"]), ("standardizer_std", model["std"])):
        sd = pc.get(field, {})
        if set(sd) != set(dn):
            raise ManifestError(f"manifest {field} key set drifted from the design features")
        for j, name in enumerate(dn):
            _close(sd, name, vec[j])
    _close(pc, "pos_weight", model["pos_weight"])
    # calibration
    cal = m["primary_calibration"]
    if cal.get("method") != "platt":
        raise ManifestError("manifest calibration method drifted from 'platt'")
    _close(cal, "a", calibration["a"])
    _close(cal, "b", calibration["b"])
    # training window: config constants + derived slice counts must all reproduce exactly
    tw = m["training_window"]
    expect = {"train_max_month_index": SLICE_T1, "calibration_max_month_index": SLICE_T2,
              "test_min_month_index": SLICE_T2 + 1, "l2": L2_LAMBDA, "irls_iters": IRLS_ITERS,
              "horizons_months": list(HORIZONS), "n_train": len(slices["train"]),
              "n_calibration": len(slices["calibration"]), "n_test": len(slices["test"])}
    for k, v in expect.items():
        if tw.get(k) != v:
            raise ManifestError(f"manifest training_window[{k!r}] drifted (expected {v!r}, got {tw.get(k)!r})")
    # risk-band thresholds (Increment 3) — recomputed on the calibration slice, must reproduce
    rb = m.get("risk_band_thresholds", {})
    for k, v in risk_bands([calibrated_probability(model, calibration, _design["X"][i]) for i in slices["calibration"]]).items():
        _close(rb, k, v)
    return True


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"manifest not found: {p} (run: retention.py build-manifest)")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ManifestError(f"manifest is not valid JSON: {e}")


def _finite_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _validate_trained_shape(m: dict) -> None:
    """Strict nested schema for a TRAINED manifest — a malformed fitted artifact fails as ManifestError,
    never a raw KeyError/TypeError downstream."""
    dset = set(DESIGN_FEATURES)
    pc = m["primary_coefficients"]
    if not isinstance(pc, dict) or set(pc) != {"intercept", "features", "standardizer_mean", "standardizer_std", "pos_weight"}:
        raise ManifestError("primary_coefficients must have exactly {intercept, features, standardizer_mean, standardizer_std, pos_weight}")
    if not _finite_num(pc.get("intercept")):
        raise ManifestError("primary_coefficients.intercept must be a finite number")
    for field in ("features", "standardizer_mean", "standardizer_std"):
        d = pc.get(field)
        if not isinstance(d, dict) or set(d) != dset or not all(_finite_num(x) for x in d.values()):
            raise ManifestError(f"primary_coefficients.{field} must map every design feature to a finite number")
    if not all(pc["standardizer_std"][k] > 0 for k in DESIGN_FEATURES):
        raise ManifestError("primary_coefficients.standardizer_std must be strictly positive")
    if not _finite_num(pc.get("pos_weight")) or pc["pos_weight"] <= 0:
        raise ManifestError("primary_coefficients.pos_weight must be a positive finite number")
    cal = m["primary_calibration"]
    if not isinstance(cal, dict) or set(cal) != {"method", "a", "b"} or cal.get("method") != "platt" \
            or not _finite_num(cal.get("a")) or not _finite_num(cal.get("b")):
        raise ManifestError("primary_calibration must be exactly {method:'platt', a:<finite>, b:<finite>}")
    tw = m["training_window"]
    if not isinstance(tw, dict) or set(tw) != {"train_max_month_index", "calibration_max_month_index",
                                               "test_min_month_index", "l2", "irls_iters", "horizons_months",
                                               "n_train", "n_calibration", "n_test"}:
        raise ManifestError("training_window has unexpected or missing keys")
    for k in ("train_max_month_index", "calibration_max_month_index", "test_min_month_index",
              "irls_iters", "n_train", "n_calibration", "n_test"):
        if not (isinstance(tw.get(k), int) and not isinstance(tw[k], bool) and tw[k] >= 0):
            raise ManifestError(f"training_window.{k} must be a non-negative int")
    if not _finite_num(tw.get("l2")) or not (isinstance(tw.get("horizons_months"), list)
                                             and tw["horizons_months"]
                                             and all(isinstance(h, int) and not isinstance(h, bool) and h > 0
                                                     for h in tw["horizons_months"])):
        raise ManifestError("training_window.l2 / horizons_months are malformed")
    rb = m.get("risk_band_thresholds")
    if not isinstance(rb, dict) or set(rb) != {"elevated", "high"} \
            or not _finite_num(rb.get("elevated")) or not _finite_num(rb.get("high")):
        raise ManifestError("risk_band_thresholds must be exactly {elevated:<finite>, high:<finite>}")
    if not (0.0 <= rb["elevated"] <= rb["high"] <= 1.0):
        raise ManifestError("risk_band_thresholds must satisfy 0 <= elevated <= high <= 1")


def validate_manifest(m: dict) -> None:
    """Fail-closed schema check of a loaded manifest (used by tests + CI). Every contract-pinned field
    must equal its source of truth, scaffold/trained must be cross-field consistent, and a trained
    manifest's nested fitted artifact must pass a strict schema (_validate_trained_shape)."""
    if not isinstance(m, dict):
        raise ManifestError("manifest must be a JSON object")
    missing = _MANIFEST_KEYS - set(m)
    extra = set(m) - _MANIFEST_KEYS
    if missing:
        raise ManifestError(f"manifest missing keys: {sorted(missing)}")
    if extra:
        raise ManifestError(f"manifest has unexpected keys: {sorted(extra)}")
    # every contract-derived field must match the module's source of truth (no silent governance drift)
    pinned = {
        "schema_version": SCHEMA_VERSION, "feature_version": FEATURE_VERSION,
        "target_event": TARGET_EVENT, "label_column": LABEL_COL, "audit_strata_column": STRATA_COL,
        "id_column": ID_COL, "time_column": TIME_COL, "event_values": list(EVENT_VALUES),
        "segment_dims": list(SEGMENT_COLS), "model_features": list(MODEL_FEATURES),
        "allowlist_features": list(ALLOWLIST_FEATURES), "decoy_features": list(DECOY_FEATURES),
        "missable_features": sorted(MISSABLE), "design_features": list(DESIGN_FEATURES),
        "missing_indicator_suffix": MISSING_SUFFIX,
    }
    for k, want in pinned.items():
        if m.get(k) != want:
            raise ManifestError(f"manifest {k} drifted from the contract")
    if m["status"] not in ("scaffold", "trained"):
        raise ManifestError(f"bad status {m['status']!r}")
    if not (isinstance(m.get("increment"), int) and not isinstance(m["increment"], bool) and m["increment"] >= 0):
        raise ManifestError("increment must be a non-negative int")
    if not (isinstance(m.get("panel_rows"), int) and not isinstance(m["panel_rows"], bool) and m["panel_rows"] > 0):
        raise ManifestError("panel_rows must be a positive int")
    h = m.get("panel_data_hash", "")
    if not (isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h)):
        raise ManifestError("panel_data_hash must be a 64-char sha256 hex string")
    if m.get("challenger_calibration") != {}:              # reserved-empty until Increment 5 — fail closed on any payload
        raise ManifestError("challenger_calibration must be {} until the Increment-5 challenger lands")
    # scaffold (Increment 0) vs trained: the fitted primary model (coefficients + calibration) must match the
    # status, so a scaffold can't be mislabeled trained and a trained manifest can't ship an empty model.
    primary_present = bool(m.get("primary_coefficients")) and bool(m.get("primary_calibration"))
    if m["status"] == "scaffold":
        if m["increment"] != 0 or m["model_version"] != _SCAFFOLD_VERSION or primary_present:
            raise ManifestError("scaffold manifest must be increment 0, scaffold model_version, and carry no fitted primary model")
    else:  # trained
        if not primary_present:
            raise ManifestError("trained manifest must carry a fitted primary model (coefficients + calibration)")
        if m["model_version"] != MODEL_VERSION:
            raise ManifestError(f"trained manifest model_version must be {MODEL_VERSION!r} (got {m['model_version']!r})")
        if m["increment"] != TRAINED_INCREMENT:
            raise ManifestError(f"trained manifest increment must be {TRAINED_INCREMENT} (got {m['increment']!r})")
        _validate_trained_shape(m)


# --------------------------------------------------------------------------- CLI

def _summary(rows):
    from collections import Counter
    ev = Counter(r[LABEL_COL] for r in rows)
    emps = {r[ID_COL] for r in rows}
    return (f"{len(rows)} person-months · {len(emps)} employees · "
            f"target(voluntary)={ev['voluntary']} · involuntary={ev['involuntary']} · "
            f"retirement={ev['retirement']} · none={ev['none']}")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "validate"
    if cmd == "build-manifest":
        m = build_manifest()
        print(f"wrote {MANIFEST_PATH.relative_to(ROOT)} (status={m['status']}, "
              f"panel_rows={m['panel_rows']}, panel_data_hash={m['panel_data_hash'][:12]}…)")
        return 0
    if cmd == "validate":
        try:
            rows = load_panel()
            print(f"panel OK — {_summary(rows)}")
            m = load_manifest()
            validate_manifest(m)
            if m["panel_data_hash"] != panel_data_hash():
                print("::error:: manifest panel_data_hash is STALE — run retention.py build-manifest")
                return 1
            check_reproducible(m)                          # re-fit + confirm the trained model reproduces (sync gate)
            print(f"manifest OK — status={m['status']}, model {m['model_version']} reproduces within tolerance")
            return 0
        except (PanelError, ManifestError) as e:
            print(f"FAIL-CLOSED: {e}")
            return 1
    print(f"unknown command {cmd!r} (use: validate | build-manifest)")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
