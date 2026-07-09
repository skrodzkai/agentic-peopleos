#!/usr/bin/env python3
"""Evals for the ISS Pay-for-Performance Screen composer.
Run: python3 evals/test_iss_pay_screen.py

Proves the dashboard is presentation-only over foundation/compute/iss_screen.py, renders the concern +
all three measures + the comparison group + the qualitative triggers, is HONEST about being an
illustrative model of ISS's PUBLIC methodology (not ISS's output), is injection/public-safety clean,
renders deterministically, and the publish gate + fail-closed paths work.
"""
import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run  # noqa: E402
from foundation.compute.iss_screen import ISSUniverse  # noqa: E402
from foundation.compute.peers import PeerUniverse  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


iss = ISSUniverse()
screen = iss.screen()
report = run.build_report(iss, PeerUniverse())
page = run.render_html(report)
digest = run.render_digest(report)

# ---- presentation-only: every value on the dashboard == the engine's value (the agent does no math) ----
ok(report["concern"] == screen["concern"], "concern on the dashboard == the engine's concern")
ok(report["measures"] == screen["measures"], "the measures come straight from the engine")
ok(report["comparison_group"]["n_group"] == screen["comparison_group"]["n_group"],
   "the comparison-group size comes from the engine")
ok(report["triggers"] == screen["qualitative_triggers"], "the qualitative triggers come from the engine")

# ---- the page carries the concern, all three measures + bands, the gauges, the overlap, the triggers ----
for needle in ["ISS Pay-for-Performance Screen", "logomark", "Anticipated ISS quantitative concern",
               report["concern"], "MOM", "RDA", "PTA", "Multiple of Median", "Relative Degree of Alignment",
               "Pay-TSR Alignment", "FPA", "comparison group", "Qualitative review"]:
    ok(needle in page, f"dashboard renders '{needle}'")
for band in (report["measures"]["mom"]["band"], report["measures"]["rda"]["band"], report["measures"]["pta"]["band"]):
    ok(band in page, f"a measure band '{band}' is shown")
ok(page.count("<svg") >= 4, "renders the brand mark + the three measure gauges as SVG")

# ---- policy-year stamp + the concrete 2026 delta, and gauges driven by the engine's bands (not hard-coded) ----
ok(report["res"]["policy"]["year"] == 2026 and "ISS 2026 policy" in page, "the dashboard stamps the ISS 2026 policy")
ok("2026 update reflected" in page and "RDA extended 3yr" in page,
   "the dashboard surfaces the concrete 2026-vs-2025 delta")
ok("non-S&amp;P-500 thresholds" in page, "the dashboard states the (non-S&P-500) threshold set it used")
b = report["res"]["bands"]
ok(f"{b['mom']['high']:.2f}" in page or f"{b['mom']['high']}" in page, "MOM high threshold from the engine appears on the gauge")
ok(str(int(b["rda"]["high"])) in page, "RDA high threshold from the engine appears on the gauge")
# the gauge thresholds are engine-driven: swapping the policy year MOVES the rendered gauge geometry,
# not just the engine bands or the delta prose. Render a real 2025 page and a real 2026 page and prove the
# RDA concern-tick sits at a different x — X(threshold) on the shared -80..20 axis — in each.
page26 = run.render_html(run.build_report(ISSUniverse(), None, policy_year=2026))
page25 = run.render_html(run.build_report(ISSUniverse(), None, policy_year=2025))
ok(page25 != page26, "rendering a different ISS policy year produces a different dashboard")
_rda_tick = lambda hi: "x1='%.1f'" % (10 + (350 - 10) * (hi - (-80.0)) / (20.0 - (-80.0)))
ok(_rda_tick(-64.0) in page26 and _rda_tick(-64.0) not in page25,
   "the 2026 dashboard draws its RDA concern tick at the engine's -64 boundary (and 2025 does not)")
ok(_rda_tick(-60.0) in page25 and _rda_tick(-60.0) not in page26,
   "the 2025 dashboard draws its RDA concern tick at the engine's -60 boundary (and 2026 does not)")
res25 = ISSUniverse().screen(policy_year=2025)
ok(res25["bands"]["rda"]["high"] == -60.0 and report["res"]["bands"]["rda"]["high"] == -64.0,
   "2025 vs 2026 RDA-high thresholds differ in the engine the dashboard reads from")
# the 2025 render's COPY must track its own policy: a 3-year RDA window, and NO "2026 update" delta line
ok("3-year TSR" in page25 and "5-year TSR" not in page25,
   "the 2025 narrative states its own 3-year RDA/TSR window (not a hard-coded 5-year)")
ok("of 5-year TSR" in page26, "the 2026 narrative states its 5-year RDA/TSR window")
ok("2026 update reflected" not in page25 and "reflected: None" not in page25,
   "the 2025 baseline render carries no '2026 update reflected' delta line (delta_from_prior is None)")
ok("2026 update reflected" in page26, "the 2026 render still shows the concrete season delta")
ok("overlap committee core" in page and str(len(report["committee"]["overlap"])) in page,
   "the ISS-vs-committee peer overlap is shown (the two-peer-object point)")

# ---- HONEST framing: illustrative model of ISS's PUBLISHED methodology, NOT ISS's actual output ----
low = (page + digest).lower()
for phrase in ("illustrative", "published", "not iss's actual output", "synthetic", "consultant review"):
    ok(phrase in low, f"the dashboard/digest states '{phrase}' (honest about what this is)")
ok("does not disclose its exact cut" not in low and "doesn't disclose its exact cut" not in low,
   "the dashboard no longer falsely claims ISS hides its thresholds (ISS publishes the table)")
ok(report["concern"] == "Medium", "the showpiece subject (Acme) lands a Medium concern — exercises the full screen")
ok(len(report["triggers"]) >= 1, "a Medium concern surfaces the qualitative-review factors")

# ---- determinism ----
ok(run.render_html(run.build_report(ISSUniverse(), PeerUniverse())) == page, "the dashboard renders deterministically")
ok(run.render_digest(run.build_report(ISSUniverse(), PeerUniverse())) == digest, "the digest renders deterministically")
# the overlap is optional: with no peer universe, the screen still renders standalone
ok("Medium" in run.render_html(run.build_report(ISSUniverse(), None)), "renders standalone if the peer arm is absent")

# ---- build-time validation gate: a self-inconsistent engine result is refused before it can render ----
import copy  # noqa: E402


def _refused(mut, why):
    global passed
    r = copy.deepcopy(screen)
    mut(r)
    try:
        run.validate_iss_result(r, 2026)
        assert False, f"FAILED (no raise): validate_iss_result rejects {why}"
    except run.ReportError:
        passed += 1


ok(run.validate_iss_result(screen, 2026) is None, "the real committed screen passes validation")
_refused(lambda r: r.update(concern="Severe"), "a concern outside the ISS Low/Medium/High vocab")
_refused(lambda r: r["measures"]["mom"].update(value=float("nan")), "a non-finite MOM value")
_refused(lambda r: r["measures"]["rda"].update(pay_pctile=140.0), "a pay percentile outside [0,100]")
_refused(lambda r: r.update(triggers_qualitative=not r["triggers_qualitative"]),
         "a qualitative-trigger flag inconsistent with the concern level")
_refused(lambda r: r["policy"].update(delta_from_prior=None), "a 2026 render with no policy-delta provenance")
_refused(lambda r: r["comparison_group"].update(group=[]), "an empty comparison group the group card would count")
# the render year must equal the screened year — a 2026 result validated as a 2025 render is false provenance
try:
    run.validate_iss_result(copy.deepcopy(screen), 2025)  # committed result is a 2026 screen
    assert False, "FAILED (no raise): validate_iss_result rejects a policy-year mismatch"
except run.ReportError:
    passed += 1

# ---- injection / public-safety: no script, no per-person ids, no real ticker / employer leakage ----
ok("<script" not in page, "no <script> in the dashboard")
ok(not re.search(r"\bE-\d{4}\b", page) and not re.search(r"\bC-\d{4}\b", page), "no per-person ids appear")
ok(not ({"NOVA", "MTRX", "PULS", "JUNO", "AAPL", "MSFT", "LUMN"} & set(re.findall(r"\b[A-Z]{2,5}\b", page))),
   "no well-known real ticker is rendered")
for term in ("Contoso", "Initech", "sk-"):
    ok(term not in page, f"no '{term}' leakage")

# ---- governance: the engine the agent reads has no decisional / pay-setting mutator ----
for danger in ("recommend_pay", "set_pay", "approve", "finalize", "decide"):
    ok(not hasattr(iss, danger), f"the ISS engine has no '{danger}' method (read-only, recommend-only)")

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
    _real = run._load_iss
    run._load_iss = lambda: (_ for _ in ()).throw(FileNotFoundError("ISS inputs missing"))
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run.main([])
        ok(rc == 1 and not run.REPORT.exists(), "engine-unavailable fails closed (exit 1, no report)")
        ok(err.getvalue().strip().startswith("FAIL CLOSED:"), "one clean fail-closed line")
    finally:
        run._load_iss = _real
finally:
    run.OUT, run.REPORT, run.DIGEST = _orig

print(f"OK — {passed} ISS pay-screen checks passed "
      f"(Acme: {report['concern']} concern, {report['comparison_group']['n_group']} ISS peers, "
      f"{len(report['triggers'])} triggers).")
