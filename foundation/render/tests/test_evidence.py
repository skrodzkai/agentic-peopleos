#!/usr/bin/env python3
"""Evals for the evidence-aware renderer and coverage gate."""
import base64
import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from core import evidence as core_evidence  # noqa: E402
from foundation.render import dashboard  # noqa: E402
from foundation.render import evidence  # noqa: E402


passed = 0


def ok(condition, label):
    global passed
    assert condition, "FAILED: " + label
    passed += 1


def fixture(artifact_type="dashboard"):
    builder = core_evidence.EvidenceBuilder(
        "artifact.render-test", "agent.render-test", "Evidence Renderer Test", artifact_type,
        "2026-06-30", "FY2026", {"value": 42})
    builder.source(**core_evidence.canonical_record_snapshot(
        "source.test", "Hostile </script><script>window.PWN=1</script>", "dataset",
        "https://example.com/evidence", "v1", "2026-06-30", {"value": 42}, "public"))
    builder.transformation("transform.test", "Test transform", "v1", "tests.fixture",
                           "Return the fixture value")
    builder.check("check.test", "Fixture check", "passed", "tests.fixture", "Fixture reconciles",
                  ["source.test"])
    builder.caveat("caveat.test", "warning", "Illustrative value")
    builder.claim("claim.alpha", "Alpha is 42.", 42, "42", "count", "FY2026", "2026-06-30",
                  ["source.test"], "transform.test", ["check.test"])
    builder.claim("claim.beta", "Hostile </script><script>window.PWN=1</script>", 7, "7", "count",
                  "FY2026", "2026-06-30", ["source.test"], "transform.test", ["check.test"],
                  status="caveated", caveat_ids=["caveat.test"])
    builder.claim("claim.support", "Supporting value is 2.", 2, "2", "count", "FY2026", "2026-06-30",
                  ["source.test"], "transform.test", ["check.test"], material=False)
    return builder.build()


manifest = fixture()

# Evidence-aware values escape display text and validate stable ids.
marked = evidence.trigger("<b>42</b>", "claim.alpha")
ok("<b>42</b>" not in marked and "&lt;b&gt;42&lt;/b&gt;" in marked, "trigger escapes its display value")
ok("data-evidence-id='claim.alpha'" in marked and "aria-haspopup='dialog'" in marked,
   "trigger is identifiable and dialog-accessible")
try:
    evidence.trigger("42", "BAD ID")
    ok(False, "bad evidence id must be rejected")
except evidence.EvidenceRenderError:
    ok(True, "bad evidence id is rejected")

# Shared KPI/table/bar renderers preserve evidence objects rather than stringifying them.
kpi = dashboard.kpi_cards([{"value": evidence.value("42", "claim.alpha"), "label": "Alpha"}])
table = dashboard.data_table(["Metric", "Value"], [["Alpha", evidence.value("42", "claim.alpha")]])
bars = dashboard.bars([{"label": "Alpha", "value": evidence.value("42", "claim.alpha", raw=42), "max": 100}])
for output, label in ((kpi, "KPI"), (table, "table"), (bars, "bar")):
    ok("data-evidence-id='claim.alpha'" in output, "shared %s renderer emits the evidence trigger" % label)

# A scope gives presentation/supporting values a traceable parent without inventing axis-tick claims.
scoped = evidence.scope("<svg></svg>", ["claim.alpha", "claim.support"])
ok("data-evidence-scope='claim.alpha claim.support'" in scoped and "Trace this view" in scoped,
   "scope carries stable claim ids and an explicit button")

# Coverage is fail-closed on missing material claims and dangling references.
base = ("<html><head></head><body>" + evidence.trigger("42", "claim.alpha")
        + evidence.trigger("7", "claim.beta")
        + evidence.scope("<div>2</div>", ["claim.support"]) + "</body></html>")
ok(evidence.coverage_violations(base, manifest, require_shell=False) == [],
   "all material claims referenced before decoration")
missing = base.replace(evidence.trigger("7", "claim.beta"), "7")
ok(any("claim.beta" in v and "not referenced" in v
       for v in evidence.coverage_violations(missing, manifest, require_shell=False)),
   "missing material claim fails coverage")
dangling = base.replace("claim.support", "claim.missing")
ok(any("missing claim 'claim.missing'" in v
       for v in evidence.coverage_violations(dangling, manifest, require_shell=False)),
   "dangling rendered claim fails coverage")
wrong_value = base.replace(evidence.trigger("42", "claim.alpha"),
                           evidence.trigger("43", "claim.alpha"))
ok(any("expected display_value" in v for v in evidence.coverage_violations(
    wrong_value, manifest, require_shell=False)),
   "a trigger on the wrong visible value fails coverage")
attribute_splice = ("<html><head></head><body><button type='button' class='evidence-trigger' "
                    "data-evidence-id='claim.alpha' aria-label='Open <button data-evidence-id=claim.beta>'>"
                    "42</button>" + evidence.trigger("7", "claim.beta") + "</body></html>")
ok(any("markup appears inside" in v for v in evidence.coverage_violations(
    attribute_splice, manifest, require_shell=False)),
   "evidence markup spliced into an attribute is rejected")
nested = ("<html><head></head><body><a href='#'>" + evidence.trigger("42", "claim.alpha") + "</a>"
          + evidence.trigger("7", "claim.beta") + "</body></html>")
ok(any("nested interactive" in v for v in evidence.coverage_violations(
    nested, manifest, require_shell=False)),
   "an evidence button nested inside a link is rejected")
duplicate_attr = base.replace("data-evidence-id='claim.alpha'",
                              "data-evidence-id='claim.alpha' data-evidence-id='claim.beta'", 1)
ok(any("duplicate attributes" in v for v in evidence.coverage_violations(
    duplicate_attr, manifest, require_shell=False)),
   "duplicate trigger attributes are rejected")
unclosed = base.replace("</button>", "", 1)
ok(any("unclosed button" in v or "unclosed interactive" in v
       for v in evidence.coverage_violations(unclosed, manifest, require_shell=False)),
   "unclosed evidence controls are rejected")
malformed_manifest = {"schema_version": "1.0", "artifact": [], "claims": None}
ok(evidence.coverage_violations(base, malformed_manifest, require_shell=False)[0].startswith(
    "invalid evidence manifest:"), "coverage returns a controlled violation for malformed manifests")
ok(evidence.coverage_report(base, malformed_manifest) == {
    "material": 0, "material_referenced": 0, "all_claims": 0,
    "all_referenced": 0, "unknown_references": 0},
   "coverage reporting cannot crash after manifest validation fails")

# Decoration is deterministic, accessible, and injection-safe. Manifest strings are base64, then the
# runtime writes them with textContent; they never appear as executable HTML in the committed artifact.
page = evidence.decorate_page(base, manifest)
ok(page == evidence.decorate_page(base, manifest), "evidence decoration is byte-deterministic")
ok("id='evidence-summary'" in page and "id='evidence-drawer'" in page and "aria-modal='true'" in page,
   "page carries a summary control and accessible modal drawer")
ok("id='evidence-manifest'" in page and "data-encoding='base64'" in page,
   "page embeds an inert base64 manifest")
ok("Hostile </script><script>window.PWN=1</script>" not in page,
   "hostile manifest text cannot close the inert script block")
ok(evidence.coverage_violations(page, manifest) == [], "decorated page passes strict shell + coverage checks")
report = evidence.coverage_report(page, manifest)
ok(report == {"material": 2, "material_referenced": 2, "all_claims": 3, "all_referenced": 3,
              "unknown_references": 0}, "coverage report distinguishes material and supporting claims")

encoded = re.search(r"id='evidence-manifest'[^>]*>([^<]+)</script>", page).group(1)
decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
ok(decoded == manifest, "embedded graph round-trips exactly")
ok(evidence.extract_embedded_manifest(page) == manifest, "runtime extractor returns the embedded graph")
duplicate_json = '{"schema_version":"1.0","schema_version":"1.0"}'
duplicate_page = page.replace(encoded, base64.b64encode(duplicate_json.encode("utf-8")).decode("ascii"))
try:
    evidence.extract_embedded_manifest(duplicate_page)
    ok(False, "duplicate keys in an embedded graph must be rejected")
except evidence.EvidenceRenderError:
    ok(True, "embedded graph decoding preserves duplicate-key rejection")
tampered_page = page.replace(encoded, base64.b64encode(core_evidence.canonical(
    {**manifest, "schema_version": "9.9"}).encode("utf-8")).decode("ascii"))
ok(any("differs" in v for v in evidence.embedded_manifest_violations(tampered_page, manifest)),
   "embedded graph drift from the sidecar is rejected")
ok(core_evidence.evidence_hash(decoded) in page, "drawer shell binds the detached evidence hash")
for needle in ("Escape", "textContent", "noopener noreferrer", "data-evidence-scope-open",
               "hashed input(s)", "attestation"):
    ok(needle in page, "runtime includes %s behavior" % needle)

try:
    evidence.decorate_page(page, manifest)
    ok(False, "double decoration must be rejected")
except evidence.EvidenceRenderError:
    ok(True, "double decoration is rejected")
try:
    evidence.decorate_page("<p>fragment</p>", manifest)
    ok(False, "incomplete document must be rejected")
except evidence.EvidenceRenderError:
    ok(True, "incomplete document is rejected")
try:
    evidence.decorate_page(nested, manifest)
    ok(False, "invalid interactive markup must not decorate")
except evidence.EvidenceRenderError:
    ok(True, "decoration rejects structurally invalid evidence markup")
bad_manifest = copy.deepcopy(manifest)
bad_manifest["claims"][0]["source_ids"] = ["source.missing"]
try:
    evidence.decorate_page(base, bad_manifest)
    ok(False, "invalid manifest must not decorate")
except evidence.EvidenceRenderError:
    ok(True, "invalid manifest is rejected before embedding")

# Markdown uses invisible, machine-readable references and the same coverage policy.
digest_manifest = fixture("digest")
digest = evidence.markdown_refs("Alpha is 42 and support is 2.", [
    evidence.reference("42", "claim.alpha"), evidence.reference("2", "claim.support")]) + "\n" + \
         evidence.markdown_refs("Beta is 7.", [evidence.reference("7", "claim.beta")])
ok(evidence.coverage_violations(digest, digest_manifest) == [], "Markdown material claims are covered")
ok("<!-- evidence:claim.alpha -->" in digest, "Markdown reference is grep-friendly")
misattached = "Alpha is 4<!-- evidence:claim.alpha -->2.\nBeta is 7<!-- evidence:claim.beta -->."
ok(any("not attached to display_value" in v for v in evidence.coverage_violations(
    misattached, digest_manifest)),
   "a Markdown marker attached to a substring instead of the exact display fails coverage")
ok(any("claim.beta" in v for v in evidence.coverage_violations(
    evidence.markdown_refs("Alpha is 42.", [evidence.reference("42", "claim.alpha")]), digest_manifest)),
   "Markdown missing a material claim fails coverage")

print("OK — %d evidence-renderer checks passed." % passed)
