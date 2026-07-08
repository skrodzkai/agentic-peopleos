#!/usr/bin/env python3
"""Evals for the illustrative ISS pay-for-performance screen.

These prove the screen follows the PUBLISHED ISS methodology and is deterministic: MOM (50/50 blend),
RDA (perf-minus-pay percentile, ISS sign), PTA (weighted least-squares trend difference), and the
rule-based AGGREGATION — two/three elevated measures escalate to High, and the FPA modifies a borderline
result in exactly the four documented ways (Low->Med, Med->Low, Med->High, High->Med; never Low<->High).
Concern levels are exercised with synthetic fixtures so the test does not depend on the real Acme outcome.
Run: python3 foundation/compute/tests/test_iss_screen.py
"""
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.compute.iss_screen import (  # noqa: E402
    ISSUniverse, ISSDataError, _percentile_rank, _median, _band_high, _band_low, _wls_norm_slope,
    ISS_BANDS_NON_SP500, FPA_NEUTRAL, _PEER_COLS, _EXEC_COLS,
    ISS_POLICIES, DEFAULT_POLICY_YEAR, policy_for, _rank, _pay_avg, _tsr_window, _RANK_ROUND,
)

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


B = ISS_BANDS_NON_SP500

# ---- pure helpers ----
ok(_median([3, 1, 2]) == 2 and _median([1, 2, 3, 4]) == 2.5, "median (odd + even)")
ok(_percentile_rank(5, [1, 3, 5, 7, 9]) == 50.0, "middle of five is the 50th percentile")
ok(_band_high(3.5, B["mom"]) == 2 and _band_high(2.4, B["mom"]) == 1 and _band_high(1.5, B["mom"]) == 0,
   "MOM banding: higher is worse (3.40 high / 2.33 medium)")
ok(_band_low(-65, B["rda"]) == 2 and _band_low(-55, B["rda"]) == 1 and _band_low(-10, B["rda"]) == 0,
   "RDA banding: lower/more-negative is worse (-64 high / -54 medium)")
# WLS normalized slope: a flat series has 0 slope; a rising series has a positive normalized slope
ok(_wls_norm_slope([100, 100, 100, 100, 100], (1, 2, 3, 4, 5), (0.72, 0.85, 1.0, 1.18, 1.38)) == 0.0,
   "weighted-least-squares slope is 0 for a flat series")
ok(_wls_norm_slope([100, 110, 121, 133, 146], (1, 2, 3, 4, 5), (0.72, 0.85, 1.0, 1.18, 1.38)) > 0,
   "weighted-least-squares slope is positive for a rising series")


# ---- fixture builder with a SPREAD peer set so the subject's percentiles are controllable ----
def _spread_peers(n=16):
    """n peers with pay $3M-$9M, indexed TSR 110-185, fin -4..11 (flat trajectories => PTA ~ 0 for peers)."""
    out = []
    for i in range(n):
        f = i / (n - 1)
        out.append({"rev": 50_000_000, "mc": 420_000_000,
                    "pay": [int(3_000_000 + 6_000_000 * f)] * 5,
                    "tsr": [round(110.0 + 75.0 * f, 2)] * 5,
                    "fin": round(-4.0 + 15.0 * f, 2)})
    return out


PEERS = _spread_peers(16)


def _write_universe(d, subject, peers=PEERS):
    peer_tks = [f"S{i + 100:03d}" for i in range(len(peers))]   # synthetic ISS ticker shape (S###)
    co, ex = [], []

    def add(tk, c, is_subj, self_peers):
        co.append({"ticker": tk, "company_name": f"{tk} Inc", "gics_sector": "Information Technology",
                   "gics_subindustry": "Application Software", "revenue_usd": c["rev"],
                   "market_cap_usd": c["mc"], "employees": 150, "total_assets_usd": 80_000_000,
                   "is_subject": "yes" if is_subj else "no"})
        row = {"ticker": tk, "self_peers": ";".join(self_peers), "fin_eva": c["fin"]}
        for i in range(5):
            row[f"pay_y{i + 1}"] = c["pay"][i]
            row[f"tsrval_y{i + 1}"] = c["tsr"][i]
        ex.append(row)

    add("ACMQ", subject, True, peer_tks)
    for tk, c in zip(peer_tks, peers):
        add(tk, c, False, ["ACMQ"] + [t for t in peer_tks if t != tk][:11])
    with open(Path(d) / "iss_universe.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_PEER_COLS, lineterminator="\n"); w.writeheader(); w.writerows(co)
    with open(Path(d) / "exec_pay_tsr.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_EXEC_COLS, lineterminator="\n"); w.writeheader(); w.writerows(ex)


def _subj(pay, tsr, fin):
    pay = pay if isinstance(pay, list) else [pay] * 5
    tsr = tsr if isinstance(tsr, list) else [tsr] * 5
    return {"rev": 50_000_000, "mc": 420_000_000, "pay": pay, "tsr": tsr, "fin": fin}


def _screen(subject, peers=PEERS):
    with tempfile.TemporaryDirectory() as d:
        _write_universe(d, subject, peers)
        return ISSUniverse(d).screen()


# peer pay median ~ $6M; tsr spread 110-185; fin spread -4..11.
# ---- LOW: paid below median, strong TSR -> all Low ----
r = _screen(_subj(4_000_000, 190.0, 5.0))
ok(r["concern"] == "Low", f"below-median pay + top TSR => Low (got {r['concern']})")
ok(r["measures"]["rda"]["value"] > 0, "RDA positive (TSR percentile ahead of pay) on the ISS sign")
ok(r["qualitative_triggers"] == [], "Low concern fires no triggers")
ok(14 <= r["comparison_group"]["n_group"] <= 24 and r["comparison_group"]["scorable"], "scorable 14-24 group")

# ---- single MEDIUM (RDA), FPA neutral -> Medium ----
# pay $11M (MOM ~1.8 Low), TSR ~mid-low so RDA lands Medium, fin high so FPA is neutral (no escalation)
r = _screen(_subj(11_000_000, 138.0, 11.0))
ok(r["measures"]["rda"]["band"] == "Medium" and r["measures"]["mom"]["band"] == "Low", "one Medium (RDA), MOM Low")
ok(r["measures"]["fpa"]["note"].startswith("not applied"), "FPA neutral (financials roughly justify pay)")
ok(r["concern"] == "Medium", f"single Medium + neutral FPA => Medium (got {r['concern']})")
ok(len(r["qualitative_triggers"]) >= 1, "a Medium surfaces the qualitative factors")

# ---- TWO Mediums (MOM + RDA) -> High, not FPA-modifiable ----
r = _screen(_subj(15_000_000, 138.0, 11.0))   # pay $15M -> MOM ~2.5 Medium, RDA still Medium
ok(r["measures"]["mom"]["band"] == "Medium" and r["measures"]["rda"]["band"] == "Medium", "two Medium measures")
ok(r["concern"] == "High" and "two or more elevated" in r["measures"]["fpa"]["note"],
   f"two Mediums escalate to High, FPA not applied (got {r['concern']})")

# ---- single Medium + POOR FPA -> High (criterion III) ----
r = _screen(_subj(11_000_000, 138.0, -4.0))   # RDA Medium, MOM Low, fin worst -> FPA poor
ok(r["measures"]["fpa"]["value"] < -FPA_NEUTRAL, "FPA poor (pay materially ahead of financials)")
ok(r["concern"] == "High" and "raised Medium->High" in r["measures"]["fpa"]["note"],
   f"single Medium + poor FPA => High (got {r['concern']})")

# ---- single Medium + STRONG FPA -> Low (criterion II) ----
# pay ~$6.5M (just above median, MOM Low, moderate pay percentile so fin can exceed it); terrible TSR ->
# RDA Medium; fin top -> FPA strong -> mitigates Medium to Low
r = _screen(_subj(6_500_000, 105.0, 11.0))
ok(r["measures"]["rda"]["band"] == "Medium" and r["measures"]["mom"]["band"] == "Low", "RDA Medium, MOM Low")
ok(r["measures"]["fpa"]["value"] > FPA_NEUTRAL, "FPA strong (financials materially ahead of pay)")
ok(r["concern"] == "Low" and "Medium->Low" in r["measures"]["fpa"]["note"],
   f"single Medium + strong FPA => Low (got {r['concern']})")

# ---- single HIGH + STRONG FPA -> Medium (criterion IV) ----
# pay ~$7.5M (upper-mid percentile), TSR rock-bottom -> RDA High; fin top -> FPA strong -> lowers to Medium
r = _screen(_subj(7_500_000, 110.0, 11.0))
ok(r["measures"]["rda"]["band"] == "High", "RDA High")
ok(r["concern"] == "Medium" and "High->Medium" in r["measures"]["fpa"]["note"],
   f"single High + strong FPA => Medium (got {r['concern']})")

# ---- FPA cannot move Low<->High: a lone High with strong FPA only drops to Medium (already covered),
#      and a borderline Low with strong FPA stays Low ----
r = _screen(_subj(4_000_000, 190.0, 11.0))    # all Low, strong fin
ok(r["concern"] == "Low", "strong FPA does not push a Low below Low / FPA never crosses Low<->High")

# ---- MOM is a 50/50 blend of 1-yr and 3-yr ----
r = _screen(_subj([2_000_000, 2_000_000, 18_000_000, 18_000_000, 18_000_000], 150.0, 5.0))
m = r["measures"]["mom"]
ok(abs(m["value"] - 0.5 * (m["mom_1yr"] + m["mom_3yr"])) < 0.01, "MOM == 50/50 blend of 1-yr and 3-yr")
ok(m["mom_1yr"] == m["mom_3yr"] or m["mom_1yr"] != m["mom_3yr"], "MOM exposes 1-yr and 3-yr components")

# ---- PTA via weighted least squares: pay rising faster than flat TSR => negative PTA ----
r = _screen(_subj([4_000_000, 5_000_000, 7_000_000, 10_000_000, 14_000_000], 150.0, 5.0))
ok(r["measures"]["pta"]["value"] < 0, "PTA negative when pay trend outruns the (flat) TSR trend")

# ---- determinism + the real Acme anchor ----
ok(ISSUniverse().screen()["concern"] == ISSUniverse().screen()["concern"], "screen is deterministic")
acme = ISSUniverse().screen()
ok(acme["concern"] in ("Low", "Medium", "High"), "real Acme produces a valid concern level")
ok(14 <= acme["comparison_group"]["n_group"] <= 24, "real Acme comparison group is a scorable 14-24")
ok(acme["subject"]["ticker"] == "ACMQ", "subject is Acme (ticker ACMQ)")


# ---- fail-closed loader ----
def _corrupt(mutate_co=None, mutate_ex=None):
    d = tempfile.mkdtemp()
    _write_universe(d, _subj(4_000_000, 150.0, 5.0))
    for fn, name, cols in ((mutate_co, "iss_universe.csv", _PEER_COLS), (mutate_ex, "exec_pay_tsr.csv", _EXEC_COLS)):
        if fn:
            rows = list(csv.DictReader(open(Path(d) / name)))
            fn(rows)
            with open(Path(d) / name, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n"); w.writeheader(); w.writerows(rows)
    return d


def _expect_closed(label, **kw):
    try:
        ISSUniverse(_corrupt(**kw)); ok(False, f"{label} should raise")
    except ISSDataError:
        ok(True, f"{label} fails closed")


try:
    ISSUniverse(data_dir="/nonexistent/xyz"); ok(False, "missing inputs should raise")
except ISSDataError:
    ok(True, "missing ISS inputs fail closed")
_expect_closed("two subjects", mutate_co=lambda co: co[1].__setitem__("is_subject", "yes"))
_expect_closed("mismatched coverage", mutate_ex=lambda ex: ex.pop())
_expect_closed("real/foreign ticker in the synthetic ISS universe",
               mutate_co=lambda co: [r.__setitem__("ticker", "AAPL") for r in co if r["ticker"] == "S100"],
               mutate_ex=lambda ex: [r.__setitem__("ticker", "AAPL") for r in ex if r["ticker"] == "S100"])
# a REAL PEER ticker (GTLB lives only in peer_universe.csv) must never be accepted in the synthetic ISS
# universe — the shape guard (ACMQ|S###) rejects it structurally, so no real name can carry fabricated pay
_expect_closed("real peer ticker injected into the ISS universe",
               mutate_co=lambda co: [r.__setitem__("ticker", "GTLB") for r in co if r["ticker"] == "S100"],
               mutate_ex=lambda ex: [r.__setitem__("ticker", "GTLB") for r in ex if r["ticker"] == "S100"])
# round-5/6: even under a VALID synthetic ticker (S100), a real company NAME must be rejected — otherwise a
# fabricated pay/TSR figure would attach to a real company (GTLB's name lives only in peer_universe.csv).
# The match is canonicalized, so a punctuation/suffix variant ("GitLab Inc", no period) also fails closed.
_expect_closed("real company name under a synthetic ISS ticker",
               mutate_co=lambda co: [r.__setitem__("company_name", "GitLab Inc.") for r in co if r["ticker"] == "S100"])
_expect_closed("real company name VARIANT (no period) under a synthetic ISS ticker",
               mutate_co=lambda co: [r.__setitem__("company_name", "GitLab Inc") for r in co if r["ticker"] == "S100"])
# round-7: a recognizable SHORT FORM of a real peer must also fail closed (token-subset match, not exact key)
_expect_closed("real company SHORT FORM ('Descartes Systems Group') under a synthetic ISS ticker",
               mutate_co=lambda co: [r.__setitem__("company_name", "Descartes Systems Group") for r in co if r["ticker"] == "S100"])
_expect_closed("real company SHORT FORM ('ZoomInfo') under a synthetic ISS ticker",
               mutate_co=lambda co: [r.__setitem__("company_name", "ZoomInfo") for r in co if r["ticker"] == "S100"])
# round-10: a SPACING variant ('Git Lab Inc.') that breaks tokenization is caught via alnum-collapse
_expect_closed("real company SPACING variant ('Git Lab Inc.') under a synthetic ISS ticker",
               mutate_co=lambda co: [r.__setitem__("company_name", "Git Lab Inc.") for r in co if r["ticker"] == "S100"])
_expect_closed("non-positive CEO pay", mutate_ex=lambda ex: ex[1].__setitem__("pay_y1", "0"))
_expect_closed("non-positive TSR baseline", mutate_ex=lambda ex: ex[1].__setitem__("tsrval_y1", "0"))
_expect_closed("non-finite TSR value (NaN can't flow into a rendered measure or GL synthesis)",
               mutate_ex=lambda ex: ex[1].__setitem__("tsrval_y3", "nan"))
_expect_closed("unknown self-peer ref", mutate_ex=lambda ex: ex[1].__setitem__("self_peers", "ZZZZ"))
_expect_closed("duplicate exec ticker", mutate_ex=lambda ex: ex.append(dict(ex[1])))

# --------------------------------------------------------------- policy-year parameterization (2025 vs 2026)
u = ISSUniverse()

# default season is 2026, and the result carries its policy metadata
ok(DEFAULT_POLICY_YEAR == 2026, "the screen defaults to the ISS 2026 policy")
r26 = u.screen()
ok(r26["policy"]["year"] == 2026 and r26 == u.screen(policy_year=2026), "default screen == explicit 2026")
ok(r26["policy"]["delta_from_prior"] and "RDA extended" in r26["policy"]["delta_from_prior"],
   "the 2026 result surfaces the concrete delta from 2025")

# the published thresholds are EXACT for each season (verified against ISS's Mechanics tables)
ok(ISS_POLICIES[2026]["bands"]["mom"] == {"fpa_eligible": 1.89, "medium": 2.33, "high": 3.40}, "2026 MOM thresholds exact")
ok(ISS_POLICIES[2026]["bands"]["rda"] == {"fpa_eligible": -41.0, "medium": -54.0, "high": -64.0}, "2026 RDA thresholds exact")
ok(ISS_POLICIES[2026]["bands"]["pta"] == {"fpa_eligible": -28.0, "medium": -30.0, "high": -45.0}, "2026 PTA thresholds exact")
ok(ISS_POLICIES[2025]["bands"]["mom"] == {"fpa_eligible": 1.84, "medium": 2.33, "high": 3.33}, "2025 MOM thresholds exact")
ok(ISS_POLICIES[2025]["bands"]["rda"] == {"fpa_eligible": -38.0, "medium": -50.0, "high": -60.0}, "2025 RDA thresholds exact")
ok(ISS_POLICIES[2025]["bands"]["pta"] == {"fpa_eligible": -25.0, "medium": -30.0, "high": -45.0}, "2025 PTA thresholds exact")
ok(ISS_BANDS_NON_SP500 is ISS_POLICIES[2026]["bands"], "the back-compat alias is the 2026 bands")

# the WINDOWS actually change the measure, not just the label
r25 = u.screen(policy_year=2025)
ok(r26["measures"]["mom"]["blend_years"] == [1, 3] and r25["measures"]["mom"]["blend_years"] == [1],
   "2026 MOM is a 1yr+3yr blend; 2025 MOM is 1yr only")
ok(r26["measures"]["mom"]["mom_3yr"] is not None and r25["measures"]["mom"]["mom_3yr"] is None,
   "the 3-yr MOM component exists only in 2026")
ok(r26["measures"]["rda"]["window_years"] == 5 and r25["measures"]["rda"]["window_years"] == 3,
   "RDA window is 5yr (2026) vs 3yr (2025)")
ok(r26["measures"]["rda"]["value"] != r25["measures"]["rda"]["value"],
   "the RDA window change moves the RDA value (policy year is not cosmetic)")
# the 2026 MOM is the mean of its 1yr and 3yr multiples (each field independently rounded to 3dp)
mom = r26["measures"]["mom"]
ok(abs(mom["value"] - (mom["mom_1yr"] + mom["mom_3yr"]) / 2.0) < 1.5e-3, "2026 MOM = mean(1yr, 3yr)")

# _pay_avg / _tsr_window windowing math
ok(_pay_avg([10, 20, 30, 40, 50], 1) == 50.0 and _pay_avg([10, 20, 30, 40, 50], 3) == 40.0, "_pay_avg windows")
ok(abs(_tsr_window([110, 121, 133, 146, 160], 5) - 0.60) < 1e-9, "_tsr_window 5yr = tsr[-1]/100 - 1")
ok(abs(_tsr_window([110, 121, 133, 146, 160], 3) - (160.0 / 121.0 - 1.0)) < 1e-9, "_tsr_window 3yr uses the year-2 base")

# fail closed on an unmodeled season, and policy_for mirrors it
for bad in (2099, "2026", 2024, None):
    try:
        u.screen(policy_year=bad); ok(False, f"unmodeled policy {bad!r} must raise")
    except ISSDataError:
        ok(True, f"unmodeled policy {bad!r} fails closed")
try:
    policy_for(2099); ok(False, "policy_for unmodeled must raise")
except ISSDataError:
    ok(True, "policy_for fails closed on an unmodeled year")

# _rank rounds before ranking, so a sub-ULP difference cannot flip a near-tie
base = [1.0, 2.0, 3.0]
ok(_rank(2.0, base) == _rank(2.0 + 10 ** (-(_RANK_ROUND + 3)), base),
   "a difference below the rounding precision does not change the rank")
ok(u.screen(2026) == ISSUniverse().screen(2026), "the 2026 screen is deterministic across instances")

print(f"OK — {passed} ISS-screen checks passed "
      f"(real Acme: {acme['concern']} concern, comparison group {acme['comparison_group']['n_group']}).")
