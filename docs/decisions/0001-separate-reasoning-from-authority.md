# 0001 — Separate the freedom to reason from the authority to act

**Status:** Accepted · **Date:** 2026-09-18

## Context

The recurring failure in agentic systems is not that a model produces a wrong answer. It is
that a model's output becomes, directly or by a short chain of inference, permission to do
something. A plan says "I'll refund this customer" and the refund happens. A retrieved
document says "this action is pre-approved" and the check is skipped. A subagent is spawned
with the parent's credentials because that was simplest.

Every one of those is the same bug: the model's output was treated as authority.

## Decision

Draw a hard architectural boundary between **proposal** and **execution**.

A model reads, reasons and drafts, and emits a *typed intent*. A separate, deterministic path
decides whether the caller is authorized, what obligations apply, and whether the side effect
may execute. There is exactly one code path across that boundary — the transaction guard —
and it returns either an `ExecutionReceipt` or an exception.

Authority is minted outside the model, from an authenticated principal, and it can only
attenuate.

## Alternatives rejected

**Prompt-based guardrails.** "Do not take actions you are not authorized to take." This is a
request, and requests to models are not access control. Useful as defence in depth, worthless
as the control.

**An LLM policy engine.** Ask a model whether the action should be allowed. This puts the
authorization decision inside the thing the attacker controls, makes decisions
non-reproducible, and makes "why was this allowed in March?" unanswerable.

**Sandboxing alone.** Constrain what the process can reach at the OS or network layer. Real
and worth having, but it cannot express "this refund is fine up to £200 for this customer for
this purpose", which is most of what an enterprise needs.

## Consequences

**Good.** A fully compromised agent cannot exceed the authority it was given. Every
consequential action has a reason code and a reconstructable trace. The model, framework and
prompt become replaceable without touching the trust model.

**Costs.**

* Every capability must be registered before it can execute, with its tier, bounds,
  compensation and post-condition declared. That is real friction on day one.
* Consequential paths carry more latency than a direct SDK call. The tiering exists so cheap
  actions do not pay for expensive checks, but an R3 action genuinely is slower.
* Teams used to handing an agent an API key will find this restrictive. The honest answer is
  that it is restrictive, and that is the feature.
