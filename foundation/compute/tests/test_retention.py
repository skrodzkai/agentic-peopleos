#!/usr/bin/env python3
"""Tests for `retention-risk` shared compute (Increments 0-4).

Covers: (0) panel load + fail-closed validation, the canonical feature-snapshot hash, quarantined/
protected exclusion, competing-risks label + panel-level temporal invariants, and the manifest schema;
(1) the row-local feature builder — leakage/row-locality, missing-indicators, planted-signal presence;
(2) the glass-box IRLS hazard model — out-of-time slices, planted-coefficient recovery, decoy top-3,
survival/horizon/median math, exact additive explanations, Platt calibration (Brier improvement), the
tolerance reproducibility gate, and fail-closed numerics;
(3) out-of-time evaluation (PR-AUC / precision@k / Brier / horizon-concordance), the realism guard, and
the calibration-slice risk bands + tier mapping;
(4) the Layer-2 segment layer — bottom-up (model) vs top-down (empirical KM) reconciliation with the gap
surfaced, region-band broad-only, planted-signal survival to segments, small-n suppression, determinism.
"""
import copy
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.compute import retention as R   # noqa: E402

CHECKS = 0


def ok(cond, msg):
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(msg)


def raises(exc, fn, msg):
    try:
        fn()
    except exc:
        ok(True, msg)
        return
    raise AssertionError(f"expected {exc.__name__}: {msg}")


# --------------------------------------------------------------- helpers (synthetic test panels)

def _feat(**over):
    f = {c: 0.0 for c in R.MODEL_FEATURES}
    f.update(over)
    return f


def _row(emp="R-0001", month="2025-01-31", idx=0, event="none", group="A", **over):
    row = {R.ID_COL: emp, R.TIME_COL: month, R.INDEX_COL: idx,
           R.STRATA_COL: group, R.LABEL_COL: event}
    for c in R.SEGMENT_COLS:
        row[c] = {"function": "Engineering", "level": "L4", "region_band": "Americas",
                  "manager_span_band": "IC", "comp_position_band": "within",
                  "tenure_band": "2-3y"}[c]
    for c in R.MODEL_FEATURES:
        row[c] = 1.0
    row.update(over)
    return row


def _write(rows, header=None):
    header = header or R.REQUIRED_COLS
    d = Path(tempfile.mkdtemp())
    p = d / "panel.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})
    return p


def _raw(lines):
    p = Path(tempfile.mkdtemp()) / "panel.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# --------------------------------------------------------------- 1) committed panel loads + sane

rows = R.load_panel()
ok(len(rows) > 10000, "committed panel loads with many rows")
from collections import Counter
ev = Counter(r[R.LABEL_COL] for r in rows)
ok(ev["voluntary"] > 0, "voluntary (target) events present")
ok(ev["involuntary"] > 0, "involuntary events present (competing risk)")
ok(ev["retirement"] > 0, "retirement events present (competing risk)")
ok(set(ev) <= set(R.EVENT_VALUES), "only allowed event values appear")

# competing-risks label correctness: per employee, <=1 non-'none' event, and it is the LAST row
by_emp = {}
for r in rows:
    by_emp.setdefault(r[R.ID_COL], []).append(r)
multi = bad_terminal = nonmono = 0
for emp, ers in by_emp.items():
    idxs = [r[R.INDEX_COL] for r in ers]
    if idxs != sorted(idxs) or len(set(idxs)) != len(idxs):
        nonmono += 1
    nz = [i for i, r in enumerate(ers) if r[R.LABEL_COL] != "none"]
    if len(nz) > 1:
        multi += 1
    elif len(nz) == 1 and nz[0] != len(ers) - 1:
        bad_terminal += 1
ok(multi == 0, "no employee has more than one terminal event")
ok(bad_terminal == 0, "a terminal event is always the employee's last observed month")
ok(nonmono == 0, "month_index is strictly increasing + unique per employee")

# missingness is genuinely present (None) for the two MISSABLE features
miss = sum(1 for r in rows if r["features"]["engagement_slope_3p"] is None)
ok(miss > 0, "engagement_slope_3p has real missingness (None) in the panel")

# --------------------------------------------------------------- 2) canonical feature-snapshot hash

AS = "2025-06-30"
h1 = R.feature_snapshot_hash(_feat(comp_ratio=0.99), as_of=AS)
h2 = R.feature_snapshot_hash(_feat(comp_ratio=0.99), as_of=AS)
ok(h1 == h2, "hash is stable for identical inputs")
ok(len(h1) == 64 and all(c in "0123456789abcdef" for c in h1), "hash is 64-char sha256 hex")
ok(R.feature_snapshot_hash(_feat(comp_ratio=0.88), as_of=AS) != h1, "different feature -> different hash")
# decimal quantization: differences below 6dp collide; at/above 6dp differ
ok(R.feature_snapshot_hash(_feat(comp_ratio=0.99123456), as_of=AS)
   == R.feature_snapshot_hash(_feat(comp_ratio=0.99123499), as_of=AS),
   "sub-6dp float differences are quantized to the same hash")
ok(R.feature_snapshot_hash(_feat(comp_ratio=0.99123), as_of=AS)
   != R.feature_snapshot_hash(_feat(comp_ratio=0.99124), as_of=AS),
   "5th-decimal differences change the hash")
# None (missing) differs from 0.0, and is hashable
ok(R.feature_snapshot_hash(_feat(engagement_slope_3p=None), as_of=AS)
   != R.feature_snapshot_hash(_feat(engagement_slope_3p=0.0), as_of=AS),
   "missing (None) hashes differently from 0.0")
# key-order independence
import collections
od = collections.OrderedDict((k, 1.0) for k in reversed(R.MODEL_FEATURES))
ok(R.feature_snapshot_hash(dict(od), as_of=AS) == R.feature_snapshot_hash(_feat(**{k: 1.0 for k in R.MODEL_FEATURES}), as_of=AS),
   "hash is independent of feature insertion order")
# as_of and feature-set rejections
raises(ValueError, lambda: R.feature_snapshot_hash(_feat(), as_of="2025/06/30"), "bad as_of format rejected")
raises(ValueError, lambda: R.feature_snapshot_hash(_feat(), as_of="2025-13-01"), "impossible as_of date rejected")
raises(ValueError, lambda: R.feature_snapshot_hash({**_feat(), "bogus": 1.0}, as_of=AS), "non-feature key rejected")
raises(ValueError, lambda: R.feature_snapshot_hash(_feat(comp_ratio=float("inf")), as_of=AS), "non-finite value rejected")
# schema/feature versions are bound into the hash (so a contract bump can't collide with the old hash)
_hbase = R.feature_snapshot_hash(_feat(), as_of=AS)
ok(R.feature_snapshot_hash(_feat(), as_of=AS, schema_version="9.9.9") != _hbase, "schema_version is bound into the hash")
ok(R.feature_snapshot_hash(_feat(), as_of=AS, feature_version="9.9.9") != _hbase, "feature_version is bound into the hash")

# --------------------------------------------------------------- 3) header / column governance

base = [_row(emp="R-0001", month="2025-01-31", idx=0),
        _row(emp="R-0001", month="2025-02-28", idx=1, event="voluntary")]
ok(len(R.load_panel(_write(base))) == 2, "a minimal valid panel loads")

raises(R.PanelError, lambda: R.load_panel(_write(base, header=R.REQUIRED_COLS[:-1])),
       "missing a required column fails closed")
raises(R.PanelError, lambda: R.load_panel(_write(base, header=R.REQUIRED_COLS + ["surprise"])),
       "an unexpected extra column fails closed")
raises(R.PanelError, lambda: R.load_panel(_write(base, header=R.REQUIRED_COLS + ["commute_change"])),
       "a quarantined column fails closed")
raises(R.PanelError, lambda: R.load_panel(_write(base, header=R.REQUIRED_COLS + ["gender"])),
       "a protected column fails closed")
raises(R.PanelError, lambda: R.load_panel(_write(base, header=R.REQUIRED_COLS + [R.ID_COL])),
       "a duplicate column fails closed")

# --------------------------------------------------------------- 4) row-level validation

raises(R.PanelError, lambda: R.load_panel(_write([_row(emp="E-0001")])), "malformed emp_id rejected")
raises(R.PanelError, lambda: R.load_panel(_write([_row(month="2025-13-01")])), "malformed month rejected")
raises(R.PanelError, lambda: R.load_panel(_write([_row(event="quit")])), "unknown event rejected")
raises(R.PanelError, lambda: R.load_panel(_write([_row(group="Z")])), "unknown audit group rejected")
raises(R.PanelError, lambda: R.load_panel(_write([_row(function="")])), "empty segment rejected")
raises(R.PanelError, lambda: R.load_panel(_write([_row(comp_ratio="abc")])), "non-numeric feature rejected")
raises(R.PanelError, lambda: R.load_panel(_write([_row(comp_ratio="")])), "blank non-missable feature rejected")
raises(R.PanelError, lambda: R.load_panel(_write([_row(month_index="x")])), "non-integer month_index rejected")
raises(R.PanelError, lambda: R.load_panel(_write([_row(month_index=-1)])), "negative month_index rejected")
dup = [_row(emp="R-0002", month="2025-03-31", idx=0), _row(emp="R-0002", month="2025-03-31", idx=1)]
raises(R.PanelError, lambda: R.load_panel(_write(dup)), "duplicate (emp_id, month) rejected")

# MISSABLE columns may be blank -> None
missable = [_row(emp="R-0003", month="2025-04-30", idx=0,
                 perf_rating_delta_4q="", engagement_slope_3p="")]
mrows = R.load_panel(_write(missable))
ok(mrows[0]["features"]["perf_rating_delta_4q"] is None
   and mrows[0]["features"]["engagement_slope_3p"] is None, "blank missable features load as None")

# panel-level (grouped-by-employee) invariants — the discrete-time-hazard shape, enforced by the LOADER
bad_terminal = [_row(emp="R-0009", month="2025-01-31", idx=0, event="voluntary"),
                _row(emp="R-0009", month="2025-02-28", idx=1, event="none")]
raises(R.PanelError, lambda: R.load_panel(_write(bad_terminal)), "a terminal event before the last row is rejected")
two_term = [_row(emp="R-0010", month="2025-01-31", idx=0, event="voluntary"),
            _row(emp="R-0010", month="2025-02-28", idx=1, event="involuntary")]
raises(R.PanelError, lambda: R.load_panel(_write(two_term)), "two terminal events for one employee rejected")
nonmono = [_row(emp="R-0011", month="2025-02-28", idx=1, event="none"),
           _row(emp="R-0011", month="2025-01-31", idx=0, event="none")]
raises(R.PanelError, lambda: R.load_panel(_write(nonmono)), "non-ascending months for one employee rejected")
good_seq = [_row(emp="R-0012", month="2025-01-31", idx=0, event="none"),
            _row(emp="R-0012", month="2025-02-28", idx=1, event="voluntary")]
ok(len(R.load_panel(_write(good_seq))) == 2, "a valid terminal-last sequence still loads")
# month_index must match the GLOBAL month position (can't drift from the calendar sequence)
drift_idx = [_row(emp="R-0013", month="2025-01-31", idx=99, event="none"),
             _row(emp="R-0013", month="2025-02-28", idx=100, event="none")]
raises(R.PanelError, lambda: R.load_panel(_write(drift_idx)), "month_index drifted from global month position rejected")
gap_idx = [_row(emp="R-0014", month="2025-01-31", idx=0, event="none"),
           _row(emp="R-0014", month="2025-03-31", idx=2, event="none")]
raises(R.PanelError, lambda: R.load_panel(_write(gap_idx)), "non-contiguous active months rejected")
# global months must be REAL, consecutive month-ends (renumbered calendar gap + mid-month dates rejected)
cal_gap = [_row(emp="R-0015", month="2025-01-31", idx=0, event="none"),
           _row(emp="R-0015", month="2025-03-31", idx=1, event="none")]   # Feb skipped, indices renumbered
raises(R.PanelError, lambda: R.load_panel(_write(cal_gap)), "skipped calendar month (renumbered) rejected")
midmonth = [_row(emp="R-0016", month="2025-01-15", idx=0, event="none"),
            _row(emp="R-0016", month="2025-02-15", idx=1, event="none")]  # not month-ends
raises(R.PanelError, lambda: R.load_panel(_write(midmonth)), "mid-month dates rejected")

# --------------------------------------------------------------- 5) manifest schema + sync gate

m = R.load_manifest()
R.validate_manifest(m)
ok(m["status"] == "trained" and m["increment"] == 3, "committed manifest is the trained model (Increment 3)")
ok(m["panel_data_hash"] == R.panel_data_hash(), "manifest panel_data_hash matches the committed panel (sync gate)")
ok(len(m["panel_data_hash"]) == 64, "panel_data_hash is a 64-char sha256 hex")
ok(m["model_features"] == list(R.MODEL_FEATURES), "manifest model_features matches the contract")
ok(m["target_event"] == "voluntary", "manifest target is voluntary-only")
ok(set(R.DECOY_FEATURES) <= set(m["model_features"]), "decoys are part of the model feature set (for the realism guard)")

raises(R.ManifestError, lambda: R.validate_manifest({**m, "status": "live"}), "bad manifest status rejected")
raises(R.ManifestError, lambda: R.validate_manifest({k: v for k, v in m.items() if k != "panel_data_hash"}),
       "manifest missing a key rejected")
raises(R.ManifestError, lambda: R.validate_manifest({**m, "surprise": 1}), "manifest extra key rejected")
raises(R.ManifestError, lambda: R.validate_manifest({**m, "model_features": m["model_features"][:-1]}),
       "manifest feature drift rejected")
raises(R.ManifestError, lambda: R.validate_manifest({**m, "panel_data_hash": "nope"}),
       "manifest bad hash rejected")

# build_manifest is deterministic (same dict twice)
import json
b1 = R.build_manifest(path=Path(tempfile.mkdtemp()) / "m.json")
b2 = R.build_manifest(path=Path(tempfile.mkdtemp()) / "m.json")
ok(json.dumps(b1, sort_keys=True) == json.dumps(b2, sort_keys=True), "build_manifest is deterministic")
ok(b1["panel_rows"] == len(rows), "manifest panel_rows matches the loaded panel")

# --------------------------------------------------------------- 6) hardening (self-review-driven)

# non-finite feature values fail closed at load (not just at hash time)
raises(R.PanelError, lambda: R.load_panel(_write([_row(comp_ratio="inf")])), "non-finite feature (inf) rejected at load")
raises(R.PanelError, lambda: R.load_panel(_write([_row(comp_ratio="nan")])), "NaN feature rejected at load")

# over-wide / short CSV rows fail closed (restkey/restval), not silently coerced
_hdr = ",".join(R.REQUIRED_COLS)
_vals = [str(_row()[c]) for c in R.REQUIRED_COLS]
raises(R.PanelError, lambda: R.load_panel(_raw([_hdr, ",".join(_vals) + ",EXTRA"])), "over-wide row rejected")
raises(R.PanelError, lambda: R.load_panel(_raw([_hdr, ",".join(_vals[:-1])])), "short row rejected")
ok(len(R.load_panel(_raw([_hdr, ",".join(_vals)]))) == 1, "an exact-width raw row still loads")

# feature_snapshot_hash requires EVERY model feature (absent != None) and normalizes -0.0
raises(ValueError, lambda: R.feature_snapshot_hash({k: 1.0 for k in R.MODEL_FEATURES[:-1]}, as_of=AS),
       "missing feature key rejected (absent != None)")
ok(R.feature_snapshot_hash(_feat(comp_ratio=-0.0), as_of=AS) == R.feature_snapshot_hash(_feat(comp_ratio=0.0), as_of=AS),
   "negative zero normalizes to the same hash")

# validate_manifest pins EVERY contract field + types
for fld, bad in [("allowlist_features", m["allowlist_features"][:-1]), ("decoy_features", []),
                 ("missable_features", ["x"]), ("segment_dims", m["segment_dims"][:-1]),
                 ("event_values", ["none", "voluntary"]), ("id_column", "x"), ("schema_version", "9.9.9")]:
    raises(R.ManifestError, lambda f=fld, b=bad: R.validate_manifest({**m, f: b}), f"manifest {fld} drift rejected")
raises(R.ManifestError, lambda: R.validate_manifest({**m, "panel_rows": 0}), "non-positive panel_rows rejected")
raises(R.ManifestError, lambda: R.validate_manifest({**m, "increment": True}), "bool increment rejected")
# scaffold/trained cross-field consistency (the committed manifest is TRAINED at Increment 2)
raises(R.ManifestError, lambda: R.validate_manifest({**m, "status": "scaffold"}), "trained manifest mislabeled scaffold rejected")
raises(R.ManifestError, lambda: R.validate_manifest({**m, "primary_coefficients": {}}), "trained manifest with empty results rejected")

# --------------------------------------------------------------- 7) feature builder (Increment 1)

D = R.build_design(rows)
names, X, Y = D["feature_names"], D["X"], D["y"]
ok(names == R.DESIGN_FEATURES, "design columns = model features + a missing-indicator per missable feature")
ok(len(X) == len(rows) and len(Y) == len(rows), "one design row per panel row")
ok(all(len(v) == len(names) for v in X), "every design vector aligns to the feature list")
ok(set(Y) <= {0, 1}, "labels are binary")

# competing risks: ONLY voluntary exits are positives; involuntary/retirement are censored (y=0)
ok(sum(Y) == ev["voluntary"], "positives == voluntary exits (involuntary/retirement never positive)")
irow = next(r for r in rows if r[R.LABEL_COL] == "involuntary")
ok(R.build_design([irow])["y"][0] == 0, "an involuntary exit is never coded as a positive")
# build_design fails closed on an invalid label instead of silently coercing it to y=0
bad = dict(irow); bad[R.LABEL_COL] = "quit"
raises(R.PanelError, lambda: R.build_design([bad]), "build_design rejects an unknown event label")

# the design feature order is pinned in the manifest + validated (future coefficients depend on it)
ok(m["design_features"] == list(R.DESIGN_FEATURES), "manifest pins the exact design feature order")
ok(m["missing_indicator_suffix"] == R.MISSING_SUFFIX, "manifest pins the missing-indicator suffix")
raises(R.ManifestError, lambda: R.validate_manifest({**m, "design_features": m["design_features"][:-1]}),
       "manifest design_features drift rejected")
raises(R.ManifestError, lambda: R.validate_manifest({**m, "missing_indicator_suffix": "_x"}),
       "manifest missing_indicator_suffix drift rejected")

# row-locality: a design vector is a pure function of its own panel row (no cross-row / future leakage)
ok(R.build_design([rows[5]])["X"][0] == X[5], "the design is row-local — no cross-row leakage")
ok(R.build_design(rows)["X"] == X, "build_design is deterministic")

# missing handling: missing -> 0.0 impute + explicit indicator 1; present -> indicator 0
jval = names.index("engagement_slope_3p")
jmis = names.index("engagement_slope_3p" + R.MISSING_SUFFIX)
mrow = next(r for r in rows if r["features"]["engagement_slope_3p"] is None)
dm = R.build_design([mrow])["X"][0]
ok(dm[jval] == 0.0 and dm[jmis] == 1.0, "a missing feature is imputed 0.0 and flagged by its indicator")
prow = next(r for r in rows if r["features"]["engagement_slope_3p"] is not None)
ok(R.build_design([prow])["X"][0][jmis] == 0.0, "a present feature leaves its missing-indicator at 0")


def _qdelta(feat):
    """Top-quartile minus bottom-quartile voluntary rate when rows are sorted by `feat` — a univariate
    read of how much that feature separates exits."""
    j = names.index(feat)
    pairs = sorted(zip((v[j] for v in X), Y))
    k = len(pairs) // 4
    return sum(v for _, v in pairs[-k:]) / k - sum(v for _, v in pairs[:k]) / k


# planted signal is present and recoverable; the named decoys carry less signal than the real drivers
ok(_qdelta("mths_since_promo") > 0, "planted: more months-since-promo -> more voluntary exits")
ok(_qdelta("comp_ratio") < 0, "planted: lower comp_ratio -> more voluntary exits")
ok(_qdelta("unvested_equity_pct_comp") < 0, "planted: lower unvested equity (handcuffs off) -> more exits")
_real = min(abs(_qdelta("mths_since_promo")), abs(_qdelta("comp_ratio")))
for _d in R.DECOY_FEATURES:
    ok(abs(_qdelta(_d)) < _real, f"decoy {_d} separates exits less than the real drivers")

# --------------------------------------------------------------- 8) glass-box hazard model (Increment 2)

import statistics  # noqa: E402
model, calib, design, slices = R.train_model()

# out-of-time temporal slices are disjoint and correctly ordered
ok(not (set(slices["train"]) & set(slices["test"])), "train and test slices are disjoint")
ok(all(design["month_index"][i] <= R.SLICE_T1 for i in slices["train"]), "train slice is the earliest months")
ok(all(design["month_index"][i] > R.SLICE_T2 for i in slices["test"]), "test slice is out-of-time (latest months)")

# the fit RECOVERS the planted signal — correct coefficient signs; decoys stay below the real drivers
signs = {"mths_since_promo": 1, "comp_ratio": -1, "unvested_equity_pct_comp": -1,
         "mgr_team_attrition_ttm": 1, "engagement_slope_3p": -1, "post_vest_window_flag": 1,
         "stuck_in_level_flag": 1, "mgr_changed_12m": 1}
for f, s in signs.items():
    ok((model["coef"][f] > 0) == (s > 0), f"recovered planted coefficient sign for {f}")
# no decoy ranks in the TOP-3 features by |coefficient| — the exact property the Increment-3 realism guard
# enforces (a weak-but-real allowlist feature can sit below a decoy under collinearity, so top-3 is the honest bar)
_top3 = {k for k, _ in sorted(model["coef"].items(), key=lambda kv: -abs(kv[1]))[:3]}
ok(not (set(R.DECOY_FEATURES) & _top3), "no decoy is in the top-3 features by |coefficient|")

# survival math (hand-checked)
sc = R.survival_curve([0.1, 0.2])
ok(abs(sc[0] - 0.9) < 1e-12 and abs(sc[1] - 0.72) < 1e-12, "S(t) = prod(1 - lambda)")
ok(abs(R.horizon_probability([0.1] * 6, 6) - (1 - 0.9 ** 6)) < 1e-12, "horizon probability = 1 - prod(1 - lambda)")
ok(R.median_months_to_exit([0.05] * 11) is None, "median = 'not reached' when survival stays >= 0.5")
ok(R.median_months_to_exit([0.2] * 12) == 4, "median = first month survival drops below 0.5 (0.8^4=0.41)")

# explanations are EXACT: additive contributions + intercept reconstruct the model logit
xr = design["X"][slices["test"][0]]
contribs = R.explain(model, xr, top=len(model["feature_names"]))
ok(abs(model["intercept"] + sum(c for _, c in contribs) - R._logit(model, xr)) < 1e-9,
   "additive explanations + intercept sum to the exact logit")

# Platt calibration: valid probabilities, and it centers the mean near the base rate on the calibration slice
ci = slices["calibration"]
base = sum(design["y"][i] for i in ci) / len(ci)
mean_cal = statistics.mean(R.calibrated_probability(model, calib, design["X"][i]) for i in ci)
ok(0.0 <= R.calibrated_probability(model, calib, xr) <= 1.0, "calibrated output is a valid probability")
ok(abs(mean_cal - base) < 0.01, "calibrated mean ≈ base rate on the calibration slice (well-calibrated in aggregate)")
# calibration genuinely IMPROVES over the raw (pos_weight-inflated) scores, and discrimination is real-but-not-perfect
_raw = [R._sigmoid(R._logit(model, design["X"][i])) for i in ci]
_cal = [R.calibrated_probability(model, calib, design["X"][i]) for i in ci]
_yci = [design["y"][i] for i in ci]
ok(R.brier_score(_cal, _yci) < R.brier_score(_raw, _yci), "Platt calibration improves the Brier score over the raw model")
_tst = slices["test"]
_auc = R.rank_auc([R._logit(model, design["X"][i]) for i in _tst], [design["y"][i] for i in _tst])
ok(0.65 < _auc < 0.90, f"out-of-time discrimination is real but not too-perfect (test AUC={_auc:.3f})")

# fail-closed numerics (self-review): degenerate inputs raise ModelError, never a raw ZeroDivision/NaN/overflow
raises(R.ModelError, lambda: R._standardizer([[1.0, 2.0]], []), "standardizer with no rows fails closed")
raises(R.ModelError, lambda: R._sigmoid(float("nan")), "a non-finite logit fails closed")
raises(R.ModelError, lambda: R._solve([[0.0, 0.0], [0.0, 0.0]], [1.0, 1.0]), "a singular system fails closed")

# eval helpers fail closed on invalid inputs (lengths, labels, ranges) — Codex Inc-2 HIGH
raises(R.ModelError, lambda: R.rank_auc([0.1], [0, 1, 1]), "rank_auc length mismatch rejected")
raises(R.ModelError, lambda: R.rank_auc([0.1, 0.2], [0, 2]), "rank_auc non-binary label rejected")
raises(R.ModelError, lambda: R.brier_score([1.2, 0.1], [1, 0]), "brier probability out of [0,1] rejected")
raises(R.ModelError, lambda: R.survival_curve([0.1, 1.5]), "survival hazard out of [0,1] rejected")
raises(R.ModelError, lambda: R.horizon_probability([0.1], -1), "negative horizon rejected")
raises(R.ModelError, lambda: R._logit(model, [0.0] * (len(model["feature_names"]) - 1)),
       "a short scoring row fails closed (not a raw IndexError)")
raises(R.ModelError, lambda: R.platt_calibrate(model, {**design, "y": [0] * len(design["y"])}, slices["calibration"]),
       "a single-class calibration slice is rejected")

# the FULL trained artifact is protected (Codex Inc-2 HIGH): a corrupted standardizer / window fails the gate
_ms = copy.deepcopy(m); _ms["primary_coefficients"]["standardizer_mean"][R.DESIGN_FEATURES[0]] = 999999.0
raises(R.ManifestError, lambda: R.check_reproducible(_ms), "a corrupted standardizer fails the reproducibility gate")
_mw = copy.deepcopy(m); _mw["training_window"]["n_train"] += 1
raises(R.ManifestError, lambda: R.check_reproducible(_mw), "a corrupted training-window count fails the gate")
# strict nested schema — malformed trained fields fail as ManifestError, not a raw KeyError
_mb = copy.deepcopy(m); _mb["primary_coefficients"]["standardizer_std"][R.DESIGN_FEATURES[0]] = -1.0
raises(R.ManifestError, lambda: R.validate_manifest(_mb), "a non-positive standardizer_std is rejected")
_mb2 = copy.deepcopy(m); _mb2["primary_calibration"] = {"method": "platt", "a": "x", "b": 0.0}
raises(R.ManifestError, lambda: R.validate_manifest(_mb2), "a non-numeric calibration param is rejected")
_mb3 = copy.deepcopy(m); del _mb3["primary_coefficients"]["standardizer_mean"][R.DESIGN_FEATURES[0]]
raises(R.ManifestError, lambda: R.validate_manifest(_mb3), "a standardizer missing a design feature is rejected")

# the committed manifest reproduces the trained model within tolerance; a tampered coefficient fails
ok(set(m["primary_coefficients"]["features"]) == set(R.DESIGN_FEATURES), "manifest pins a coefficient per design feature")
ok(R.check_reproducible(m) is True, "committed model reproduces within tolerance (the trained sync gate)")
import copy  # noqa: E402
_mt = copy.deepcopy(m); _mt["primary_coefficients"]["intercept"] += 1.0
raises(R.ManifestError, lambda: R.check_reproducible(_mt), "a tampered coefficient fails reproduction")

# --------------------------------------------------------------- 9) evaluation + realism guard (Increment 3)

E = R.evaluate(model, calib, design, slices)
ok(0.65 < E["roc_auc"] < 0.90, f"out-of-time ROC-AUC is in a believable band ({E['roc_auc']:.3f})")
ok(E["pr_auc"] > E["base_rate"], "PR-AUC beats the base rate (real lift under imbalance)")
ok(E["brier"] < E["base_rate"] * (1 - E["base_rate"]), "Brier beats a constant base-rate predictor")
ok(0.6 < E["horizon_concordance"] < 0.95, f"survival concordance is real but not perfect ({E['horizon_concordance']:.3f})")
ok(E["precision_at_k"]["status"] == "ok" and E["precision_at_k"]["n_flagged"] >= 50, "precision@k has a sufficient denominator")
ok(E["precision_at_k"]["precision"] > E["base_rate"], "top-decile precision beats the base rate")
ok(E["validation"].startswith("synthetic-only"), "the evaluation is labeled synthetic-validation-only")

# realism guard: passes on the honest model, trips on each tripwire
ok(R.realism_guard(model, E) is True, "realism guard passes on the honest model")
raises(R.ModelError, lambda: R.realism_guard(model, {**E, "roc_auc": 0.97}), "realism guard trips on ROC-AUC > 0.90")
raises(R.ModelError, lambda: R.realism_guard(model, {**E, "precision_at_k": {"status": "ok", "precision": 1.0, "n_flagged": 100}}),
       "realism guard trips on a perfect precision@k")
_fake = {**model, "coef": {**model["coef"], R.DECOY_FEATURES[0]: 999.0}}
raises(R.ModelError, lambda: R.realism_guard(_fake, E), "realism guard trips when a decoy is forced into the top-3")
raises(R.ModelError, lambda: R.realism_guard(model, {**E, "pr_auc": 0.8}), "realism guard trips on an implausible PR-AUC")
raises(R.ModelError, lambda: R.realism_guard(model, {"roc_auc": 0.8}), "realism guard rejects a metrics dict missing keys")
# risk_tier fails closed on non-finite / reversed bands (module fail-closed-numerics contract)
raises(R.ModelError, lambda: R.risk_tier(float("nan"), m["risk_band_thresholds"]), "risk_tier rejects a non-finite probability")
raises(R.ModelError, lambda: R.risk_tier(0.5, {"elevated": 0.7, "high": 0.3}), "risk_tier rejects reversed bands")

# precision@k denominator rule (a tiny population reports insufficient rather than a fluke)
ok(R.precision_at_k([0.9, 0.1, 0.8], [1, 0, 1], [0, 0, 0], min_denom=50)["status"] == "insufficient_denominator",
   "precision@k reports insufficient denominator for a tiny population")

# horizon concordance covers BOTH comparable-pair types
ok(R.horizon_concordance([0.9, 0.1], [3, None]) == 1.0, "higher risk correctly ranks the exiter over the event-free")
ok(R.horizon_concordance([0.9, 0.1], [2, 4]) == 1.0, "higher risk correctly ranks the earlier of two in-window exits")
ok(R.horizon_concordance([0.1, 0.9], [2, 4]) == 0.0, "discordant when the lower-risk person exits earlier")

# risk bands are ordered in [0,1] and the tier mapping is monotone
_b = m["risk_band_thresholds"]
ok(0.0 <= _b["elevated"] <= _b["high"] <= 1.0, "risk bands are ordered within [0,1]")
ok(R.risk_tier(_b["high"] + 0.01, _b) == "high" and R.risk_tier(0.0, _b) == "low"
   and R.risk_tier((_b["elevated"] + _b["high"]) / 2, _b) == "elevated", "risk_tier maps probabilities to the correct tier")

# --------------------------------------------------------------- 10) segment layer / Layer 2 (Increment 4)
seg = R.segment_risk(rows, model, calib, design, slices)
ok(set(seg) == set(R.SEGMENT_COLS), "segment_risk covers every governed segment dimension")
_rendered = [s for segs in seg.values() for s in segs if not s.get("suppressed")]
ok(len(_rendered) > 0, "segment_risk renders segments on the committed panel")
ok(all({"bottom_up_6mo", "top_down_6mo", "reconciliation_gap", "gap_flagged"} <= set(s) for s in _rendered),
   "each rendered segment carries the model estimate, the empirical estimate, and their gap")
ok(all(0.0 <= s["bottom_up_6mo"] <= 1.0 and 0.0 <= s["top_down_6mo"] <= 1.0 for s in _rendered),
   "both segment 6-month probabilities lie within [0,1]")
ok(all(abs(round(s["bottom_up_6mo"] - s["top_down_6mo"], 6) - s["reconciliation_gap"]) < 1e-9 for s in _rendered),
   "the reconciliation gap is exactly bottom-up minus top-down (surfaced, never hidden)")
ok(all(s.get("horizon_months") == 6 for s in _rendered), "each rendered segment labels its own exit window")
# the displayed numbers are the REAL ones: independently recompute one segment's two estimates from scratch
_cs = next(s for s in seg["function"] if s["value"] == "Customer Success")
_idxs = [i for i in slices["test"] if rows[i]["segments"]["function"] == "Customer Success"]
_haz = {i: R.calibrated_probability(model, calib, design["X"][i]) for i in _idxs}
_bu = round(R._km_exit_probability(_idxs, rows, lambda i: _haz[i], 6), 6)
_td = round(R._km_exit_probability(_idxs, rows, lambda i: design["y"][i], 6), 6)
ok(_cs["bottom_up_6mo"] == _bu and _cs["top_down_6mo"] == _td,
   "the surfaced segment estimates equal an independent from-scratch recomputation (not a self-consistent label)")
# region band stays broad-only — a governance requirement, never a country-level slice
ok(all(s["value"] in {"Americas", "EMEA", "APAC"} for s in seg["region_band"]),
   "region_band exposes only broad regions")
# the reconciliation is not a rubber stamp: it flags genuine bottom-up/top-down disagreement, but not everything
_recon = R.reconciliation_summary(seg)
ok(_recon["n_segments"] == len(_rendered), "reconciliation summary counts every rendered segment")
ok(0 < _recon["n_flagged"] < _recon["n_segments"], "reconciliation flags real disagreements without flagging all")
ok(_recon["max_abs_gap"] < 0.15, "no rendered segment diverges implausibly far (sanity ceiling)")
# the planted signal survives aggregation to the segment level (segment layer inherits Layer-1 drivers) —
# checked on BOTH the model estimate and the independent empirical estimate, so neither can carry it alone
_comp = {s["value"]: s["bottom_up_6mo"] for s in seg["comp_position_band"]}
_comp_td = {s["value"]: s["top_down_6mo"] for s in seg["comp_position_band"]}
ok(_comp["below"] > _comp["within"] > _comp["above"], "underpaid segments carry higher model segment risk than overpaid")
ok(_comp_td["below"] > _comp_td["within"] > _comp_td["above"], "the empirical rate shows the same comp-position ordering")
_ten = {s["value"]: s["bottom_up_6mo"] for s in seg["tenure_band"]}
ok(_ten["<1y"] < _ten["1-2y"] < _ten["2-3y"] and _ten["<1y"] < _ten["5y+"],
   "segment risk rises monotonically off the newest-tenure floor")
# small-n suppression hides thin segments and never leaks an estimate for them
_supp = R.segment_risk(rows, model, calib, design, slices, min_n=10 ** 9)
ok(all(s.get("suppressed") for segs in _supp.values() for s in segs), "an impossible threshold suppresses every segment")
ok(all("bottom_up_6mo" not in s for segs in _supp.values() for s in segs), "a suppressed segment leaks no estimate")
ok(all("n_employees" in s for segs in _supp.values() for s in segs), "a suppressed segment still reports its size")
# reconciliation_summary handles the all-suppressed / empty-rendered case without crashing (default=0.0 path)
_rs = R.reconciliation_summary(_supp)
ok(_rs["n_segments"] == 0 and _rs["n_flagged"] == 0 and _rs["max_abs_gap"] == 0.0 and _rs["n_suppressed"] > 0,
   "reconciliation_summary is well-defined when every segment is suppressed")
# the small-n privacy floor CANNOT be lowered or disabled — the governance-critical guard
for _bad in (0, -5, 10.0, True, None):
    raises(R.ModelError, lambda b=_bad: R.segment_risk(rows, model, calib, design, slices, min_n=b),
           f"segment_risk rejects a suppression floor of {_bad!r} (privacy floor cannot be lowered)")
# horizon must be a positive int — a fat-fingered 0 must not read as a false 'all reconciled'
for _bad in (0, -3, 3.0, None):
    raises(R.ModelError, lambda b=_bad: R.segment_risk(rows, model, calib, design, slices, horizon=b),
           f"segment_risk rejects a horizon of {_bad!r}")
# an unknown segment dimension fails closed (ModelError), not a raw KeyError
raises(R.ModelError, lambda: R.segment_risk(rows, model, calib, design, slices, dims=["not_a_dimension"]),
       "segment_risk rejects an unknown segment dimension")
# determinism + fail-closed on BOTH a length-mismatched AND a same-length-but-reordered panel
ok(R.segment_risk(rows, model, calib, design, slices) == seg, "segment_risk is deterministic")
raises(R.ModelError, lambda: R.segment_risk(rows[:-1], model, calib, design, slices),
       "segment_risk fails closed when the panel length differs from the design matrix")
raises(R.ModelError, lambda: R.segment_risk(list(reversed(rows)), model, calib, design, slices),
       "segment_risk fails closed on a same-length but reordered panel (identity guard, not just length)")

print(f"OK — {CHECKS} retention Increment 0+1+2+3+4 checks passed "
      f"(contract + feature builder + glass-box hazard + eval/realism-guard + segment layer; {len(rows)} "
      f"person-months, {len(names)} design features, voluntary={ev['voluntary']}; test AUC={E['roc_auc']:.3f}, "
      f"concordance={E['horizon_concordance']:.3f}; {_recon['n_segments']} segments reconciled, "
      f"{_recon['n_flagged']} gaps surfaced, max gap {_recon['max_abs_gap']:.3f}; realism-guarded, reproducible).")
