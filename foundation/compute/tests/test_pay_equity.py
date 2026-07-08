#!/usr/bin/env python3
"""Tests for the pay-equity engine: the fail-closed data contract + the pay-gap invariants.
Fail-closed cases copy the committed workers.csv to a tmp dir and corrupt exactly one thing."""
import csv
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.compute import pay_equity as PE  # noqa: E402

_ACME = Path(__file__).resolve().parents[3] / "foundation" / "data" / "acme"
passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


def raises(fn, label):
    global passed
    try:
        fn()
        assert False, f"FAILED (no raise): {label}"
    except PE.PayEquityDataError:
        passed += 1


def _set(header, row, col, val):
    """Return a copy of `row` with the cell under column `col` replaced by `val`."""
    row = list(row)
    row[header.index(col)] = val
    return row


def _tmp_with(mutate_rows):
    """A tmp dir holding a copy of workers.csv, with mutate_rows(header, rows) applied and written back.
    mutate_rows returns (header, rows); return header=None to write a header-only corruption."""
    d = Path(tempfile.mkdtemp())
    with open(_ACME / "workers.csv", newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        rows = [r for r in rd]
    header, rows = mutate_rows(list(header), rows)
    with open(d / "workers.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        if header is not None:
            wr.writerow(header)
        wr.writerows(rows)
    return d


# ---- happy path: shape + population -----------------------------------------------------------------
r = PE.compute()
ok(set(r) >= {"company", "as_of", "population", "pay_measure", "headline", "dimensions", "disclaimer"},
   "compute returns the documented top-level keys")
ok(r["as_of"] == "2026-01-31" and r["company"] == "Acme Corp (ACMQ)", "company + as-of are stamped")
ok(r["population"]["n_analyzed"] == 2023, "analyzes the 2023 in-scope employees")
ok(r["population"]["excluded"]["not_employee"] == 260, "excludes the 260 contractors (no protected-class data)")
ok(r["population"]["excluded"]["not_employed_status"] == 377, "excludes terminated workers")
ok("hourly" in r["pay_measure"].lower() and "base only" in r["pay_measure"].lower(),
   "pay measure is disclosed as FTE-hourly, base only")

_gender = next(d for d in r["dimensions"] if d["key"] == "gender_group")
_eth = next(d for d in r["dimensions"] if d["key"] == "ethnicity_group")

# ---- unadjusted: reference group is the highest-paid, its own gap is exactly zero -------------------
_u = _gender["unadjusted"]
ok(_u["reference_group"] == "A", "reference group is the highest-paid group (A)")
_ref = next(g for g in _u["groups"] if g["is_reference"])
ok(_ref["mean_gap_pct"] == 0.0 and _ref["median_gap_pct"] == 0.0, "the reference group's gap vs itself is 0")
_b = next(g for g in _u["groups"] if g["group"] == "B")
ok(_b["median_gap_pct"] > 0 and _b["mean_gap_pct"] > 0, "the non-reference group shows a positive (behind) raw gap")
ok(sum(g["n"] for g in _u["groups"]) == r["population"]["n_analyzed"], "the group counts partition the population")

# ---- adjusted: the like-for-like residual is far below the raw gap, with a CI that brackets it ------
_a = _gender["adjusted"]
_ab = _a["groups"][0]
ok(_a["n"] == r["population"]["n_analyzed"], "the regression uses the full analyzed population")
ok(0.0 <= _a["r2"] <= 1.0 and _a["r2"] > 0.5, "the pay model explains a majority of variance (level/role/geo)")
ok(_ab["ci_lo_pct"] <= _ab["adjusted_gap_pct"] <= _ab["ci_hi_pct"], "the adjusted CI brackets the point estimate")
ok(abs(_ab["adjusted_gap_pct"]) < _b["median_gap_pct"],
   "controlling for level/role/geo/tenure shrinks the gap far below the raw median gap (composition effect)")
ok(_ab["significant"] is False and _ab["ci_lo_pct"] < 0 < _ab["ci_hi_pct"],
   "the residual gender gap is not statistically distinguishable from zero (CI spans 0)")
# every control the model claims to hold fixed is actually in the design
ok(any(c.startswith("lvl::") for c in _a["controls"]) and any(c.startswith("loc::") for c in _a["controls"])
   and "tenure_years" in _a["controls"] and "mgr" in _a["controls"], "the adjusted model carries its named controls")

# ethnicity uses the same machinery with 2 non-reference groups
ok(len(_eth["adjusted"]["groups"]) == 2, "the 3-group ethnicity lens models 2 non-reference groups")

# ---- EU Pay Transparency: 5% joint-assessment trigger, per category, mean + median ------------------
_eu = _gender["eu_pay_transparency"]
ok(_eu["threshold_pct"] == 5.0, "the EU joint-assessment threshold is 5%")
ok(_eu["category_dimension"] == "job_level", "categories of workers are operationalized as job level")
_assess = [c for c in _eu["categories"] if c.get("assessable")]
ok(all("mean_gap_pct" in c and "median_gap_pct" in c for c in _assess),
   "every assessable category reports BOTH the mean and median gap (Article 9)")
ok(all(c["mean_gap_pct"] >= 0 and c["median_gap_pct"] >= 0 for c in _assess),
   "the within-category gap is a magnitude (disadvantaged vs advantaged), never negative")
ok(all(c["exceeds_threshold"] == (c["mean_gap_pct"] >= 5.0) for c in _assess),
   "a category is flagged iff its MEAN gap is at least 5% (Article 10 >= trigger)")
_l7 = next(c for c in _eu["categories"] if c["category"] == "L7")
ok(_l7["exceeds_threshold"] is True and _l7["mean_gap_pct"] > 5.0, "L7 crosses the 5% mean trigger")
ok(_eu["joint_assessment_required"] is True and _eu["n_flagged"] >= 1,
   "a flagged category makes a joint pay assessment required")
ok(_l7["advantaged_group"] != _l7["disadvantaged_group"], "the flagged category names distinct advantaged/disadvantaged groups")

# ---- headline mirrors the detail -------------------------------------------------------------------
_h = r["headline"]
ok(_h["adjusted_gap_pct"] == _ab["adjusted_gap_pct"] and _h["unadjusted_median_gap_pct"] == _b["median_gap_pct"],
   "the headline numbers are exactly the primary-lens detail (no separate re-computation)")
ok(_h["eu_joint_assessment_required"] is True, "the headline surfaces the EU trigger")

# ---- determinism -----------------------------------------------------------------------------------
ok(PE.compute() == r, "compute() is deterministic (same committed data -> identical result)")

# ---- fail-closed data contract ---------------------------------------------------------------------
raises(lambda: PE.compute(Path(tempfile.mkdtemp())), "a missing workers.csv fails closed")
raises(lambda: PE.compute(_tmp_with(lambda h, rows: (h[:-1], rows))), "a short/renamed header fails closed")
raises(lambda: PE.compute(_tmp_with(lambda h, rows: (h, [_set(h, r, "base_salary", "oops") for r in rows]))),
       "a non-numeric base_salary is caught only if it reaches an in-scope row")  # see helper note below
raises(lambda: PE.compute(_tmp_with(lambda h, rows: (h, [_set(h, r, "hire_date", "2099-01-01") for r in rows]))),
       "a hire_date after the as-of date fails closed")
raises(lambda: PE.compute(_tmp_with(lambda h, rows: (h, [_set(h, r, "job_family", "") for r in rows]))),
       "a blank job_family on an in-scope employee fails closed (it is a regression control)")
raises(lambda: PE.compute(_tmp_with(lambda h, rows: (h, [_set(h, r, "location", "") for r in rows]))),
       "a blank location on an in-scope employee fails closed (it is a regression control)")

# a single-group population is degenerate-but-valid: it computes no gap rather than crashing
_one = PE.compute(_tmp_with(lambda h, rows: (h, [_set(h, r, "gender_group", "A") for r in rows if r])))
_g1 = next(d for d in _one["dimensions"] if d["key"] == "gender_group")
ok(len(_g1["unadjusted"]["groups"]) == 1 and _g1["adjusted"]["groups"] == [],
   "a single-group dimension yields no comparison groups (degenerate, not an error)")
ok(_g1["eu_pay_transparency"]["joint_assessment_required"] is False,
   "with one group present, no category is assessable and no assessment triggers")

print(f"OK — {passed} pay-equity checks passed "
      f"(n={r['population']['n_analyzed']}; gender raw median gap {_b['median_gap_pct']:.2f}% -> adjusted "
      f"{_ab['adjusted_gap_pct']:.2f}% (n.s.); EU flagged {_eu['n_flagged']} category, joint assessment "
      f"{'required' if _eu['joint_assessment_required'] else 'not required'}).")
