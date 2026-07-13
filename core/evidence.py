#!/usr/bin/env python3
"""Deterministic claims-to-evidence manifests for Agentic PeopleOS.

The metric registry defines what a metric MEANS.  An evidence manifest proves why a
specific value shown in a specific artifact should be trusted: source snapshots,
transformation version, assumptions, checks, review, decision use, and prior-cycle
change all remain machine-readable.

This module is intentionally standard-library only and offline.  It does not fetch a
source or execute a calculation.  It records and verifies the evidence produced by a
calculation layer, and it fails closed on malformed or dangling provenance.

CLI:

    python3 -m core.evidence validate path/to/report.evidence.json
    python3 -m core.evidence inspect path/to/report.evidence.json --claim claim.id
    python3 -m core.evidence hash path/to/report.evidence.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA_VERSION = "1.0"

ARTIFACT_TYPES = {"dashboard", "digest", "report", "dataset", "decision_packet"}
ARTIFACT_STATUSES = {"draft", "reviewed", "approved", "published", "withdrawn"}
SOURCE_KINDS = {"dataset", "filing", "model", "policy", "registry", "report", "manual_input"}
CLASSIFICATIONS = {"synthetic", "public", "internal", "confidential", "restricted"}
HASH_SCOPES = {"file_bytes", "canonical_record", "source_version"}
ASSUMPTION_STATUSES = {"illustrative", "approved", "policy", "observed"}
CHECK_STATUSES = {"passed", "failed", "not_run"}
CLAIM_TYPES = {"metric", "narrative", "disclosure"}
CLAIM_STATUSES = {"supported", "caveated", "blocked"}
CAVEAT_SEVERITIES = {"info", "warning", "blocking"}
REVIEW_STATUSES = {"reviewed", "approved", "rejected"}
DECISION_STATUSES = {"proposed", "approved", "rejected", "deferred", "implemented"}
COMPARABILITY = {"comparable", "definition_changed", "not_comparable", "not_available"}
CHANGE_TYPES = {"business_change", "population_change", "source_correction", "logic_change",
                "assumption_change", "definition_change", "restatement", "unknown"}

_COLLECTIONS = ("sources", "transformations", "assumptions", "checks", "caveats",
                "claims", "reviews", "decisions")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:@/-]{0,159}$")
_SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class EvidenceError(ValueError):
    """Raised when evidence cannot be constructed or written safely."""


def _no_dup_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise EvidenceError("duplicate JSON key '%s'" % key)
        seen[key] = value
    return seen


def load_manifest(path):
    """Load a manifest while rejecting duplicate JSON keys."""
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_no_dup_keys)


def _utf8_text(value):
    """Return whether ``value`` is a string that can be represented as UTF-8."""
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _assert_json_value(value, path="$"):
    """Reject values whose canonical JSON encoding would be lossy or non-portable."""
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        if not _utf8_text(value):
            raise EvidenceError("%s contains text that is not valid UTF-8" % path)
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidenceError("%s contains a non-finite number" % path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_value(item, "%s[%d]" % (path, index))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise EvidenceError("%s contains a non-string object key %r" % (path, key))
            if not _utf8_text(key):
                raise EvidenceError("%s contains an object key that is not valid UTF-8" % path)
            _assert_json_value(item, "%s.%s" % (path, key))
        return
    raise EvidenceError("%s contains a non-JSON value of type %s" %
                        (path, type(value).__name__))


def canonical(value):
    """Canonical JSON used for hashes and byte-stable committed manifests."""
    _assert_json_value(value)
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise EvidenceError("value cannot be encoded as canonical JSON: %s" % exc)


def format_manifest(value):
    """Deterministic, review-friendly JSON; hashes still use compact canonical JSON."""
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def hash_bytes(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def hash_text(value):
    text = str(value)
    if not _utf8_text(text):
        raise EvidenceError("text is not valid UTF-8")
    return hash_bytes(text.encode("utf-8"))


def hash_json(value):
    return hash_text(canonical(value))


def hash_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def evidence_hash(manifest):
    """Detached evidence hash.  It is never embedded in the manifest it hashes."""
    return hash_json(manifest)


def _inside(path, root):
    return path == root or root in path.parents


def repo_snapshot(path, root, source_id, label, kind, version, as_of,
                  classification="synthetic"):
    """Build a file-byte source snapshot with canonical, traversal-safe provenance.

    Both paths are resolved before the containment check.  A lexical path such as
    ``policy/../cases/file`` or a symlink escape therefore cannot acquire trusted
    provenance merely by beginning with an allowed-looking prefix.
    """
    real_root = Path(root).resolve(strict=True)
    real_path = Path(path).resolve(strict=True)
    if not real_path.is_file():
        raise EvidenceError("source snapshot is not a file: %s" % real_path)
    if not _inside(real_path, real_root):
        raise EvidenceError("source snapshot escapes repository root: %s" % real_path)
    rel = real_path.relative_to(real_root).as_posix()
    return {
        "id": source_id,
        "label": label,
        "kind": kind,
        "uri": "repo:" + rel,
        "version": version,
        "as_of": as_of,
        "classification": classification,
        "content_hash": hash_file(real_path),
        "hash_scope": "file_bytes",
    }


def canonical_record_snapshot(source_id, label, kind, uri, version, as_of, record,
                              classification="public"):
    """Snapshot an extracted record while retaining a source URI for human inspection."""
    return {
        "id": source_id,
        "label": label,
        "kind": kind,
        "uri": uri,
        "version": version,
        "as_of": as_of,
        "classification": classification,
        "content_hash": hash_json(record),
        "hash_scope": "canonical_record",
    }


def _one_line(value, max_len=1000):
    return (_utf8_text(value) and bool(value.strip()) and "\n" not in value
            and "\r" not in value and len(value) <= max_len)


def _json_scalar(value):
    return value is None or isinstance(value, (str, int, float, bool))


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _keys(obj, required, allowed, tag, violations):
    if not isinstance(obj, dict):
        violations.append("%s must be an object" % tag)
        return False
    missing = sorted(set(required) - set(obj))
    extra = sorted(set(obj) - set(allowed))
    for key in missing:
        violations.append("%s missing field '%s'" % (tag, key))
    for key in extra:
        violations.append("%s has unknown field '%s'" % (tag, key))
    return not missing


def _id(value, tag, violations):
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        violations.append("%s id must match %s" % (tag, _ID_RE.pattern))


def _enum(value, allowed, tag, violations):
    if value not in allowed:
        violations.append("%s must be one of %s" % (tag, sorted(allowed)))


def _date(value, tag, violations):
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        violations.append("%s must be an ISO date (YYYY-MM-DD)" % tag)


def _stamp(value, tag, violations):
    if not isinstance(value, str) or not _STAMP_RE.fullmatch(value):
        violations.append("%s must be a UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)" % tag)


def _id_list(value, tag, violations, allow_empty=True):
    if not isinstance(value, list):
        violations.append("%s must be a list" % tag)
        return []
    if not allow_empty and not value:
        violations.append("%s must not be empty" % tag)
    out = []
    for i, item in enumerate(value):
        if not isinstance(item, str) or not _ID_RE.fullmatch(item):
            violations.append("%s[%d] is not a valid id" % (tag, i))
        else:
            out.append(item)
    if len(out) != len(set(out)):
        violations.append("%s contains duplicate ids" % tag)
    return out


def _validate_artifact(obj, violations):
    required = {"id", "agent_id", "title", "artifact_type", "as_of", "period", "status",
                "semantic_hash"}
    if not _keys(obj, required, required, "artifact", violations):
        return
    _id(obj.get("id"), "artifact", violations)
    _id(obj.get("agent_id"), "artifact.agent_id", violations)
    if not _one_line(obj.get("title"), 240):
        violations.append("artifact.title must be a non-empty one-line string")
    _enum(obj.get("artifact_type"), ARTIFACT_TYPES, "artifact.artifact_type", violations)
    _date(obj.get("as_of"), "artifact.as_of", violations)
    if not _one_line(obj.get("period"), 240):
        violations.append("artifact.period must be a non-empty one-line string")
    _enum(obj.get("status"), ARTIFACT_STATUSES, "artifact.status", violations)
    if not isinstance(obj.get("semantic_hash"), str) or not _SHA_RE.fullmatch(obj["semantic_hash"]):
        violations.append("artifact.semantic_hash must be sha256:<64 lowercase hex>")


def _validate_source(obj, tag, violations):
    required = {"id", "label", "kind", "uri", "version", "as_of", "classification",
                "content_hash", "hash_scope"}
    if not _keys(obj, required, required, tag, violations):
        return
    _id(obj.get("id"), tag, violations)
    if not _one_line(obj.get("label"), 240):
        violations.append("%s.label must be a non-empty one-line string" % tag)
    _enum(obj.get("kind"), SOURCE_KINDS, tag + ".kind", violations)
    uri = obj.get("uri")
    if not _one_line(uri, 2000):
        violations.append("%s.uri must be a non-empty one-line string" % tag)
    elif uri.startswith(("http://", "https://")):
        try:
            parsed = urlsplit(uri)
            hostname = parsed.hostname
            _ = parsed.port
            userinfo_present = parsed.username is not None or parsed.password is not None
        except (ValueError, UnicodeError):
            violations.append("%s.uri is not a valid HTTP(S) URI" % tag)
        else:
            if not hostname:
                violations.append("%s.uri HTTP(S) URI must include a host" % tag)
            elif userinfo_present:
                violations.append("%s.uri must not contain user information" % tag)
    elif not uri.startswith(("repo:", "http://", "https://", "urn:")):
        violations.append("%s.uri must use repo:, http(s):, or urn:" % tag)
    if not _one_line(obj.get("version"), 240):
        violations.append("%s.version must be a non-empty one-line string" % tag)
    _date(obj.get("as_of"), tag + ".as_of", violations)
    _enum(obj.get("classification"), CLASSIFICATIONS, tag + ".classification", violations)
    if not isinstance(obj.get("content_hash"), str) or not _SHA_RE.fullmatch(obj["content_hash"]):
        violations.append("%s.content_hash must be sha256:<64 lowercase hex>" % tag)
    _enum(obj.get("hash_scope"), HASH_SCOPES, tag + ".hash_scope", violations)
    if isinstance(uri, str) and uri.startswith("repo:") and obj.get("hash_scope") != "file_bytes":
        violations.append("%s repo: source must use file_bytes hash scope" % tag)


def _validate_transformation(obj, tag, violations):
    required = {"id", "name", "version", "implementation", "description"}
    if not _keys(obj, required, required, tag, violations):
        return
    _id(obj.get("id"), tag, violations)
    for field, limit in (("name", 240), ("version", 120), ("implementation", 300), ("description", 1000)):
        if not _one_line(obj.get(field), limit):
            violations.append("%s.%s must be a non-empty one-line string" % (tag, field))


def _validate_assumption(obj, tag, violations):
    required = {"id", "name", "value", "unit", "version", "status", "source_ids"}
    if not _keys(obj, required, required, tag, violations):
        return
    _id(obj.get("id"), tag, violations)
    if not _one_line(obj.get("name"), 240):
        violations.append("%s.name must be a non-empty one-line string" % tag)
    if not _json_scalar(obj.get("value")) or (_finite_number(obj.get("value")) is False
                                               and isinstance(obj.get("value"), float)):
        violations.append("%s.value must be a finite JSON scalar" % tag)
    if not _one_line(obj.get("unit"), 80):
        violations.append("%s.unit must be a non-empty one-line string" % tag)
    if not _one_line(obj.get("version"), 120):
        violations.append("%s.version must be a non-empty one-line string" % tag)
    _enum(obj.get("status"), ASSUMPTION_STATUSES, tag + ".status", violations)
    _id_list(obj.get("source_ids"), tag + ".source_ids", violations)


def _validate_check(obj, tag, violations):
    required = {"id", "name", "status", "implementation", "details"}
    if not _keys(obj, required, required, tag, violations):
        return
    _id(obj.get("id"), tag, violations)
    for field, limit in (("name", 240), ("implementation", 300), ("details", 1000)):
        if not _one_line(obj.get(field), limit):
            violations.append("%s.%s must be a non-empty one-line string" % (tag, field))
    _enum(obj.get("status"), CHECK_STATUSES, tag + ".status", violations)


def _validate_caveat(obj, tag, violations):
    required = {"id", "severity", "text"}
    if not _keys(obj, required, required, tag, violations):
        return
    _id(obj.get("id"), tag, violations)
    _enum(obj.get("severity"), CAVEAT_SEVERITIES, tag + ".severity", violations)
    if not _one_line(obj.get("text"), 1000):
        violations.append("%s.text must be a non-empty one-line string" % tag)


def _validate_change(obj, tag, current_value, unit, violations):
    required = {"prior_claim_id", "prior_value", "prior_display_value", "absolute", "percent",
                "comparability", "drivers"}
    if not _keys(obj, required, required, tag, violations):
        return
    if obj.get("prior_claim_id") is not None:
        _id(obj.get("prior_claim_id"), tag + ".prior_claim_id", violations)
    if not _json_scalar(obj.get("prior_value")):
        violations.append("%s.prior_value must be a JSON scalar" % tag)
    if not _one_line(obj.get("prior_display_value"), 120):
        violations.append("%s.prior_display_value must be a non-empty one-line string" % tag)
    for field in ("absolute", "percent"):
        value = obj.get(field)
        if value is not None and not _finite_number(value):
            violations.append("%s.%s must be null or a finite number" % (tag, field))
    _enum(obj.get("comparability"), COMPARABILITY, tag + ".comparability", violations)
    drivers = obj.get("drivers")
    if not isinstance(drivers, list):
        violations.append("%s.drivers must be a list" % tag)
        drivers = []
    effects = []
    for i, driver in enumerate(drivers):
        dtag = "%s.drivers[%d]" % (tag, i)
        dreq = {"type", "label", "effect", "unit"}
        if not _keys(driver, dreq, dreq, dtag, violations):
            continue
        _enum(driver.get("type"), CHANGE_TYPES, dtag + ".type", violations)
        if not _one_line(driver.get("label"), 240):
            violations.append("%s.label must be a non-empty one-line string" % dtag)
        if driver.get("effect") is not None and not _finite_number(driver.get("effect")):
            violations.append("%s.effect must be null or a finite number" % dtag)
        if not _one_line(driver.get("unit"), 80):
            violations.append("%s.unit must be a non-empty one-line string" % dtag)
        if _finite_number(driver.get("effect")) and driver.get("unit") == unit:
            effects.append(float(driver["effect"]))
    if (obj.get("comparability") == "comparable" and _finite_number(current_value)
            and _finite_number(obj.get("prior_value")) and _finite_number(obj.get("absolute"))):
        expected = float(current_value) - float(obj["prior_value"])
        if not math.isclose(float(obj["absolute"]), expected, rel_tol=1e-9, abs_tol=1e-9):
            violations.append("%s.absolute does not equal current minus prior" % tag)
        if effects and not math.isclose(sum(effects), float(obj["absolute"]), rel_tol=1e-9, abs_tol=1e-9):
            violations.append("%s driver effects do not reconcile to absolute change" % tag)


def _validate_claim(obj, tag, violations):
    required = {"id", "claim_type", "metric_id", "statement", "value", "display_value", "unit",
                "period", "as_of", "material", "status", "source_ids", "transformation_id",
                "assumption_ids", "check_ids", "supporting_claim_ids", "caveat_ids", "change"}
    if not _keys(obj, required, required, tag, violations):
        return
    _id(obj.get("id"), tag, violations)
    _enum(obj.get("claim_type"), CLAIM_TYPES, tag + ".claim_type", violations)
    if obj.get("metric_id") is not None:
        _id(obj.get("metric_id"), tag + ".metric_id", violations)
    if not _one_line(obj.get("statement"), 1000):
        violations.append("%s.statement must be a non-empty one-line string" % tag)
    value = obj.get("value")
    if not _json_scalar(value) or (isinstance(value, float) and not math.isfinite(value)):
        violations.append("%s.value must be a finite JSON scalar" % tag)
    if not _one_line(obj.get("display_value"), 120):
        violations.append("%s.display_value must be a non-empty one-line string" % tag)
    if not _one_line(obj.get("unit"), 80):
        violations.append("%s.unit must be a non-empty one-line string" % tag)
    if not _one_line(obj.get("period"), 240):
        violations.append("%s.period must be a non-empty one-line string" % tag)
    _date(obj.get("as_of"), tag + ".as_of", violations)
    if not isinstance(obj.get("material"), bool):
        violations.append("%s.material must be a boolean" % tag)
    _enum(obj.get("status"), CLAIM_STATUSES, tag + ".status", violations)
    sources = _id_list(obj.get("source_ids"), tag + ".source_ids", violations)
    if obj.get("transformation_id") is not None:
        _id(obj.get("transformation_id"), tag + ".transformation_id", violations)
    assumptions = _id_list(obj.get("assumption_ids"), tag + ".assumption_ids", violations)
    checks = _id_list(obj.get("check_ids"), tag + ".check_ids", violations)
    supporting = _id_list(obj.get("supporting_claim_ids"), tag + ".supporting_claim_ids", violations)
    caveats = _id_list(obj.get("caveat_ids"), tag + ".caveat_ids", violations)
    if obj.get("material"):
        if not sources and not supporting:
            violations.append("%s material claim needs a source or supporting claim" % tag)
        if not obj.get("transformation_id"):
            violations.append("%s material claim needs a transformation" % tag)
        if not checks:
            violations.append("%s material claim needs at least one check" % tag)
    if obj.get("status") == "caveated" and not caveats:
        violations.append("%s caveated claim needs a caveat" % tag)
    if obj.get("status") == "blocked" and obj.get("material") is False:
        violations.append("%s only a material claim may be blocked" % tag)
    if obj.get("change") is not None:
        _validate_change(obj["change"], tag + ".change", value, obj.get("unit"), violations)
    # Retain local variables to make the shape checks above explicit to static reviewers.
    _ = assumptions


def _validate_review(obj, tag, violations):
    required = {"id", "status", "actor_role", "reviewed_at", "claim_ids", "notes"}
    if not _keys(obj, required, required, tag, violations):
        return
    _id(obj.get("id"), tag, violations)
    _enum(obj.get("status"), REVIEW_STATUSES, tag + ".status", violations)
    if not _one_line(obj.get("actor_role"), 240):
        violations.append("%s.actor_role must be a non-empty one-line string" % tag)
    _stamp(obj.get("reviewed_at"), tag + ".reviewed_at", violations)
    _id_list(obj.get("claim_ids"), tag + ".claim_ids", violations, allow_empty=False)
    if not _one_line(obj.get("notes"), 1000):
        violations.append("%s.notes must be a non-empty one-line string" % tag)


def _validate_decision(obj, tag, violations):
    required = {"id", "decision_type", "status", "owner_role", "decided_at", "artifact_id",
                "claim_ids", "notes"}
    if not _keys(obj, required, required, tag, violations):
        return
    _id(obj.get("id"), tag, violations)
    if not _one_line(obj.get("decision_type"), 240):
        violations.append("%s.decision_type must be a non-empty one-line string" % tag)
    _enum(obj.get("status"), DECISION_STATUSES, tag + ".status", violations)
    if not _one_line(obj.get("owner_role"), 240):
        violations.append("%s.owner_role must be a non-empty one-line string" % tag)
    _stamp(obj.get("decided_at"), tag + ".decided_at", violations)
    _id(obj.get("artifact_id"), tag + ".artifact_id", violations)
    _id_list(obj.get("claim_ids"), tag + ".claim_ids", violations, allow_empty=False)
    if not _one_line(obj.get("notes"), 1000):
        violations.append("%s.notes must be a non-empty one-line string" % tag)


def _verify_repo_source(source, root, tag, violations):
    uri = source.get("uri")
    if not isinstance(uri, str) or not uri.startswith("repo:"):
        return
    if root is None:
        violations.append("%s cannot verify repo: source without a repository root" % tag)
        return
    try:
        real_root = Path(root).resolve(strict=True)
        rel = uri[len("repo:"):]
        if not rel or Path(rel).is_absolute():
            raise EvidenceError("repo URI must contain a relative path")
        real_path = (real_root / rel).resolve(strict=True)
        if not _inside(real_path, real_root) or not real_path.is_file():
            raise EvidenceError("repo URI escapes root or is not a file")
        actual = hash_file(real_path)
        if actual != source.get("content_hash"):
            violations.append("%s source hash mismatch (%s != %s)" %
                              (tag, actual, source.get("content_hash")))
    except (OSError, EvidenceError) as exc:
        violations.append("%s repo source cannot be verified: %s" % (tag, exc))


def validate_manifest(data, root=None, verify_sources=False, require_material=True):
    """Return violations; an empty list means the manifest is structurally and referentially valid."""
    violations = []
    if not isinstance(data, dict):
        return ["manifest must be an object"]
    try:
        _assert_json_value(data)
    except EvidenceError as exc:
        if "non-finite number" in str(exc):
            return ["manifest value must be a finite JSON scalar: %s" % exc]
        return ["manifest must be losslessly encodable as UTF-8 JSON: %s" % exc]
    required = {"schema_version", "artifact"} | set(_COLLECTIONS)
    _keys(data, required, required, "manifest", violations)
    if data.get("schema_version") != SCHEMA_VERSION:
        violations.append("manifest.schema_version must be '%s'" % SCHEMA_VERSION)
    _validate_artifact(data.get("artifact"), violations)

    validators = {
        "sources": _validate_source,
        "transformations": _validate_transformation,
        "assumptions": _validate_assumption,
        "checks": _validate_check,
        "caveats": _validate_caveat,
        "claims": _validate_claim,
        "reviews": _validate_review,
        "decisions": _validate_decision,
    }
    indexes = {}
    global_ids = {}
    for collection in _COLLECTIONS:
        items = data.get(collection)
        if not isinstance(items, list):
            violations.append("manifest.%s must be a list" % collection)
            items = []
        index = {}
        for i, item in enumerate(items):
            tag = "%s[%d]" % (collection, i)
            validators[collection](item, tag, violations)
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                node_id = item["id"]
                if node_id in index:
                    violations.append("%s duplicate id '%s'" % (collection, node_id))
                index[node_id] = item
                if node_id in global_ids:
                    violations.append("node id '%s' reused by %s and %s" %
                                      (node_id, global_ids[node_id], collection))
                global_ids[node_id] = collection
        indexes[collection] = index

    def refs(tag, ids, collection):
        if not isinstance(ids, list):
            return
        for node_id in ids:
            if isinstance(node_id, str) and node_id not in indexes[collection]:
                violations.append("%s references missing %s '%s'" % (tag, collection[:-1], node_id))

    for assumption in indexes["assumptions"].values():
        refs("assumption '%s'.source_ids" % assumption["id"], assumption.get("source_ids"), "sources")

    for claim in indexes["claims"].values():
        cid = claim["id"]
        refs("claim '%s'.source_ids" % cid, claim.get("source_ids"), "sources")
        refs("claim '%s'.assumption_ids" % cid, claim.get("assumption_ids"), "assumptions")
        refs("claim '%s'.check_ids" % cid, claim.get("check_ids"), "checks")
        refs("claim '%s'.supporting_claim_ids" % cid, claim.get("supporting_claim_ids"), "claims")
        refs("claim '%s'.caveat_ids" % cid, claim.get("caveat_ids"), "caveats")
        transform = claim.get("transformation_id")
        if isinstance(transform, str) and transform not in indexes["transformations"]:
            violations.append("claim '%s' references missing transformation '%s'" % (cid, transform))
        if cid in (claim.get("supporting_claim_ids") or []):
            violations.append("claim '%s' cannot support itself" % cid)
        if claim.get("material") and claim.get("status") in ("supported", "caveated"):
            for check_id in claim.get("check_ids") or []:
                check = indexes["checks"].get(check_id)
                if check and check.get("status") != "passed":
                    violations.append("claim '%s' relies on check '%s' with status '%s'" %
                                      (cid, check_id, check.get("status")))

    artifact = data.get("artifact") if isinstance(data.get("artifact"), dict) else {}
    artifact_id = artifact.get("id")
    for review in indexes["reviews"].values():
        refs("review '%s'.claim_ids" % review["id"], review.get("claim_ids"), "claims")
    for decision in indexes["decisions"].values():
        refs("decision '%s'.claim_ids" % decision["id"], decision.get("claim_ids"), "claims")
        if decision.get("artifact_id") != artifact_id:
            violations.append("decision '%s' artifact_id does not match this artifact" % decision["id"])

    material_ids = {c["id"] for c in indexes["claims"].values() if c.get("material")}
    if require_material and not material_ids:
        violations.append("manifest has no material claims")
    if artifact.get("status") in ("approved", "published"):
        blocked = sorted(c["id"] for c in indexes["claims"].values()
                         if c.get("material") and c.get("status") == "blocked")
        if blocked:
            violations.append("approved/published artifact contains blocked material claims: %s" % blocked)
        approved_ids = set()
        for review in indexes["reviews"].values():
            if review.get("status") == "approved":
                approved_ids.update(review.get("claim_ids") or [])
        missing_review = sorted(material_ids - approved_ids)
        if missing_review:
            violations.append("approved/published artifact has material claims without approved review: %s" %
                              missing_review)

    if verify_sources:
        for i, source in enumerate(data.get("sources") or []):
            if isinstance(source, dict) and source.get("hash_scope") == "file_bytes":
                _verify_repo_source(source, root, "sources[%d]" % i, violations)

    return violations


def coverage(manifest):
    claims = manifest.get("claims", []) if isinstance(manifest, dict) else []
    material = [c for c in claims if isinstance(c, dict) and c.get("material")]
    return {
        "claims": len(claims),
        "material": len(material),
        "supported": sum(c.get("status") == "supported" for c in material),
        "caveated": sum(c.get("status") == "caveated" for c in material),
        "blocked": sum(c.get("status") == "blocked" for c in material),
        "traceable": sum(bool(c.get("transformation_id")) and bool(c.get("check_ids"))
                         and bool(c.get("source_ids") or c.get("supporting_claim_ids")) for c in material),
    }


def inspect_claim(manifest, claim_id):
    """Return the immediate support subgraph for one claim."""
    indexes = {name: {item.get("id"): item for item in manifest.get(name, [])
                      if isinstance(item, dict) and item.get("id")} for name in _COLLECTIONS}
    claim = indexes["claims"].get(claim_id)
    if claim is None:
        raise EvidenceError("claim not found: %s" % claim_id)

    def select(collection, ids):
        return [indexes[collection][node_id] for node_id in ids if node_id in indexes[collection]]

    reviews = [r for r in manifest.get("reviews", []) if claim_id in (r.get("claim_ids") or [])]
    decisions = [d for d in manifest.get("decisions", []) if claim_id in (d.get("claim_ids") or [])]
    return {
        "artifact": manifest["artifact"],
        "claim": claim,
        "sources": select("sources", claim.get("source_ids") or []),
        "transformation": indexes["transformations"].get(claim.get("transformation_id")),
        "assumptions": select("assumptions", claim.get("assumption_ids") or []),
        "checks": select("checks", claim.get("check_ids") or []),
        "supporting_claims": select("claims", claim.get("supporting_claim_ids") or []),
        "caveats": select("caveats", claim.get("caveat_ids") or []),
        "reviews": reviews,
        "decisions": decisions,
        "evidence_hash": evidence_hash(manifest),
    }


class EvidenceBuilder:
    """Small deterministic builder; all semantic validation still happens at ``build``."""

    def __init__(self, artifact_id, agent_id, title, artifact_type, as_of, period,
                 semantic_payload, status="draft"):
        self.artifact = {
            "id": artifact_id,
            "agent_id": agent_id,
            "title": title,
            "artifact_type": artifact_type,
            "as_of": as_of,
            "period": period,
            "status": status,
            "semantic_hash": hash_json(semantic_payload),
        }
        self.nodes = {name: {} for name in _COLLECTIONS}

    def _add(self, collection, node):
        node_id = node.get("id") if isinstance(node, dict) else None
        if node_id in self.nodes[collection]:
            raise EvidenceError("duplicate %s id '%s'" % (collection[:-1], node_id))
        self.nodes[collection][node_id] = dict(node)
        return node_id

    def source(self, **node):
        return self._add("sources", node)

    def repo_source(self, path, root, source_id, label, kind, version, as_of,
                    classification="synthetic"):
        return self._add("sources", repo_snapshot(path, root, source_id, label, kind, version,
                                                  as_of, classification))

    def transformation(self, transformation_id, name, version, implementation, description):
        return self._add("transformations", {
            "id": transformation_id, "name": name, "version": version,
            "implementation": implementation, "description": description,
        })

    def assumption(self, assumption_id, name, value, unit, version, status, source_ids=None):
        return self._add("assumptions", {
            "id": assumption_id, "name": name, "value": value, "unit": unit,
            "version": version, "status": status, "source_ids": list(source_ids or []),
        })

    def check(self, check_id, name, status, implementation, details):
        return self._add("checks", {
            "id": check_id, "name": name, "status": status,
            "implementation": implementation, "details": details,
        })

    def caveat(self, caveat_id, severity, text):
        return self._add("caveats", {"id": caveat_id, "severity": severity, "text": text})

    def claim(self, claim_id, statement, value, display_value, unit, period, as_of,
              source_ids, transformation_id, check_ids, metric_id=None, claim_type="metric",
              material=True, status="supported", assumption_ids=None, supporting_claim_ids=None,
              caveat_ids=None, change=None):
        return self._add("claims", {
            "id": claim_id, "claim_type": claim_type, "metric_id": metric_id,
            "statement": statement, "value": value, "display_value": display_value,
            "unit": unit, "period": period, "as_of": as_of, "material": material,
            "status": status, "source_ids": list(source_ids or []),
            "transformation_id": transformation_id,
            "assumption_ids": list(assumption_ids or []), "check_ids": list(check_ids or []),
            "supporting_claim_ids": list(supporting_claim_ids or []),
            "caveat_ids": list(caveat_ids or []), "change": change,
        })

    def review(self, review_id, status, actor_role, reviewed_at, claim_ids, notes):
        return self._add("reviews", {
            "id": review_id, "status": status, "actor_role": actor_role,
            "reviewed_at": reviewed_at, "claim_ids": list(claim_ids), "notes": notes,
        })

    def decision(self, decision_id, decision_type, status, owner_role, decided_at, claim_ids, notes):
        return self._add("decisions", {
            "id": decision_id, "decision_type": decision_type, "status": status,
            "owner_role": owner_role, "decided_at": decided_at,
            "artifact_id": self.artifact["id"], "claim_ids": list(claim_ids), "notes": notes,
        })

    def build(self, require_material=True):
        manifest = {"schema_version": SCHEMA_VERSION, "artifact": dict(self.artifact)}
        for collection in _COLLECTIONS:
            manifest[collection] = [self.nodes[collection][key]
                                    for key in sorted(self.nodes[collection])]
        violations = validate_manifest(manifest, require_material=require_material)
        if violations:
            raise EvidenceError("invalid evidence manifest: " + violations[0])
        return manifest


def write_manifest(path, manifest):
    """Atomically write canonical JSON with a trailing newline."""
    violations = validate_manifest(manifest)
    if violations:
        raise EvidenceError("refusing to write invalid evidence manifest: " + violations[0])
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(format_manifest(manifest))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Validate and inspect Agentic PeopleOS evidence manifests")
    sub = parser.add_subparsers(dest="command", required=True)
    val = sub.add_parser("validate")
    val.add_argument("manifest")
    val.add_argument("--root")
    val.add_argument("--verify-sources", action="store_true")
    ins = sub.add_parser("inspect")
    ins.add_argument("manifest")
    ins.add_argument("--claim", required=True)
    hsh = sub.add_parser("hash")
    hsh.add_argument("manifest")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "validate":
            violations = validate_manifest(manifest, root=args.root, verify_sources=args.verify_sources)
            if violations:
                print("EVIDENCE INVALID — %d violation(s):" % len(violations), file=sys.stderr)
                for violation in violations:
                    print("  - " + violation, file=sys.stderr)
                return 1
            cov = coverage(manifest)
            print("EVIDENCE OK — %d claims; %d/%d material claims traceable; hash %s" %
                  (cov["claims"], cov["traceable"], cov["material"], evidence_hash(manifest)))
            return 0
        if args.command == "inspect":
            print(json.dumps(inspect_claim(manifest, args.claim), indent=2, sort_keys=True,
                             ensure_ascii=False, allow_nan=False))
            return 0
        print(evidence_hash(manifest))
        return 0
    except (OSError, ValueError, EvidenceError) as exc:
        print("EVIDENCE ERROR — %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_main())
