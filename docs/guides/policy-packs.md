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

```python
signature = pack.sign(secret)
pack.verify(signature, secret)      # raises SignatureInvalid
```

A detached HMAC over the canonical pack encoding. Production signs in the control plane and
distributes signed bundles, so an execution-plane node can verify a pack it did not build.
