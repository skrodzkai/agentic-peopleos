#!/usr/bin/env python3
"""retention.py — data contract + hashing for the `retention-risk` model (Increment 0).

This module is the **contract**, not the model. Increment 0 ships only what's needed to validate
the synthetic monthly person-period panel and pin its provenance — there is deliberately **no model
math** here yet (no fitting, no scoring, no calibration); those arrive in later increments, each
behind a Codex adversarial review.

What it provides:
  * `load_panel()`      — fail-closed load + schema validation of the committed retention panel.
  * `feature_snapshot_hash()` — the canonical, cross-platform-stable hash of a scoring input
                          (FEATURES ONLY; `model_version` is logged separately, never mixed in).
  * `panel_data_hash()` — sha256 of the committed panel file (provenance for the manifest).
  * the manifest scaffold (`build_manifest()`) + its schema validator (`validate_manifest()`).

Governance properties enforced here (so the first code drop can't encode ambiguity):
  * QUARANTINED / PROTECTED columns may **never** appear in the panel (defense-in-depth).
  * the model feature set is an explicit allowlist + named decoys; nothing else is a feature.
  * the canonical hash pins serialization (UTF-8, sorted keys, compact separators, no NaN/Inf,
    decimal-quantized floats, ISO dates) so it is stable across Python versions and float drift.
  * the hash is an INTEGRITY POINTER, not anonymization — a small feature space is brute-forceable;
    the real-data posture (documented, not built) uses a keyed HMAC / governed digest service.

stdlib only; deterministic; offline; fail-closed.

CLI:
    python foundation/compute/retention.py validate         # load + validate panel + manifest
    python foundation/compute/retention.py build-manifest   # (re)emit the canonical scaffold manifest
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
MODEL_VERSION = "0.0.0-scaffold"          # Increment 0: no trained model exists yet

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
    serialization is identical across Python versions / float repr (no binary-float drift in the hash)."""
    if v is None:
        return None
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
    voluntary exit), emp, month_index, month — the last three for grouped/temporal splits in Increment 3.
    """
    miss = sorted(MISSABLE)
    names = list(MODEL_FEATURES) + [f"{c}{MISSING_SUFFIX}" for c in miss]
    X, y, emp, midx, month = [], [], [], [], []
    for r in rows:
        ev = r.get(LABEL_COL)
        if ev not in EVENT_VALUES:                        # fail closed — never silently coerce a bad label to y=0
            raise PanelError(f"build_design got an invalid {LABEL_COL}: {ev!r}")
        f = r["features"]
        vec = [0.0 if f[c] is None else float(f[c]) for c in MODEL_FEATURES]
        vec += [1.0 if f[c] is None else 0.0 for c in miss]
        X.append(vec)
        y.append(1 if ev == TARGET_EVENT else 0)
        emp.append(r[ID_COL])
        midx.append(r[INDEX_COL])
        month.append(r[TIME_COL])
    return {"feature_names": names, "X": X, "y": y, "emp": emp, "month_index": midx, "month": month}


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
    """(Re)build the canonical model-manifest scaffold from the committed panel and write it
    deterministically. In Increment 0 the model fields are empty placeholders (status=scaffold);
    they fill in at Increments 2–5. CI byte-diffs this against the committed file (sync gate)."""
    rows = load_panel(panel_path)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "model_version": MODEL_VERSION,
        "status": "scaffold",
        "increment": 0,
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
        "design_features": list(DESIGN_FEATURES),          # the exact model-ready column ORDER (Increment 1)
        "missing_indicator_suffix": MISSING_SUFFIX,
        "primary_coefficients": {},      # filled at Increment 2 (glass-box hazard)
        "primary_calibration": {},       # filled at Increment 2/3 (Platt on the calibration slice)
        "challenger_calibration": {},    # filled at Increment 5
        "risk_band_thresholds": {},      # filled at Increment 3 (on calibrated probability)
        "training_window": {"slices": None},   # train/calibration/test set at Increment 3
        "panel_rows": len(rows),
        "panel_data_hash": panel_data_hash(panel_path),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    return manifest


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"manifest not found: {p} (run: retention.py build-manifest)")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ManifestError(f"manifest is not valid JSON: {e}")


def validate_manifest(m: dict) -> None:
    """Fail-closed schema check of a loaded manifest (used by tests + CI). Every contract-pinned field
    must equal its source of truth, and scaffold/trained must be cross-field consistent."""
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
    # scaffold (Increment 0) vs trained: results must match the status, so a scaffold can't be mislabeled trained
    tw = m.get("training_window")
    result_fields = ("primary_coefficients", "primary_calibration", "challenger_calibration", "risk_band_thresholds")
    results_empty = (all(not m.get(f) for f in result_fields)
                     and isinstance(tw, dict) and tw.get("slices") is None)
    if m["status"] == "scaffold":
        if m["increment"] != 0 or m["model_version"] != MODEL_VERSION or not results_empty:
            raise ManifestError("scaffold manifest must be increment 0, scaffold model_version, empty model results")
    else:  # trained
        if results_empty or m["model_version"] == MODEL_VERSION:
            raise ManifestError("trained manifest must carry non-empty model results + a non-scaffold model_version")


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
            print(f"manifest OK — status={m['status']}, in sync with the committed panel")
            return 0
        except (PanelError, ManifestError) as e:
            print(f"FAIL-CLOSED: {e}")
            return 1
    print(f"unknown command {cmd!r} (use: validate | build-manifest)")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
