#!/usr/bin/env python3
"""Increment-0 contract tests for `retention-risk` — data contract + hashing only (no model math).

Covers: panel load + fail-closed validation, the canonical feature-snapshot hash (stability,
decimal quantization, None handling, order-independence, rejection rules), quarantined/protected
column exclusion, competing-risks label correctness (voluntary-only target; involuntary/retirement
censored; one terminal event per employee), and the model-manifest schema + sync gate.
"""
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
ok(m["status"] == "scaffold", "committed manifest is the Increment-0 scaffold")
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
# scaffold/trained cross-field consistency (a scaffold can't be mislabeled trained, or carry results)
raises(R.ManifestError, lambda: R.validate_manifest({**m, "status": "trained"}), "scaffold mislabeled trained rejected")
raises(R.ManifestError, lambda: R.validate_manifest({**m, "primary_coefficients": {"comp_ratio": 1.0}}),
       "scaffold carrying model results rejected")

print(f"OK — {CHECKS} retention Increment-0 contract checks passed "
      f"({len(rows)} person-months, target voluntary={ev['voluntary']}).")
