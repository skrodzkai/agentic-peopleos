#!/usr/bin/env python3
"""Content-addressed bundles for approving exact evidence-bearing artifacts.

An evidence manifest proves a claim.  An evidence bundle proves exactly which rendered
bytes and which manifests a human reviewed together.  The detached bundle hash is the
authorization target carried unchanged by recommendation -> approval -> action events.

The hash is deliberately detached: embedding it in a rendered artifact would create a
circular hash.  Rendered content and evidence manifests are hashed into the bundle; the
bundle is hashed into the event authorization envelope.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath

from core import evidence as ev


BUNDLE_SCHEMA_VERSION = "1.0"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:@/-]{0,159}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_BUNDLE_FIELDS = {"schema_version", "bundle_id", "agent_id", "as_of", "period", "artifacts"}
_ARTIFACT_FIELDS = {"artifact_id", "artifact_type", "content_uri", "evidence_uri",
                    "content_hash", "evidence_hash", "semantic_hash", "material_claim_ids"}
_AUTH_FIELDS = {"bundle_id", "bundle_hash", "artifacts", "material_claim_ids_hash"}
_AUTH_ARTIFACT_FIELDS = {"artifact_id", "content_hash", "evidence_hash"}


class EvidenceBundleError(ValueError):
    """Raised when a bundle or authorization envelope is malformed or inconsistent."""


def _no_dup_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise EvidenceBundleError("duplicate JSON key '%s'" % key)
        out[key] = value
    return out


def _exact_keys(value, expected, label, violations):
    if not isinstance(value, dict):
        violations.append("%s must be an object" % label)
        return False
    for key in sorted(expected - set(value), key=str):
        violations.append("%s missing field '%s'" % (label, key))
    for key in sorted(set(value) - expected, key=str):
        violations.append("%s has unknown field '%s'" % (label, key))
    return expected <= set(value)


def _valid_id(value):
    return isinstance(value, str) and bool(_ID_RE.fullmatch(value))


def _valid_hash(value):
    return isinstance(value, str) and bool(_HASH_RE.fullmatch(value))


def _valid_uri(value):
    if not isinstance(value, str) or len(value) > 2000 or "\\" in value or \
            any(ord(char) <= 32 for char in value):
        return False
    if value.startswith("repo:"):
        rel = value[len("repo:"):]
        path = PurePosixPath(rel)
        return bool(rel) and not path.is_absolute() and ".." not in path.parts
    return value.startswith("urn:") and len(value) > len("urn:")


def artifact_entry(content, manifest, content_uri=None, evidence_uri=None):
    """Build one exact rendered-content + evidence-manifest entry."""
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
    elif isinstance(content, bytes):
        content_bytes = content
    else:
        raise EvidenceBundleError("rendered artifact content must be UTF-8 text or bytes")
    try:
        content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise EvidenceBundleError("rendered artifact bytes must be valid UTF-8")
    violations = ev.validate_manifest(manifest)
    if violations:
        raise EvidenceBundleError("invalid evidence manifest: %s" % violations[0])
    artifact = manifest["artifact"]
    content_uri = content_uri or "urn:agentic-peopleos:%s:rendered" % artifact["id"]
    evidence_uri = evidence_uri or "urn:agentic-peopleos:%s:evidence" % artifact["id"]
    if not _valid_uri(content_uri) or not _valid_uri(evidence_uri):
        raise EvidenceBundleError("artifact references must use repo: or urn: URIs")
    material_ids = sorted(claim["id"] for claim in manifest["claims"] if claim["material"])
    if not material_ids:
        raise EvidenceBundleError("evidence manifest has no material claims")
    return {
        "artifact_id": artifact["id"],
        "artifact_type": artifact["artifact_type"],
        "content_uri": content_uri,
        "evidence_uri": evidence_uri,
        "content_hash": ev.hash_bytes(content_bytes),
        "evidence_hash": ev.evidence_hash(manifest),
        "semantic_hash": artifact["semantic_hash"],
        "material_claim_ids": material_ids,
    }


def validate_bundle(bundle):
    violations = []
    if not _exact_keys(bundle, _BUNDLE_FIELDS, "bundle", violations):
        return violations
    if bundle.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        violations.append("bundle.schema_version must be %s" % BUNDLE_SCHEMA_VERSION)
    for field in ("bundle_id", "agent_id"):
        if not _valid_id(bundle.get(field)):
            violations.append("bundle.%s is not a valid stable id" % field)
    if not isinstance(bundle.get("as_of"), str) or not _DATE_RE.fullmatch(bundle["as_of"]):
        violations.append("bundle.as_of must be an ISO date")
    else:
        try:
            dt.date.fromisoformat(bundle["as_of"])
        except ValueError:
            violations.append("bundle.as_of must be a real calendar date")
    if not isinstance(bundle.get("period"), str) or not bundle["period"].strip():
        violations.append("bundle.period must be a non-empty string")
    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        violations.append("bundle.artifacts must be a non-empty list")
        return violations
    ids = []
    for index, artifact in enumerate(artifacts):
        label = "bundle.artifacts[%d]" % index
        if not _exact_keys(artifact, _ARTIFACT_FIELDS, label, violations):
            continue
        artifact_id = artifact.get("artifact_id")
        if not _valid_id(artifact_id):
            violations.append("%s.artifact_id is not a valid stable id" % label)
        else:
            ids.append(artifact_id)
        if artifact.get("artifact_type") not in ev.ARTIFACT_TYPES:
            violations.append("%s.artifact_type is invalid" % label)
        for field in ("content_uri", "evidence_uri"):
            if not _valid_uri(artifact.get(field)):
                violations.append("%s.%s must use a repo: or urn: URI" % (label, field))
        for field in ("content_hash", "evidence_hash", "semantic_hash"):
            if not _valid_hash(artifact.get(field)):
                violations.append("%s.%s must be sha256:<64 lowercase hex>" % (label, field))
        claim_ids = artifact.get("material_claim_ids")
        if not isinstance(claim_ids, list) or not claim_ids:
            violations.append("%s.material_claim_ids must be a non-empty list" % label)
        elif any(not _valid_id(cid) for cid in claim_ids) or \
                claim_ids != sorted(set(claim_ids)):
            violations.append("%s.material_claim_ids must be sorted unique stable ids" % label)
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        violations.append("bundle.artifacts must be sorted by unique artifact_id")
    return violations


def build_bundle(bundle_id, rendered_manifests, artifact_uris=None):
    """Build a deterministic bundle from ``[(rendered_text, manifest), ...]``."""
    try:
        raw_pairs = list(rendered_manifests)
    except TypeError:
        raise EvidenceBundleError("rendered_manifests must be iterable")
    pairs = []
    for index, item in enumerate(raw_pairs):
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise EvidenceBundleError("rendered_manifests[%d] must be (rendered_text, manifest)" % index)
        content, manifest = item
        manifest_violations = ev.validate_manifest(manifest)
        if manifest_violations:
            raise EvidenceBundleError("invalid evidence manifest: %s" % manifest_violations[0])
        pairs.append((content, manifest))
    if not pairs:
        raise EvidenceBundleError("an evidence bundle needs at least one artifact")
    try:
        artifact_uris = dict(artifact_uris or {})
    except (TypeError, ValueError):
        raise EvidenceBundleError("artifact_uris must be a mapping")
    artifact_ids = {manifest["artifact"]["id"] for _content, manifest in pairs}
    unknown_locations = sorted(set(artifact_uris) - artifact_ids, key=str)
    if unknown_locations:
        raise EvidenceBundleError("artifact_uris contains unknown artifact id '%s'" % unknown_locations[0])
    entries = []
    for content, manifest in pairs:
        artifact_id = manifest["artifact"]["id"]
        locations = artifact_uris.get(artifact_id, (None, None))
        if not isinstance(locations, (tuple, list)) or len(locations) != 2:
            raise EvidenceBundleError("artifact_uris values must be (content_uri, evidence_uri) pairs")
        entries.append(artifact_entry(content, manifest, locations[0], locations[1]))
    entries.sort(key=lambda item: item["artifact_id"])
    first_manifest = pairs[0][1]["artifact"]
    cycle_identity = (first_manifest["agent_id"], first_manifest["as_of"], first_manifest["period"])
    for _content, manifest in pairs[1:]:
        artifact = manifest["artifact"]
        if (artifact["agent_id"], artifact["as_of"], artifact["period"]) != cycle_identity:
            raise EvidenceBundleError(
                "all evidence-bundle artifacts must share agent_id, as_of, and period")
    bundle = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "agent_id": first_manifest["agent_id"],
        "as_of": first_manifest["as_of"],
        "period": first_manifest["period"],
        "artifacts": entries,
    }
    violations = validate_bundle(bundle)
    if violations:
        raise EvidenceBundleError("invalid evidence bundle: %s" % violations[0])
    return bundle


def bundle_hash(bundle):
    violations = validate_bundle(bundle)
    if violations:
        raise EvidenceBundleError("invalid evidence bundle: %s" % violations[0])
    return ev.hash_json(bundle)


def authorization_envelope(bundle):
    """The minimal exact authorization target copied into three ledger events."""
    violations = validate_bundle(bundle)
    if violations:
        raise EvidenceBundleError("invalid evidence bundle: %s" % violations[0])
    claim_ids = sorted({claim_id for artifact in bundle["artifacts"]
                        for claim_id in artifact["material_claim_ids"]})
    return {
        "bundle_id": bundle["bundle_id"],
        "bundle_hash": bundle_hash(bundle),
        "artifacts": [{"artifact_id": item["artifact_id"],
                       "content_hash": item["content_hash"],
                       "evidence_hash": item["evidence_hash"]}
                      for item in bundle["artifacts"]],
        "material_claim_ids_hash": ev.hash_json(claim_ids),
    }


def validate_authorization(envelope):
    violations = []
    if not _exact_keys(envelope, _AUTH_FIELDS, "authorization", violations):
        return violations
    if not _valid_id(envelope.get("bundle_id")):
        violations.append("authorization.bundle_id is not a valid stable id")
    for field in ("bundle_hash", "material_claim_ids_hash"):
        if not _valid_hash(envelope.get(field)):
            violations.append("authorization.%s must be sha256:<64 lowercase hex>" % field)
    artifacts = envelope.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        violations.append("authorization.artifacts must be a non-empty list")
        return violations
    ids = []
    for index, artifact in enumerate(artifacts):
        label = "authorization.artifacts[%d]" % index
        if not _exact_keys(artifact, _AUTH_ARTIFACT_FIELDS, label, violations):
            continue
        if not _valid_id(artifact.get("artifact_id")):
            violations.append("%s.artifact_id is not a valid stable id" % label)
        else:
            ids.append(artifact["artifact_id"])
        for field in ("content_hash", "evidence_hash"):
            if not _valid_hash(artifact.get(field)):
                violations.append("%s.%s must be sha256:<64 lowercase hex>" % (label, field))
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        violations.append("authorization.artifacts must be sorted by unique artifact_id")
    return violations


def authorization_violations(bundle, envelope):
    violations = validate_bundle(bundle) + validate_authorization(envelope)
    if not violations and ev.canonical(authorization_envelope(bundle)) != ev.canonical(envelope):
        violations.append("authorization envelope does not match the evidence bundle")
    return violations


def _repo_reference(uri, root, label, violations):
    if not isinstance(uri, str) or not uri.startswith("repo:"):
        violations.append("%s is not independently resolvable (expected repo: URI)" % label)
        return None
    rel = uri[len("repo:"):]
    root = Path(root).resolve()
    target = (root / rel).resolve()
    if not rel or (target != root and root not in target.parents):
        violations.append("%s escapes the verification root" % label)
        return None
    if not target.is_file():
        violations.append("%s not found: %s" % (label, target))
        return None
    return target


def reference_violations(bundle, root):
    """Re-hash every ``repo:`` artifact, evidence manifest, and manifest source."""
    violations = list(validate_bundle(bundle))
    if violations:
        return violations
    for index, artifact in enumerate(bundle["artifacts"]):
        label = "bundle.artifacts[%d]" % index
        content_path = _repo_reference(artifact["content_uri"], root, label + ".content_uri", violations)
        evidence_path = _repo_reference(artifact["evidence_uri"], root, label + ".evidence_uri", violations)
        if content_path is not None and ev.hash_file(content_path) != artifact["content_hash"]:
            violations.append("%s rendered content hash does not match %s" % (label, content_path))
        if evidence_path is None:
            continue
        try:
            manifest = ev.load_manifest(evidence_path)
        except (OSError, ValueError) as exc:
            violations.append("%s cannot load evidence manifest: %s" % (label, exc))
            continue
        manifest_violations = ev.validate_manifest(manifest, root=root, verify_sources=True)
        violations.extend("%s evidence manifest: %s" % (label, issue) for issue in manifest_violations)
        if ev.evidence_hash(manifest) != artifact["evidence_hash"]:
            violations.append("%s evidence hash does not match %s" % (label, evidence_path))
        manifest_artifact = manifest.get("artifact", {}) if isinstance(manifest, dict) else {}
        if manifest_artifact.get("id") != artifact["artifact_id"]:
            violations.append("%s artifact id does not match its evidence manifest" % label)
        if manifest_artifact.get("artifact_type") != artifact["artifact_type"]:
            violations.append("%s artifact type does not match its evidence manifest" % label)
        if manifest_artifact.get("semantic_hash") != artifact["semantic_hash"]:
            violations.append("%s semantic hash does not match its evidence manifest" % label)
        material_ids = sorted(claim.get("id") for claim in manifest.get("claims", [])
                              if isinstance(claim, dict) and claim.get("material")
                              and isinstance(claim.get("id"), str))
        if material_ids != artifact["material_claim_ids"]:
            violations.append("%s material claim ids do not match its evidence manifest" % label)
    return violations


def load_bundle(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_no_dup_keys)


def write_bundle(path, bundle):
    violations = validate_bundle(bundle)
    if violations:
        raise EvidenceBundleError("refusing to write invalid evidence bundle: %s" % violations[0])
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write((json.dumps(bundle, sort_keys=True, indent=2, ensure_ascii=False,
                                     allow_nan=False) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _ledger_authorizations(path, bundle_id):
    """Return only authorizations for one bundle from a potentially multi-cycle ledger."""
    authorizations = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line, object_pairs_hook=_no_dup_keys)
        except (ValueError, EvidenceBundleError) as exc:
            raise EvidenceBundleError("ledger line %d: %s" % (line_number, exc))
        if not isinstance(event, dict):
            raise EvidenceBundleError("ledger line %d must be an object" % line_number)
        authorization = event.get("authorization")
        if event.get("type") in ("recommendation", "approval", "action") and \
                isinstance(authorization, dict) and authorization.get("bundle_id") == bundle_id:
            authorizations.append((line_number, event["type"], authorization))
    if not authorizations:
        raise EvidenceBundleError("ledger contains no authorization for bundle '%s'" % bundle_id)
    return authorizations


def main(argv=None):
    """Validate a bundle, optionally resolving every decision-ledger authorization to it."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate one evidence bundle")
    validate.add_argument("bundle")
    validate.add_argument("--ledger", help="also require every decision authorization to match the bundle")
    validate.add_argument("--verify-artifacts", action="store_true",
                          help="re-hash repo: artifact/evidence references and their repository sources")
    validate.add_argument("--root", default=str(Path(__file__).resolve().parents[1]),
                          help="repository root for repo: references (default: this checkout)")
    args = parser.parse_args(argv)

    ledger_authorizations = []
    try:
        evidence_bundle = load_bundle(args.bundle)
        violations = (reference_violations(evidence_bundle, args.root)
                      if args.verify_artifacts else validate_bundle(evidence_bundle))
        if args.ledger and not violations:
            ledger_authorizations = _ledger_authorizations(args.ledger, evidence_bundle["bundle_id"])
            for line_number, _event_type, authorization in ledger_authorizations:
                violations.extend("ledger line %d: %s" % (line_number, issue)
                                  for issue in authorization_violations(evidence_bundle, authorization))
    except (OSError, ValueError) as exc:
        print("INVALID evidence bundle — %s" % exc, file=sys.stderr)
        return 1
    if violations:
        print("INVALID evidence bundle", file=sys.stderr)
        for violation in violations:
            print("  - %s" % violation, file=sys.stderr)
        return 1
    material_ids = {claim_id for artifact in evidence_bundle["artifacts"]
                    for claim_id in artifact["material_claim_ids"]}
    ledger_note = ""
    if args.ledger:
        ledger_note = "; %d ledger authorization event(s) match" % len(ledger_authorizations)
    print("OK %s — %d artifact(s), %d material claim(s); %s%s" % (
        args.bundle, len(evidence_bundle["artifacts"]), len(material_ids),
        bundle_hash(evidence_bundle), ledger_note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
