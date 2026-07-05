#!/usr/bin/env python3
"""Evals for the glass-lewis-screen agent: validation + render invariants + illustrative/honesty labeling +
fail-closed + the publish gate. The agent renders the engine's 5-test concern scorecard; it must never claim
to BE the advisors."""
import copy
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EX = HERE.parent
REPO = EX.parents[1]
for p in (EX, REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import run as R  # noqa: E402
from foundation.compute import glass_lewis_screen as G  # noqa: E402
from foundation.compute.peers import real_peer_identifiers  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


# ---- build + validate ------------------------------------------------------------------------------------
result = G.compute()
report = R.build_report(result)
ok(report["gl"]["concern"] == "Low" and report["iss"]["concern"] == "Medium", "committed data: GL Low, ISS Medium")
ok(report["syn"]["verdict"] == "ISS-ONLY FLAG", "verdict is ISS-ONLY FLAG (the advisors diverge)")
ok(report["band"][0] < report["band"][1], "say-on-pay band is an ordered range")

html = R.render_html(report)
digest = R.render_digest(report)

# ---- render invariants -----------------------------------------------------------------------------------
ok(html.startswith("<!doctype html>") and html.rstrip().endswith("</html>"), "renders a full HTML doc")
for token in ("ISS-ONLY FLAG", "Glass Lewis", "Medium", "Low", "scorecard", "composite"):
    ok(token in html, f"HTML surfaces {token!r}")
for t in G._TEST_LABELS.values():             # all five test names render in the scorecard
    ok(t in html, f"HTML surfaces the test {t!r}")
ok("war" in html and "trow" in html, "the war-room cards + the 5-test scorecard render")
# the say-on-pay RESPONSIVENESS factor + the STI-relative-to-target framing render
ok("Say-on-pay responsiveness" in html and "engagement threshold" in html,
   "HTML surfaces the say-on-pay responsiveness factor")
ok(f"{report['gl']['say_on_pay']['prior_support_pct']:.1f}%" in html,
   "HTML shows the subject's prior say-on-pay support %")
ok("relative to target" in html, "HTML notes STI is measured relative to target, not raw dollars")
ok("responsiveness" in digest.lower(), "digest surfaces the say-on-pay responsiveness factor")

# ---- illustrative / honesty labeling (must never claim to BE the advisors) --------------------------------
low = html.lower()
ok("illustrative reconstruction" in low, "HTML labels the models illustrative reconstructions")
ok("not glass lewis or iss output" in low, "HTML states it is not the advisors' output")
ok("not affiliated" in low, "HTML disclaims affiliation with either firm")
ok("synthetic" in low, "HTML labels the universe synthetic")
ok("not a vote forecast" in low, "HTML labels the support band as not a vote forecast")
ok("legacy a" in low and "retired" in low, "HTML notes the legacy A-F grade is retired")
ok("not a vote forecast" in digest.lower() and "illustrative" in digest.lower(), "digest carries the disclaimers")
for overclaim in ("official glass lewis", "actual iss score", "glass lewis's grade for"):
    ok(overclaim not in low, f"no overclaim: {overclaim!r}")

# ---- public-safety: no real peer identity, no email-like PII ---------------------------------------------
_real_tk, real_names = real_peer_identifiers(require=True)
ok(not any(nm and nm.lower() in low for nm in real_names), "no real peer NAME appears in the rendered HTML")
ok(not re.search(r"[\w.]+@[\w.]+\.[a-z]{2,}", html), "no email-like PII in the HTML")

# ---- determinism ---------------------------------------------------------------------------------------
ok(R.render_html(R.build_report(G.compute())) == html, "render is deterministic")


# ---- fail-closed: a corrupted engine result is refused ---------------------------------------------------
def _raises_report_error(mutate, label):
    global passed
    bad = copy.deepcopy(result)
    mutate(bad)
    try:
        R.build_report(bad)
        assert False, f"FAILED (no raise): {label}"
    except R.ReportError:
        passed += 1


_raises_report_error(lambda b: b["synthesis"].__setitem__("verdict", "BOGUS"), "unknown verdict refused")
_raises_report_error(lambda b: b["gl"].__setitem__("concern", "Zzz"), "unknown GL concern refused")
_raises_report_error(lambda b: b["gl"].__setitem__("composite_score", 10.0),
                     "a composite/concern mismatch refused (10 is Severe, not the reported Low)")
_raises_report_error(lambda b: b["synthesis"].__setitem__("say_on_pay_support_band_pct", [90.0, 50.0]),
                     "unordered say-on-pay band refused")
_raises_report_error(lambda b: b["iss"]["subject"].__setitem__("ticker", "OTHER"), "ISS/GL subject mismatch refused")
_raises_report_error(lambda b: b["synthesis"]["contrast"].__setitem__("gl_composite", 0.0),
                     "a contrast composite that disagrees with the GL score refused")
_raises_report_error(lambda b: b["gl"]["tests"][0].__setitem__("score", float("nan")),
                     "a non-finite test score refused")
_raises_report_error(lambda b: b["gl"]["counterfactuals"].__setitem__("tsr_only_score", float("nan")),
                     "a non-finite counterfactual score refused")
_raises_report_error(lambda b: b["iss"]["measures"]["mom"].__setitem__("value", float("nan")),
                     "a non-finite ISS MOM value (rendered in the card) refused")
_raises_report_error(lambda b: b["gl"]["say_on_pay"].__setitem__("prior_support_pct", 150.0),
                     "a say-on-pay support % above 100 (rendered) refused")
_raises_report_error(lambda b: b["gl"]["say_on_pay"].__setitem__("responsiveness", "maybe"),
                     "an unknown say-on-pay responsiveness category refused")
_raises_report_error(lambda b: b["gl"]["say_on_pay"].__setitem__("engage_threshold_pct", float("nan")),
                     "a non-finite say-on-pay engagement threshold (rendered as ~nan%) refused")
_raises_report_error(lambda b: b["gl"]["say_on_pay"].__setitem__("below_threshold", True),
                     "a below_threshold flag inconsistent with the support-vs-threshold numbers refused "
                     "(would render a false, self-contradicting sentence)")

# the digest must NEUTRALIZE Markdown in the engine's free-text driver (HTML-escaping alone wouldn't)
_bad = copy.deepcopy(result)
_bad["synthesis"]["divergence_driver"] = "**APPROVED** [x](http://evil.example)"
_dig = R.render_digest(R.build_report(_bad))
ok("**APPROVED**" not in _dig and "](http://evil.example)" not in _dig,
   "digest neutralizes Markdown syntax in the engine's free-text divergence driver")

# ---- publish gate --------------------------------------------------------------------------------------
ok(R.main(["--publish"]) == 2, "publish without a named approver is refused")
ok(R.main(["--publish", "--approved-by", "Bad\nName"]) == 2, "control chars in approver are refused")
ok(R.main([]) == 0, "draft run succeeds")
pub = EX / "output" / "PUBLISHED.json"
ok(R.main(["--publish", "--approved-by", "Compensation Committee Chair"]) == 0 and pub.exists(),
   "publish with a named approver writes PUBLISHED.json")
ok(R.main(["--publish"]) == 2 and not pub.exists() and pub.with_name("PUBLISHED.json.stale").exists(),
   "a refused re-publish stales the prior PUBLISHED.json")
R.main([])                                                  # clears the .stale marker + regenerates draft artifacts
pub.unlink(missing_ok=True)                                 # leave the sample tree in its draft state

print(f"OK — {passed} glass-lewis agent checks passed "
      f"(GL {report['gl']['concern']} vs ISS {report['iss']['concern']} -> {report['syn']['verdict']}).")
