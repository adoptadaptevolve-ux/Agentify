# Policy packs

Policy is a versioned data artifact, not code and not a prompt. See
[ADR 0008](../decisions/0008-policy-as-a-signed-artifact.md) for why.

## Anatomy

```json
{
  "name": "ml-platform",
  "version": "2026.09.18",
  "description": "What the ML platform domain permits.",
  "hard_deny_capabilities": ["delete_experiment_lineage"],
  "rules": [ ... ],
  "tier_posture": { ... },
  "thresholds": { ... }
}
```

Unknown top-level keys are **rejected at load time**. A typo in an obligation name must fail
loudly rather than quietly weakening a control.

## Rules

A rule matches when every stated condition matches; empty conditions are wildcards.

```json
{
  "id": "ml-engineer-notify-owner",
  "effect": "allow",
  "description": "ML engineers may notify a model owner about a review finding",
  "capabilities": ["notify_model_owner"],
  "principal_roles": ["ml-engineer", "ml-platform-agent"],
  "purposes": ["model_review"],
  "domains": ["ml-platform"],
  "max_risk_tier": 3,
  "min_auth_strength": 2,
  "obligations": ["redact_protected_fields"]
}
```

**Evaluation is deny-overrides with a default deny, and rules carry no priorities.** Any
matching deny wins. No matching allow means deny with `no_matching_rule`. Otherwise allow, with
the union of every matching allow rule's obligations plus the tier posture's.

No ordering means a reader does not have to simulate a firewall in their head to know what a
pack does.

### Purpose is a boundary, not a label

```json
{
  "id": "no-promotion-from-review-purpose",
  "effect": "deny",
  "reason_code": "purpose_mismatch",
  "capabilities": ["promote_model_to_production"],
  "purposes": ["model_review"]
}
```

A run authorised to *review* a model may not *promote* one, even if the principal holds the
role for it in some other context. Purpose-of-use travels in the delegation and is checked on
every action.

## Tier postures

```json
"tier_posture": {
  "3": {
    "obligations": ["require_idempotency_key", "verify_after_execute", "emit_enhanced_evidence"],
    "min_auth_strength": 2,
    "review_queue": "platform-review"
  }
}
```

Declared per tier, not per capability, so a new R3 capability inherits R3's controls the day it
is registered rather than depending on whoever registered it having remembered them.

A tier with no declared posture inherits the nearest lower tier's, so adding R4 to a pack that
declares up to R3 tightens rather than silently dropping controls.

## Thresholds

```json
"thresholds": {
  "injection_deny": 0.7,
  "egress_deny": 0.6,
  "board_block_review": 0.5,
  "min_decision_confidence": 0.5,
  "groundedness_floor": 0.6
}
```

These are in the pack because they are **risk decisions**. A risk owner should be able to move
`injection_deny` from 0.7 to 0.5, have it reviewed, ship it, and replay old decisions against
both versions — none of which is possible if the number lives in a constant.

## Composition tightens, never loosens

```python
effective = compose(
    load_pack("policies/platform-baseline.json"),
    load_pack("policies/ml-platform.json"),
    name="ml-platform-effective",
    version="2026.09.18",
)
```

Rules and hard-deny lists union. Postures take the strongest requirement. Thresholds take the
strictest value. A domain pack can add obligations and denials; it cannot grant what the
baseline withholds.

This will surprise a domain author who expected to relax a baseline control. It is correct, and
it is the same attenuation rule delegation uses.

## Simulate before you ship

```bash
causeway policy-simulate notify_model_owner \
  --arguments '{"model_id":"m","message":"x"}' \
  --role ml-engineer --purpose model_review \
  --destination model-owner-direct \
  --signal board_concern=0.8
```

```
effect       : REVIEW
reason code  : approval_required
tier reached : L3_EXCEPTIONAL
obligations  : emit_enhanced_evidence, require_human_approval, require_idempotency_key, verify_after_execute
```

`simulate` runs the **same code path** as a real decision and cannot execute anything. A
simulation that used a different path would be worth nothing.

## Test packs in CI

Policy rules are the kind of thing that breaks silently. Write tests that assert the *outcome*,
not the rule text:

```python
def test_review_run_cannot_promote(platform, run):
    decision = platform.guard.simulate(
        capability_id="promote_model_to_production",
        arguments={"model_id": "m", "validation_ref": "v"},
        delegation=run.delegation, purpose="model_review",
    )
    assert decision.effect is Effect.DENY
    assert decision.reason_code == "purpose_mismatch"
```

`tests/test_policy_fail_closed.py` is the worked set.

## Signing

Production signs packs in the control plane with a private key, and every execution-plane
node checks them with the public key alone, so a node that can verify a pack cannot forge
one. Needs the optional `signing` extra.

```bash
causeway keygen --key-id cp-1 --out-dir keys/            # once, in the control plane
causeway sign-pack policies/ml-platform.json --private-key keys/cp-1.private.pem --key-id cp-1
causeway verify-pack policies/ml-platform.json --public-key keys/cp-1.public.pem --key-id cp-1
```

`sign-pack` writes a small detached file next to the pack (`ml-platform.json.sig.json`)
naming the pack, its version, its digest and the key. On a node, load packs through the
check rather than around it:

```python
from causeway.authority import Ed25519Verifier
from causeway.policy import load_signed_pack

node = Ed25519Verifier.from_public_pem({"cp-1": public_pem_bytes})
pack = load_signed_pack("policies/ml-platform.json", node)   # raises SignatureInvalid
```

A missing signature file is a refusal, never a fallback to an unsigned load. A signature for
one version cannot be presented with another. The signature covers the pack's meaning, not its
formatting, so re-indenting a signed pack does not break it while relaxing a threshold does.
Pack signatures carry their own context, so one can never be passed off as a delegation
signature made with the same key, or the reverse.

### Making signatures mandatory

Signing proves nothing unless something checks before the pack runs. Load policy through
`load_policy`, which verifies every pack that has a signature, records what it found, and in
strict mode refuses any pack that is unsigned, signed by an unknown key, or edited after
signing:

```python
from causeway.policy import load_policy
from causeway.sdk.platform import Platform

policy = load_policy(["policies/platform-baseline.json", "policies/ml-platform.json"],
                     verifier=node, require_signed=True)
platform = Platform.local(policy=policy, require_signed_policy=True, capabilities=...)
platform.policy_sources     # each pack, whether it verified, and by which key
```

On a server, set **`CAUSEWAY_REQUIRE_SIGNED_POLICY=1`**. Strict mode then applies everywhere
and no code path can turn it off: `load_policy` refuses unsigned packs whatever its arguments
say, and `Platform.local` refuses to start on anything but verified policy, including an
in-memory pack built by code that never asked for strict mode.

Two rules hold even when signatures are optional: a signature that is present but does not
verify is a refusal, because a tampered pack is evidence of tampering; and a missing key in
strict mode is a refusal, never a quiet downgrade.

From the command line:

```bash
causeway --policy-key keys/cp-1.public.pem --policy-key-id cp-1 --require-signed-policy policy-status
#   platform-baseline@2026.09.18  signed by cp-1 (EdDSA), verified
#   ml-platform@2026.09.18        signed by cp-1 (EdDSA), verified
```

`policy-simulate` prints the same provenance under the decision, and every command exits 2
with `REFUSED:` when strict mode stops it.

`pack.sign(secret)` / `pack.verify(signature, secret)` remain for the sandbox. They are HMAC,
so anyone who can check one can forge one.
