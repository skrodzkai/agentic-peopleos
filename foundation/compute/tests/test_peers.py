#!/usr/bin/env python3
"""Evals for the executive-comp peer-group screener.

These prove the screen is correct and defensible: every band check ties out to the math, the
peer set is exactly the companies that pass every active criterion, criteria can be turned on/off
honestly, the ordering is fully specified (peers-first, then revenue-closeness, ties by ticker),
and the loader fails closed when the universe is missing, has the wrong number of subjects, carries
a non-numeric or degenerate field, or is screened with no active criteria. The screener SCREENS and
RECOMMENDS — it never finalizes a group or sets pay.
Run: python foundation/compute/tests/test_peers.py
"""
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.compute.peers import (  # noqa: E402
    PeerUniverse, PeerDataError, DEFAULT_CRITERIA, _NUM,
)

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


u = PeerUniverse()

# ---- loader ----
ok(u.subject is not None and u.subject["is_subject"] == "yes", "loader finds the subject company")
ok(u.subject["ticker"] == "ACMQ" and u.subject["company_name"] == "Acme Corp", "subject is Acme (ticker ACMQ)")
ok(all(c["is_subject"] != "yes" for c in u.candidates), "candidates exclude the subject")
ok(len(u.candidates) == 220, "universe has 220 candidate companies")
ok(all(isinstance(c[k], int) for c in u.candidates for k in _NUM), "numeric fields are parsed to int")
ok(u.subject not in u.candidates, "subject is not also a candidate")

# ---- public-safety: no minted ticker collides with a well-known real ticker, all tickers unique ----
REAL = {"AAPL", "MSFT", "AMZN", "GOOG", "GOOGL", "META", "NVDA", "TSLA", "CRM", "ORCL", "LUMN",
        "GRAB", "DRIP", "MERC", "SPY", "QQQ", "VTI", "IBM", "INTC", "AMD"}
tickers = [c["ticker"] for c in u.candidates] + [u.subject["ticker"]]
ok(len(tickers) == len(set(tickers)), "every ticker in the universe is unique")
ok(not (set(tickers) & REAL), "no minted ticker collides with a well-known real ticker")

# ---- default screen: structure ----
r = u.screen()
for key in ("subject", "criteria", "active_criteria", "results", "peers", "n_peers", "n_candidates"):
    ok(key in r, f"screen result has '{key}'")
ok(r["criteria"] == DEFAULT_CRITERIA, "no-arg screen uses the shipped default criteria")
ok(r["n_candidates"] == len(u.candidates) == 220, "n_candidates counts the universe")
ok(len(r["results"]) == 220, "every candidate is evaluated (one result each)")
ok(set(r["active_criteria"]) == {"revenue", "market_cap", "gics"},
   "default HARD screen = revenue + market cap + sub-industry (headcount is a soft fit factor, not a gate)")
ok("employees" not in r["active_criteria"], "headcount is NOT a hard gate by default")

# ---- the core invariant: peers == companies that pass every active criterion ----
need = len(r["active_criteria"])
ok(all(p["pass_count"] == need and p["is_peer"] for p in r["peers"]),
   "every peer passes all active criteria")
ok(all(all(p["checks"].values()) for p in r["peers"]), "every peer's checks are all True")
ok(r["n_peers"] == len(r["peers"]) == sum(1 for x in r["results"] if x["is_peer"]),
   "n_peers counts exactly the is_peer results")
ok(all((not x["is_peer"]) for x in r["results"] if not all(x["checks"].values())),
   "any company failing a check is excluded under default (ALL) policy")

# ---- band math ties out for EVERY company (inclusive boundaries) — headcount is NOT gated ----
subj = r["subject"]
rlo, rhi = subj["revenue_usd"] * 0.5, subj["revenue_usd"] * 2.0
mlo, mhi = subj["market_cap_usd"] * 0.5, subj["market_cap_usd"] * 2.0
for x in r["results"]:
    c = x["company"]
    exp_rev = rlo <= c["revenue_usd"] <= rhi
    exp_mc = mlo <= c["market_cap_usd"] <= mhi
    exp_gics = c["gics_subindustry"] == subj["gics_subindustry"]
    ok(x["checks"]["revenue"] == exp_rev and x["checks"]["market_cap"] == exp_mc
       and x["checks"]["gics"] == exp_gics and "employees" not in x["checks"],
       f"hard-gate checks tie out for {c['ticker']} (no headcount gate)")
ok(all(p["company"]["gics_subindustry"] == subj["gics_subindustry"] for p in r["peers"]),
   "every default peer matches the subject's sub-industry")
# headcount is SOFT: a peer outside a 0.5-2.0x headcount band is STILL a full member (it only ranks)
off_hc = [p for p in r["peers"] if not (0.5 <= p["company"]["employees"] / subj["employees"] <= 2.0)]
ok(off_hc and all(p["is_peer"] for p in off_hc),
   "a peer outside a 0.5-2.0x headcount band is still a member (headcount ranks, it does not gate)")

# ---- peer count is a credible minority (methodology-tied, not a magic number) ----
# A tight screen funnels a broad universe down to a small minority: not 1-2, not half the universe.
appsw = [c for c in u.candidates if c["gics_subindustry"] == subj["gics_subindustry"]]
frac = r["n_peers"] / r["n_candidates"]
ok(0.03 <= frac <= 0.25, f"peer group is a credible minority of the universe ({r['n_peers']}/{r['n_candidates']})")
ok(2 < r["n_peers"] < len(appsw),
   "the size screen keeps a usable group but rejects some same-sub-industry candidates (it actually filters)")

# ---- ordering: peers-first; peers fit-ranked (size-closeness desc), ties broken by ticker ----
flags = [x["is_peer"] for x in r["results"]]
ok(flags == sorted(flags, reverse=True), "results are ordered peers-first")
fit_key = [(-p["fit"], p["company"]["ticker"]) for p in r["peers"]]
ok(fit_key == sorted(fit_key), "peers are fit-ranked (size-closeness desc), ties broken by ticker")

# ---- fit-rank: a size-closeness signal on [0,100] that ORDERS the group but never GATES membership ----
ok("fit_weights" in r and abs(sum(r["fit_weights"].values()) - 1.0) < 1e-9,
   "fit weights are disclosed and sum to 1.0")
ok(all(0.0 <= x["fit"] <= 100.0 for x in r["results"]), "every fit score is on [0,100] (clamped, never negative)")
ok(r["peers"][0]["fit"] == max(p["fit"] for p in r["peers"]), "the first peer is the highest-fit (best size match)")

# the separation that makes the hybrid defensible: membership is decided by the SCREEN, never by the fit
need_active = len(r["active_criteria"])
ok(all(x["is_peer"] == (x["pass_count"] >= need_active) for x in r["results"]),
   "membership is decided by the screen (pass_count), never by the fit score")
# a band-edge, LOW-fit in-band company is still a FULL member — fit orders, it does not gate
low = min(r["peers"], key=lambda p: p["fit"])
ok(low["is_peer"] and low["fit"] >= 0.0, "the lowest-fit in-band company is still a full member (fit never gates)")

# re-weighting the fit changes the ORDER but NEVER the membership set (the headline hybrid invariant)
import foundation.compute.peers as _P  # noqa: E402
_saved = dict(_P.FIT_WEIGHTS)
try:
    _P.FIT_WEIGHTS.clear()
    _P.FIT_WEIGHTS.update({"revenue_usd": 0.1, "market_cap_usd": 0.1, "employees": 0.8})
    r_alt = PeerUniverse().screen()
    ok({p["company"]["ticker"] for p in r_alt["peers"]} == {p["company"]["ticker"] for p in r["peers"]},
       "re-weighting fit leaves the membership SET unchanged (the screen, not the score, decides who is in)")
    ok([p["company"]["ticker"] for p in r_alt["peers"]] != [p["company"]["ticker"] for p in r["peers"]]
       or r["n_peers"] <= 1, "re-weighting actually reorders the group (the rank really is weight-driven)")
finally:
    _P.FIT_WEIGHTS.clear()
    _P.FIT_WEIGHTS.update(_saved)

# golden: pin the shipped fit-ranked order so a silent weight/score/tie-break change fails loudly
GOLDEN_TOP = [("CREL", 89.1), ("HARA", 85.7), ("KESS", 84.9), ("NIMS", 81.0),
              ("SLAP", 78.6), ("CINS", 78.1), ("CINF", 76.0), ("TIDA", 74.4)]
ok([(p["company"]["ticker"], p["fit"]) for p in r["peers"][:8]] == GOLDEN_TOP,
   "the shipped fit-ranked order matches the golden (a weight/score/tie-break change is now a deliberate update)")

# a company identical in size to the subject scores 100; a band edge scores 0; the math is exact
from foundation.compute.peers import _fit as _fitfn, _closeness  # noqa: E402
ok(_fitfn(subj, subj) == 100.0, "a company identical in size to the subject scores a perfect 100 fit")
ok(_closeness(subj["revenue_usd"] * 2.0, subj["revenue_usd"]) == 0.0, "the 2.0x band edge scores 0 closeness")
ok(_closeness(subj["revenue_usd"] * 0.5, subj["revenue_usd"]) == 0.0, "the 0.5x band edge scores 0 closeness (log-symmetric)")

# ---- bad criteria fail closed: malformed / inverted / non-numeric size bands ----
for bad in ({"revenue_mult": (0, 2)}, {"revenue_mult": (2.0, 0.5)}, {"market_cap_mult": 5},
            {"employees_mult": (1,)}, {"revenue_mult": ("a", "b")}, {"market_cap_mult": (True, False)}):
    try:
        u.screen(bad)
        ok(False, f"bad criteria {bad} should be rejected")
    except PeerDataError:
        ok(True, f"bad criteria {bad} fails closed")

# ---- criteria can be turned off honestly ----
r_no_gics = u.screen({"gics": None})
ok("gics" not in r_no_gics["active_criteria"], "disabling gics removes it from active criteria")
ok(all("gics" not in x["checks"] for x in r_no_gics["results"]), "no gics check is recorded when disabled")
ok(r_no_gics["n_peers"] >= r["n_peers"], "dropping the industry screen never shrinks the peer set")
# turning EVERY criterion off must fail closed, not silently return all 220 as "peers"
try:
    u.screen({"revenue_mult": None, "market_cap_mult": None, "employees_mult": None, "gics": None})
    ok(False, "an all-criteria-off screen should fail closed")
except PeerDataError:
    ok(True, "all-criteria-off screen fails closed (no meaningless 'everyone is a peer')")

# ---- sector vs sub-industry tightness ----
r_sector = u.screen({"gics": "sector"})
ok(r_sector["n_peers"] >= r["n_peers"], "broader sector match yields at least as many peers as sub-industry")
ok(all(p["company"]["gics_sector"] == subj["gics_sector"] for p in r_sector["peers"]),
   "sector screen keeps same-sector companies only")

# ---- min_criteria: pass >= N instead of ALL (default active = revenue, market_cap, gics = 3) ----
n_active = len(r["active_criteria"])
ok(n_active == 3, "default screen has exactly 3 hard criteria (headcount is soft)")
r_any2 = u.screen({"min_criteria": 2})   # 2-of-3 genuinely relaxes the gate (3-of-3 would be a no-op)
ok(r_any2["n_peers"] >= r["n_peers"], "relaxing to 2-of-3 never shrinks the peer set")
ok(all(p["pass_count"] >= 2 for p in r_any2["peers"]), "every 2-of-3 peer passes at least 2 criteria")
strict_tk = {p["company"]["ticker"] for p in r["peers"]}
relaxed_tk = {p["company"]["ticker"] for p in r_any2["peers"]}
ok(strict_tk <= relaxed_tk, "every strict (3-of-3) peer is still a peer under 2-of-3")
ok(r_any2["n_peers"] > r["n_peers"], "2-of-3 actually admits new near-misses (the relaxation is real)")
ok(all(p["pass_count"] == 2 for p in r_any2["peers"] if p["company"]["ticker"] not in strict_tk),
   "every company admitted only by the 2-of-3 relaxation is a genuine 2-of-3 near-miss")
# min_criteria must be a sane PLAIN int — 0/n+1 out of range, and bool/float/str must NOT slip through
# (bool subclasses int so True==1; a float/str would compare and silently blow membership wide open)
for bad in (0, 4, -1, True, False, 1.5, 2.0, "2", "3", [1]):
    try:
        u.screen({"min_criteria": bad})
        ok(False, f"min_criteria={bad!r} should be rejected")
    except PeerDataError:
        ok(True, f"min_criteria={bad!r} is rejected (must be a plain int in 1..n_active)")
# the valid path still works (a real int passes)
ok(u.screen({"min_criteria": 3})["n_peers"] == r["n_peers"], "min_criteria=3 (== all active) matches the default")

# ---- determinism: the full ordered peer list + fit scores, not just the count ----
peers_a = [(p["company"]["ticker"], p["fit"]) for p in u.screen()["peers"]]
peers_b = [(p["company"]["ticker"], p["fit"]) for p in PeerUniverse().screen()["peers"]]
ok(peers_a == peers_b and len(peers_a) == r["n_peers"],
   "screen is deterministic across instances (full ordered list + fit, not just the count)")

# ---- fail-closed loader: missing file, wrong subject count, bad/degenerate fields ----
FIELDS = ["ticker", "company_name", "gics_sector", "gics_subindustry",
          "revenue_usd", "market_cap_usd", "employees", "total_assets_usd", "is_subject"]


def _row(tk, subj, rev=50, mc=400, emp=150):
    return {"ticker": tk, "company_name": f"{tk} Inc", "gics_sector": "Information Technology",
            "gics_subindustry": "Application Software", "revenue_usd": rev, "market_cap_usd": mc,
            "employees": emp, "total_assets_usd": 80, "is_subject": subj}


def _write_universe(d, rows):
    with open(Path(d) / "peer_universe.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r_ in rows:
            w.writerow(r_)


try:
    PeerUniverse(data_dir="/nonexistent/path/xyz")
    ok(False, "missing universe should raise")
except PeerDataError:
    ok(True, "missing universe fails closed (PeerDataError)")

bad_cases = {
    "no subject": [_row("FOO", "no"), _row("BAR", "no")],
    "two subjects": [_row("FOO", "yes"), _row("BAR", "yes"), _row("BAZ", "no")],
    "non-numeric field": [dict(_row("SUBJ", "yes"), revenue_usd="N/A"), _row("BAR", "no")],
    "non-positive subject": [_row("SUBJ", "yes", rev=0), _row("BAR", "no")],
    "duplicate ticker": [_row("DUP", "yes"), _row("DUP", "no")],
    "real ticker minted": [_row("SUBJ", "yes"), _row("AAPL", "no")],
    "malformed ticker": [_row("SUBJ", "yes"), _row("b@d", "no")],
}
for label, rows in bad_cases.items():
    with tempfile.TemporaryDirectory() as d:
        _write_universe(d, rows)
        try:
            PeerUniverse(data_dir=d)
            ok(False, f"{label} should raise")
        except PeerDataError:
            ok(True, f"{label} fails closed (PeerDataError)")

# a universe with the WRONG schema (missing columns) must fail closed before any screen
with tempfile.TemporaryDirectory() as d:
    with open(Path(d) / "peer_universe.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "company_name", "revenue_usd"], lineterminator="\n")
        w.writeheader()
        w.writerow({"ticker": "FOO", "company_name": "Foo Inc", "revenue_usd": 1})
    try:
        PeerUniverse(data_dir=d)
        ok(False, "wrong-schema universe should raise")
    except PeerDataError:
        ok(True, "wrong-schema universe fails closed (PeerDataError)")

# a valid minimal universe still loads + screens to exactly the in-band peer
with tempfile.TemporaryDirectory() as d:
    _write_universe(d, [_row("SUBJ", "yes"), _row("PEER", "no", rev=60, mc=500, emp=170),
                        _row("BIG", "no", rev=5000, mc=40000, emp=9000)])
    mr = PeerUniverse(data_dir=d).screen()
    ok(mr["n_peers"] == 1 and mr["peers"][0]["company"]["ticker"] == "PEER",
       "minimal valid universe screens to the in-band peer only")

print(f"OK — {passed} peer-screener checks passed "
      f"(universe {r['n_candidates']}, {len(appsw)} same sub-industry, {r['n_peers']} peers selected).")
