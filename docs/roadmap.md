# Roadmap

Honest status. The prototype covers phase 0 and most of phase 1.

## Built

* Canonical contracts: action envelope, signed attenuating delegation, risk tiers, evidence
  schema, the eight canonical service protocols
* Authority: HMAC signer, issuer, verifier, nonce store, JIT credential broker
* Policy: tiered L0–L3 engine, versioned and signed packs, composition, simulation
* Capability registry with schema normalisation, semantic validators, verifiers, compensation,
  kill switch, rate limits
* Typed judgment plane with a hosted adapter and a deterministic offline fixture
* Falsification suites and the supervisor board
* Transaction guard: digest binding, obligation discharge, idempotency, JIT credentials,
  post-execution verification
* Human decision service: digest-bound, domain-queued, dual control, expiry-denies
* Hash-chained evidence ledger, run reconstruction, forensic export
* Workflow runtime: explicit graphs, budgets, checkpoints, pause and resume, cancellation
* SDK and CLI
* One golden path end to end: the genAI sparring partner

## Next — consequential actions

* Compensation execution: the guard declares and records compensations but does not yet run
  them on a failed multi-step journey
* Durable stores behind the approval queue, idempotency store and nonce store
* KMS/HSM `Signer` implementation
* Semantic egress broker with format-aware DLP, beyond the current typed egress questions
* Red-team automation: an injection corpus run against every registered capability in CI

## Then — enterprise scale

* Multi-region cells, operators and CRDs
* Signed policy bundle distribution with bounded-staleness caching
* Entitlement-aware retrieval caching with correct invalidation
* Cost routing by risk and capability
* Self-service onboarding and fleet governance
* The offline eval harness named in [10 Operations](architecture/10-operations.md) — currently
  the largest gap, because a green test suite says nothing about model quality

## Later — research

Hierarchical memory, multimodal retrieval and guardrails, adaptive routing, agent-to-agent
protocols, policy optimisation. Only after the authority and evidence invariants are stable and
measured.

## Deliberately not planned

**A second agent framework adapter, yet.** Adding one before a second real workload exists
would produce an abstraction fitted to one example.

**A visual policy editor.** JSON is a poor authoring surface, but the answer is probably a pack
DSL or a generator, not a GUI over the current schema.
