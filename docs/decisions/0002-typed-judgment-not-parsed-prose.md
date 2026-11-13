# 0002 — Typed System One judgment, not parsed prose

**Status:** Accepted · **Date:** 2026-09-18

## Context

The platform needs judgments constantly: is this injection, is this claim supported, which
capability does this mean, how consequential is this. The default approach is to ask a
general-purpose model in natural language and parse what comes back.

That has three problems that matter at an enforcement boundary.

1. **The answer space is open.** Even with a JSON schema, a model can return a capability
   name, a destination or an approver that does not exist. The schema constrains the shape;
   the platform still has to validate every value.
2. **Uncertainty is prose.** "This seems likely, though it's hard to be sure" cannot be
   thresholded. A system that wants to route genuinely uncertain cases to humans needs a
   number.
3. **Decomposition is expensive.** Twenty narrow questions is the right design and twenty
   round-trips is the wrong cost, so teams ask one broad question instead — and a broad
   question is one a model can be talked out of.

## Decision

Use a **System One** judgment plane with three typed primitives: `Noul` (is this true? → a
calibrated probability), `Choice` (which of these? → one supplied option plus a distribution),
`Score` (which level? → a weighted position on an ordered rubric).

The platform's reference implementation targets TypeSafe's Jev via the `SystemOne` protocol,
documented at <https://docs.typesafe.ai/>.

Three properties follow:

* **Closed answer spaces.** A `Choice` whose options *are* the capability registry cannot name
  an unregistered capability.
* **Uncertainty is a number.** `confidence` is a statistic over the distribution the answer
  already gives you, so policy can gate on it.
* **Questions batch.** Independent questions about one state evaluate in one call, so twenty
  falsifiers cost about what one does.

A judgment is never authorization. It produces a named signal; the deterministic policy engine
decides.

## Alternatives rejected

**Structured output from a general model.** Closer than prose, and the right fallback if no
System One model is available — but the values are still generated rather than selected, and
the probabilities, where they exist at all, are not calibrated for thresholding.

**A fine-tuned classifier per question.** Better calibration, far worse ergonomics: every new
falsifier becomes a training job, which kills the "suites should grow after every incident"
property that makes falsification useful.

**Logprob extraction from a chat model.** Workable for binary questions, fragile across model
versions and providers, and not something to build an enforcement boundary on.

## Consequences

**Good.** Guardrails are cheap enough to run on every consequential action. Confidence
routing is possible. Judgment calls are recorded as typed values that replay cleanly.

**Costs.**

* A dependency on a judgment provider for production-quality answers. Mitigated by the
  `SystemOne` protocol and [ADR 0007](0007-offline-judge-by-default.md), but real.
* Questions must be written narrowly and with explicit criteria. That is a skill, and a badly
  written falsifier is worse than none because it looks like coverage.
* Some judgments genuinely need open-ended reasoning. Those belong inside a workflow step
  using a System Two model, whose output is then a *proposal*, not a signal.
