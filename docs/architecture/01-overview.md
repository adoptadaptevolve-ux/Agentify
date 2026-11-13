# 01 — Overview

This document is the whole system in one read. Each section links to the deeper one.

## The boundary everything is organised around

```
              PROPOSAL                    │                 EXECUTION
                                          │
   a model reads, reasons, drafts,        │   deterministic code decides whether
   and emits a typed intent               │   the caller is authorized, what
                                          │   obligations apply, and whether the
   nothing here can change the world      │   side effect may execute
```

Everything to the left of that line is replaceable: the model, the framework, the
prompt, the planner. Everything to the right is the platform, and it is stable.

The line is not a convention. There is exactly one code path from a proposed intent to a
side effect — `gateway/transaction_guard.py` — and it returns either an
`ExecutionReceipt` or an exception. A caller that does not hold a receipt caused no
authorized side effect.

## The three planes

**Control plane.** Registries, policy artifacts, risk taxonomies, delegation rules,
approvals, release state. Control artifacts are versioned, signed and independently
inspectable. In this repository: `registry/`, `policy/pack.py`, `policies/*.json`.

**Execution plane.** Workflows, inference, retrieval, memory and tool calls running with
just-in-time credentials. Protected-system adapters expose narrow capabilities, never
raw infrastructure credentials. Here: `runtime/`, `judgment/`, `knowledge/`, `gateway/`.

**Evidence plane.** Tamper-evident run and action events, quality and safety metrics,
evaluation results. Evidence schemas are stable across frameworks and models. Here:
`evidence/`.

## Walking one action end to end

This is the sparring partner opening a review case. Every numbered step is a real
function call in the demo.

**1. A run begins.** `Platform.begin()` mints a **root delegation** from an authenticated
`Principal` and an attested `WorkloadIdentity`. The capability set named here is the
ceiling for the entire run, including anything it spawns. The run cannot later decide it
needs one more capability — that decision belonged to whoever authorised the run.
→ [03 Authority](03-authority.md)

**2. Untrusted input is swept.** The proposal is a document someone pasted. It goes
through the platform's injection questions before anything reads it for content. A
document is data, never policy. → [05 Judgment](05-judgment.md)

**3. Knowledge is retrieved, filtered first.** The ACL check happens *in the query*,
against the caller's real entitlements, before any chunk is loaded. A model that has been
shown an unauthorized document has been shown it; an instruction not to use it is a
request, and requests to models are not access control. What was withheld is reported,
not hidden. → [09 Knowledge](09-knowledge.md)

**4. Claims are attacked.** The falsification suite runs — twenty narrow questions whose
*yes* is bad news — in one batched judgment call. The verdict is `SURVIVED`, `REFUTED` or
`INCONCLUSIVE`. Never `CORRECT`. → [06 Verification](06-verification.md)

**5. The board deliberates.** Five or six supervisors, each narrow, each independent,
each returning its own verdict. A pure function aggregates them into two numbers.
→ [06 Verification](06-verification.md)

**6. An intent is proposed.** Arguments are validated against the capability's schema and
**normalised**, and only then does the action get an identity: `ActionIntent.digest`.
Normalising first is what makes the digest stable across clients that spell an amount
differently — which is what makes approval binding and idempotency work at all.
→ [02 Contracts](02-contracts.md)

**7. The envelope is narrowed and verified.** The run's delegation is attenuated down to
exactly the one capability being called, so the gateway sees the least authority that can
do the job. Signature, validity window, audience and capability presence are checked
before any other field is read.

**8. Policy decides.** Four tiers, and only as deep as the action requires:

| Tier | Checks | Semantics |
|---|---|---|
| **L0** local invariant | kill switch, prohibited list, tenant, principal, capability presence, expiry, risk ceiling | single-digit ms, no remote dependency, fail closed |
| **L1** cached authorization | deny-overrides rules, default deny, auth strength, attestation, resource scope | from a signed bundle with bounded staleness |
| **L2** transactional risk | amount, currency, destination allowlist, injection and egress signals, velocity, proposed-tier escalation | runs only when the action can change something |
| **L3** exceptional | irreversible, R4+, board concern, low confidence | pauses for a human; never times out into allow |

The engine is **pure**: intent, delegation, capability and signals in, a `PolicyDecision`
out. No I/O, no model call. That is what makes a recorded decision replayable.
→ [04 Policy](04-policy.md)

**9. Obligations are discharged.** Approval, dual control, step-up authentication,
attested workload, idempotency key, declared compensation. An obligation the gateway does
not recognise is a **denial** — a new control must never be able to become a silent
no-op, and a test asserts the gateway handles every obligation the contract can express.

**10. A human decides, if required.** The approval item binds to the action digest,
routes to a **domain queue**, and carries an evidence packet with risk factors,
alternatives and the rollback path. Expiry denies. An approval is spent once. When it is
granted, the decision is explicitly upgraded from `REVIEW` to `ALLOW` — in one place, in
the open, keeping the same decision id so evidence still ties the execution to the
decision that was reviewed.

**11. The workflow pauses and resumes.** `ApprovalRequired` is raised; the runner
checkpoints and marks the run `PAUSED`. Later, `resume()` re-enters the same step. The
idempotency key means re-running it cannot duplicate what it already did.
→ [08 Runtime](08-runtime.md)

**12. Credentials are minted.** Only now. Scoped to the capability and resource, expiring
in the order of the action rather than the session, carried as a reference so no secret
material reaches a checkpoint, a log or a stack trace.

**13. The side effect executes, and is verified.** The capability's post-condition is
what turns "the call returned" into "the world changed". Failing it produces
`UNVERIFIED` — queued for reconciliation, never retried blindly.

**14. Evidence is written inside the transaction.** The receipt and its ledger entry are
part of the same call, so "the action happened but nothing recorded it" is not a
reachable state. → [07 Evidence](07-evidence.md)

## What makes this different from a framework

Most agent frameworks give you an orchestration loop and leave authority, policy and
evidence to you. Causeway inverts that: the orchestration is the small, replaceable part,
and the trust contracts are the product.

Three specific choices are unusual enough to have their own decision records:

1. **Typed judgment instead of parsed prose.** `Noul`, `Choice`, `Score` — closed answer
   spaces, calibrated probabilities, batched evaluation.
   → [ADR 0002](../decisions/0002-typed-judgment-not-parsed-prose.md)
2. **Falsification instead of confirmation.** Never ask a model whether something is
   right; ask, per failure mode, whether there is evidence it is wrong.
   → [ADR 0003](../decisions/0003-falsification-over-confirmation.md)
3. **A board of supervisors aggregated by code.** Many narrow reviewers; a pure function
   combines them; no model gets to decide what the board concluded.
   → [ADR 0004](../decisions/0004-a-board-aggregated-by-code.md)

## The developer surface

A domain team touches one object. It exposes only governed operations — there is no
`raw_model()`, no `http()`, no way to reach a credential. A team that can only do
governed things cannot accidentally do an ungoverned one.

```python
run.retrieve(...)      # entitlement-filtered, cited, budget-charged, recorded
run.remember(...)      # scoped, TTL-bounded, provenance attached
run.ask(...)           # typed questions, batched, recorded
run.falsify(suite, s)  # a whole suite in one call
run.deliberate(...)    # board, deterministic aggregation
run.invoke_tool(...)   # guardrails → policy → obligations → execute → verify → evidence
run.spawn(...)         # a subagent with strictly narrower authority
run.emit_evidence(...) # a domain milestone in the chain
```

Guardrails are automatic for any side-effecting capability. Budgets charge automatically.
Delegation narrows on every hop. None of this is opt-in, and none of it can be forgotten.

## Where the prototype stops

See *Production gaps* in the README. The short version: signing, evidence storage,
approval queues, idempotency and nonce stores are in-memory reference implementations
behind protocols. Replacing them is a constructor argument, not a migration — which was
the point of writing the domain code against contracts.
