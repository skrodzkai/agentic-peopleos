#!/usr/bin/env python3
"""Approval registry for Agentic PeopleOS.

A reaction is not an approval until identity and authority are modeled. Authority is
**role-scoped and satisfied by a pool**: a decision class (scope) requires a role, and
the role is held by *several* people — so any one entitled person can approve and
vacation/illness never blocks the work (no single point of failure). Channels are
ACL'd: only members may post or react.

Config is plain JSON (stdlib only); production can project this onto an IdP/SCIM group.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class ApprovalRegistry:
    """Resolves 'may this actor approve this decision class?' and channel ACLs."""

    def __init__(self, config: dict):
        self.actors = config.get("actors", {})          # id -> {display, kind, role}
        self.roles = config.get("roles", {})            # role -> [actor_id, ...] (the pool)
        self.scopes = config.get("scopes", {})          # scope -> required_role
        self.channels = config.get("channels", {})      # channel -> {"members": [...]}

    @classmethod
    def from_json(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def version(self) -> str:
        """A content hash of the registry. Stamped onto approvals so a decision can be replayed
        against the registry version that was in force when it was made (point-in-time integrity);
        a later registry change surfaces as a version mismatch rather than silently revaluing a
        past approval. (Production stores a full snapshot per version; this hashes the live config.)"""
        parts = {"actors": self.actors, "roles": self.roles, "scopes": self.scopes,
                 "channels": self.channels}
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()[:12]

    # -- approval authority ------------------------------------------------
    def entitled_pool(self, scope: str):
        """Everyone who can currently approve this scope (coverage / bus-factor view)."""
        role = self.scopes.get(scope)
        return list(self.roles.get(role, [])) if role else []

    def can_approve(self, actor_id: str, scope: str):
        """Return (entitled: bool, reason: str)."""
        if scope not in self.scopes:
            return False, f"unknown decision scope '{scope}'"
        role = self.scopes[scope]
        actor = self.actors.get(actor_id)
        if actor is None:
            return False, f"unknown actor '{actor_id}'"
        if actor.get("kind") != "human":
            return False, f"actor '{actor_id}' is not human (kind={actor.get('kind')})"
        if actor_id not in self.roles.get(role, []):
            return False, f"actor '{actor_id}' lacks role '{role}' required for scope '{scope}'"
        return True, f"{actor_id} holds '{role}' (1 of {len(self.roles.get(role, []))} entitled)"

    # -- channel ACL -------------------------------------------------------
    def is_member(self, actor_id: str, channel: str) -> bool:
        return actor_id in self.channels.get(channel, {}).get("members", [])

    def can_react(self, actor_id: str, channel: str):
        if not self.is_member(actor_id, channel):
            return False, f"actor '{actor_id}' is not a member of '{channel}'"
        return True, "channel member"


# A reference registry for the Acme Corp examples (all synthetic).
ACME = {
    # Identities are JOB TITLES, not people (roles, not names). The entitled role is a seat;
    # any of the three HR seats can approve, so vacation/illness/turnover never stalls a decision.
    "actors": {
        "agent.coordinator": {"display": "Coordinator", "kind": "agent", "role": "coordinator"},
        "agent.ta-reporting": {"display": "TA Reporting", "kind": "agent", "role": "reporter"},
        "hr.business-partner": {"display": "People Business Partner", "kind": "human", "role": "hr_approver"},
        "hr.total-rewards": {"display": "Total Rewards Partner", "kind": "human", "role": "hr_approver"},
        "hr.people-ops": {"display": "People Ops Lead", "kind": "human", "role": "hr_approver"},
        "obs.engineering": {"display": "Engineering Observer", "kind": "human", "role": "viewer"},
    },
    "roles": {
        "hr_approver": ["hr.business-partner", "hr.total-rewards", "hr.people-ops"],
        "viewer": ["obs.engineering"],
        "coordinator": ["agent.coordinator"],
        "reporter": ["agent.ta-reporting"],
    },
    "scopes": {
        "publish.ta_report": "hr_approver",
        "publish.comp_summary": "hr_approver",
        "publish.operating_review": "hr_approver",
    },
    "channels": {
        "people-analytics": {
            "members": ["agent.coordinator", "agent.ta-reporting",
                        "hr.business-partner", "hr.total-rewards", "hr.people-ops", "obs.engineering"],
        },
    },
}
