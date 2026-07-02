#!/usr/bin/env python3
"""Evals for the Executive Compensation Peer Group Builder.
Run: python3 evals/test_peer_builder.py

Proves the agent is presentation-only over the shared screener, the hybrid is built correctly (a hard
screen for membership + a fit-rank that orders but never gates), the core/watchlist split is right,
the dashboard renders deterministically and injection-safe, the publish gate refuses an invalid
approver, and it fails closed when the universe is unavailable.
"""
import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run  # noqa: E402
from foundation.compute.peers import PeerUniverse  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


u = PeerUniverse()
screen = u.screen()
report = run.build_report(u)
page = run.render_html(report)
digest = run.render_digest(report)

# ---- presentation-only: the group + order + fit come STRAIGHT from the screener (no agent math) ----
ok([p["company"]["ticker"] for p in report["peers"]] == [p["company"]["ticker"] for p in screen["peers"]],
   "the peer group + order on the dashboard == the screener's fit-ranked peers")
ok(all(p["fit"] == s["fit"] for p, s in zip(report["peers"], screen["peers"])),
   "every fit score is the screener's value (the agent recomputes nothing)")
ok(report["subject"]["revenue_usd"] == screen["subject"]["revenue_usd"] == u.subject["revenue_usd"],
   "the subject is the shared-foundation Acme (one consistent company)")
ok(report["n_peers"] == screen["n_peers"] and report["n_universe"] == screen["n_candidates"],
   "peer + universe counts come from the screener")

# ---- the hybrid is built correctly: active hard screens gate, fit-rank orders ----
ok(all(all(p["checks"].values()) for p in report["peers"]),
   "every peer cleared every active hard-screen criterion (membership = the gate)")
fits = [p["fit"] for p in report["peers"]]
ok(fits == sorted(fits, reverse=True), "the group is ordered by fit (highest size-match first)")
ok(report["core"] == report["peers"][:run.CORE_N], "the core is the top-CORE_N fit-ranked peers")
ok(report["watchlist"] == report["peers"][run.CORE_N:], "the watchlist is the in-band remainder")
ok(len(report["core"]) + len(report["watchlist"]) == report["n_peers"], "core + watchlist == the whole group")
ok(all(all(w["checks"].values()) for w in report["watchlist"]),
   "watchlist alternates also clear every active screen criterion (a defensible bench, not a relaxation)")
ok("employees" not in report["peers"][0]["checks"], "headcount is a soft fit factor, not a hard gate")

# ---- the funnel reconciles (universe = same-industry + other-industry; same-industry = peers + size-outs) ----
ok(report["excl_industry"] + report["n_same"] == report["n_universe"], "industry funnel reconciles")
ok(report["n_peers"] + report["excl_size"] == report["n_same"], "size funnel reconciles within the industry")

# ---- defensible exclusions: same sub-industry, kept out on SIZE (the gate, not the score) ----
sub_ind = report["subject"]["gics_subindustry"]
for r in report["near_misses"]:
    c = r["company"]
    ok(c["gics_subindustry"] == sub_ind and not r["is_peer"] and r["checks"]["gics"],
       f"near-miss {c['ticker']} is same-industry, excluded, and failed a SIZE criterion")
    ok(not all((r["checks"]["revenue"], r["checks"]["market_cap"])),
       f"near-miss {c['ticker']} actually fails at least one active hard size band")

# ---- the page carries the signature, every section, the logo, the fit column, the percentile bridge ----
for needle in ["Peer Group Builder", "logomark", "Subject company", "Screen criteria",
               "Recommended core peer group", "Watchlist", "Defensible exclusions", "Size fit",
               "Target percentile policy", "Application Software", "the screen — not the score — decides"]:
    ok(needle in page, f"dashboard renders '{needle}'")
# target percentiles are shown as RANGES (disclosed practice is a band, not a single point)
ok("P45–55" in page and "P50–65" in page and "Total direct comp" in page,
   "the target-percentile bridge (ranges, the handoff to benchmarking) is on the dashboard")
ok("0.5–2.0×" in page, "the hard-screen bands are shown")
ok("headcount · soft" in page.lower(), "headcount is labeled as a soft rank factor")

# ---- honest about the REAL-peer universe (illustrative snapshot, synthetic subject) + the human gate ----
ok("real public peers" in page.lower() and "illustrative" in page.lower(), "real-peer disclaimer present in the page")
ok("real public-company peers" in digest.lower() and "illustrative snapshot" in digest.lower()
   and "acme) is synthetic" in digest.lower(), "digest discloses real peers + illustrative snapshot + synthetic subject")
ok("Compensation Committee" in page and "Compensation Committee" in digest, "the human approver is named")

# ---- determinism: same universe -> identical bytes ----
ok(run.render_html(run.build_report(PeerUniverse())) == page, "the dashboard renders deterministically")
ok(run.render_digest(run.build_report(PeerUniverse())) == digest, "the digest renders deterministically")

# ---- SVG <defs> ids unique across the document (no cross-tile gradient/filter collision) ----
ids = re.findall(r"id='([^']+)'", page)
ok(len(ids) == len(set(ids)), f"no duplicate SVG ids ({len(ids)} ids, all unique)")

# ---- injection / public-safety: no script, no per-person ids, no real-ticker / employer leakage ----
ok("<script" not in page, "the dashboard contains no <script>")
ok(not re.search(r"\bE-\d{4}\b", page) and not re.search(r"\bC-\d{4}\b", page),
   "no per-person employee/contractor ids appear (aggregate/company-level only)")
# the peer TICKERS are intentionally real (this arm benchmarks against real comps); but no OUT-OF-UNIVERSE
# mega-cap ticker should ever leak in — those aren't peers and their presence would signal a bug
ok(not ({"AAPL", "MSFT", "AMZN", "GOOGL", "NVDA", "META"} & set(re.findall(r"\b[A-Z]{2,5}\b", page))),
   "no out-of-universe mega-cap ticker is rendered (real peers are intentional; mega-caps are not peers)")
for term in ("Contoso", "Initech", "sk-"):
    ok(term not in page, f"no '{term}' leakage in the dashboard")

# a company name carrying HTML is ESCAPED — inject into a company that actually RENDERS (the top peer
# AND a near-miss), then assert the raw tag is absent AND the escaped form is present (so the test fails
# if a future change drops _e()/html.escape OR silently drops the row instead of rendering it).
_rep = run.build_report(PeerUniverse())
_rep["peers"][0]["company"]["company_name"] = "<script>x</script>"
_rep["near_misses"][0]["company"]["company_name"] = "<script>y</script>"
_pi = run.render_html(_rep)
ok("<script>x</script>" not in _pi and "&lt;script&gt;x" in _pi,
   "an injected peer-row name is escaped and still rendered (peer table)")
ok("<script>y</script>" not in _pi and "&lt;script&gt;y" in _pi,
   "an injected exclusion-row name is escaped and still rendered (exclusions table)")

# ---- governance: the screener the agent reads has no decisional / pay-setting mutator ----
for danger in ("recommend_pay", "set_pay", "approve", "finalize", "change_comp"):
    ok(not hasattr(u, danger), f"the screener has no '{danger}' method (read-only, recommend-only)")

# ---- publish gate + fail-closed via main(), writing to a throwaway output dir ----
_orig = (run.OUT, run.REPORT, run.DIGEST)


def _set_out():
    d = Path(tempfile.mkdtemp()) / "output"
    run.OUT, run.REPORT, run.DIGEST = d, d / "r.html", d / "d.md"


try:
    _set_out(); ok(run.main([]) == 0 and run.REPORT.exists(), "draft run exits 0 and writes the dashboard")
    _set_out(); ok(run.main(["--publish"]) == 2 and not run.REPORT.exists(), "publish without an approver exits 2")
    _set_out()
    ok(run.main(["--publish", "--approved-by", "Compensation Committee Chair"]) == 0, "valid approver publishes (exit 0)")
    ok((run.OUT / "PUBLISHED.json").exists(), "an approved publish writes PUBLISHED.json")
    ok(run.main([]) == 0 and not (run.OUT / "PUBLISHED.json").exists(),
       "a redrawn draft removes the prior PUBLISHED.json (no stale approval)")
    for bad in ("bad\nname", "bad\tname", "x" * 120):
        _set_out()
        ok(run.main(["--publish", "--approved-by", bad]) == 2 and not run.REPORT.exists(),
           f"a malformed approver {bad!r} is refused (exit 2, nothing written)")

    # fail closed when the universe is unavailable
    _set_out()
    _real = run._load_universe
    run._load_universe = lambda: (_ for _ in ()).throw(FileNotFoundError("peer universe missing"))
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main([])
        ok(rc == 1 and not run.REPORT.exists(), "universe-unavailable fails closed (exit 1, no report)")
        ok(err.getvalue().strip().startswith("FAIL CLOSED:"), "one clean fail-closed line")
    finally:
        run._load_universe = _real

    # a FAILURE AFTER A SUCCESS must quarantine the now-invalid prior report (rename to .stale), so a
    # stale-but-valid-looking governance report is never left live; a later good run clears the .stale.
    _set_out()
    ok(run.main([]) == 0 and run.REPORT.exists(), "seed a good report before forcing a failure")
    _stale = run.REPORT.with_name(run.REPORT.name + ".stale")
    _real2 = run._load_universe
    run._load_universe = lambda: (_ for _ in ()).throw(FileNotFoundError("gone"))
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            rc = run.main([])
        ok(rc == 1 and not run.REPORT.exists() and _stale.exists(),
           "a failure after a success quarantines the prior report to .stale (nothing valid-looking left live)")
    finally:
        run._load_universe = _real2
    ok(run.main([]) == 0 and run.REPORT.exists() and not _stale.exists(),
       "a later good run clears the .stale quarantine")

    # the 'no peers' branch is a DISTINCT fail-closed path (ReportError -> exit 1), not the generic one
    _set_out()

    class _EmptyUniverse:
        subject = {"ticker": "ACME", "gics_subindustry": "Application Software",
                   "revenue_usd": 1, "market_cap_usd": 1, "employees": 1}

        def screen(self):
            return {"subject": self.subject, "peers": [], "n_peers": 0}

    _real3 = run._load_universe
    run._load_universe = lambda: _EmptyUniverse()
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main([])
        ok(rc == 1 and not run.REPORT.exists(), "an empty peer group fails closed (exit 1, no report)")
        ok("no peers" in err.getvalue().lower(), "the no-peers fail-closed names the empty-group reason")
    finally:
        run._load_universe = _real3
finally:
    run.OUT, run.REPORT, run.DIGEST = _orig

print(f"OK — {passed} executive-comp peer-builder checks passed "
      f"({report['n_peers']} peers: core {len(report['core'])} + watchlist {len(report['watchlist'])}, "
      f"top fit {report['peers'][0]['fit']:.0f}, {len(ids)} unique SVG ids).")
