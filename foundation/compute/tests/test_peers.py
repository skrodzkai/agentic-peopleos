#!/usr/bin/env python3
"""Evals for the executive-comp peer-group screener.

These prove the screen is correct and defensible: every band check ties out to the math, the
peer set is exactly the companies that pass every active criterion, criteria can be turned on/off
honestly, the ordering is fully specified (peers-first, then revenue-closeness, ties by ticker),
and the loader fails closed when the universe is missing, has the wrong number of subjects, carries
a non-numeric or degenerate field, or is screened with no active criteria. The screener SCREENS and
RECOMMENDS — it never finalizes a group or sets pay.
Run: python3 foundation/compute/tests/test_peers.py
"""
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.compute.peers import (  # noqa: E402
    PeerUniverse, PeerDataError, DEFAULT_CRITERIA, SOFTWARE_PEER_GROUP,
    real_peer_identifiers, name_matches_real, _canon_name, _NUM,
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
ok(len(u.candidates) == 50, "universe has 50 real candidate companies")
ok(all(isinstance(c[k], int) for c in u.candidates for k in _NUM), "numeric fields are parsed to int")
ok(u.subject not in u.candidates, "subject is not also a candidate")

# ---- the PEERS are real public companies (a peer screen benchmarks against real comps); the SUBJECT is the
#      only synthetic issuer. Tickers unique; ACMQ = synthetic Acme; provenance in governance/real-peer-data.md.
tickers = [c["ticker"] for c in u.candidates] + [u.subject["ticker"]]
ok(len(tickers) == len(set(tickers)), "every ticker in the universe is unique")
ok(u.subject["ticker"] == "ACMQ", "the subject issuer is the synthetic Acme (ACMQ)")
_expect_real = {"GTLB", "KVYO", "MNDY", "BILL", "PCTY", "BSY"}
ok(_expect_real <= {c["ticker"] for c in u.candidates},
   "the peer universe is drawn from real public companies (e.g. GTLB/KVYO/MNDY/BILL/PCTY/BSY)")

# ---- default screen: structure ----
r = u.screen()
for key in ("subject", "criteria", "active_criteria", "results", "peers", "n_peers", "n_candidates"):
    ok(key in r, f"screen result has '{key}'")
ok(r["criteria"] == DEFAULT_CRITERIA, "no-arg screen uses the shipped default criteria")
ok(r["n_candidates"] == len(u.candidates) == 50, "n_candidates counts the universe")
ok(len(r["results"]) == 50, "every candidate is evaluated (one result each)")
ok(set(r["active_criteria"]) == {"revenue", "market_cap", "gics"},
   "default HARD screen = revenue + market cap + software/SaaS group (headcount is a soft fit factor, not a gate)")
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
    exp_gics = c["gics_subindustry"] in SOFTWARE_PEER_GROUP     # GROUP membership, not exact sub-industry
    ok(x["checks"]["revenue"] == exp_rev and x["checks"]["market_cap"] == exp_mc
       and x["checks"]["gics"] == exp_gics and "employees" not in x["checks"],
       f"hard-gate checks tie out for {c['ticker']} (no headcount gate)")
ok(all(p["company"]["gics_subindustry"] in SOFTWARE_PEER_GROUP for p in r["peers"]),
   "every default peer is in the documented software/SaaS peer group")
# the group SPANS GICS sectors — a correctly-labeled peer (Paylocity = Industrials / HR & Employment
# Services) qualifies without sharing the subject's exact 'Application Software' sub-industry
ok(any(p["company"]["gics_subindustry"] != subj["gics_subindustry"] for p in r["peers"]),
   "the software/SaaS group admits a peer outside the subject's exact GICS sub-industry (multi-sector by design)")
# headcount is SOFT: a peer outside a 0.5-2.0x headcount band is STILL a full member (it only ranks)
off_hc = [p for p in r["peers"] if not (0.5 <= p["company"]["employees"] / subj["employees"] <= 2.0)]
ok(off_hc and all(p["is_peer"] for p in off_hc),
   "a peer outside a 0.5-2.0x headcount band is still a member (headcount ranks, it does not gate)")

# ---- peer count is a credible minority (methodology-tied, not a magic number) ----
# A tight screen funnels a broad universe down to a small minority: not 1-2, not half the universe.
in_group = [c for c in u.candidates if c["gics_subindustry"] in SOFTWARE_PEER_GROUP]
frac = r["n_peers"] / r["n_candidates"]
ok(0.05 <= frac <= 0.40, f"peer group is a credible minority of the universe ({r['n_peers']}/{r['n_candidates']})")
ok(2 < r["n_peers"] < len(in_group),
   "the size screen keeps a usable group but rejects some in-group candidates (it actually filters)")

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

# golden: pin the shipped fit-ranked order (REAL peers) so a silent weight/score/tie-break change fails loudly
GOLDEN_TOP = [("DSGX", 84.8), ("APPF", 81.3), ("GTLB", 77.6), ("QLYS", 67.4), ("CVLT", 61.0),
              ("QTWO", 59.5), ("KVYO", 58.6), ("MANH", 46.9), ("ZETA", 45.9), ("PCOR", 44.0),
              ("BILL", 37.2), ("MNDY", 34.5), ("PCTY", 33.2), ("PEGA", 21.2), ("GWRE", 20.8),
              ("BSY", 19.2)]
ok([(p["company"]["ticker"], p["fit"]) for p in r["peers"]] == GOLDEN_TOP,
   "the shipped fit-ranked REAL peer group matches the golden (a weight/score/tie-break change is a deliberate update)")

# a company identical in size to the subject scores 100; a band edge scores 0; the math is exact
from foundation.compute.peers import _fit as _fitfn, _closeness  # noqa: E402
ok(_fitfn(subj, subj) == 100.0, "a company identical in size to the subject scores a perfect 100 fit")
ok(_closeness(subj["revenue_usd"] * 2.0, subj["revenue_usd"]) == 0.0, "the 2.0x band edge scores 0 closeness")
ok(_closeness(subj["revenue_usd"] * 0.5, subj["revenue_usd"]) == 0.0, "the 0.5x band edge scores 0 closeness (log-symmetric)")

# ---- bad criteria fail closed: malformed / inverted / non-numeric size bands ----
for bad in ({"revenue_mult": (0, 2)}, {"revenue_mult": (2.0, 0.5)}, {"market_cap_mult": 5},
            {"employees_mult": (1,)}, {"revenue_mult": ("a", "b")}, {"market_cap_mult": (True, False)},
            {"revenue_mult": (0.5, float("inf"))}, {"revenue_mult": (float("nan"), 2.0)},
            {"market_cap_mult": (0.5, float("nan"))}):
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

# ---- group vs sector vs sub-industry tightness ----
# the software/SaaS GROUP spans several GICS sectors, so it is at least as permissive as a single-sector
# screen, which in turn is at least as permissive as the subject's exact sub-industry
r_sector = u.screen({"gics": "sector"})
r_sub = u.screen({"gics": "subindustry"})
ok(r["n_peers"] >= r_sector["n_peers"] >= r_sub["n_peers"],
   "group >= sector >= exact sub-industry in permissiveness (group is multi-sector by design)")
ok(all(p["company"]["gics_sector"] == subj["gics_sector"] for p in r_sector["peers"]),
   "sector screen keeps same-sector companies only")
ok(all(p["company"]["gics_subindustry"] == subj["gics_subindustry"] for p in r_sub["peers"]),
   "exact sub-industry screen keeps only the subject's own GICS sub-industry")
# the group gate is FUNCTIONAL, not a rubber stamp: a candidate outside the software/SaaS group is rejected
# even if it clears both size bands (proves the gate filters, though this curated universe is all-software)
_non_sw = PeerUniverse()
_target = next(cc for cc in _non_sw.candidates if cc["ticker"] == "GTLB")   # a size-passing peer
_target["gics_subindustry"], _target["gics_sector"] = "Biotechnology", "Health Care"
_rg = _non_sw.screen()
ok(not any(p["company"]["ticker"] == "GTLB" for p in _rg["peers"]),
   "a non-software company (outside SOFTWARE_PEER_GROUP) is excluded even when it clears both size bands")
# round-6: fail-closed on a SUBJECT outside the software/SaaS group — gating peers on a group the subject
# isn't in is incoherent, so it must raise rather than return a nonsense group
_bad_subj = PeerUniverse()
_bad_subj.subject["gics_subindustry"], _bad_subj.subject["gics_sector"] = "Biotechnology", "Health Care"
try:
    _bad_subj.screen()
    ok(False, "a subject outside the software/SaaS group should fail closed")
except PeerDataError:
    ok(True, "a subject outside the software/SaaS group fails closed (group gate is incoherent otherwise)")
# round-6: an unknown gics mode must raise, not silently disable the industry gate
try:
    u.screen({"gics": "bogus"})
    ok(False, "an unknown gics mode should be rejected")
except PeerDataError:
    ok(True, "an unknown gics mode fails closed (not a silent no-op gate)")
# round-7 (verify LOW): in GROUP mode, software/SaaS membership is a HARD gate even under a relaxed
# min_criteria — a non-software company that clears size bands must NEVER be admitted as a "software peer"
_relax = PeerUniverse()
_energy = next(cc for cc in _relax.candidates if cc["ticker"] == "GTLB")   # a size-passing in-group peer
_energy["gics_subindustry"], _energy["gics_sector"] = "Oil & Gas Equipment & Services", "Energy"
_rr = _relax.screen({"min_criteria": 2})
ok(not any(p["company"]["ticker"] == "GTLB" for p in _rr["peers"]),
   "an out-of-group company is excluded even under min_criteria=2 (group membership is a hard gate)")
# and the funnel invariant holds: no peer is out-of-group, so 'outside size' (n_same - n_peers) can't go negative
ok(all(p["checks"].get("gics") for p in _rr["peers"]),
   "every peer under a relaxed min_criteria is still in the software/SaaS group")

# ---- round-6: real_peer_identifiers fails CLOSED under require=True (a public-safety guard can never be
# silently disabled by an unloadable roster), and NAME matching is punctuation/suffix-insensitive ----
rt, rn = real_peer_identifiers()
ok(rt and rn, "the shipped roster loads a non-empty ticker + name set")
ok("GTLB" in rt and _canon_name("GitLab, Inc.") in rn, "roster carries real tickers + canonicalized names")
# every real ticker/name is a valid identifier (sanity), and the canon key is punctuation/suffix-free
ok(_canon_name("GitLab, Inc.") == _canon_name("GitLab Inc") == _canon_name("GITLAB  INC.") == "gitlab",
   "name canonicalization is punctuation/suffix-insensitive (variants can't evade the guard)")
ok(_canon_name("BigCommerce Holdings, Inc.") in rn,
   "a renamed company's FORMER name is still in the reject set (folded via _FORMER_REAL_NAMES)")
# round-7 (verify HIGH): matching is TOKEN-SUBSET, not exact-key — a recognizable SHORT FORM of a real peer
# is caught even though its canonical key differs from the stored full name
ok(name_matches_real("Descartes Systems Group", rn) and name_matches_real("Descartes Systems", rn)
   and name_matches_real("The Descartes Systems Group Inc.", rn),
   "a real short form ('Descartes Systems Group') matches the stored full name via token-subset")
ok(name_matches_real("ZoomInfo", rn), "a real short form ('ZoomInfo') matches 'ZoomInfo Technologies' via token-subset")
ok(name_matches_real("BigCommerce", rn), "a former short form ('BigCommerce') is matched (renamed-company safety)")
ok(not name_matches_real("Issuer 001", rn) and not name_matches_real("Synthetic Peer Co", rn),
   "a genuinely synthetic name does NOT false-match the real roster (subset match is not over-broad)")
with tempfile.TemporaryDirectory() as _d:
    try:
        real_peer_identifiers(_d, require=True)     # empty dir -> no peer_universe.csv
        ok(False, "require=True must fail closed when the roster file is absent")
    except PeerDataError:
        ok(True, "require=True fails closed when peer_universe.csv is absent")
    # a schema-drifted roster (wrong columns) also fails closed under require=True
    import csv as _csv
    with open(Path(_d) / "peer_universe.csv", "w", newline="", encoding="utf-8") as _fh:
        _w = _csv.writer(_fh); _w.writerow(["foo", "bar"]); _w.writerow(["1", "2"])
    try:
        real_peer_identifiers(_d, require=True)
        ok(False, "require=True must fail closed on a schema-drifted roster")
    except PeerDataError:
        ok(True, "require=True fails closed on a schema-drifted roster")
    # require=False stays best-effort (returns former-names only, never crashes) for the scanner's use
    _t, _n = real_peer_identifiers(Path(_d) / "nope", require=False)
    ok(_t == set() and _canon_name("BigCommerce Holdings, Inc.") in _n,
       "require=False is best-effort (empty tickers, former-names retained) when the file is absent")

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
    "malformed ticker": [_row("SUBJ", "yes"), _row("b@d", "no")],
}
# NOTE: real tickers/names are no longer rejected — the peers are intentionally REAL public companies now
# (the subject Acme is the only synthetic issuer). The loader's job is schema/uniqueness/subject integrity.
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
      f"(universe {r['n_candidates']}, {len(in_group)} in software/SaaS group, {r['n_peers']} peers selected).")
