#!/usr/bin/env python3
"""Evals for the Executive Compensation Benchmarking agent.
Run: python3 evals/test_benchmarking_agent.py

Proves the agent is presentation-only over the shared benchmarking engine (it recomputes no percentile,
quantile or status), that the honest story is faithfully rendered (cash competitive, equity below
target), that a thin role is SUPPRESSED (never given a spurious percentile), that the dashboard renders
deterministically and injection-safe with unique SVG ids, that peer figures are labelled ACTUAL (not
target), that the publish gate refuses an invalid approver, and that it fails closed.
"""
import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run  # noqa: E402
from foundation.compute.benchmarking import benchmark, MIN_PEER_N  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


result = benchmark()
report = run.build_report(result)
page = run.render_html(report)
digest = run.render_digest(report)

# ---- presentation-only: every position/percentile/status comes STRAIGHT from the engine ----
ok(report["positions"] is result["positions"], "the dashboard positions ARE the engine's positions (no agent copy/recompute)")
ok(report["n_below"] == result["n_below_target"] == sum(1 for p in report["positions"] if p["status"] == "below"),
   "the below-target count is the engine's (agent recomputes nothing)")
ok({p["role"] for p in report["positions"]} == set(result["roles_benchmarked"]),
   "only engine-benchmarked roles are positioned")
ok(report["n_peers"] == result["n_peers_total"], "peer count comes from the engine")

# ---- the honest story is faithful: below-target is concentrated in EQUITY, cash is competitive ----
equity_below = [p for p in report["positions"] if p["element"] in ("ltie", "tdc") and p["status"] == "below"]
cash = [p for p in report["positions"] if p["element"] in ("base", "sti", "total_cash")]
ok(len(equity_below) >= 4, "the equity shortfall is real (LTI/TDC below target across roles)")
ok(all(p["status"] in ("within", "above") for p in cash), "annual cash is at or above the target band (the honest 'cash competitive' claim)")
ok(all(p in report["positions"] for p in report["below_sorted"]) and
   [p["gap"] for p in report["below_sorted"]] == sorted((p["gap"] for p in report["below_sorted"]), reverse=True),
   "the gap list is the below-target positions, widest shortfall first")

# ---- a thin role is SUPPRESSED and NEVER given a percentile ----
ok(any(s["role"] == "CHRO" for s in report["suppressed"]), "CHRO is suppressed (thin peer disclosure)")
supp_roles = {s["role"] for s in report["suppressed"]}
ok(not (supp_roles & {p["role"] for p in report["positions"]}),
   "a suppressed role appears in NO position (no spurious percentile for a thin role)")
ok(all(s["peer_n"] < MIN_PEER_N for s in report["suppressed"]), "every suppressed role is below the peer-n floor")

# ---- every benchmarked position meets the peer floor + has ordered quartiles + an in-range percentile ----
ok(all(p["peer_n"] >= MIN_PEER_N for p in report["positions"]), "every rendered position clears the min-peer floor")
ok(all(p["peer_p25"] <= p["peer_median"] <= p["peer_p75"] for p in report["positions"]), "peer quartiles are ordered")
ok(all(0.0 <= p["percentile"] <= 100.0 for p in report["positions"]), "every percentile is in [0,100]")

# ---- the page carries the signature, every section, the logo, the matrix, the per-role tables ----
for needle in ["Pay Positioning vs Peers", "logomark", "Headline", "Positioning at a glance",
               "Gap to target", "pay positioning", "Position vs peers", "Suppressed roles",
               "Total direct comp", "actual SCT"]:
    ok(needle in page, f"dashboard renders '{needle}'")
# every benchmarked role has its own positioning section
for role in report["roles"]:
    ok(f"{role} — pay positioning" in page, f"{role} has a positioning table")
# the matrix shows a percentile for each rendered position (role x element)
ok(page.count("class='mcell") >= report["n_positions"], "the status matrix has a cell per position")

# ---- the target band + percentile framing is on the page, and it's honest about ACTUAL vs TARGET ----
ok("target P" in page and "committee target" in page.lower(), "the committee target band is shown")
ok("actual sct" in page.lower() and "not target opportunity" in page.lower(),
   "the page is explicit: peer pay is ACTUAL SCT-disclosed, not target opportunity")
ok("real public peers" in page.lower() and "illustrative" in page.lower(), "real-peer illustrative-snapshot disclaimer present")

# ---- digest: honest headline + suppression + actual-not-target + synthetic subject ----
ok("below" in digest.lower() and "long-term equity" in digest.lower(), "digest states the equity-below-target headline")
ok("actual sct-disclosed" in digest.lower(), "digest labels peer pay actual SCT-disclosed (not target)")
ok("acme) is synthetic" in digest.lower(), "digest discloses the subject is synthetic")
ok("chro" in digest.lower() and "suppress" in digest.lower(), "digest names the suppressed role")
ok("Compensation Committee" in page and "Compensation Committee" in digest, "the human approver is named")

# ---- determinism: same engine result -> identical bytes ----
ok(run.render_html(run.build_report(benchmark())) == page, "the dashboard renders deterministically")
ok(run.render_digest(run.build_report(benchmark())) == digest, "the digest renders deterministically")

# ---- SVG <defs> ids unique across the document (the hero strip namespaces its own ids) ----
ids = re.findall(r"id='([^']+)'", page)
ok(len(ids) == len(set(ids)), f"no duplicate SVG ids ({len(ids)} ids, all unique)")

# ---- injection / public-safety: no script, no per-person ids, no ticker leakage (this view shows none) ----
ok("<script" not in page, "the dashboard contains no <script>")
ok(not re.search(r"\bE-\d{4}\b", page) and not re.search(r"\bC-\d{4}\b", page),
   "no per-person employee/contractor ids appear (aggregate/role-level only)")
ok(not ({"AAPL", "MSFT", "AMZN", "GOOGL", "NVDA", "META"} & set(re.findall(r"\b[A-Z]{2,5}\b", page))),
   "no out-of-universe mega-cap ticker leaks (this view positions roles, not named peers)")

# a hostile element LABEL / role must be escaped if it ever reaches the render (defense in depth: the
# engine's vocab is fixed, but the agent must not trust it blindly) — inject into a copy and re-render.
_rep = run.build_report(benchmark())
_rep["el_label"] = dict(_rep["el_label"]); _rep["el_label"][_rep["positions"][0]["element"]] = "<script>x</script>"
_pi = run.render_html(_rep)
ok("<script>x</script>" not in _pi and "&lt;script&gt;x" in _pi, "an injected element label is escaped and still rendered")

# a hostile SUPPRESSED-ROLE name must be escaped in BOTH the narrative ribbon and the suppressed section
# (this path was previously inserted raw into HTML)
_rs = run.build_report(benchmark())
_rs["suppressed"] = [{"role": "CHRO<script>alert(1)</script>&x", "peer_n": 2, "reason": "thin disclosure"}]
_rs["narrative"] = run._narrative(_rs)
_ps = run.render_html(_rs)
ok("<script>alert(1)</script>" not in _ps and "&lt;script&gt;alert(1)" in _ps,
   "a hostile suppressed-role name is escaped (narrative + suppressed section), never inserted as raw HTML")

# the below-target CONCENTRATION phrase is DATA-DRIVEN, never hardcoded: an all-cash below-set must not be
# described as 'equity', and the digest must not carry a hardcoded 'concentrated in long-term equity'
import copy as _copy  # noqa: E402
_cash = _copy.deepcopy(benchmark())
for _p in _cash["positions"]:
    _p["status"] = "below" if _p["element"] == "base" else "within"
    _p["gap"] = 5.0 if _p["element"] == "base" else 0.0
_cash["n_below_target"] = sum(1 for _p in _cash["positions"] if _p["status"] == "below")
_cr = run.build_report(_cash)
ok("equity" not in _cr["concentration"][0], "an all-cash below-set concentration is not described as 'equity'")
ok("concentrated in **long-term equity" not in run.render_digest(_cr),
   "the digest concentration claim is derived from the data, not a hardcoded 'long-term equity'")

# the per-role pay tables read 'Positioning' (NOT 'Recommend-only'), and peer_n is surfaced with a thin caveat
ok("t-scope'>Positioning<" in page and "Recommend-only" not in page,
   "per-role pay tables read 'Positioning' — the agent positions pay, it never recommends a level")
ok("16 peers</b> disclose this role" in page, "peer_n is surfaced on a benchmarked role (transparency)")
ok("thin peer set" in page, "a thin non-suppressed role (COO n=7 / CLO n=8, < 10) carries a read-with-care caveat")

# ---- governance: the engine the agent reads exposes no pay-setting / decisional mutator ----
import foundation.compute.benchmarking as B  # noqa: E402
for danger in ("set_pay", "recommend_pay", "approve", "finalize", "adjust"):
    ok(not hasattr(B, danger), f"the benchmarking engine has no '{danger}' (read-only, position-only)")

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

    # fail closed when the engine is unavailable
    _set_out()
    _real = run._load_benchmark
    run._load_benchmark = lambda: (_ for _ in ()).throw(FileNotFoundError("proxy data missing"))
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main([])
        ok(rc == 1 and not run.REPORT.exists(), "engine-unavailable fails closed (exit 1, no report)")
        ok(err.getvalue().strip().startswith("FAIL CLOSED:"), "one clean fail-closed line")
    finally:
        run._load_benchmark = _real

    # a FAILURE AFTER A SUCCESS quarantines the now-invalid prior report (rename to .stale)
    _set_out()
    ok(run.main([]) == 0 and run.REPORT.exists(), "seed a good report before forcing a failure")
    _stale = run.REPORT.with_name(run.REPORT.name + ".stale")
    _real2 = run._load_benchmark
    run._load_benchmark = lambda: (_ for _ in ()).throw(FileNotFoundError("gone"))
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            rc = run.main([])
        ok(rc == 1 and not run.REPORT.exists() and _stale.exists(),
           "a failure after a success quarantines the prior report to .stale")
    finally:
        run._load_benchmark = _real2
    ok(run.main([]) == 0 and run.REPORT.exists() and not _stale.exists(), "a later good run clears the .stale quarantine")

    # the 'no positions' branch is a DISTINCT fail-closed path (ReportError -> exit 1)
    _set_out()
    _real3 = run._load_benchmark
    run._load_benchmark = lambda: {"positions": [], "roles_benchmarked": [], "elements": [],
                                   "n_peers_total": 0, "n_positions": 0, "n_below_target": 0,
                                   "roles_suppressed": []}
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main([])
        ok(rc == 1 and not run.REPORT.exists(), "an empty positioning fails closed (exit 1, no report)")
        ok("no positions" in err.getvalue().lower(), "the empty fail-closed names the empty-positioning reason")
    finally:
        run._load_benchmark = _real3
finally:
    run.OUT, run.REPORT, run.DIGEST = _orig

print(f"OK — {passed} executive-comp benchmarking-agent checks passed "
      f"({report['n_positions']} positions across {len(report['roles'])} roles, {report['n_below']} below target, "
      f"{len(report['suppressed'])} suppressed, {len(ids)} unique SVG ids).")
