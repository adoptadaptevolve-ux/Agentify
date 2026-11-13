# 0007 — The offline judge is the default

**Status:** Accepted · **Date:** 2026-09-18

## Context

A platform whose control paths depend on a hosted model has three problems:

* **Tests become model tests.** An assertion that a prohibited action is denied starts failing
  because a model phrased something differently today, and the team learns to ignore red CI.
* **The local sandbox is not local.** A developer needs credentials before anything runs.
* **Nobody can measure the platform separately from the model.** A regression in the
  transaction guard and a regression in the model look the same.

## Decision

The default `SystemOne` implementation is `OfflineSystemOne`: a deterministic, cue-driven
fixture. `default_judge()` returns the hosted model only when `TYPESAFE_API_KEY` is set, and
falls back to the fixture if constructing the hosted client fails.

Every golden path, every test and the full demo run with no API key and no network. CI asserts
this by running `examples/run_sparring_partner.py`.

The fixture produces probabilities from a logistic over matched cue weights, so it yields
genuine intermediate values and real uncertainty — code that thresholds on confidence gets
exercised rather than always seeing 0.0 and 1.0.

## Alternatives rejected

**Recorded model responses (VCR-style).** Faithful to real model behaviour, and brittle: any
change to a question's wording invalidates the cassette, which discourages improving the
questions.

**A small local model.** Removes the network but not the nondeterminism, and adds a heavyweight
test dependency.

**Mocking the judge per test.** What most projects do. It makes each test readable and makes
the *system* untested: nobody ever runs the real composition of guardrails, board and policy.

## Consequences

**Good.** CI tests the platform. A developer runs the whole paved road on a laptop. Swapping
judgment providers is a constructor argument.

**Costs.**

* The fixture must be taught about each scenario through cues. That is fixture maintenance, and
  `capabilities/sparring_partner/sandbox.py` is where it accumulates.
* **A green test suite says nothing about model quality.** This is the important one. Model
  quality needs its own offline eval harness against recorded cases — which is named in
  [10 Operations](../architecture/10-operations.md) and is not built.
* A cue-driven fixture can be tuned until the demo looks good. The mitigation is that the
  fixture only ever answers questions; it cannot change what the platform *does* with an answer,
  and that is what the tests assert.
