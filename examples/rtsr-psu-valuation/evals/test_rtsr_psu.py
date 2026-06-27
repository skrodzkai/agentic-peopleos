#!/usr/bin/env python3
"""Evals for the rTSR PSU valuation example.
Run: python evals/test_rtsr_psu.py
"""
import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.compute.peers import REAL_TICKERS  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


inputs = run.load_inputs()
report = run.build_report(inputs)
page = run.render_html(report)
digest = run.render_digest(report)

# ---- source discipline: synthetic public-safe plan terms, no real issuer sample ----
ok(report["plan"]["issuer"] == "ACMQ" and report["plan"]["company_name"] == "Acme Corp",
   "Acme is the unified executive-comp issuer")
ok(report["plan"]["averaging_days"] == 30, "plan uses a 30-day average convention")
ok(report["performance"]["included_peer_tickers"], "included peer list is present")
ok(report["performance"]["excluded_peer_tickers"], "excluded peer list is present")
sample_tickers = {str(c["ticker"]).upper() for c in inputs["companies"]}
sample_tickers |= {str(t).upper() for t in inputs["prices"]}
sample_tickers |= {str(t).upper() for t in inputs["assumptions"]["tickers"]}
sample_tickers.add(str(report["plan"]["issuer"]).upper())
collisions = sorted(sample_tickers & REAL_TICKERS)
ok(not collisions, f"rTSR sample uses no real or recognizable tickers: {collisions}")
ok(all("Q" in t for t in sample_tickers), "rTSR sample tickers carry an obvious synthetic marker")
ok("Northstar" not in page + digest, "rTSR sample uses the Acme executive-comp subject throughout")
for public_safe_term in ("guaranteed payout", "issuer-confirmed", "official valuation"):
    ok(public_safe_term not in (page + digest).lower(),
       f"sample output does not carry '{public_safe_term}' overclaim")

# ---- math comes from the shared engine and carries expected plan mechanics ----
ok(report["performance"]["issuer_percentile"] == 56.25, "issuer percentile is calculated and rounded to .01%")
ok(round(report["performance"]["payout_percent"], 2) == 106.25, "payout follows the 25/55/75 curve")
ok(report["valuation"]["paths"] == 5000, "Monte Carlo path count comes from assumptions")
ok(report["valuation"] == run.build_report(run.load_inputs())["valuation"], "Monte Carlo output is deterministic")
ok(report["valuation"]["fair_value_per_target_share"] > 0, "valuation returns a positive per-target-share value")
ok(report["valuation"]["fair_value_standard_error"] >= 0, "Monte Carlo standard error is reported")
ok(report["valuation"]["fair_value_ci95"]["low"] <= report["valuation"]["fair_value_per_target_share"]
   <= report["valuation"]["fair_value_ci95"]["high"], "Monte Carlo 95% confidence interval brackets the estimate")
ok(0 <= report["valuation"]["mean_payout_percent"] <= 200, "mean payout is inside the plan cap")
ok("payout_history" in inputs and len(inputs["payout_history"]) >= 8,
   "synthetic payout-history snapshots are loaded")

# ---- report content: committee useful, but not accounting/advice overclaim ----
for needle in [
    "Relative TSR PSU",
    "Performance Tracker",
    "Monte Carlo",
    "Payout history",
    "Target 100%",
    "Daily payout snapshots",
    "Peer TSR distribution",
    "Issuer highlighted",
    "25th",
    "55th",
    "75th",
    "Monte Carlo SE",
    "95% MC CI",
    "not accounting advice",
    "Demo review required",
    "S&P Software & Services-style",
]:
    ok(needle in page or needle in digest, f"report carries '{needle}'")
ok("data-chart='payout-history'" in page, "payout history chart has a stable chart marker")
ok("data-chart='peer-tsr-distribution'" in page, "peer TSR distribution chart has a stable chart marker")
ok("class='bar issuer'" in page, "peer TSR distribution highlights the issuer bar")
ok("data-target-line='100'" in page, "payout history includes the 100% target line")
ok("auditor-grade" not in (page + digest).lower(), "no auditor-grade overclaim")
ok("official valuation" not in (page + digest).lower(), "no official-valuation overclaim")

# ---- safety and determinism ----
ok("<script" not in page.lower(), "report contains no script tags")
ok(page == run.render_html(run.build_report(run.load_inputs())), "HTML renders deterministically")
ok(digest == run.render_digest(run.build_report(run.load_inputs())), "digest renders deterministically")

hostile = run.load_inputs()
hostile["plan"]["company_name"] = "</p><script>alert(1)</script><p>"
hostile_page = run.render_html(run.build_report(hostile))
ok("<script" not in hostile_page.lower() and "&lt;script&gt;" in hostile_page,
   "hostile plan text is escaped in the narrative and header")

source_text = "\n".join((Path(run.__file__).read_text(encoding="utf-8"),
                         (Path(run.REPO) / "foundation/compute/rtsr.py").read_text(encoding="utf-8")))
for forbidden in ("import urllib", "import requests", "import socket", "import http", "import subprocess"):
    ok(forbidden not in source_text, f"source contains no network/process import: {forbidden}")

# ---- publish gate + fail closed ----
orig = (run.OUT, run.REPORT, run.DIGEST)
real = run.load_inputs


def set_out():
    d = Path(tempfile.mkdtemp()) / "output"
    run.OUT, run.REPORT, run.DIGEST = d, d / "report.html", d / "digest.md"


def call_main(args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return run.main(args)


try:
    set_out()
    ok(call_main([]) == 0 and run.REPORT.exists() and run.DIGEST.exists(), "draft run writes report and digest")
    set_out()
    ok(call_main(["--publish"]) == 2 and not run.REPORT.exists(), "publish without approver exits 2")
    set_out()
    ok(call_main(["--publish", "--approved-by", "Compensation Committee Chair"]) == 0,
       "valid named approver publishes")
    ok((run.OUT / "PUBLISHED.json").exists(), "publish writes approval record")
    for bad in ("bad\nname", "bad\tname", "x" * 120):
        set_out()
        ok(call_main(["--publish", "--approved-by", bad]) == 2 and not run.REPORT.exists(),
           f"malformed approver {bad!r} is refused")

    run.load_inputs = lambda: (_ for _ in ()).throw(FileNotFoundError("missing assumptions"))
    set_out()
    run.REPORT.parent.mkdir(parents=True, exist_ok=True)
    run.REPORT.write_text("old report", encoding="utf-8")
    run.DIGEST.write_text("old digest", encoding="utf-8")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = run.main([])
    ok(rc == 1 and not run.REPORT.exists() and not run.DIGEST.exists(),
       "missing inputs fail closed and remove current-looking output")
    ok((run.REPORT.with_name(run.REPORT.name + ".stale")).exists(),
       "fail-closed marks prior report stale instead of leaving it current")
    ok(err.getvalue().startswith("FAIL CLOSED:"), "fail-closed path emits one clean error line")
finally:
    run.load_inputs = real
    run.OUT, run.REPORT, run.DIGEST = orig

ids = re.findall(r"id='([^']+)'", page)
ok(len(ids) == len(set(ids)), "SVG ids are unique")

print(f"OK — {passed} rTSR PSU example checks passed "
      f"({report['performance']['issuer_percentile']}th pctile, "
      f"{report['valuation']['fair_value_per_target_share']} FV/share).")
