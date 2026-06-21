#!/usr/bin/env python3
"""Evals for the compensation reporting agent. Run: python evals/test_comp.py

Plain stdlib asserts so it runs anywhere with no test framework installed.
Covers the metric math, the governance invariant (agent never recommends/changes
pay), the fail-closed data contract, and the human publish gate.
"""
import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run  # noqa: E402

HEADER = ("emp_id,level,job_family,location,base_salary,"
          "range_min,range_mid,range_max,exception_flag")
GOOD = "E-900,L4,Engineering,US,160000,150000,175000,200000,no"

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


def _csv(text: str) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "comp.csv"
    tmp.write_text(text, encoding="utf-8")
    return tmp


def _raises(text_or_path, exc=run.CompContractError) -> bool:
    path = text_or_path if isinstance(text_or_path, Path) else _csv(text_or_path)
    try:
        run.load_snapshot(path)
        return False
    except exc:
        return True
    except Exception:
        return False


# ---------- metric math: compa-ratio = base / range_mid ----------

rows = run.enrich(run.load_snapshot())
by_id = {r["emp_id"]: r for r in rows}

for r in rows:
    expect = round(r["base_salary"] / r["range_mid"], 2)
    ok(r["compa_ratio"] == expect, f"{r['emp_id']} compa = base/range_mid")

# E-110: above max but documented exception -> out of band, NOT a violation.
e110 = by_id["E-110"]
ok(e110["out_of_band"] and e110["direction"] == "above max", "E-110 is above max")
ok(e110["exception_flag"] == "yes" and not e110["unexcepted_oob"], "E-110 excepted -> not a violation")

# E-111: below min with NO exception -> the governance violation we surface.
e111 = by_id["E-111"]
ok(e111["out_of_band"] and e111["direction"] == "below min", "E-111 is below min")
ok(e111["exception_flag"] == "no" and e111["unexcepted_oob"], "E-111 unexcepted -> the violation")

# In-band employee is never flagged.
ok(not by_id["E-102"]["out_of_band"], "in-band employee not flagged")

# ---------- report invariants / reconciliation ----------

report = run.build_report(run.load_snapshot())
k = report["kpis"]
ok(sum(report["distribution"].values()) == k["population"], "compa distribution reconciles to population")
ok(sum(s["n"] for s in report["by_level"].values()) == k["population"], "by-level reconciles to population")
ok(len(report["oob_flags"]) == k["out_of_band"], "oob flag rows == out_of_band count")
ok(k["out_of_band"] == 2 and k["unexcepted_oob"] == 1, "2 out of band, 1 without exception")
ok(sum(1 for r in rows if r["exception_flag"] == "yes") == k["exceptions"], "exception count matches")
ok(bool(report["narrative"]) and any(ch.isdigit() for ch in report["narrative"]), "narrative is data-derived")

# ---------- governance: the registry forbids pay changes; the agent honors it ----------

ok(run.METRICS is not None, "comp-reporting loaded the metric registry")
for mid in ("compa_ratio", "range_penetration", "out_of_band_rate", "comp_exception_rate"):
    g = run.METRICS.get(mid)
    ok(g is not None, f"{mid} exists in the registry")
    ok("recommend_pay_change" in g["agent_forbidden_actions"], f"{mid} forbids recommend_pay_change")
    ok("change_salary" in g["agent_forbidden_actions"], f"{mid} forbids change_salary")
    ok(not run.METRICS.may(mid, "change_salary"), f"registry.may({mid}, change_salary) is False")

# The agent's own output must never instruct a pay change — it flags and defers to a human.
page = run.render_html(report)
digest = run.render_digest(report)
lowered = (page + digest).lower()
for banned in ("recommend a raise", "set salary to", "increase pay to", "give a raise"):
    ok(banned not in lowered, f"output never says '{banned}'")
ok("never recommend or change pay" in page.lower() or "never recommends or change" in page.lower(),
   "report states the agent never changes pay")

# ---------- html / digest carry brand + citation + draft gate ----------

ok(run.COMPANY in page and "Agentic People" in page, "html carries brand")
ok("What needs attention" in page and report["narrative"][:20] in page, "html carries narrative")
ok("Draft" in page or "DRAFT" in page, "html carries draft gate")
ok("metrics.registry.json" in page and "Metric definitions" in page, "report cites the metric registry")
ok("Publish gate" in digest, "digest carries publish gate")

# ---------- fail-closed: data contract ----------

ok(_raises(Path("/no/such/file.csv"), FileNotFoundError), "missing file fails closed")
ok(_raises(""), "empty file fails closed")
ok(_raises(HEADER + "\n"), "header-only fails closed")
ok(_raises(HEADER.replace(",exception_flag", "") + "\n" + GOOD), "missing column fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace(",160000,", ",x,")), "non-int salary fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace(",160000,", ",-5,")), "negative salary fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace(",no", ",maybe")), "bad exception_flag fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace("150000,175000,200000", "200000,175000,150000")),
   "unordered band fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace("E-900,L4", ",L4")), "empty required field fails closed")
ok(_raises(HEADER + "\n" + GOOD + "\n" + GOOD), "duplicate emp_id fails closed")
ok(_raises(HEADER + ",extra\n" + GOOD + ",x"), "unexpected extra column fails closed")
ok(_raises(HEADER.replace("location", "level") + "\n" + GOOD), "duplicate header fails closed")
ok(_raises(HEADER + "\n" + GOOD + ",ragged"), "ragged row (too many fields) fails closed")
ok(_raises(HEADER + "\n" + GOOD.replace("150000,175000,200000", "175000,175000,200000")),
   "zero-width band (min==mid) fails closed")

# ---------- adversarial: STRUCTURAL markdown/control-character injection is blocked ----------
# The strict ingest charset blocks newlines, control chars, and markdown/HTML metacharacters.
# (Structural guarantee — it does not claim to semantically detect instruction-like prose.)

# A quoted newline in emp_id would otherwise inject a new markdown bullet into the digest.
INJECT = '"E-9\n- another bullet",L4,Engineering,US,160000,150000,175000,200000,no'
ok(_raises(HEADER + "\n" + INJECT), "newline-injection in emp_id is rejected at ingest")
# Markdown/HTML metacharacters in a text field are rejected too.
ok(_raises(HEADER + "\n" + GOOD.replace("Engineering", "Eng|`<b>`")), "markdown/html metachars rejected")
# A '$' (and other out-of-charset chars) is rejected structurally — not because we parse meaning.
ok(_raises(HEADER + "\n" + GOOD.replace("Engineering", "raise of $50k")),
   "a field with an out-of-charset char ($) is rejected at ingest")

# ---------- adversarial: extreme-but-valid compa still reconciles ----------

EXTREME = "E-8,L4,Engineering,US,1000000,10000,11000,12000,yes"  # compa ~90.9, far above the top band edge
xreport = run.build_report(run.load_snapshot(_csv(HEADER + "\n" + GOOD + "\n" + EXTREME)))
ok(sum(xreport["distribution"].values()) == xreport["kpis"]["population"],
   "an extreme compa-ratio still lands in a band (distribution reconciles)")

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
    bad = _csv(HEADER + "\n" + GOOD.replace(",160000,", ",x,"))
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

    # publish with a named approver records the approval locally
    _set_out()
    ok(run.main(["--publish", "--approved-by", "Total Rewards Partner", "--data", str(run.DATA)]) == 0,
       "publish with approver exits 0")
    ok((run.OUT / "PUBLISHED.json").exists(), "approval recorded locally (JSON)")
    import json as _json
    rec = _json.loads((run.OUT / "PUBLISHED.json").read_text())
    ok(rec["approved_by"] == "Total Rewards Partner" and rec["scope"] == run.SCOPE, "approval record is structured JSON")

    # a newline-injecting approver name is refused by the publish gate
    _set_out()
    ok(run.main(["--publish", "--approved-by", "Evil\n- extra", "--data", str(run.DATA)]) == 2,
       "publish gate refuses an approver name with control characters")

    # registry unavailable -> the agent fails closed (no uncited report on un-governed numbers)
    _set_out()
    _sm, _se = run.METRICS, run.REGISTRY_ERROR
    run.METRICS, run.REGISTRY_ERROR = None, "metric registry unavailable: (simulated)"
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main(["--data", str(run.DATA)])
        ok(rc == 1, "missing registry fails closed (exit 1)")
        ok(not run.REPORT.exists(), "missing registry writes no report")
        ok(err.getvalue().strip().startswith("FAIL CLOSED:"), "missing registry prints one clean line")
    finally:
        run.METRICS, run.REGISTRY_ERROR = _sm, _se
finally:
    run.OUT, run.REPORT, run.DIGEST = _orig

print(f"OK — {passed} checks passed ({k['population']} employees, "
      f"{k['out_of_band']} out of band, {k['unexcepted_oob']} without exception).")
