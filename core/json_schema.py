#!/usr/bin/env python3
"""Small offline validator for the closed JSON Schemas shipped by this repository.

The project intentionally has no runtime dependencies. This module implements the
Draft 2020-12 keywords used by the evidence schemas so CI validates real instances
against the published contracts instead of merely checking that schema files parse.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re


class SchemaError(ValueError):
    """Raised when a schema reference is invalid."""


def _json_equal(left, right):
    if type(left) is not type(right):
        return False
    return left == right


def _type_matches(value, expected):
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return isinstance(value, float) and math.isfinite(value)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    raise SchemaError("unsupported schema type %r" % expected)


def _resolve(root, reference):
    if not isinstance(reference, str) or not reference.startswith("#/"):
        raise SchemaError("only local JSON Pointer references are supported: %r" % reference)
    node = root
    for raw in reference[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            raise SchemaError("schema reference does not resolve: %s" % reference)
        node = node[key]
    return node


def _stable(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def validate(instance, schema):
    """Return human-readable violations for the supported closed-schema subset."""
    violations = []

    def walk(value, rule, path):
        if not isinstance(rule, dict):
            raise SchemaError("schema node at %s must be an object" % path)
        if "$ref" in rule:
            walk(value, _resolve(schema, rule["$ref"]), path)
            return
        if "oneOf" in rule:
            matches = []
            for option in rule["oneOf"]:
                local = []
                before = len(violations)
                walk(value, option, path)
                local.extend(violations[before:])
                del violations[before:]
                if not local:
                    matches.append(option)
            if len(matches) != 1:
                violations.append("%s must match exactly one oneOf branch" % path)
            return
        if "const" in rule and not _json_equal(value, rule["const"]):
            violations.append("%s must equal %r" % (path, rule["const"]))
        if "enum" in rule and not any(_json_equal(value, item) for item in rule["enum"]):
            violations.append("%s is not in the allowed enum" % path)
        expected = rule.get("type")
        if expected is not None:
            expected_types = [expected] if isinstance(expected, str) else expected
            if not isinstance(expected_types, list) or not any(
                    _type_matches(value, item) for item in expected_types):
                violations.append("%s has the wrong JSON type" % path)
                return
        if isinstance(value, dict):
            required = rule.get("required", [])
            for key in required:
                if key not in value:
                    violations.append("%s missing required property %r" % (path, key))
            properties = rule.get("properties", {})
            if rule.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        violations.append("%s has unknown property %r" % (path, key))
            for key, child in value.items():
                if key in properties:
                    walk(child, properties[key], "%s.%s" % (path, key))
        if isinstance(value, list):
            if "minItems" in rule and len(value) < rule["minItems"]:
                violations.append("%s needs at least %d item(s)" % (path, rule["minItems"]))
            if rule.get("uniqueItems"):
                try:
                    encoded = [_stable(item) for item in value]
                except (TypeError, ValueError):
                    violations.append("%s contains a non-JSON item" % path)
                else:
                    if len(encoded) != len(set(encoded)):
                        violations.append("%s must contain unique items" % path)
            if "items" in rule:
                for index, item in enumerate(value):
                    walk(item, rule["items"], "%s[%d]" % (path, index))
        if isinstance(value, str):
            if "minLength" in rule and len(value) < rule["minLength"]:
                violations.append("%s is shorter than minLength" % path)
            # JSON Schema defines ``pattern`` as a search, not an implicit full match.
            # Closed full-string contracts in this repository therefore carry their
            # own anchors and explicit CR/LF rejection in the published schema.
            if "pattern" in rule and re.search(rule["pattern"], value) is None:
                violations.append("%s does not match its pattern" % path)
            if rule.get("format") == "date":
                try:
                    dt.date.fromisoformat(value)
                except ValueError:
                    violations.append("%s is not a real calendar date" % path)
            elif rule.get("format") == "date-time":
                try:
                    dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    violations.append("%s is not a real UTC timestamp" % path)

    walk(instance, schema, "$")
    return violations
