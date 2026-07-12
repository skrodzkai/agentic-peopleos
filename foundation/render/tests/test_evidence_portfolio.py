#!/usr/bin/env python3
"""Focused checks for the declarative portfolio evidence adapter."""
import sys
import tempfile
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
ok({"sbc-forecasting", "executive-comp-benchmarking"}.isdisjoint(portfolio.SPECS),
   "domain-rich reference verticals do not fall back to the portfolio adapter")
inventory = portfolio.portfolio_inventory(REPO)
managed_agents = set(portfolio.SPECS) | set(portfolio.REFERENCE_VERTICALS)
ok({agent for agent, _report, _digest in inventory} == managed_agents,
   "discovered artifacts exactly match specs plus reference verticals")
ok(len(inventory) == 18, "exactly eighteen managed dashboard/digest pairs are discovered")

with tempfile.TemporaryDirectory() as tmp:
    tmp_repo = Path(tmp)
    for agent_id in managed_agents:
        output = tmp_repo / "examples" / agent_id / "output"
        output.mkdir(parents=True)
        (output / "report.sample.html").write_text("report", encoding="utf-8")
        (output / ("committee-digest.sample.md" if agent_id == "retention-risk"
                   else "day1-digest.sample.md")).write_text("digest", encoding="utf-8")
    ok(len(portfolio.portfolio_inventory(tmp_repo)) == 18,
       "complete discovered inventory validates")
    (tmp_repo / "examples" / "ta-reporting" / "output" / "report.sample.html").unlink()
    try:
        portfolio.portfolio_inventory(tmp_repo)
        ok(False, "a deleted report must not shrink the inventory")
    except portfolio.PortfolioEvidenceError:
        ok(True, "a deleted report fails exact inventory reconciliation")
    rogue = tmp_repo / "examples" / "rogue-dashboard" / "output"
    rogue.mkdir(parents=True)
    (rogue / "report.sample.html").write_text("report", encoding="utf-8")
    (rogue / "day1-digest.sample.md").write_text("digest", encoding="utf-8")
    try:
        portfolio.portfolio_inventory(tmp_repo)
        ok(False, "an uncatalogued report must not join the inventory")
    except portfolio.PortfolioEvidenceError:
        ok(True, "an uncatalogued report fails exact inventory reconciliation")

for agent_id, spec in portfolio.SPECS.items():
    ok(len(spec.source_paths) >= 1,
       "%s declares at least one concrete source" % agent_id)
    ok(callable(spec.claim_factory), "%s has an explicit claim factory" % agent_id)
    for source_path in spec.source_paths:
        ok((REPO / source_path).is_file(), "%s source exists: %s" % (agent_id, source_path))

claims = [
    portfolio.ClaimSpec("claim.test.alpha", "Alpha is 42.", 42, "42", "count", "42", "42"),
    portfolio.ClaimSpec("claim.test.beta", "Beta is hostile.", 7, "7", "count",
                        "7 </button><script>bad()</script>", "beta seven is 7"),
]
page = ("<!doctype html><html><head><style>.n{width:42px}</style></head><body>"
        "<svg><text>42</text></svg><p>Alpha 42</p>"
        "<p>7 &lt;/button&gt;&lt;script&gt;bad()&lt;/script&gt;</p></body></html>")
marked = portfolio._annotate_html(page, claims)
ok("<svg><text>42</text></svg>" in marked, "annotator never inserts HTML buttons into SVG")
ok("width:42px" in marked, "annotator never treats CSS text as a metric")
ok(marked.count("data-evidence-id=") == 2, "each declared HTML claim gets exactly one trigger")
ok("<script>bad()</script>" not in marked, "a hostile declared anchor stays escaped")
ok("data-evidence-id='claim.test.beta'" in marked, "hostile-looking text remains traceable")

# All spans are selected from the original text before any trigger markup is inserted. This is the
# regression for the public aria-label/nested-button corruption Fable found in the first stack.
rescan_claims = [
    portfolio.ClaimSpec("claim.test.answer", "Alpha label includes 2.", 42, "42", "count", "42", "42"),
    portfolio.ClaimSpec("claim.test.two", "Two is 2.", 2, "2", "count", "2", "2"),
]
rescan_page = portfolio._annotate_html(
    "<html><body><p>Answer 42</p><p>Visible two: 2</p></body></html>", rescan_claims)
ok("aria-label='Alpha label includes 2.'" in rescan_page,
   "a later short claim never rescans an earlier trigger attribute")
ok(rescan_page.count("data-evidence-id=") == 2 and "aria-label='Alpha label includes <button" not in rescan_page,
   "annotation planning cannot create nested markup inside aria-label")

short_two = portfolio.ClaimSpec("claim.test.two", "Two is 2.", 2, "2", "count", "2", "2")
try:
    portfolio._annotate_html("<html><body><p>As of 2026.</p></body></html>", [short_two])
    ok(False, "a bare short number must not match inside a date")
except portfolio.PortfolioEvidenceError:
    ok(True, "numeric boundaries prevent a claim for 2 from attaching to 2026")
try:
    portfolio._annotate_html("<html><body><p>2</p><p>2</p></body></html>", [short_two])
    ok(False, "ambiguous HTML anchors must not pick the first substring")
except portfolio.PortfolioEvidenceError:
    ok(True, "ambiguous HTML anchors fail closed")
same_span = [
    portfolio.ClaimSpec("claim.test.first", "First 42.", 42, "42", "count", "42", "42"),
    portfolio.ClaimSpec("claim.test.second", "Second 42.", 42, "42", "count", "42", "42"),
]
try:
    portfolio._annotate_html("<html><body><p>Only 42</p></body></html>", same_span)
    ok(False, "two claims must not own the same visible span")
except portfolio.PortfolioEvidenceError:
    ok(True, "overlapping annotation plans fail closed without tuple-comparison errors")
try:
    portfolio._annotate_digest("As of 2026.\n", [short_two])
    ok(False, "a digest claim for 2 must not attach to a date")
except portfolio.PortfolioEvidenceError:
    ok(True, "digest numeric boundaries prevent date-line corruption")
ok(portfolio._bounded_occurrences("Change: -124", "-124") == [8],
   "a signed value remains matchable after a real separator")
ok(portfolio._bounded_occurrences("team-124", "-124") == [],
   "a signed value cannot match inside a hyphenated identifier")
ok(portfolio._bounded_occurrences("team_124", "124") == [],
   "a numeric value cannot match inside an underscored identifier")
ok(portfolio.SPECS["pay-equity"].source_paths == (portfolio.WORKERS,),
   "pay-equity lineage names only the workers dataset the engine reads")

digest = "Alpha **42**.\nThe beta seven is 7 and illustrative.\n"
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
