#!/usr/bin/env python3
"""Evals for the Pay-versus-Performance (Item 402(v)) example.
Run: python3 evals/test_pvp_agent.py
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
table = report["table"]

# ---- source discipline: synthetic public-safe subject, no real ticker ----
ok(inputs["awards"]["subject"]["ticker"] == "ACMQ" and report["company_name"] == "Acme Corp",
   "Acme is the unified executive-comp issuer")
tickers = {str(inputs["awards"]["subject"]["ticker"]).upper()}
tickers |= {str(t).upper() for t in (inputs["awards"]["market"]["rtsr"]["peer_tickers"])}
collisions = sorted(tickers & REAL_TICKERS)
ok(not collisions, f"PvP sample uses no real or recognizable tickers: {collisions}")
ok(all("Q" in t for t in tickers), "PvP sample tickers carry an obvious synthetic marker")

# ---- 402(v) structure comes from the shared engine ----
ok(len(table["rows"]) == 5, "five covered fiscal years")
ok([r["fy"] for r in table["rows"]] == [2022, 2023, 2024, 2025, 2026], "covered years are FY2022-FY2026")
ok(all("peo_cap" in r and "avg_nonpeo_cap" in r for r in table["rows"]), "table carries PEO and average non-PEO CAP")
ok(all("company_tsr_value" in r and "peer_tsr_value" in r for r in table["rows"]), "table carries company and peer TSR")
ok(report["alignment"]["aligned"] and report["alignment"]["cap_direction"] == "up",
   "shipped case is pay-for-performance aligned (CAP rises with TSR)")
# the reconciliation bridge must tie to CAP (build_report self-checks this and would raise otherwise)
ok(report["peo_bridge"]["bridge"][-1][0] == "CAP", "the reconciliation bridge ends on CAP")
ok(abs((report["peo_bridge"]["bridge"][0][1]
        + sum(v for _l, v, k in report["peo_bridge"]["bridge"] if k != "total"))
       - report["peo_bridge"]["cap"]) < 0.02, "itemized bridge ties to reported CAP")
ok(report["table"] == run.build_report(run.load_inputs())["table"], "engine output is deterministic")

# ---- report content: committee-useful 402(v), no overclaim ----
for needle in [
    "Pay versus Performance",
    "Compensation Actually Paid",
    "CAP reconciliation",
    "Pay-versus-Performance table",
    "CAP vs company TSR",
    "CAP vs net income",
    "Company-selected measure",
    "Black-Scholes",
    "Monte Carlo",
    "402(v)",
    "Summary Compensation Table",
    "not accounting",
    "Demo review required",
]:
    ok(needle in page or needle in digest, f"report carries '{needle}'")
for marker in ("data-chart='cap-bridge'", "data-chart='cap-vs-tsr'",
               "data-chart='cap-vs-ni'", "data-chart='cap-vs-csm'"):
    ok(marker in page, f"report has stable chart marker {marker}")
for overclaim in ("auditor-approved valuation", "official 402", "the company's filed 402(v)"):
    # the methodology explicitly DISCLAIMS being the filed/approved disclosure; it must never assert it IS one
    ok(f"is {overclaim}" not in page.lower() and f"this {overclaim}" not in page.lower(),
       f"no '{overclaim}' overclaim")
ok("auditor-approved" in (page + digest).lower(), "report states it is NOT an auditor-approved valuation")
ok("illustrative reconstruction" in page.lower(), "report labels itself an illustrative reconstruction")

# ---- safety and determinism ----
ok("<script" not in page.lower(), "report contains no script tags")
ok(page == run.render_html(run.build_report(run.load_inputs())), "HTML renders deterministically")
ok(digest == run.render_digest(run.build_report(run.load_inputs())), "digest renders deterministically")

hostile = run.load_inputs()
hostile["awards"]["subject"]["name"] = "</p><script>alert(1)</script><p>"
hostile_page = run.render_html(run.build_report(hostile))
ok("<script" not in hostile_page.lower() and "&lt;script&gt;" in hostile_page,
   "hostile subject text is escaped in the narrative and header")

source_text = "\n".join((Path(run.__file__).read_text(encoding="utf-8"),
                         (Path(run.REPO) / "foundation/compute/pvp.py").read_text(encoding="utf-8")))
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

    run.load_inputs = lambda: (_ for _ in ()).throw(FileNotFoundError("missing awards"))
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

print(f"OK — {passed} Pay-versus-Performance example checks passed "
      f"(PEO CAP FY{table['rows'][-1]['fy']} = ${table['rows'][-1]['peo_cap']:,.0f}, "
      f"{'aligned' if report['alignment']['aligned'] else 'divergent'}).")
