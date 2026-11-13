# 02 — Canonical contracts

Teams choose their orchestration. They do not invent trust contracts.

Everything in `causeway/contracts/` is either a frozen dataclass or a `Protocol`. Nothing
in that package performs I/O, calls a model or executes anything — which is what lets a
team read the entire trust model in one sitting.

## The eight services

| Contract | Minimum obligation |
|---|---|
| **Agent Runtime** | run / resume / cancel; durable state; bounded subagent spawn; checkpoints; budgets; deterministic replay metadata |
| **Model Gateway** | approved model id, data class, task risk, latency/cost tier, structured output, declared fallback policy |
| **Retrieval** | query + authenticated caller + purpose → ACL-filtered cited chunks with source, version, classification, freshness |
| **Memory** | scoped read/write with owner, purpose, TTL, provenance, confidence, sensitivity, deletion semantics |
| **Tool Invocation** | typed intent + signed delegation + idempotency key → policy decision → execution receipt |
| **Policy** | allow / deny / review + constraints + obligations + reason code + policy version + risk tier. No model-generated authorization |
| **Human Decision** | approval item bound to an immutable action digest; signed decision + approver authority + expiry |
| **Evidence** | append-only events for identity, agent, model, retrieval, policy, tool, approval, side effect and outcome |

## Canonical encoding

Everything the platform signs, approves, caches or audits is identified by the digest of
its **canonical** encoding, never by the bytes that arrived on the wire.

* objects encode with sorted keys and no insignificant whitespace;
* text is NFC-normalised, so `café` and `café` are the same string;
* `Decimal` encodes as a string — money must not round-trip through binary floats;
* `datetime` encodes as UTC RFC 3339 with a `Z`; naive datetimes are refused;
* sets encode as sorted arrays;
* non-finite floats are refused rather than encoded as `NaN`;
* digests are self-describing (`sha256:…`), so stored evidence records its own algorithm.

Without this, two structurally identical payloads could produce different digests, and an
attacker could re-order a dict to present an approved action as a new one — or, worse, a
reviewer could approve one rendering of an action while a different one executes.

## The action digest

`ActionIntent.digest` is the identity of a proposed side effect. A policy decision, a
human approval and an execution receipt all bind to it.

It covers: capability, **normalised** arguments, purpose, tenant, run id, principal,
resource, destination, amount, currency.

It deliberately **excludes**:

* `action_id` — so a retry of the same proposal reuses an existing approval;
* `idempotency_key` — two attempts at the same payload are the same action to a reviewer.

It deliberately **includes** `run_id`, so an approval granted in one run can never be
spent in another.

## Obligations

An obligation is how policy says "yes, but". The gateway enforces every obligation it
receives and **refuses any it does not recognise**, so adding one to a policy pack can
never silently become a no-op.

`REQUIRE_HUMAN_APPROVAL` · `REQUIRE_DUAL_CONTROL` · `REQUIRE_STEP_UP_AUTH` ·
`REQUIRE_ATTESTED_WORKLOAD` · `REQUIRE_IDEMPOTENCY_KEY` ·
`REQUIRE_COMPENSATION_DECLARED` · `VERIFY_AFTER_EXECUTE` · `EMIT_ENHANCED_EVIDENCE` ·
`REDACT_PROTECTED_FIELDS`

`tests/test_obligations.py` asserts `HANDLED_OBLIGATIONS == frozenset(Obligation)`.
Adding a control to the contract without teaching the gateway to enforce it breaks CI.

## Errors are contracts too

Every refusal is a distinct type carrying a machine-readable `reason_code` that appears
verbatim in evidence. "Denied" and "we could not tell" are not the same outcome to an
auditor, so they are not the same exception.

Nothing in `causeway/errors.py` inherits from a success path. There is no exception a
caller can swallow to obtain an allow.
