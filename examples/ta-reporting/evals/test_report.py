#!/usr/bin/env python3
"""Evals for the TA reporting agent. Run: python evals/test_report.py

Plain stdlib asserts so it runs anywhere with no test framework installed.
Covers the happy path, the fail-closed data contract, and the publish gate.
"""
import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run  # noqa: E402

AS_OF = run._date(run.DEFAULT_AS_OF)

HEADER = ("req_id,title,department,location,country,opened_date,stage,recruiter,"
          "hiring_manager,pipeline,last_update,priority,status")
GOOD = "REQ-1,Eng,Engineering,Remote,US,2025-12-01,Screen,Dana Lopez,Mgr,5,2026-01-10,P2,open"

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


def _csv(text: str) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "reqs.csv"
    tmp.write_text(text, encoding="utf-8")
    return tmp


def _raises(text_or_path, exc=run.DataContractError) -> bool:
    path = text_or_path if isinstance(text_or_path, Path) else _csv(text_or_path)
    try:
        run.load_requisitions(path)
        return False
    except exc:
        return True
    except Exception:
        return False


# ---------- happy path: report invariants ----------

reqs = run.load_requisitions()
report = run.build_report(reqs, AS_OF)
k = report["kpis"]

ok(sum(report["stage_mix"].values()) == k["total_open"], "stage mix reconciles to open")
ok(sum(report["age_bands"].values()) == k["total_open"], "age bands reconcile to open")
ok(sum(s["reqs"] for s in report["scorecard"].values()) == k["total_open"], "scorecard reconciles to open")
ok(k["at_risk"] == len(report["risk_flags"]), "at_risk == flagged open reqs")

enriched = run.enrich(run.load_requisitions(), AS_OF)
ok(all(not r["flags"] for r in enriched if r["status"] != "open"), "non-open reqs never flagged")
ok(all(w["days_since_update"] > run.STALE_DAYS for w in report["watchlist"]), "watchlist only stale reqs")
for r in enriched:
    if "AGING" in r["flags"]:
        ok(r["days_open"] > run.AGING_DAYS, f"{r['req_id']} AGING rule")
    if "THIN_PIPELINE" in r["flags"]:
        ok(r["priority"] == "P1" and r["pipeline"] < run.THIN_PIPELINE, f"{r['req_id']} THIN rule")

ok(bool(report["narrative"]) and any(ch.isdigit() for ch in report["narrative"]), "narrative is data-derived")
page = run.render_html(report)
ok(run.COMPANY in page and "Agentic People" in page, "html carries brand")
ok("What needs attention" in page and report["narrative"][:20] in page, "html carries narrative")
ok("Draft" in page or "DRAFT" in page, "html carries draft gate")
ok("Publish gate" in run.render_digest(report), "digest carries publish gate")

# ---------- fail-closed: data contract ----------

ok(_raises(Path("/no/such/file.csv"), FileNotFoundError), "missing file fails closed")
ok(_raises(""), "empty file fails closed")
ok(_raises(HEADER + "\n"), "header-only fails closed")
ok(_raises(HEADER.replace(",status", "") + "\n" + GOOD), "missing column fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace(",5,", ",x,")), "non-int pipeline fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace(",5,", ",-2,")), "negative pipeline fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace(",P2,", ",P9,")), "bad priority fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace(",open", ",weird")), "bad status fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace(",Screen,", ",Coffee,")), "bad stage fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace("2025-12-01", "not-a-date")), "bad date fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace("Eng,Engineering", ",Engineering")), "empty text field fails closed")

# ---------- main(): exit codes, side effects, clean failure (redirected to temp) ----------

_orig = (run.OUT, run.REPORT, run.DIGEST)


def _set_out():
    d = Path(tempfile.mkdtemp()) / "output"
    run.OUT, run.REPORT, run.DIGEST = d, d / "r.html", d / "d.md"


try:
    # happy path writes both artifacts
    _set_out()
    ok(run.main(["--data", str(run.DATA)]) == 0, "main happy path exits 0")
    ok(run.REPORT.exists() and run.DIGEST.exists(), "main writes report + digest")

    # fail-closed: exit 1, no report written, single clean line, no traceback
    _set_out()
    bad = _csv(HEADER + "\n" + GOOD.replace(",5,", ",x,"))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = run.main(["--data", str(bad)])
    msg = err.getvalue().strip()
    ok(rc == 1, "fail-closed exits 1")
    ok(not run.REPORT.exists(), "fail-closed writes no report")
    ok("Traceback" not in msg, "fail-closed prints no traceback")
    ok(msg.startswith("FAIL CLOSED:") and "\n" not in msg, "fail-closed prints one clean line")

    # publish gate refuses BEFORE writing anything
    _set_out()
    ok(run.main(["--publish", "--data", str(run.DATA)]) == 2, "publish gate without approver exits 2")
    ok(not run.REPORT.exists(), "publish gate refuses before writing")

    # publish with approver records approval locally
    _set_out()
    ok(run.main(["--publish", "--approved-by", "Dana Lopez", "--data", str(run.DATA)]) == 0, "publish with approver exits 0")
    ok((run.OUT / "PUBLISHED.txt").exists(), "approval recorded locally")

    # as-of earlier than the data fails closed
    _set_out()
    ok(run.main(["--as-of", "2020-01-01", "--data", str(run.DATA)]) == 1, "as-of before data fails closed")
finally:
    run.OUT, run.REPORT, run.DIGEST = _orig

print(f"OK — {passed} checks passed ({k['total_open']} open reqs, {k['at_risk']} at risk).")
