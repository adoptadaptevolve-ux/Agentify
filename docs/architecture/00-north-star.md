# 00 — North star

## The one sentence

> Build one enterprise paved road where agents may reason with approved models and
> enterprise knowledge, but every real-world side effect is executed only through a
> typed capability, verified delegation, deterministic policy, transaction guardrails
> and a complete evidence trail.

## The thesis

**Separate the freedom to reason from the authority to act.**

Frameworks, models and orchestration techniques change every few months. The contracts
for authority, data access, side effects, evidence and lifecycle governance have to
outlast all of them. So the platform standardises the *execution contract*, the
*security model*, the *evidence schema* and the *developer experience* — and keeps the
agent framework replaceable.

This is what lets product teams innovate quickly without every new agent becoming a new
security perimeter.

## The invariants

Non-negotiable. Each is enforced structurally and asserted by a test.

| Invariant | Why it matters |
|---|---|
| **LLM output is advice, never authority.** | A model cannot grant itself access, create approval state or widen scope. Authorization is server-side and deterministic. |
| **No standing agent privilege.** | Every sensitive capability uses scoped, short-lived authority derived from an authenticated principal and a task. |
| **Every side effect crosses a policy enforcement point.** | No SDK, credential, network route or connector may bypass the enforcement path. |
| **Untrusted content cannot modify control state.** | Prompts, web pages, retrieved documents, tool output and memory are data — not policy. |
| **Delegation can only attenuate.** | A subagent receives equal or narrower rights than its parent, never broader. |
| **Approval binds to an action digest.** | Approval for one payload cannot be replayed or substituted for another. |
| **Authorization uncertainty fails closed.** | Timeout, dependency failure or ambiguous identity never converts into permission. |
| **Every action is idempotent or explicitly not.** | Retries and failovers must not duplicate transfers, refunds, messages or mutations. |
| **Every action produces evidence.** | Audit is part of the transaction, not an asynchronous best-effort log. |
| **Risk changes the execution path.** | Read, reversible write, customer-impacting and privileged actions do not share one control path. |
| **Policies are code and artifacts.** | Versioned, tested, signed, staged, replayable, independently reviewable. |
| **Safe shutdown is a first-class feature.** | Kill switch, connector revocation, model disablement and workflow suspension are designed before production, not after an incident. |

## What the invariants cost

An architecture document that lists only benefits is marketing. These are the real
trade-offs, and a team adopting this should accept them deliberately.

**Flexibility.** A workflow can only go where its edges go. A genuinely novel situation
ends the run rather than improvising through it. That is the cost of being able to
checkpoint, resume, replay and review.

**Latency on consequential paths.** An R3 action carries a guardrail sweep, a policy
evaluation, an idempotency reservation, a post-execution verification and two evidence
writes. R0 and R1 paths are cheap; the expensive path is the one where the expense is
worth it, and the tiering exists so the two do not share a budget.

**Registration friction.** A capability that is not registered cannot execute. Adding
one means declaring its tier, its bounds, its compensation and its post-condition. This
is deliberate: those declarations are what the platform enforces, and a team that cannot
state them has not finished designing the capability.

**Human review is finite.** Routing everything ambiguous to a queue destroys the queue.
The platform treats review budget as a scarce resource — domain queues, digest binding,
evidence packets with alternatives and rollback — but a team that sets its thresholds
carelessly will still produce alert fatigue, and no architecture prevents that.

## Where to go next

[01 — Overview](01-overview.md) assembles these invariants into a system.
