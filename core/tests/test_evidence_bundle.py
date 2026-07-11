#!/usr/bin/env python3
"""Adversarial checks for content-addressed evidence bundles."""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import evidence as ev  # noqa: E402
from core import evidence_bundle as bundle  # noqa: E402


passed = 0


def ok(condition, label):
    global passed
    assert condition, "FAILED: " + label
    passed += 1


def manifest(artifact_id, artifact_type):
    b = ev.EvidenceBuilder(artifact_id, "agent.bundle-test", "Bundle Test", artifact_type,
                           "2026-01-31", "January 2026", {"value": 42})
    b.source(**ev.canonical_record_snapshot("source.bundle", "Fixture", "dataset",
                                            "https://example.com/source", "v1", "2026-01-31",
                                            {"value": 42}, "public"))
    b.transformation("transform.bundle", "Fixture transform", "v1", "tests.fixture", "Return 42")
    b.check("check.bundle", "Fixture contract", "passed", "tests.fixture", "Fixture is valid")
    b.claim("claim.bundle.answer", "The answer is 42.", 42, "42", "count", "January 2026",
            "2026-01-31", ["source.bundle"], "transform.bundle", ["check.bundle"])
    return b.build()


report_manifest = manifest("artifact.bundle.report", "dashboard")
digest_manifest = manifest("artifact.bundle.digest", "digest")
evidence_bundle = bundle.build_bundle("bundle.test.january", [
    ("<html><body>42</body></html>", report_manifest),
    ("# Digest\n42\n", digest_manifest),
])
auth = bundle.authorization_envelope(evidence_bundle)

ok(bundle.validate_bundle(evidence_bundle) == [], "valid bundle passes")
ok(bundle.validate_authorization(auth) == [], "valid authorization envelope passes")
ok(bundle.authorization_violations(evidence_bundle, auth) == [], "envelope binds exactly to bundle")
ok(evidence_bundle["artifacts"] == sorted(evidence_bundle["artifacts"], key=lambda a: a["artifact_id"]),
   "artifact entries are deterministic")
ok(len(auth["artifacts"]) == 2 and auth["bundle_hash"].startswith("sha256:"),
   "authorization exposes exact content/evidence hashes and detached bundle hash")
try:
    bundle.build_bundle("bundle.test.bad-input", [("only one tuple item",)])
    ok(False, "malformed rendered-manifest pairs must fail")
except bundle.EvidenceBundleError as exc:
    ok("must be (rendered_text, manifest)" in str(exc),
       "malformed rendered-manifest pairs fail with a controlled error")

changed_bytes = bundle.build_bundle("bundle.test.january", [
    ("<html><body>43</body></html>", report_manifest),
    ("# Digest\n42\n", digest_manifest),
])
ok(bundle.bundle_hash(changed_bytes) != bundle.bundle_hash(evidence_bundle),
   "one rendered-byte change produces a different authorization target")

changed_graph = copy.deepcopy(report_manifest)
changed_graph["claims"][0]["display_value"] = "forty-two"
changed_evidence = bundle.build_bundle("bundle.test.january", [
    ("<html><body>42</body></html>", changed_graph),
    ("# Digest\n42\n", digest_manifest),
])
ok(bundle.bundle_hash(changed_evidence) != bundle.bundle_hash(evidence_bundle),
   "one evidence-graph change produces a different authorization target")

tampered_auth = copy.deepcopy(auth)
tampered_auth["artifacts"][0]["content_hash"] = "sha256:" + "0" * 64
ok(any("does not match" in issue for issue in bundle.authorization_violations(evidence_bundle, tampered_auth)),
   "well-shaped but altered authorization is rejected")

bad_bundle = copy.deepcopy(evidence_bundle)
bad_bundle["artifacts"] = list(reversed(bad_bundle["artifacts"]))
ok(any("sorted" in issue for issue in bundle.validate_bundle(bad_bundle)),
   "non-deterministic artifact order is rejected")

traversal_bundle = copy.deepcopy(evidence_bundle)
traversal_bundle["artifacts"][0]["content_uri"] = "repo:../outside.html"
ok(any("repo: or urn:" in issue for issue in bundle.validate_bundle(traversal_bundle)),
   "artifact references cannot escape a repository verification root")

bad_auth = copy.deepcopy(auth)
bad_auth["unexpected"] = True
ok(any("unknown field" in issue for issue in bundle.validate_authorization(bad_auth)),
   "unknown authorization fields are rejected")

mismatched_cycle = copy.deepcopy(digest_manifest)
mismatched_cycle["artifact"]["as_of"] = "2026-02-28"
try:
    bundle.build_bundle("bundle.test.bad-cycle", [
        ("<html><body>42</body></html>", report_manifest),
        ("# Digest\n42\n", mismatched_cycle),
    ])
    ok(False, "mismatched artifact cycles must fail")
except bundle.EvidenceBundleError as exc:
    ok("share agent_id, as_of, and period" in str(exc),
       "a bundle cannot mix artifacts from different reporting cycles")

path = Path(tempfile.mkdtemp()) / "bundle.json"
bundle.write_bundle(path, evidence_bundle)
ok(bundle.load_bundle(path) == evidence_bundle, "bundle writes and loads deterministically")
ok(path.read_text(encoding="utf-8") == json.dumps(
    evidence_bundle, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
   "committed bundle bytes are deterministic")

repo = Path(__file__).resolve().parents[2]
bundle_schema = json.loads((repo / "schemas/evidence-bundle.schema.json").read_text(encoding="utf-8"))
auth_schema = json.loads((repo / "schemas/evidence-authorization.schema.json").read_text(encoding="utf-8"))
ok(set(bundle_schema["required"]) == bundle._BUNDLE_FIELDS and
   set(bundle_schema["properties"]) == bundle._BUNDLE_FIELDS,
   "published evidence-bundle schema stays in lockstep with the validator")
ok(set(bundle_schema["$defs"]["artifact"]["required"]) == bundle._ARTIFACT_FIELDS and
   set(bundle_schema["$defs"]["artifact"]["properties"]) == bundle._ARTIFACT_FIELDS,
   "published evidence-bundle artifact schema stays in lockstep with the validator")
ok(set(auth_schema["required"]) == bundle._AUTH_FIELDS and
   set(auth_schema["properties"]) == bundle._AUTH_FIELDS,
   "published authorization schema stays in lockstep with the validator")
ok(set(auth_schema["$defs"]["artifact"]["required"]) == bundle._AUTH_ARTIFACT_FIELDS and
   set(auth_schema["$defs"]["artifact"]["properties"]) == bundle._AUTH_ARTIFACT_FIELDS,
   "published authorization-artifact schema stays in lockstep with the validator")

reference_root = Path(tempfile.mkdtemp())
(reference_root / "artifacts").mkdir()
(reference_root / "evidence").mkdir()
(reference_root / "artifacts/report.html").write_text("<html><body>42</body></html>", encoding="utf-8")
(reference_root / "artifacts/digest.md").write_text("# Digest\n42\n", encoding="utf-8")
ev.write_manifest(reference_root / "evidence/report.json", report_manifest)
ev.write_manifest(reference_root / "evidence/digest.json", digest_manifest)
referenced_bundle = bundle.build_bundle("bundle.test.resolvable", [
    ("<html><body>42</body></html>", report_manifest),
    ("# Digest\n42\n", digest_manifest),
], artifact_uris={
    "artifact.bundle.report": ("repo:artifacts/report.html", "repo:evidence/report.json"),
    "artifact.bundle.digest": ("repo:artifacts/digest.md", "repo:evidence/digest.json"),
})
referenced_path = reference_root / "bundle.json"
bundle.write_bundle(referenced_path, referenced_bundle)
ok(bundle.reference_violations(referenced_bundle, reference_root) == [],
   "repo references re-hash exact rendered bytes, manifests, and manifest sources")
reference_cli = subprocess.run([
    sys.executable, "-m", "core.evidence_bundle", "validate", str(referenced_path),
    "--verify-artifacts", "--root", str(reference_root),
], cwd=repo, capture_output=True, text=True)
ok(reference_cli.returncode == 0, "CLI independently resolves and re-hashes referenced artifacts")
(reference_root / "artifacts/report.html").write_text("<html><body>43</body></html>", encoding="utf-8")
ok(any("rendered content hash" in issue
       for issue in bundle.reference_violations(referenced_bundle, reference_root)),
   "one changed committed artifact byte is caught by independent reference verification")

ledger = path.with_name("events.jsonl")
ledger.write_text("\n".join(json.dumps({"type": event_type, "authorization": auth},
                                        sort_keys=True, separators=(",", ":"))
                            for event_type in ("recommendation", "approval", "action")) + "\n",
                  encoding="utf-8")
cli = subprocess.run([sys.executable, "-m", "core.evidence_bundle", "validate", str(path),
                      "--ledger", str(ledger)], cwd=repo, capture_output=True, text=True)
ok(cli.returncode == 0 and "3 ledger authorization event(s) match" in cli.stdout,
   "CLI resolves all three decision events to the exact bundle")
bad_ledger = path.with_name("bad.events.jsonl")
bad_ledger.write_text(json.dumps({"type": "approval", "authorization": tampered_auth},
                                 sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
bad_cli = subprocess.run([sys.executable, "-m", "core.evidence_bundle", "validate", str(path),
                          "--ledger", str(bad_ledger)], cwd=repo, capture_output=True, text=True)
ok(bad_cli.returncode == 1 and "does not match" in bad_cli.stderr,
   "CLI rejects a ledger authorization that does not resolve to the bundle")

path.write_text('{"schema_version":"1.0","schema_version":"9"}\n', encoding="utf-8")
try:
    bundle.load_bundle(path)
    ok(False, "duplicate JSON keys must fail")
except bundle.EvidenceBundleError:
    ok(True, "duplicate JSON keys fail closed")

print("OK — %d evidence-bundle checks passed." % passed)
