# Build a capability

The whole workflow for a domain team, in the order you actually do it. The worked example is
`capabilities/sparring_partner/`, which is roughly four hundred lines and contains no security
code.

## 1. Write the capability, narrowly

The single most important design decision is how narrow the capability is. `run_sql`,
`send_email` and `http_post` hand an agent an unbounded blast radius and leave policy nothing
specific to reason about. `open_model_review_case` and `issue_refund_up_to_approved_bound` are
small enough to type, tier, bound and verify.

```python
Capability(
    id="open_model_review_case",
    version="1.0.0",
    owner="ml-platform",
    summary="Open a model review case with the sparring partner's findings",
    risk_tier=RiskTier.R2_REVERSIBLE_WRITE,
    arguments=(
        ArgumentSpec("model_id", ArgumentType.STRING, "Model the case is about", max_length=120),
        ArgumentSpec("severity", ArgumentType.ENUM, "Rating", choices=("advisory", "concern", "blocking")),
        ArgumentSpec("findings", ArgumentType.STRING_LIST, "Falsifier ids", required=False),
    ),
    handler=_open_case,
    verifier=_verify_case_exists,             # did the side effect actually land?
    semantic_validators=(_reject_empty_findings,),
    compensation_capability="close_model_review_case",
    approval_queue="ml-platform-reviews",     # a domain queue, never "AI review"
    rate_limit_per_minute=30,
)
```

Four fields carry most of the weight:

* **`risk_tier`** determines the control posture. Be honest: a capability that contacts a
  customer is R3 even if it feels small.
* **`verifier`** is what turns "the call returned 200" into "the world changed". Without it, a
  retry after a timeout has no way to know whether it is creating a duplicate.
* **`compensation_capability`** declares the rollback. A capability with neither a compensation
  nor `irreversible=True` has not been thought about.
* **`semantic_validators`** express what a schema cannot — "a blocking case must list its
  findings" — and run *before* the action has an identity.

## 2. Add a policy rule

Packs default-deny, so nothing works until a rule permits it.

```json
{
  "id": "ml-engineer-review-cases",
  "effect": "allow",
  "capabilities": ["open_model_review_case", "close_model_review_case"],
  "principal_roles": ["ml-engineer", "ml-platform-agent"],
  "purposes": ["model_review"],
  "domains": ["ml-platform"],
  "max_risk_tier": 2
}
```

You do not write the tier's controls here. R2's idempotency and verification obligations come
from the baseline pack's tier posture, so a new R2 capability inherits them on the day it is
registered. See [Policy packs](policy-packs.md).

## 3. Write falsifiers for the claims your workflow rests on

This is the part that takes judgment, and it has [its own guide](write-falsifiers.md).

## 4. Seat a board

Three platform supervisors plus whatever your domain needs:

```python
def sparring_board() -> list[Supervisor]:
    return [
        SafetySupervisor(),                                       # vetoes from R2
        FalsificationSupervisor(suite=MODEL_CHANGE_SUITE),
        GroundednessSupervisor(),
        RiskSupervisor(),
        MODEL_RISK_SUPERVISOR,                                    # DomainSupervisor, vetoes from R4
    ]
```

Set veto authority where you can defend it. "This supervisor can stop an irreversible
production promotion" is defensible; "this supervisor can block a ticket" is how a team learns
to route around the board.

## 5. Draw the workflow

```python
Step(
    id="record",
    handler=_record,
    edges=frozenset({"escalate", "report"}),
    has_side_effect=True,                        # runner checkpoints on both sides
    compensation="close_model_review_case",
)
```

Steps take `(run, state)` and return `StepOutcome.go(...)` or `StepOutcome.finish(...)`. A step
that returns a destination outside its declared edges fails the run.

Write the step body as though the call simply happens. If policy routes it to a human,
`invoke_tool` raises `ApprovalRequired`, the runner checkpoints and pauses, and `resume()`
re-enters the same step later. Your idempotency key makes that safe.

## 6. Add sandbox cues so it runs in CI

The offline judge needs to be taught your scenarios — see
[ADR 0007](../decisions/0007-offline-judge-by-default.md) for why this is the trade, and
`capabilities/sparring_partner/sandbox.py` for the pattern.

```python
Cue(when_question="evaluation data overlaps", when_state="random row split", weight=3.6)
Cue(when_question="evaluation data overlaps", when_state="split by customer", weight=-3.0)
```

Write both directions. A cue bank with only positive cues makes every fixture proposal look
guilty.

## 7. Test the invariants, not the model

Assert what the platform did: the run paused, exactly one case exists, the prohibited
capability was refused with this reason code, the chain verifies, the reconstruction has no
unverified actions. `tests/test_sparring_partner_e2e.py` is the template.

## Checklist before you ship

- [ ] Every capability declares a tier, and you would defend it to a risk reviewer
- [ ] Every mutating capability has a verifier and either a compensation or `irreversible=True`
- [ ] Idempotency keys are deterministic from the run and the subject
- [ ] Destinations are an allowlist on the capability, not a parameter the model chooses
- [ ] The prohibited action for your domain is *registered* at R5, so the denial is
      "prohibited", not "not found"
- [ ] The workflow's commitment boundary (`irreversible_steps`) is where you expect
- [ ] Falsifier thresholds and severities were set by someone who owns the risk
- [ ] Tests assert platform behaviour, not model phrasing
