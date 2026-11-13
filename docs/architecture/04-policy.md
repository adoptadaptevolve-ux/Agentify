# 04 — Policy

## Why tiered, not monolithic

A single "sub-100 ms policy engine" is the wrong abstraction. It forces cheap,
always-required checks to share a latency budget with expensive, occasionally-required
ones — and the usual resolution is to make the expensive checks optional, which is
exactly backwards.

So the path is tiered. Deterministic authorization always runs. Transactional risk
analysis runs when the action can actually cost something. The exceptional path pauses for
a human and never times out into an allow.

| Tier | What it checks | Latency / failure semantics |
|---|---|---|
| **L0** local invariant | kill switch, prohibited list, tenant boundary, principal match, capability presence, expiry, delegated risk ceiling | single-digit ms, no remote dependency, fail closed |
| **L1** cached authorization | rules (deny-overrides, default deny), auth strength floors, workload attestation, resource scope | low tens of ms from a signed bundle with bounded staleness |
| **L2** transactional risk | amount, currency, destination allowlist, injection and egress signals, velocity, proposed-tier escalation | synchronous, runs only for `has_side_effect` capabilities |
| **L3** exceptional | irreversible actions, R4+, board concern, low confidence | pauses the workflow; never times out into allow |

Every decision records `tier_reached`, so the platform can measure how often the expensive
paths run, and an auditor can see which checks *actually ran* rather than which exist.

## The engine is pure

`PolicyEngine.evaluate()` takes an intent, a delegation, a capability and a dict of
signals, and returns a `PolicyDecision`. No I/O, no model call, no clock beyond the one it
is handed.

That purity is what makes a recorded decision replayable, and what lets the whole rule set
be tested without a running platform.

**The engine consumes judgment signals but never produces them.** A caller that wants an
injection probability asks the judgment plane and passes the number in. That separation
keeps "what did the model think" and "what did the platform decide" as two auditable
steps rather than one.

Only names in `ALLOWED_SIGNALS` are read. An unknown signal is dropped, not trusted, so a
caller cannot invent a signal name that happens to relax a threshold.

## Risk tiers

| Tier | Examples | Control posture |
|---|---|---|
| **R0** informational | public knowledge, non-sensitive drafting | model gateway, content safety, basic telemetry |
| **R1** internal read | entitled policy search, case summarisation | ACL-before-retrieval, citations, classification boundary |
| **R2** reversible write | create a ticket, draft a communication | typed tool, idempotency, rollback, post-action verification |
| **R3** customer or financial impact | send a notice, bounded refund, account update | step-up checks, recipient/amount/purpose constraints, enhanced evidence |
| **R4** high consequence | large monetary action, access change, destructive admin | dual control or named approver, strong auth, digest binding |
| **R5** prohibited autonomy | institution-defined | hard deny in the agent path; a separate controlled process exists |

Tiers are an `IntEnum` so policy expresses floors and ceilings with comparisons, and so a
judgment plane's *proposed* tier can be clamped against the registry's declared tier.

**A proposal can only ever raise a tier, never lower it.** An unusually large instance of
an ordinarily routine action should attract more control; talking the platform down a tier
is exactly what an attacker would want.

One subtlety worth knowing: a `Score` answer is a probability-weighted mean, so an
unambiguous "level 2" arrives as `2.16`. The engine compares the **rounded** level against
the registered tier — comparing the raw mean would escalate almost every action by a
rounding artifact. The raw value stays in `signals` for audit.

## Tier postures

Postures are declared per tier rather than per capability, so a new R3 capability inherits
R3's controls the day it is registered, instead of depending on whoever registered it
having remembered them.

A tier with no declared posture inherits the nearest lower tier's, so adding R4 to a pack
that only declares up to R3 tightens rather than silently dropping controls.

## Policy packs

Policy is not code scattered through the runtime, and it is not a prompt. It is a data
artifact with a version, a signature and a test suite. That makes three things possible
that matter more than any individual rule:

* **Replay.** "Why was this allowed in March?" is answerable by evaluating the March pack
  against the recorded intent, because the pack version is on the decision.
* **Simulation.** A proposed pack runs against recorded traffic before it ships, so a
  tightening is measured rather than discovered.
* **Independent review.** Risk and security read the pack without reading the runtime, and
  diff two versions without diffing a codebase.

### Evaluation is deny-overrides with a default deny

A rule matches when every stated condition matches; empty conditions are wildcards. Rules
carry **no priorities** — a reader does not have to simulate an ordering in their head to
know what a pack does.

1. Any matching `deny` rule → DENY.
2. No matching `allow` rule → DENY (`no_matching_rule`). An action nobody wrote a rule for
   is not an action the platform has decided to permit.
3. Otherwise ALLOW, with the union of every matching allow rule's obligations plus the
   tier posture's.

### Composition can only tighten

A platform baseline pack and a domain pack evaluate together, and the result is the
tightest of them: denials and obligations union, hard-deny lists union, thresholds take
the strictest value, postures take the strongest requirement. A domain pack can add
obligations and denials but cannot grant what the baseline withholds.

That is the same attenuation rule delegation uses, applied to policy.

## Thresholds live in the pack

Moving the injection-deny threshold from 0.7 to 0.5 is a **risk decision**, not an
engineering decision. A risk owner should be able to make it, review it and replay it
without a deployment, so thresholds are pack data:

`injection_deny` · `egress_deny` · `board_block_review` · `min_decision_confidence` ·
`groundedness_floor`

See the guide: [Policy packs](../guides/policy-packs.md).
