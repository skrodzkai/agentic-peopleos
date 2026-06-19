#!/usr/bin/env bash
#
# newagent.sh — onboard a new agent with the standard fleet structure.
#
# Usage:   ./scaffold/newagent.sh <agent-name> <domain>
# Example: ./scaffold/newagent.sh research-scout intelligence
#
# Creates an agent directory with the three things every agent must have:
#   - SOUL.md           (identity / job description)
#   - run.py            (entrypoint stub)
#   - cost_tracker.json (budget)
# ...and reminds you to add a registry entry. Nothing is overwritten.

set -euo pipefail

AGENT_NAME="${1:-}"
DOMAIN="${2:-}"

if [[ -z "$AGENT_NAME" || -z "$DOMAIN" ]]; then
  echo "usage: $0 <agent-name> <domain>" >&2
  exit 1
fi

if [[ ! "$AGENT_NAME" =~ ^[A-Za-z0-9_-]+$ || ! "$DOMAIN" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "agent-name and domain may only contain letters, numbers, underscores, and hyphens" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_REL_DIR="agents/${DOMAIN}/${AGENT_NAME}"
AGENT_DIR="${ROOT}/${AGENT_REL_DIR}"

if [[ -e "$AGENT_DIR" ]]; then
  echo "refusing to overwrite existing agent at $AGENT_DIR" >&2
  exit 1
fi

mkdir -p "$AGENT_DIR"

# 1) Identity
sed \
  -e "s/<AGENT NAME>/${AGENT_NAME}/g" \
  -e "s/<agent name>/${AGENT_NAME}/g" \
  -e "s/<which part of the org it belongs to>/${DOMAIN}/g" \
  "$ROOT/templates/SOUL.template.md" > "$AGENT_DIR/SOUL.md"

# 2) Budget
sed -e "s/<agent-name>/${AGENT_NAME}/" -e "s/<domain>/${DOMAIN}/" \
  "$ROOT/templates/cost_tracker.template.json" > "$AGENT_DIR/cost_tracker.json"

# 3) Entrypoint stub
cat > "$AGENT_DIR/run.py" <<PY
"""Entrypoint for the ${AGENT_NAME} agent (domain: ${DOMAIN})."""


def main() -> None:
    # 1. Load SOUL.md and honor the immutable guardrails.
    # 2. Pick the cheapest model tier that can do this task.
    # 3. Do the work; fail closed if the world can't be confirmed safe.
    # 4. Log spend to cost_tracker.json and the run to the system of record.
    raise NotImplementedError("implement ${AGENT_NAME}")


if __name__ == "__main__":
    main()
PY

echo "onboarded '${AGENT_NAME}' in ${AGENT_REL_DIR}"
echo "   next steps:"
echo "     1. fill in SOUL.md (identity + immutable guardrails)"
echo "     2. set the budget in cost_tracker.json"
echo "     3. add a registry entry so headcount stays accurate"
echo "     4. run the verification checklist before you call it done"
