# Verification

> No "probably works" sign-offs.

A change to an agent isn't done when the code is written. It's done when it's *verified*.
Agentic PeopleOS uses a fixed checklist as a gate — every build/change passes all of it or it
isn't shipped.

## The gate

1. **The agent loads and runs.** The scheduler/daemon confirms it's actually running, not
   just present on disk.
2. **Its data layer is initialized** with the expected schema and populated.
3. **A first real run completes successfully** end to end.
4. **The manifest/registry is updated** to reflect the change.
5. **A concise completion summary** is delivered to the owner.

## Rollback is mandatory, not optional

Before any change to a live agent:

- Stage the change so it can be reverted in one command (version control is the durable
  rollback layer; a timestamped pre-edit backup covers anything outside it).
- Write down the exact restore command *before* applying the change.
- **Stop condition:** if a daemon shows a non-zero exit after the change, stop immediately,
  restore, re-validate health, and report — before doing anything else.

## Why a reviewer should care

Anyone can make an agent work once. The discipline that keeps 30 of them working for
months is treating every change as reversible and every "done" as something you can prove.
