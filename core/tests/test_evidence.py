#!/usr/bin/env python3
"""Adversarial evals for the claims-to-evidence kernel."""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core import evidence as ev  # noqa: E402
from core import json_schema  # noqa: E402


passed = 0


def ok(condition, label):
    global passed
    assert condition, "FAILED: " + label
    passed += 1


ROOT = Path(tempfile.mkdtemp())
DATA = ROOT / "data" / "facts.csv"
DATA.parent.mkdir()
DATA.write_text("metric,value\nsbc,45.2\n", encoding="utf-8")


def valid_builder(reverse=False):
    builder = ev.EvidenceBuilder(
        artifact_id="artifact.sbc.2026q2",
        agent_id="agent.sbc-forecasting",
        title="SBC Forecast",
        artifact_type="dashboard",
        as_of="2026-06-30",
        period="FY2026",
        semantic_payload={"forecast": 45.2, "period": "FY2026"},
    )
    local = ev.repo_snapshot(DATA, ROOT, "source.sbc-data", "SBC facts", "dataset", "v1",
                             "2026-06-30", "synthetic")
    external = ev.canonical_record_snapshot(
        "source.policy", "Approved definition", "policy", "https://example.com/policy",
        "2026.1", "2026-06-30", {"metric": "sbc", "basis": "GAAP"}, "public")
    for source in ([external, local] if reverse else [local, external]):
        builder.source(**source)
    builder.transformation("transform.sbc-forecast.v1", "SBC forecast", "v1",
                           "foundation.compute.sbc_forecast.compute",
                           "Aggregate award expense under the stated assumptions")
    builder.assumption("assumption.forfeiture.v1", "Forfeiture rate", 5.0, "percent", "v1",
                       "illustrative", ["source.policy"])
    builder.check("check.sbc-reconcile", "SBC reconciliation", "passed",
                  "foundation.compute.tests.test_sbc_forecast", "Components tie to total",
                  ["source.sbc-data", "source.policy"])
    builder.caveat("caveat.illustrative", "warning", "The forward grant rate is illustrative")
    builder.claim(
        "claim.sbc-forecast", "FY2026 SBC expense is forecast at $45.2M.", 45_200_000,
        "$45.2M", "USD", "FY2026", "2026-06-30", ["source.sbc-data", "source.policy"],
        "transform.sbc-forecast.v1", ["check.sbc-reconcile"], metric_id="sbc_expense",
        status="caveated", assumption_ids=["assumption.forfeiture.v1"],
        caveat_ids=["caveat.illustrative"],
        change={
            "prior_claim_id": "claim.sbc-forecast@2026q1",
            "prior_value": 41_800_000,
            "prior_display_value": "$41.8M",
            "absolute": 3_400_000,
            "percent": 8.133971291866029,
            "comparability": "comparable",
            "drivers": [
                {"type": "business_change", "label": "New grants", "effect": 2_100_000, "unit": "USD"},
                {"type": "assumption_change", "label": "Updated assumptions", "effect": 1_300_000,
                 "unit": "USD"},
            ],
        },
    )
    return builder


manifest = valid_builder().build()
ok(ev.validate_manifest(manifest) == [], "a complete manifest validates")
ok(ev.coverage(manifest) == {"claims": 1, "material": 1, "supported": 0, "caveated": 1,
                             "blocked": 0, "traceable": 1}, "coverage distinguishes caveated traceability")
ok(ev.hash_json({"b": 2, "a": 1}) == ev.hash_json({"a": 1, "b": 2}),
   "canonical hashes ignore object insertion order")
try:
    ev.hash_json({1: "integer key", "1": "string key"})
    ok(False, "JSON hashing must reject key coercion")
except ev.EvidenceError:
    ok(True, "JSON hashing rejects non-string keys instead of collapsing them")
ok(ev.canonical(valid_builder().build()) == ev.canonical(valid_builder(reverse=True).build()),
   "builder sorts graph nodes for byte-stable output")

# The immediate support graph contains every referenced node and its detached evidence hash.
subgraph = ev.inspect_claim(manifest, "claim.sbc-forecast")
ok(subgraph["claim"]["display_value"] == "$45.2M", "claim inspection returns the claim")
ok({s["id"] for s in subgraph["sources"]} == {"source.sbc-data", "source.policy"},
   "claim inspection resolves sources")
ok(subgraph["transformation"]["id"] == "transform.sbc-forecast.v1",
   "claim inspection resolves the transformation")
ok(subgraph["checks"][0]["status"] == "passed" and subgraph["evidence_hash"] == ev.evidence_hash(manifest),
   "claim inspection carries checks and a detached hash")

# File provenance is canonical and source verification detects post-snapshot drift.
source = manifest["sources"][0] if manifest["sources"][0]["uri"].startswith("repo:") else manifest["sources"][1]
ok(source["uri"] == "repo:data/facts.csv", "repo source stores a canonical relative URI")
ok(ev.validate_manifest(manifest, root=ROOT, verify_sources=True) == [], "source bytes verify against the root")
DATA.write_text("metric,value\nsbc,99.9\n", encoding="utf-8")
ok(any("source hash mismatch" in v for v in ev.validate_manifest(manifest, root=ROOT, verify_sources=True)),
   "source mutation invalidates the evidence snapshot")
DATA.write_text("metric,value\nsbc,45.2\n", encoding="utf-8")

# Traversal and symlink escapes do not receive repo provenance.
OUTSIDE = Path(tempfile.mkdtemp()) / "outside.csv"
OUTSIDE.write_text("not,inside\n", encoding="utf-8")
try:
    ev.repo_snapshot(OUTSIDE, ROOT, "source.escape", "Escape", "dataset", "v1", "2026-06-30")
    ok(False, "outside path must be rejected")
except ev.EvidenceError:
    ok(True, "outside path is rejected")
link = ROOT / "data" / "escape.csv"
try:
    os.symlink(OUTSIDE, link)
    try:
        ev.repo_snapshot(link, ROOT, "source.symlink", "Symlink", "dataset", "v1", "2026-06-30")
        ok(False, "symlink escape must be rejected")
    except ev.EvidenceError:
        ok(True, "symlink escape is rejected")
except (OSError, NotImplementedError):
    pass

# Dangling references and global id reuse fail closed.
bad = copy.deepcopy(manifest)
bad["claims"][0]["source_ids"] = ["source.missing"]
ok(any("references missing source" in v for v in ev.validate_manifest(bad)),
   "missing source reference is rejected")
bad = copy.deepcopy(manifest)
bad["checks"][0]["id"] = bad["sources"][0]["id"]
bad["claims"][0]["check_ids"] = [bad["checks"][0]["id"]]
ok(any("node id" in v and "reused" in v for v in ev.validate_manifest(bad)),
   "node ids are globally unique across the graph")
bad = copy.deepcopy(manifest)
bad["claims"][0]["supporting_claim_ids"] = ["claim.sbc-forecast"]
ok(any("cannot support itself" in v for v in ev.validate_manifest(bad)),
   "a claim cannot support itself")

# A support graph must be acyclic and terminate at a real source.
bad = copy.deepcopy(manifest)
root_claim = bad["claims"][0]
root_claim["source_ids"] = []
root_claim["supporting_claim_ids"] = ["claim.support-a"]
support = copy.deepcopy(root_claim)
support.update({"id": "claim.support-a", "material": False,
                "supporting_claim_ids": [root_claim["id"]]})
bad["claims"].append(support)
issues = ev.validate_manifest(bad)
ok(any("supporting-claim cycle" in v for v in issues),
   "mutually supporting claims are rejected as a cycle")
ok(any("no source-grounded support path" in v for v in issues),
   "a circular support graph cannot manufacture traceability")
bad = copy.deepcopy(manifest)
root_claim = bad["claims"][0]
root_claim["source_ids"] = []
root_claim["supporting_claim_ids"] = ["claim.support-leaf"]
support = copy.deepcopy(root_claim)
support.update({"id": "claim.support-leaf", "material": False, "supporting_claim_ids": []})
bad["claims"].append(support)
ok(any("no source-grounded support path" in v for v in ev.validate_manifest(bad)),
   "an acyclic but source-free support chain is not traceable")

# A material claim cannot hide behind a failed/not-run check.
bad = copy.deepcopy(manifest)
bad["checks"][0]["status"] = "failed"
ok(any("relies on check" in v for v in ev.validate_manifest(bad)),
   "supported/caveated claim rejects a failed check")
bad = copy.deepcopy(manifest)
bad["claims"][0]["check_ids"] = []
ok(any("needs at least one check" in v for v in ev.validate_manifest(bad)),
   "material claim without checks is rejected")
bad = copy.deepcopy(manifest)
bad["claims"][0]["source_ids"] = ["source.sbc-data"]
bad["claims"][0]["assumption_ids"] = []
bad["checks"][0]["source_ids"] = ["source.policy"]
ok(any("no source in the claim support closure" in v for v in ev.validate_manifest(bad)),
   "a producer check cannot cite an unrelated hashed source")
bad = copy.deepcopy(manifest)
bad["checks"][0]["source_ids"] = []
ok(any("hashed source reference" in v for v in ev.validate_manifest(bad)),
   "a passed producer-attested check must bind to hashed inputs")
bad = copy.deepcopy(manifest)
bad["checks"][0]["attestation"] = "self-certified"
ok(any("attestation" in v for v in ev.validate_manifest(bad)),
   "check assurance level is a closed enum")

# Blocking caveats are executable policy, not inert metadata.
bad = copy.deepcopy(manifest)
bad["caveats"].append({"id": "caveat.blocker", "severity": "blocking",
                       "text": "Independent validation is incomplete"})
bad["claims"][0]["caveat_ids"].append("caveat.blocker")
ok(any("blocking caveat" in v and "must be blocked" in v for v in ev.validate_manifest(bad)),
   "a blocking caveat forces the linked material claim to blocked")

# Change arithmetic and decomposition are verified, not trusted as labels.
bad = copy.deepcopy(manifest)
bad["claims"][0]["change"]["absolute"] = 3_300_000
ok(any("absolute does not equal" in v for v in ev.validate_manifest(bad)),
   "current-minus-prior mismatch is rejected")
bad = copy.deepcopy(manifest)
bad["claims"][0]["change"]["drivers"][0]["effect"] = 2_000_000
ok(any("driver effects do not reconcile" in v for v in ev.validate_manifest(bad)),
   "change drivers must reconcile")
bad = copy.deepcopy(manifest)
bad["claims"][0]["change"]["prior_value"] = "41800000"
bad["claims"][0]["change"]["prior_display_value"] = "41800000"
ok(any("comparable change requires numeric" in v for v in ev.validate_manifest(bad)),
   "a string prior value cannot bypass comparable change arithmetic")
bad = copy.deepcopy(manifest)
bad["claims"][0]["change"]["drivers"][0]["unit"] = "usd"
ok(any("must exactly match" in v for v in ev.validate_manifest(bad)),
   "driver units cannot disappear from reconciliation through case mismatch")
bad = copy.deepcopy(manifest)
bad["claims"][0]["change"]["percent"] = 99.0
ok(any("percent does not equal" in v for v in ev.validate_manifest(bad)),
   "reported percent change must reconcile")
bad = copy.deepcopy(manifest)
bad["claims"][0]["change"]["absolute"] = 3_400_000.000001
ok(any("absolute does not equal" in v for v in ev.validate_manifest(bad)),
   "change arithmetic is exact and does not admit an isclose epsilon")
bad = copy.deepcopy(manifest)
bad["claims"][0]["change"]["comparability"] = "not_comparable"
ok(any("non-comparable change must leave" in v for v in ev.validate_manifest(bad)),
   "non-comparable changes cannot publish numeric deltas")
bad = copy.deepcopy(manifest)
claim = bad["claims"][0]
claim["value"] = 10
claim["display_value"] = "10"
claim["change"].update({"prior_value": 0, "prior_display_value": "0", "absolute": 10,
                        "percent": 100, "drivers": [
                            {"type": "business_change", "label": "Launch", "effect": 10, "unit": "USD"}
                        ]})
ok(any("percent must be null" in v for v in ev.validate_manifest(bad)),
   "zero-prior comparisons cannot invent a percent change")

# Approved/published artifacts require an approved review covering every material claim.
bad = copy.deepcopy(manifest)
bad["artifact"]["status"] = "approved"
ok(any("without approved review" in v for v in ev.validate_manifest(bad)),
   "approved artifact without claim-level approval is rejected")
approved = valid_builder()
approved.artifact["status"] = "approved"
approved.review("review.comp-committee", "approved", "Compensation Committee Chair",
                "2026-07-10T18:00:00Z", ["claim.sbc-forecast"], "Reviewed synthetic reference output")
approved_manifest = approved.build()
ok(ev.validate_manifest(approved_manifest) == [], "approved review can cover every material claim")
bad = copy.deepcopy(approved_manifest)
bad["reviews"].append({"id": "review.comp-committee-recheck", "status": "rejected",
                       "actor_role": "Compensation Committee Chair",
                       "reviewed_at": "2026-07-11T18:00:00Z",
                       "claim_ids": ["claim.sbc-forecast"],
                       "notes": "Newer review rejected the claim"})
ok(any("without approved review" in v for v in ev.validate_manifest(bad)),
   "a newer rejection supersedes a stale approval")
bad = copy.deepcopy(approved_manifest)
bad["reviews"].append({"id": "review.conflict", "status": "rejected",
                       "actor_role": "Independent Reviewer",
                       "reviewed_at": "2026-07-10T18:00:00Z",
                       "claim_ids": ["claim.sbc-forecast"], "notes": "Same-time conflict"})
ok(any("conflicting latest reviews" in v for v in ev.validate_manifest(bad)),
   "conflicting reviews at the latest timestamp fail closed")

# Shape violations remain ordinary validation results; post-validation graph checks never raise.
for field in ("source_ids", "assumption_ids", "check_ids", "supporting_claim_ids", "caveat_ids"):
    bad = copy.deepcopy(manifest)
    bad["claims"][0][field] = 7
    ok(any(field in v and "list" in v for v in ev.validate_manifest(bad)),
       "malformed %s is a controlled violation" % field)
bad = copy.deepcopy(approved_manifest)
bad["reviews"][0]["claim_ids"] = 7
ok(any("claim_ids must be a list" in v for v in ev.validate_manifest(bad)),
   "malformed review coverage cannot crash approval validation")
bad = copy.deepcopy(approved_manifest)
bad["reviews"].append({"id": "review.bad-time", "status": "rejected",
                       "actor_role": "Independent Reviewer", "reviewed_at": 7,
                       "claim_ids": ["claim.sbc-forecast"], "notes": "Malformed timestamp"})
ok(any("UTC timestamp" in v for v in ev.validate_manifest(bad)),
   "mixed timestamp types cannot crash latest-review selection")
bad = copy.deepcopy(manifest)
bad["claims"][0]["value"] = 10 ** 10000
bad["claims"][0]["display_value"] = "1" + ("0" * 10000)
bad["claims"][0]["change"] = None
ok(any("display_value" in v for v in ev.validate_manifest(bad)),
   "arbitrarily large JSON integers produce violations without float overflow")
try:
    ev.EvidenceBuilder("artifact.valid", "agent.valid", "Valid", "dashboard", "2026-06-30",
                       "FY2026", {}).source(id=None)
    ok(False, "builder must reject a missing node id before sorting")
except ev.EvidenceError:
    ok(True, "builder rejects a missing node id with a controlled error")

# Duplicate keys, unknown fields, malformed hashes, and non-finite numbers are controlled errors.
dup_path = ROOT / "duplicate.json"
dup_path.write_text('{"schema_version":"1.0","schema_version":"9.9"}', encoding="utf-8")
try:
    ev.load_manifest(dup_path)
    ok(False, "duplicate JSON key must be rejected")
except ev.EvidenceError:
    ok(True, "duplicate JSON key is rejected")
bad = copy.deepcopy(manifest)
bad["artifact"]["surprise"] = True
ok(any("unknown field" in v for v in ev.validate_manifest(bad)), "unknown field is rejected")
bad = copy.deepcopy(manifest)
bad["sources"][0]["content_hash"] = "sha256:nope"
ok(any("content_hash" in v for v in ev.validate_manifest(bad)), "malformed content hash is rejected")
bad = copy.deepcopy(manifest)
bad["claims"][0]["value"] = float("inf")
ok(any("finite JSON scalar" in v for v in ev.validate_manifest(bad)), "non-finite claim value is rejected")
bad = copy.deepcopy(manifest)
bad["claims"][0]["display_value"] = "$46.2M"
ok(any("display_value does not reconcile" in v for v in ev.validate_manifest(bad)),
   "displayed value cannot drift from the machine value")
bad = copy.deepcopy(manifest)
bad["artifact"]["as_of"] = "２０２６-０６-３０"
ok(any("ISO date" in v for v in ev.validate_manifest(bad)),
   "Unicode lookalike digits are not accepted as an ISO date")
bad = copy.deepcopy(manifest)
bad["artifact"]["as_of"] = "2026-02-30"
ok(any("real calendar date" in v for v in ev.validate_manifest(bad)),
   "impossible calendar dates are rejected")
bad = copy.deepcopy(approved_manifest)
bad["reviews"][0]["reviewed_at"] = "2026-02-30T18:00:00Z"
ok(any("real UTC calendar timestamp" in v for v in ev.validate_manifest(bad)),
   "impossible review timestamps are rejected")

# Malformed web locations and non-UTF-8 Unicode fail as controlled validation errors.
bad = copy.deepcopy(manifest)
bad["sources"][1]["uri"] = "https://["
ok(any("valid HTTP(S) URI" in v for v in ev.validate_manifest(bad)),
   "malformed HTTP URI fails validation without raising")
bad = copy.deepcopy(manifest)
bad["sources"][1]["uri"] = "https://example.com/\ud800"
issues = ev.validate_manifest(bad)
ok(any("UTF-8 JSON" in v for v in issues), "lone surrogate fails manifest validation")
try:
    ev.evidence_hash(bad)
    ok(False, "lone surrogate must not reach hashing")
except ev.EvidenceError:
    ok(True, "lone surrogate raises a controlled evidence error during hashing")

# The formal schema is committed, parseable, and version-aligned with the runtime validator.
schema = json.loads((Path(__file__).resolve().parents[2] / "schemas/evidence-manifest.schema.json").read_text())
ok(schema["properties"]["schema_version"]["const"] == ev.SCHEMA_VERSION,
   "JSON Schema and runtime validator share a version")
ok(set(ev._COLLECTIONS) <= set(schema["properties"]), "JSON Schema declares every graph collection")
for definition, valid in (("id", "claim.valid"), ("date", "2026-06-30"),
                          ("stamp", "2026-06-30T12:00:00Z"),
                          ("hash", "sha256:" + ("a" * 64))):
    ok(bool(json_schema.validate(valid + "\n", schema["$defs"][definition])),
       "schema %s rejects a trailing newline like the runtime" % definition)

# Atomic write round-trips canonical bytes and the CLI validates/inspects without traceback.
out = ROOT / "artifact.evidence.json"
ev.write_manifest(out, manifest)
ok(out.read_text(encoding="utf-8") == ev.format_manifest(manifest),
   "writer emits deterministic, review-friendly JSON")
ok(ev.load_manifest(out) == manifest, "written manifest round-trips")
ok(ev._main(["validate", str(out), "--root", str(ROOT), "--verify-sources"]) == 0,
   "CLI validates a manifest and its source bytes")
ok(ev._main(["inspect", str(out), "--claim", "claim.missing"]) == 1,
   "CLI fails closed on a missing inspected claim")
invalid_out = ROOT / "invalid.evidence.json"
invalid_out.write_text(ev.format_manifest({**manifest, "claims": 7}), encoding="utf-8")
ok(ev._main(["inspect", str(invalid_out), "--claim", "claim.sbc-forecast"]) == 1,
   "CLI refuses to inspect an invalid graph without a traceback")

print("OK — %d evidence-graph kernel checks passed." % passed)
