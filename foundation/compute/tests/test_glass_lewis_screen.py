#!/usr/bin/env python3
"""Tests for the illustrative Glass Lewis 5-test concern scorecard + the ISS-vs-GL war room: happy-path
invariants, the advisor-synthesis verdict lattice, and a fail-closed inventory. Fail-closed cases copy the
committed CSVs to a tmp dir and corrupt exactly one thing."""
import csv
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.compute import glass_lewis_screen as G  # noqa: E402

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
    except G.GLDataError:
        passed += 1


def _tmp_with(mutate):
    d = Path(tempfile.mkdtemp())
    for name in ("iss_universe.csv", "exec_pay_tsr.csv", "gl_financials.csv"):
        shutil.copy(_ACME / name, d / name)
    mutate(d)
    return d


def _rewrite(path, fn):
    rows = list(csv.DictReader(open(path)))
    fields = list(rows[0].keys())
    fn(rows)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# ---- happy path: the committed data yields a coherent two-advisor view -----------------------------------
r = G.compute()
gl, iss, syn = r["gl"], r["iss"], r["synthesis"]
ok(r["subject"] == "ACMQ" and iss["subject"]["ticker"] == "ACMQ", "both advisors score the same subject")
ok(gl["peer_group"]["n"] == 15 and gl["peer_group"]["scorable"], "GL peer group is ~15 firms (min 10 viable)")
ok(gl["concern"] in ("Negligible", "Low", "Medium", "High", "Severe"), "GL emits a concern level")
ok(0.0 <= gl["composite_score"] <= 100.0, "composite score in [0,100]")
ok(gl["concern"] == G.concern_for_score(gl["composite_score"]), "concern matches the composite band")
# the 5 quantitative tests are all present, scored, and banded
ok(len(gl["tests"]) == 5, "five quantitative tests")
ok(all(0.0 <= t["score"] <= 100.0 and t["band"] for t in gl["tests"]), "each test scored [0,100] + banded")
ok(abs(sum(t["weight"] for t in gl["tests"]) - 1.0) < 1e-9, "test weights sum to 1")
# the calibrated teaching case: the advisors DIVERGE — GL's broad scorecard reads lower concern than ISS
ok(iss["concern"] == "Medium", "ISS reads Medium on the committed data (CEO pay vs weak TSR)")
ok(gl["concern"] == "Low", "GL reads Low concern (only the pay-vs-TSR test flags; financials aligned)")
ok(syn["verdict"] == "ISS-ONLY FLAG" and syn["agree"] is False, "synthesis: the advisors disagree (ISS-ONLY FLAG)")
# the counterfactuals carry the WHY: a pure-TSR read is far worse than a financials-only read
cf = gl["counterfactuals"]
ok(cf["tsr_only_score"] < gl["composite_score"] < cf["financials_only_score"],
   "the real composite sits between GL's pure-TSR read and its financials-only read")
ok(G._GL_ORDINAL[cf["tsr_only_concern"]] > G._GL_ORDINAL[cf["financials_only_concern"]],
   "TSR-only concern is worse than financials-only concern (the divergence mechanism)")
# the flagging test really is the granted-CEO-pay-vs-TSR one
tsr_test = next(t for t in gl["tests"] if t["key"] == "granted_ceo_pay_vs_tsr")
ok(G._GL_ORDINAL.get(tsr_test["band"], 2) >= 1 or tsr_test["score"] < 50, "the CEO-pay-vs-TSR test is the flagging one")
lo, hi = syn["say_on_pay_support_band_pct"]
ok(0.0 <= lo < hi <= 100.0, "say-on-pay band is an ordered range within [0,100]")
ok("NOT a vote forecast" in syn["band_basis"], "SOP band is labeled a directional estimate, not a forecast")

# ---- determinism ----------------------------------------------------------------------------------------
ok(json.dumps(G.compute(), sort_keys=True, default=str) == json.dumps(r, sort_keys=True, default=str),
   "compute() is deterministic")


# ---- advisor_synthesis verdict lattice (unit — no data needed) ------------------------------------------
def _iss(concern, pay=80.0, tsr=30.0):
    return {"concern": concern, "measures": {"rda": {"pay_pctile": pay, "tsr_pctile": tsr}},
            "subject": {"ticker": "ACMQ"}}


def _gl(concern, comp=70.0):
    return {"concern": concern, "composite_score": comp, "pay_pctile": 90.0, "fin_pctile": 80.0,
            "tsr_pctile": 30.0, "subject": "ACMQ"}


ok(G.advisor_synthesis(_iss("Low"), _gl("Negligible"))["verdict"] == "CLEAN SWEEP", "Low + Negligible -> CLEAN SWEEP")
ok(G.advisor_synthesis(_iss("Low"), _gl("Low"))["verdict"] == "CLEAN SWEEP", "Low + Low -> CLEAN SWEEP")
ok(G.advisor_synthesis(_iss("Medium"), _gl("Low"))["verdict"] == "ISS-ONLY FLAG", "Medium + Low -> ISS-ONLY FLAG")
ok(G.advisor_synthesis(_iss("Low"), _gl("Medium"))["verdict"] == "GL-ONLY FLAG", "Low + Medium -> GL-ONLY FLAG")
ok(G.advisor_synthesis(_iss("Medium"), _gl("Medium"))["verdict"] == "DUAL WATCH", "Medium + Medium -> DUAL WATCH")
ok(G.advisor_synthesis(_iss("High"), _gl("Low"))["verdict"] == "ISS-ONLY FLAG",
   "High + Low -> ISS-ONLY FLAG (severity doesn't change WHO flags)")
ok(G.advisor_synthesis(_iss("High"), _gl("Medium"))["verdict"] == "TWO-FRONT FIGHT", "High + Medium -> TWO-FRONT FIGHT")
ok(G.advisor_synthesis(_iss("Medium"), _gl("Severe"))["verdict"] == "TWO-FRONT FIGHT", "Medium + Severe -> TWO-FRONT FIGHT")
clean = G.advisor_synthesis(_iss("Low"), _gl("Negligible"))["say_on_pay_support_band_pct"]
fight = G.advisor_synthesis(_iss("High"), _gl("Severe"))["say_on_pay_support_band_pct"]
ok(fight[0] < clean[0] and fight[1] < clean[1], "SOP band is lower for a two-front fight than a clean sweep")
# severity is NOT flattened onto the verdict: a High unilateral flag lands BELOW a Medium one (same verdict)
med_flag = G.advisor_synthesis(_iss("Medium"), _gl("Low"))["say_on_pay_support_band_pct"]
high_flag = G.advisor_synthesis(_iss("High"), _gl("Low"))["say_on_pay_support_band_pct"]
ok(high_flag[0] < med_flag[0] and high_flag[1] < med_flag[1],
   "a severe unilateral flag lands below a medium one (severity-weighted SOP band)")
raises(lambda: G.advisor_synthesis(_iss("Medium"),
       {"concern": "Low", "composite_score": 70.0, "pay_pctile": 90.0, "fin_pctile": 80.0, "tsr_pctile": 30.0}),
       "advisor_synthesis fails closed when the GL subject id is missing")

# ---- FAIL-CLOSED inventory ------------------------------------------------------------------------------
raises(lambda: G.GLUniverse(Path(tempfile.mkdtemp())), "missing data files fail closed")
raises(lambda: G.compute(_tmp_with(lambda d: _rewrite(d / "gl_financials.csv", lambda rows: rows.pop()))),
       "roster mismatch (gl_financials missing a ticker) fails closed")
raises(lambda: G.compute(_tmp_with(lambda d: _rewrite(d / "gl_financials.csv",
       lambda rows: rows[0].__setitem__("eps_y2", "0")))), "non-positive growth base fails closed")
raises(lambda: G.compute(_tmp_with(lambda d: _rewrite(d / "gl_financials.csv",
       lambda rows: rows[0].__setitem__("sti_payout_y5", "0")))), "non-positive STI payout fails closed")
raises(lambda: G.compute(_tmp_with(lambda d: _rewrite(d / "gl_financials.csv",
       lambda rows: rows[0].__setitem__("cap_y5", "-1")))), "non-positive CAP fails closed")
raises(lambda: G.compute(_tmp_with(lambda d: _rewrite(d / "gl_financials.csv",
       lambda rows: rows[0].__setitem__("neo_other_pay_y3", "not-a-number")))),
       "non-numeric NEO pay fails closed with GLDataError (not ISSDataError)")
raises(lambda: G.compute(_tmp_with(lambda d: _rewrite(d / "exec_pay_tsr.csv",
       lambda rows: rows[0].__setitem__("tsrval_y1", "0")))), "a zero TSR baseline fails closed")


def _two_subjects(d):
    _rewrite(d / "iss_universe.csv",
             lambda rows: next(x for x in rows if x["is_subject"] == "no").__setitem__("is_subject", "yes"))


raises(lambda: G.compute(_tmp_with(_two_subjects)), "two subjects fail closed")


def _real_name_leak(d):
    _rewrite(d / "iss_universe.csv",
             lambda rows: next(x for x in rows if x["ticker"] == "ACMQ").__setitem__("company_name", "GitLab Inc."))


raises(lambda: G.compute(_tmp_with(_real_name_leak)), "a real issuer NAME on a synthetic ticker fails closed")


def _real_ticker_shape(d):
    for name in ("iss_universe.csv", "exec_pay_tsr.csv", "gl_financials.csv"):
        _rewrite(d / name, lambda rows: rows[1].__setitem__("ticker", "AAPL"))


raises(lambda: G.compute(_tmp_with(_real_ticker_shape)), "a non-synthetic ticker shape fails closed")
raises(lambda: G.compute(_tmp_with(lambda d: _rewrite(d / "gl_financials.csv",
       lambda rows: rows.append(dict(rows[0]))))),
       "a duplicate gl_financials ticker fails closed (no silent last-row-wins overwrite)")
raises(lambda: G.advisor_synthesis(_iss("Medium"),
       {"concern": "Low", "composite_score": 70.0, "pay_pctile": 90.0, "fin_pctile": 80.0,
        "tsr_pctile": 30.0, "subject": "S001"}),
       "advisor_synthesis fails closed when ISS and GL describe different subjects")

# EVERY consumed field is validated (not just a subset of years) — folded from the modernization review
raises(lambda: G.compute(_tmp_with(lambda d: _rewrite(d / "gl_financials.csv",
       lambda rows: rows[0].__setitem__("neo_other_pay_y1", "0")))), "non-positive NEO pay in y1 fails closed")
raises(lambda: G.compute(_tmp_with(lambda d: _rewrite(d / "gl_financials.csv",
       lambda rows: rows[0].__setitem__("sti_payout_y2", "-1000")))), "negative STI payout in y2 fails closed")
raises(lambda: G.compute(_tmp_with(lambda d: _rewrite(d / "gl_financials.csv",
       lambda rows: rows[0].__setitem__("cap_y1", "-1")))), "negative CAP in y1 fails closed")
raises(lambda: G.compute(_tmp_with(lambda d: _rewrite(d / "gl_financials.csv",
       lambda rows: rows[0].__setitem__("eps_y5", "0")))), "non-positive terminal-year EPS fails closed")


def _cap_overflow(d):
    def fn(rows):
        rows[0]["cap_y1"] = "1e308"
        rows[0]["cap_y2"] = "1e308"
    _rewrite(d / "gl_financials.csv", fn)


raises(lambda: G.compute(_tmp_with(_cap_overflow)),
       "a CAP aggregate that overflows to non-finite fails closed (not rendered as inf)")

# robustness lock: the GL=Low / ISS=Medium divergence survives a ±5% perturbation of the subject's terminal
# financials + pay components (the review verified this manually; this regression-tests the property)
_PERTURB = (tuple(f"{m}_y5" for m in ("eps", "rev", "ocf", "roe", "roa"))
            + tuple(f"{p}_y{y}" for p in ("neo_other_pay", "sti_payout", "cap") for y in range(1, 6)))


def _scaled(factor):
    def mut(d):
        def fn(rows):
            for r in rows:
                if r["ticker"] == "ACMQ":
                    for c in _PERTURB:
                        r[c] = str(round(float(r[c]) * factor, 4))
        _rewrite(d / "gl_financials.csv", fn)
    return mut


for _f in (0.95, 1.05):
    ok(G.compute(_tmp_with(_scaled(_f)))["gl"]["concern"] == "Low",
       f"GL concern stays Low under a {_f:.0%} perturbation of the subject (robust, not knife-edge)")

# a too-small peer group cannot be scored (unit — pass an undersized group straight to screen())
_glu = G.GLUniverse()
raises(lambda: _glu.screen({"members": ["S001", "S002"], "n": 2, "scorable": False}),
       "an undersized peer group fails closed (below the scorable minimum)")

print(f"OK — {passed} Glass Lewis scorecard + war-room checks passed "
      f"(GL {gl['concern']} [{gl['composite_score']}] vs ISS {iss['concern']} -> {syn['verdict']}).")
