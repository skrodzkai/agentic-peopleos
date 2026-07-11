#!/usr/bin/env python3
"""Focused checks for the declarative portfolio evidence adapter."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from foundation import evidence_portfolio as portfolio  # noqa: E402
from foundation.render import evidence as evidence_render  # noqa: E402
from core import evidence as evidence_core  # noqa: E402


passed = 0


def ok(condition, label):
    global passed
    assert condition, "FAILED: " + label
    passed += 1


ok(len(portfolio.SPECS) == 16, "sixteen non-reference dashboards have explicit portfolio specs")
ok(len(portfolio.PORTFOLIO_OUTPUTS) == 18, "portfolio inventory includes all eighteen dashboards")
ok(len({agent for agent, _digest in portfolio.PORTFOLIO_OUTPUTS}) == 18,
   "portfolio inventory has no duplicate agent")
ok({"sbc-forecasting", "executive-comp-benchmarking"}.isdisjoint(portfolio.SPECS),
   "domain-rich reference verticals do not fall back to the portfolio adapter")

for agent_id, spec in portfolio.SPECS.items():
    ok(len(spec.source_paths) >= 1,
       "%s declares at least one concrete source" % agent_id)
    ok(callable(spec.claim_factory), "%s has an explicit claim factory" % agent_id)
    for source_path in spec.source_paths:
        ok((REPO / source_path).is_file(), "%s source exists: %s" % (agent_id, source_path))

claims = [
    portfolio.ClaimSpec("claim.test.alpha", "Alpha is 42.", 42, "42", "count", "42", "42"),
    portfolio.ClaimSpec("claim.test.beta", "Beta is hostile.", 7, "7", "count",
                        "</button><script>bad()</script>", "beta seven"),
]
page = ("<!doctype html><html><head><style>.n{width:42px}</style></head><body>"
        "<svg><text>42</text></svg><p>Alpha 42</p>"
        "<p>&lt;/button&gt;&lt;script&gt;bad()&lt;/script&gt;</p></body></html>")
marked = portfolio._annotate_html(page, claims)
ok("<svg><text>42</text></svg>" in marked, "annotator never inserts HTML buttons into SVG")
ok("width:42px" in marked, "annotator never treats CSS text as a metric")
ok(marked.count("data-evidence-id=") == 2, "each declared HTML claim gets exactly one trigger")
ok("<script>bad()</script>" not in marked, "a hostile declared anchor stays escaped")
ok("data-evidence-id='claim.test.beta'" in marked, "hostile-looking text remains traceable")

digest = "Alpha **42**.\nThe beta seven result is illustrative.\n"
marked_digest = portfolio._annotate_digest(digest, claims)
ok("<!-- evidence:claim.test.alpha -->" in marked_digest,
   "digest marker attaches to the line carrying alpha")
ok("<!-- evidence:claim.test.beta -->" in marked_digest,
   "digest marker attaches to the line carrying beta")
ok(evidence_render.referenced_claims(marked_digest, "digest") ==
   {"claim.test.alpha", "claim.test.beta"}, "digest references are machine-readable")

try:
    portfolio._annotate_html("<html><body>nothing</body></html>", claims[:1])
    ok(False, "missing HTML anchor must fail")
except portfolio.PortfolioEvidenceError:
    ok(True, "missing HTML anchor fails closed")

try:
    portfolio._annotate_digest("nothing\n", claims[:1])
    ok(False, "missing digest anchor must fail")
except portfolio.PortfolioEvidenceError:
    ok(True, "missing digest anchor fails closed")

ok(portfolio.sidecar_path(Path("report.sample.html")).name == "report.sample.evidence.json",
   "HTML sidecar naming is deterministic")
ok(portfolio.sidecar_path(Path("committee-digest.sample.md")).name ==
   "committee-digest.sample.evidence.json", "nonstandard digest name maps deterministically")
ok(tuple(path.name for path in portfolio.managed_outputs(
    Path("report.sample.html"), Path("day1-digest.sample.md"))) ==
   ("report.sample.html", "day1-digest.sample.md", "report.sample.evidence.json",
    "day1-digest.sample.evidence.json"), "artifact and evidence sidecars share one fail-closed lifecycle")
ok(len(portfolio.portfolio_artifacts(REPO)) == 36,
   "managed portfolio expands to eighteen report/digest pairs")
material_claims = 0
for artifact_path in portfolio.portfolio_artifacts(REPO):
    manifest = evidence_core.load_manifest(portfolio.sidecar_path(artifact_path))
    material_claims += sum(claim.get("material") is True for claim in manifest["claims"])
ok(material_claims == 216,
   "public 216-claim coverage statement reconciles to all committed portfolio manifests")

print("OK — %d portfolio-evidence checks passed." % passed)
