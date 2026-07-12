#!/usr/bin/env python3
"""Validate every committed evidence instance against its published JSON Schema."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core import evidence, json_schema  # noqa: E402

try:  # Evidence bundles enter the five-PR stack one layer after manifests.
    from core import evidence_bundle  # noqa: E402
except ImportError:  # pragma: no cover - exercised by the PR #14 intermediate head
    evidence_bundle = None


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=evidence._no_dup_keys)


def main():
    manifest_schema = _load(REPO / "schemas/evidence-manifest.schema.json")
    failures = []
    counts = {"manifest": 0, "bundle": 0, "authorization": 0}

    for path in sorted(REPO.glob("examples/*/output/*.evidence.json")):
        instance = evidence.load_manifest(path)
        issues = json_schema.validate(instance, manifest_schema)
        issues += evidence.validate_manifest(instance)
        counts["manifest"] += 1
        failures.extend((path, issue) for issue in issues)

    bundle_paths = sorted(set(REPO.glob("examples/*/output/*evidence-bundle.json")))
    bundle_schema_path = REPO / "schemas/evidence-bundle.schema.json"
    if bundle_paths and (evidence_bundle is None or not bundle_schema_path.is_file()):
        failures.append((bundle_schema_path,
                         "bundle instances exist without their runtime and published schema"))
    elif bundle_paths:
        bundle_schema = _load(bundle_schema_path)
        for path in bundle_paths:
            instance = evidence_bundle.load_bundle(path)
            issues = json_schema.validate(instance, bundle_schema)
            issues += evidence_bundle.validate_bundle(instance)
            counts["bundle"] += 1
            failures.extend((path, issue) for issue in issues)

    authorization_instances = []
    for path in sorted(REPO.glob("examples/*/output/*.jsonl")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            event = json.loads(line, object_pairs_hook=evidence._no_dup_keys)
            if isinstance(event, dict) and event.get("authorization") is not None:
                authorization_instances.append(
                    (Path("%s:%d" % (path, line_number)), event["authorization"]))
    authorization_schema_path = REPO / "schemas/evidence-authorization.schema.json"
    if authorization_instances and (evidence_bundle is None or not authorization_schema_path.is_file()):
        failures.append((authorization_schema_path,
                         "authorization instances exist without their runtime and published schema"))
    elif authorization_instances:
        authorization_schema = _load(authorization_schema_path)
        for path, instance in authorization_instances:
            issues = json_schema.validate(instance, authorization_schema)
            issues += evidence_bundle.validate_authorization(instance)
            counts["authorization"] += 1
            failures.extend((path, issue) for issue in issues)

    if failures:
        for path, issue in failures:
            print("INVALID %s — %s" % (path, issue), file=sys.stderr)
        print("evidence schema validation FAILED — %d violation(s)" % len(failures), file=sys.stderr)
        return 1
    print("evidence schemas OK — %(manifest)d manifests, %(bundle)d bundles, "
          "%(authorization)d authorization envelopes" % counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
