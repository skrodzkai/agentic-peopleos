#!/usr/bin/env python3
"""Evals for the deterministic SVG chart toolkit. Run: python foundation/render/tests/test_charts.py

Proves every chart is deterministic (same inputs -> identical bytes, so a committed dashboard can be
byte-diffed), well-formed SVG, and escapes caller-supplied labels (no markup injection through a
metric name or group label).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from foundation.render import charts as c  # noqa: E402

passed = 0


def ok(cond, label):
    global passed
    assert cond, f"FAILED: {label}"
    passed += 1


def _svg_ok(s):
    return s.startswith("<svg") and s.rstrip().endswith("</svg>") and "viewBox" in s


# ---- every chart returns well-formed SVG ----
spark = c.sparkline([1, 3, 2, 5, 4])
beacon = c.percentile_strip(251, 94, 420, [(130, "25th"), (200, "median"), (300, "75th")], target=300, you_label="Acme")
wf = c.waterfall([("Begin", 219, "total"), ("Hires", 28, "add"), ("Exit", -41, "sub"), ("End", 196, "total")])
dal = c.dual_axis_line(["Q1", "Q2", "Q3"], [205, 230, 251], [219, 210, 196])
hist = c.histogram([3, 7, 12, 9, 4], ["<min", "a", "b", "c", ">max"], highlight={0: c.RED, 4: c.AMBER})
forest = c.forest_plot([{"group": "Women vs men", "adj": -1.2, "ci_lo": -2.5, "ci_hi": 0.1, "raw": -7.8},
                        {"group": "URM vs non-URM", "adj": 2.0, "ci_lo": 0.5, "ci_hi": 3.5, "raw": 4.0}])
nine = c.heatmap_9box([[4, 27, 24], [5, 63, 32], [9, 27, 5]])
diamond = c.org_diamond([("L7", 2, 5), ("L6", 9, 14), ("L5", 12, 31), ("L4", 12, 53), ("L3", 0, 58)])
for name, s in [("sparkline", spark), ("percentile_strip", beacon), ("waterfall", wf),
                ("dual_axis_line", dal), ("histogram", hist), ("forest_plot", forest),
                ("heatmap_9box", nine), ("org_diamond", diamond)]:
    ok(_svg_ok(s), f"{name} returns well-formed <svg>")
ok(c.CYAN in diamond and "#345a7d" in diamond, "org_diamond draws a manager core + IC flanks (two colors)")
ok(c.org_diamond([]).startswith("<svg"), "org_diamond([]) returns a clean empty SVG")
ok("<script" not in c.org_diamond([("</text><script>x</script>", 1, 1)]), "org_diamond escapes the level label")

# ---- determinism: identical inputs -> identical bytes ----
ok(c.waterfall([("Begin", 219, "total"), ("Hires", 28, "add"), ("Exit", -41, "sub"), ("End", 196, "total")]) == wf,
   "waterfall is deterministic")
ok(c.forest_plot([{"group": "Women vs men", "adj": -1.2, "ci_lo": -2.5, "ci_hi": 0.1, "raw": -7.8},
                  {"group": "URM vs non-URM", "adj": 2.0, "ci_lo": 0.5, "ci_hi": 3.5, "raw": 4.0}]) == forest,
   "forest_plot is deterministic")

# ---- escaping: a malicious label cannot inject markup ----
evil = "</text></svg><script>x</script>"
ok("<script>" not in c.forest_plot([{"group": evil, "adj": 1.0, "ci_lo": -1.0, "ci_hi": 3.0, "raw": 2.0}]),
   "forest_plot escapes the group label")
ok("<script>" not in c.percentile_strip(100, 0, 200, [(50, evil)], you_label=evil), "percentile_strip escapes labels")
ok("<script>" not in c.histogram([1, 2], [evil, evil]), "histogram escapes bin labels")
ok("<script>" not in c.waterfall([("Begin", 1, "total"), (evil, 1, "add"), ("End", 2, "total")]),
   "waterfall escapes step labels")

# ---- color is an attribute sink: a malicious color must NOT inject an SVG attribute ----
_evilcolor = "#fff' onload='alert(1)"
ok("onload" not in c.sparkline([1, 2, 3], color=_evilcolor), "sparkline allowlists the line color (no attr injection)")
ok("onload" not in c.histogram([1, 2], ["a", "b"], highlight={0: _evilcolor}),
   "histogram allowlists a highlight color (no attr injection)")
ok(c.RED in c.histogram([1, 2], ["a", "b"], highlight={0: c.RED}), "a legitimate palette color still passes through")

# ---- robustness: empty inputs return a well-formed empty SVG, never a crash ----
for name, fn in [("histogram", lambda: c.histogram([], [])), ("waterfall", lambda: c.waterfall([])),
                 ("forest_plot", lambda: c.forest_plot([])), ("sparkline", lambda: c.sparkline([]))]:
    s = fn()
    ok(s.startswith("<svg") and "NaN" not in s and "Infinity" not in s, f"{name}([]) returns a clean empty SVG (no crash/NaN)")

# ---- shape validation: mismatched/partial-empty/wrong-shape inputs fail with a controlled error ----
for label, fn in [("dual_axis_line len mismatch", lambda: c.dual_axis_line(["Q1", "Q2"], [1, 2, 3], [4, 5, 6])),
                  ("dual_axis_line partial-empty", lambda: c.dual_axis_line(["Q1"], [], [4])),
                  ("histogram len mismatch", lambda: c.histogram([1, 2, 3], ["a", "b"])),
                  ("histogram partial-empty", lambda: c.histogram([], ["a"])),
                  ("9-box non-3x3", lambda: c.heatmap_9box([[1, 2], [3, 4]])),
                  ("9-box non-list rows", lambda: c.heatmap_9box([1, 2, 3]))]:
    try:
        fn()
        ok(False, f"{label} should raise ValueError")
    except ValueError:
        ok(True, f"{label} raises a controlled ValueError (no fail-open, no raw TypeError)")
# only ALL-empty renders empty (partial-empty above raised)
ok(c.dual_axis_line([], [], []).startswith("<svg"), "dual_axis_line all-empty returns a clean empty SVG")
ok(c.histogram([], []).startswith("<svg"), "histogram all-empty returns a clean empty SVG")

# ---- def-id namespacing: explicit uids AND collision-safe defaults ----
a = c.percentile_strip(50, 0, 100, [(50, "mid")], uid="a")
bb = c.percentile_strip(50, 0, 100, [(50, "mid")], uid="b")
ok("a_track" in a and "b_track" in bb and "a_track" not in bb, "explicit uid namespaces the ids (no collision)")
# two DEFAULT strips with different content get different auto-ids (the footgun fix)
d1 = c.percentile_strip(50, 0, 100, [(50, "m")])
d2 = c.percentile_strip(75, 0, 100, [(50, "m")])
import re as _re  # noqa: E402
id1 = _re.search(r"id='(ps[0-9a-f]+)_track'", d1).group(1)
id2 = _re.search(r"id='(ps[0-9a-f]+)_track'", d2).group(1)
ok(id1 != id2, "two DEFAULT percentile strips with different data get different auto-ids (collision-safe default)")
ok(c.percentile_strip(50, 0, 100, [(50, "m")]) == d1, "the auto-id is deterministic for identical inputs")
# a malicious uid is sanitized to a safe id charset (no markup/quote breakout)
mal = c.percentile_strip(50, 0, 100, [(50, "x")], uid="a'/><script>onload=")
ok("<script" not in mal and "onload=" not in mal, "a malicious uid injects no tag or event-handler attribute")
ok("id='ascriptonload_track'" in mal,
   "the malicious uid is sanitized to exactly [A-Za-z0-9_-] ('ascriptonload' — quote/markup stripped)")

# ---- a couple of semantics: forest plot flags a CI that clears 0 in the risk color ----
ok(c.RED in forest, "forest_plot draws a significant (CI-excludes-0) row in the risk color")
ok("_track'" in beacon and "url(#ps" in beacon, "percentile_strip paints the (auto-id) gradient track")

print(f"OK — {passed} chart-toolkit checks passed.")
