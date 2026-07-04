#!/usr/bin/env python3
"""Evals for the exec-comp benchmarking engine (real proxy data; synthetic subject).
Run: python3 foundation/compute/tests/test_benchmarking.py
"""
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from foundation.compute import benchmarking as B  # noqa: E402
from foundation.compute.benchmarking import (  # noqa: E402
    BenchmarkError, benchmark, load_proxy_comp, percentile_rank, position, _incumbents, ROLES, MIN_PEER_N,
)

passed = 0


def ok(cond, msg):
    global passed
    assert cond, f"FAILED: {msg}"
    passed += 1


def raises(exc, fn, msg):
    global passed
    try:
        fn()
        assert False, f"FAILED (no raise): {msg}"
    except exc:
        passed += 1


# ---- percentile_rank: mid-rank convention (below + 0.5*equal)/n ----
d = sorted([1.0, 2.0, 3.0, 4.0])
ok(percentile_rank(d, 0.5) == 0.0, "a value below all peers is P0")
ok(percentile_rank(d, 5.0) == 100.0, "a value above all peers is P100")
ok(percentile_rank(d, 2.0) == 100.0 * (1 + 0.5) / 4, "mid-rank counts below + half the ties")
ok(percentile_rank([10.0, 10.0, 10.0], 10.0) == 50.0, "equal-to-all lands at the median (P50)")
raises(BenchmarkError, lambda: percentile_rank(d, True), "percentile_rank rejects a bool value")
raises(BenchmarkError, lambda: percentile_rank(d, float("nan")), "percentile_rank rejects NaN")

# ---- position: below / within / above the target band + the gap ----
peers = [1_000_000.0 * i for i in range(1, 21)]   # 1M..20M
p = position(3_000_000, peers, 50, 65)            # ~P12.5 -> below
ok(p["status"] == "below" and p["gap"] > 0, "a low subject value is below the target band with a positive gap")
p2 = position(11_000_000, peers, 50, 60)          # ~P52.5 -> within 50-60
ok(p2["status"] == "within" and p2["gap"] == 0.0, "a value inside the band is 'within' with zero gap")
p3 = position(19_000_000, peers, 45, 55)          # high -> above
ok(p3["status"] == "above" and p3["gap"] > 0, "a high value is above the band")
raises(BenchmarkError, lambda: position(1, peers, 60, 40), "an inverted target band is rejected")
raises(BenchmarkError, lambda: position(1, peers, 45, 105), "an out-of-range band is rejected")
raises(BenchmarkError, lambda: position(-5, peers, 50, 60), "a negative subject value is rejected")

# ---- quantiles: HAND-COMPUTED exact values (not the implementation as its own oracle) ----
# {1,2,3,4}M linearly interpolated: P25 pos=0.75 -> 1.75M ; P50 pos=1.5 -> 2.5M ; P75 pos=2.25 -> 3.25M
q = position(2_500_000, [1_000_000.0, 2_000_000.0, 3_000_000.0, 4_000_000.0], 50, 60)
ok(q["peer_p25"] == 1_750_000.0, "P25 of {1,2,3,4}M is exactly 1.75M (linear interpolation, hand-computed)")
ok(q["peer_median"] == 2_500_000.0, "median of {1,2,3,4}M is exactly 2.5M (hand-computed)")
ok(q["peer_p75"] == 3_250_000.0, "P75 of {1,2,3,4}M is exactly 3.25M (hand-computed)")

# ---- direct-call math is FAIL-CLOSED even when it bypasses the CSV loader (no fabricated percentile) ----
raises(BenchmarkError, lambda: percentile_rank([], 5.0), "percentile_rank of an empty distribution raises (not ZeroDivisionError)")
raises(BenchmarkError, lambda: position(5.0, [], 50, 60), "position rejects an empty peer distribution")
raises(BenchmarkError, lambda: position(5.0, [1.0, float("nan"), 3.0], 50, 60), "a NaN peer value fails closed (worst case: it would look like a valid percentile)")
ok(position(5.0, [1.0, "2", 3.0], 50, 60)["peer_n"] == 3, "a NUMERIC string amount is accepted (same coercion the CSV loader relies on)")
raises(BenchmarkError, lambda: position(5.0, [1.0, "abc", 3.0], 50, 60), "a NON-numeric string peer value fails closed")
raises(BenchmarkError, lambda: position(True, [1.0, 2.0, 3.0], 50, 60), "a bool subject value fails closed at the math layer")
raises(BenchmarkError, lambda: percentile_rank([1.0, True, 3.0], 2.0), "a bool distribution value fails closed")

# ---- _incumbents tie-break is deterministic regardless of CSV row order ----
tie = [
    {"ticker": "ZZZ", "role_bucket": "CEO", "title": "Chief Executive Officer B", "total": 5_000_000.0},
    {"ticker": "ZZZ", "role_bucket": "CEO", "title": "Chief Executive Officer A", "total": 5_000_000.0},
]
ok(_incumbents(tie)[0]["title"] == _incumbents(list(reversed(tie)))[0]["title"],
   "an exact-tie incumbent pick is identical regardless of row order (stable tertiary key)")

# ---- incumbents dedup: a 'Former' officer is dropped when an incumbent exists for the same (ticker, role) ----
rows = [
    {"ticker": "AAA", "role_bucket": "CEO", "title": "Former Chief Executive Officer", "total": 9_000_000.0},
    {"ticker": "AAA", "role_bucket": "CEO", "title": "Chief Executive Officer", "total": 5_000_000.0},
    {"ticker": "BBB", "role_bucket": "CEO", "title": "Chief Executive Officer", "total": 7_000_000.0},
]
inc = _incumbents(rows)
ok(len(inc) == 2, "dedup keeps one CEO per company")
aaa = next(r for r in inc if r["ticker"] == "AAA")
ok("former" not in aaa["title"].lower(), "the incumbent (non-'Former') CEO is kept over the outgoing one")

# ---- load + schema/reconciliation fail-closed on a crafted dataset ----
FIELDS = list(B.REQUIRED_COLS)


def _row(tk, rb, sal, stk, tot, subj="no", title="", nei=0.0, other=0.0, bonus=0.0, opt=0.0):
    return {"ticker": tk, "company_name": f"{tk} Inc", "role_bucket": rb, "title": title or rb,
            "salary": sal, "bonus": bonus, "stock_awards": stk, "option_awards": opt,
            "non_equity_incentive": nei, "other_comp": other, "total": tot,
            "fiscal_year": "FY2025", "disclosure": "def14a", "is_subject": subj}


def _write(d, rows, fields=FIELDS):
    p = Path(d) / "proxy_comp.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return p


with tempfile.TemporaryDirectory() as dd:
    # one subject + 6 peer CEOs whose components sum to Total
    good = [_row("ACMQ", "CEO", 500_000, 3_000_000, 3_500_000, subj="yes")] + \
           [_row(f"P{i}", "CEO", 400_000, 2_000_000 + i * 100_000, 2_400_000 + i * 100_000) for i in range(6)]
    peers_l, subj_l = load_proxy_comp(_write(dd, good))
    ok(len(peers_l) == 6 and len(subj_l) == 1, "loader splits peers vs subject and parses money")
    # reconciliation: components must sum to Total
    bad = list(good)
    bad[1] = _row("P0", "CEO", 400_000, 2_000_000, 9_999_999)   # Total nowhere near components
    raises(BenchmarkError, lambda: load_proxy_comp(_write(dd, bad)), "a component/Total mismatch fails closed")
    # schema drift
    raises(BenchmarkError, lambda: load_proxy_comp(_write(dd, good, FIELDS[:-1])), "a missing column fails closed")
    # a DUPLICATE header must fail closed (DictReader silently collapses dups + drops a column's data;
    # a plain set() compare would miss it, so the loader also checks the field COUNT)
    dup_path = Path(dd) / "dup.csv"
    dup_path.write_text(",".join(FIELDS) + ",salary\n", encoding="utf-8")   # header with salary twice
    raises(BenchmarkError, lambda: load_proxy_comp(dup_path), "a duplicate CSV header fails closed")
    # total=0 with POSITIVE components must fail reconciliation (a dropped/miskeyed Total can't slip through)
    zero_total = list(good)
    zero_total[1] = _row("P0", "CEO", 400_000, 2_000_000, 0)   # components > 0 but Total = 0
    raises(BenchmarkError, lambda: load_proxy_comp(_write(dd, zero_total)),
           "positive components with a zero SCT Total fail reconciliation (not skipped)")
    # a non-numeric salary in the CSV fails closed. NOTE: csv.DictWriter serializes Python True -> "True",
    # so this row exercises the non-numeric-STRING guard, not the bool-type guard (that is proven directly
    # below, so the isinstance(x, bool) check in _money is demonstrably load-bearing).
    boolrow = list(good)
    boolrow[0] = {**good[0], "salary": True}
    raises(BenchmarkError, lambda: load_proxy_comp(_write(dd, boolrow)), "a non-numeric salary string fails closed")
    raises(BenchmarkError, lambda: B._money(True, "salary"), "the _money bool guard is load-bearing (a real bool is rejected)")

    # ---- benchmark() end to end on the crafted set: CEO positioned, a thin role suppressed ----
    thin = good + [_row("ACMQ", "CHRO", 400_000, 1_000_000, 1_400_000, subj="yes"),
                   _row("P0", "CHRO", 300_000, 900_000, 1_200_000)]
    res = benchmark(_write(dd, thin))
    ok("CEO" in res["roles_benchmarked"], "CEO (n>=MIN_PEER_N) is benchmarked")
    ok(any(s["role"] == "CHRO" for s in res["roles_suppressed"]),
       "a role with fewer than MIN_PEER_N peers is SUPPRESSED, not given a spurious percentile")
    ok(len([p for p in res["positions"] if p["role"] == "CEO"]) == len(B.ELEMENTS),
       "every pay element is positioned for a benchmarked role")
    ok(res["n_below_target"] == sum(1 for p in res["positions"] if p["status"] == "below"),
       "n_below_target counts exactly the below-band positions")

# ---- the SHIPPED committed dataset benchmarks cleanly + tells a coherent story ----
r = benchmark()
ok(r["subject_company"] == "Acme Corp", "the shipped subject is Acme")
ok(r["n_peers_total"] == 14, "positions against the 14 US SCT peers (foreign issuers excluded from the distribution)")
# the two foreign private issuers are EXCLUDED from the SCT distribution + surfaced as a caveated reference
ok({f["ticker"] for f in r["foreign_excluded"]} == {"MNDY", "DSGX"},
   "foreign private issuers (monday.com, Descartes) are excluded from the SCT-comparable distribution")
ok(set(r["roles_benchmarked"]) == {"CEO", "CFO", "COO", "CLO"} and
   any(s["role"] == "CHRO" for s in r["roles_suppressed"]),
   "CEO/CFO/COO/CLO benchmarked; CHRO suppressed (thin peer disclosure)")
ok(all(0.0 <= p["percentile"] <= 100.0 for p in r["positions"]), "every percentile is in [0,100]")
ok(all(p["peer_p25"] <= p["peer_median"] <= p["peer_p75"] for p in r["positions"]),
   "peer quartiles are ordered P25<=median<=P75 for every position")
ok(all(p["peer_n"] >= MIN_PEER_N for p in r["positions"]), "every benchmarked position meets the min peer floor")
# the honest headline: Acme is below target on long-term equity (LTI/TDC) across roles
ltie_below = [p for p in r["positions"] if p["element"] in ("ltie", "tdc") and p["status"] == "below"]
ok(len(ltie_below) >= 4, "the LTI/TDC equity gap shows up (subject below target on long-term pay)")
ok(all(p["element"] in ("ltie", "tdc") for p in r["positions"] if p["status"] == "below"),
   "every below-target position is long-term equity (cash is at or above target)")
ok(r["disclosure_note"].startswith("peer figures are actual US SCT"),
   "the result is labelled US-SCT-actual (not target) + foreign-excluded + synthetic subject")

# ---- determinism ----
ok(benchmark()["positions"] == benchmark()["positions"], "benchmark() is deterministic")

print(f"OK — {passed} exec-comp benchmarking checks passed "
      f"({r['n_positions']} positions across {len(r['roles_benchmarked'])} roles, "
      f"{r['n_below_target']} below target; CHRO suppressed).")
