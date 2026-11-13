# 0008 — Policy as a versioned, signed artifact

**Status:** Accepted · **Date:** 2026-09-18

## Context

Authorization logic tends to end up in three bad places: scattered through the runtime as `if`
statements, embedded in prompts, or in a policy service whose rules nobody outside the platform
team can read.

All three make the same three questions unanswerable: why was this allowed in March, what would
this proposed change actually do, and can risk review the rules without reading the codebase.

## Decision

A `PolicyPack` is a data artifact: a name, a version, rules, tier postures, thresholds and a
hard-deny list. It loads from JSON, carries a digest, and can be HMAC-signed.

The engine that evaluates it is **pure** — intent, delegation, capability and signals in, a
decision out, no I/O.

Evaluation is **deny-overrides with a default deny**, and rules carry **no priorities**: a
reader does not have to simulate an ordering in their head to know what a pack does.

Packs **compose to the tightest**: denials and obligations union, thresholds take the strictest
value, postures take the strongest requirement. A domain pack can add controls but cannot grant
what the baseline withholds — the same attenuation rule delegation uses, applied to policy.

Thresholds live in the pack, not in code, because moving the injection-deny threshold from 0.7
to 0.5 is a risk decision that a risk owner should be able to make, review and replay.

## Alternatives rejected

**Rego / OPA.** A serious option, and what a production deployment may well adopt for the L1
tier. Rejected here for the dependency ([ADR 0006](0006-zero-dependency-core.md)) and because a
small, explicit rule model is more readable for a reference architecture whose job is to be
understood.

**Rules in the capability registry.** Convenient, and it conflates two things that change at
different rates and are owned by different people: what a capability *is* versus who may use it.

**Priority-ordered rules.** Familiar from firewalls, and a reliable source of production
surprises. Deny-overrides plus default-deny has one reading.

## Consequences

**Good.** "Why was this allowed?" is answerable by replaying the recorded intent against the
recorded pack version. A proposed pack can be simulated against recorded traffic through the
same code path a real decision uses (`TransactionGuard.simulate`). Risk and security review a
JSON file.

**Costs.**

* A default-deny pack means every new capability needs a rule before it works. That is the
  intended friction, and it will be reported as a bug by every team's first integration.
* JSON is a poor authoring surface for anything complex. A pack DSL or a generator is probably
  needed before a real institution's rule set fits in it.
* Composition to the tightest is easy to state and occasionally surprising: a domain pack author
  who expected to relax a baseline control will find they cannot, which is correct and still
  needs explaining.
