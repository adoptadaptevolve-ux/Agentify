# 03 — Authority

Identity is a workload-security problem, not a prompt-engineering problem. A compromised
model or agent process must be unable to forge who authorized an action or what scope was
granted.

## Nothing here comes from a model

`Principal`, `WorkloadIdentity`, `Delegation` and `Constraints` are built by the runtime
from the authenticated session and the deployment's own attested identity. None of them
is ever populated from model output, retrieved documents or tool results.

`Principal.roles` and `.entitlements` are what enterprise IAM asserted, not what a prompt
claimed. `AuthStrength` is an ordered enum so policy can require a floor with a
comparison — and `UNAUTHENTICATED = 0` exists so an absent credential is represented
explicitly and denies, rather than being `None` and accidentally passing a truthiness
check.

## The attenuation invariant

> A child delegation may receive equal or narrower rights than its parent, never broader.

This is enforced **structurally**. `Delegation.attenuate()` is the only way to derive a
child, and it raises `AttenuationViolation` before a widened envelope exists to be
signed. There is no code path that produces a signed envelope holding rights its parent
did not hold.

So an agent that is fully compromised — one that can call every function in
`causeway/authority/` — still cannot mint itself authority. The worst it can do is spend
the authority it was already given.

Attenuation is checked on every dimension:

| Dimension | Rule |
|---|---|
| capabilities | child set must be a subset |
| `max_amount` | child must be bounded and no larger |
| `max_risk_tier` | child must be bounded and no higher |
| destinations, resources, currencies, data classes | child set must be a subset |
| `expires_at` | child must expire no later |
| principal | cannot change |

A dimension set to `None` means *this envelope did not narrow it further* — not
"unbounded in the world". The capability registry and the policy pack place their own
bounds, and the effective bound is the tightest of all of them.

## Signing

The envelope **body** is what is signed and hashed. Signatures live outside it in
`SignedDelegation`, so the body digest stays stable and can serve as the `parent_hash` of
the next link in the chain.

The reference `HmacSigner` is right for a prototype and wrong for production: a symmetric
key means any service that can verify can also mint. Production implements the `Signer`
protocol against an HSM, KMS or workload-identity signing service, so minting authority
is a separate trust domain from verifying services. `key_id` travels with every
signature, so keys rotate without invalidating live envelopes.

## Verification, in order

1. **Signature** — every other field is only meaningful once the envelope is authentic.
2. **Validity window**, with a deliberately small clock leeway. A large leeway extends the
   life of a revoked or spent envelope.
3. **Audience** — an envelope minted for the tool gateway cannot be spent elsewhere.
4. **Capability presence**.
5. **Nonce consumption**, where single-use semantics are required.

Every check fails closed. A missing key, an unparseable envelope and an expired envelope
all produce refusals, never a downgraded allow.

## Zero standing privilege

Agents never receive long-lived secrets. A capability handler is *handed* a
`CredentialGrant` as an argument, minted only after the policy decision is `ALLOW` and
every obligation is discharged. The grant carries a `secret_ref`, never the secret — which
keeps credential material out of checkpoints, evidence and stack traces — and expires in
the order of the action, not the session.

## Least authority on every hop

`AgentRun.invoke_tool` attenuates the run's envelope down to exactly the one capability
being called before presenting it to the gateway. `AgentRun.spawn` attenuates to the
subagent's narrower set. Neither is optional.

The demo shows both layers holding independently: a run that was never granted a
prohibited capability fails at attenuation, and a run that *was* granted it is still
denied by policy at L0. Defence in depth means the second control holds on its own, not
because the first one fired.
