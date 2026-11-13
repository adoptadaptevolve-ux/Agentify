"""Authorization uncertainty must never become permission."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from causeway.contracts.identity import AuthStrength, Principal, PrincipalKind
from causeway.contracts.policy import DecisionTier, Effect, Obligation
from causeway.contracts.risk import RiskTier
from causeway.errors import DelegationExpired, PolicyDenied
from causeway.registry.capability import Capability
from causeway.runtime.budgets import RunBudget
from causeway.sdk.platform import Platform
from causeway.sdk.run import AgentRun

ALL_CAPABILITIES = frozenset(
    {
        "open_model_review_case",
        "close_model_review_case",
        "notify_model_owner",
        "promote_model_to_production",
        "delete_experiment_lineage",
    }
)


def _privileged_run(platform: Platform, engineer: Principal, workload) -> AgentRun:
    """A run deliberately granted more than it should need."""
    return platform.begin(
        principal=engineer,
        workload=workload,
        capabilities=ALL_CAPABILITIES,
        purpose="model_review",
        domain="ml-platform",
        budget=RunBudget(max_tool_calls=20),
    )


def test_prohibited_capability_is_denied_at_l0(platform, engineer, workload) -> None:
    """R5 is refused before any rule is read, even with the capability granted."""
    run = _privileged_run(platform, engineer, workload)
    decision = platform.guard.simulate(
        capability_id="delete_experiment_lineage",
        arguments={"experiment_id": "exp-1"},
        delegation=platform.issuer.attenuate(
            run.delegation, capabilities=frozenset({"delete_experiment_lineage"})
        ),
        purpose="model_review",
    )
    assert decision.effect is Effect.DENY
    assert decision.reason_code == "prohibited_autonomy"
    assert decision.tier_reached is DecisionTier.L0_LOCAL_INVARIANT


def test_an_unrouted_capability_defaults_to_deny(platform, engineer, workload, pack) -> None:
    """A capability nobody wrote a rule for is not permitted by omission."""
    orphan = Capability(
        id="orphan_capability",
        version="1.0.0",
        owner="nobody",
        summary="A capability with no policy rule",
        risk_tier=RiskTier.R2_REVERSIBLE_WRITE,
        arguments=(),
        handler=lambda intent, grant: None,  # type: ignore[arg-type,return-value]
    )
    platform.registry.register(orphan)
    run = platform.begin(
        principal=engineer,
        workload=workload,
        capabilities=frozenset({"orphan_capability"}),
        purpose="model_review",
        domain="ml-platform",
    )
    decision = platform.guard.simulate(
        capability_id="orphan_capability",
        arguments={},
        delegation=run.delegation,
        purpose="model_review",
    )
    assert decision.effect is Effect.DENY
    assert decision.reason_code == "no_matching_rule"


def test_purpose_is_a_boundary_not_a_label(platform, engineer, workload) -> None:
    """A run authorised to review a model may not promote one."""
    run = _privileged_run(platform, engineer, workload)
    decision = platform.guard.simulate(
        capability_id="promote_model_to_production",
        arguments={"model_id": "m-1", "validation_ref": "v-1"},
        delegation=platform.issuer.attenuate(
            run.delegation, capabilities=frozenset({"promote_model_to_production"})
        ),
        purpose="model_review",
    )
    assert decision.effect is Effect.DENY
    assert decision.reason_code == "purpose_mismatch"


def test_weak_authentication_is_denied_not_downgraded(platform, workload) -> None:
    """A weaker session is refused; it does not silently get a lesser action."""
    weak = Principal(
        id="a.rivera",
        kind=PrincipalKind.HUMAN,
        tenant="acme",
        roles=frozenset({"ml-engineer"}),
        auth_strength=AuthStrength.SINGLE_FACTOR,
    )
    run = platform.begin(
        principal=weak,
        workload=workload,
        capabilities=frozenset({"notify_model_owner"}),
        purpose="model_review",
        domain="ml-platform",
    )
    decision = platform.guard.simulate(
        capability_id="notify_model_owner",
        arguments={"model_id": "m-1", "message": "hello"},
        delegation=run.delegation,
        purpose="model_review",
        destination="model-owner-direct",
    )
    assert decision.effect is Effect.DENY
    assert decision.reason_code == "insufficient_auth_strength"



def test_expired_delegation_is_refused(platform, engineer, workload) -> None:
    """An envelope outside its window is not usable, however valid its signature."""
    run = platform.begin(
        principal=engineer,
        workload=workload,
        capabilities=frozenset({"open_model_review_case"}),
        purpose="model_review",
        domain="ml-platform",
    )
    expired = replace(
        run.delegation,
        body=replace(
            run.delegation.body,
            issued_at=run.delegation.body.issued_at - timedelta(hours=2),
            expires_at=run.delegation.body.issued_at - timedelta(hours=1),
        ),
    )
    # Re-sign so the test exercises expiry rather than signature failure.
    signature, key_id = platform.issuer._signer.sign(expired.body)
    resigned = replace(expired, signature=signature, key_id=key_id)
    with pytest.raises(DelegationExpired):
        platform.guard.simulate(
            capability_id="open_model_review_case",
            arguments={"model_id": "m", "summary": "s", "severity": "advisory"},
            delegation=resigned,
            purpose="model_review",
        )


def test_destination_outside_the_allowlist_is_denied(platform, engineer, workload) -> None:
    """Egress goes where the capability says, not where the caller asks."""
    run = platform.begin(
        principal=engineer,
        workload=workload,
        capabilities=frozenset({"notify_model_owner"}),
        purpose="model_review",
        domain="ml-platform",
    )
    decision = platform.guard.simulate(
        capability_id="notify_model_owner",
        arguments={"model_id": "m-1", "message": "hello"},
        delegation=run.delegation,
        purpose="model_review",
        destination="https://exfil.example.com/collect",
    )
    assert decision.effect is Effect.DENY
    assert decision.reason_code == "destination_not_allowlisted"


def test_a_high_injection_signal_denies(platform, engineer, workload) -> None:
    """The policy engine denies on the signal itself, with no supervisor needed."""
    run = platform.begin(
        principal=engineer,
        workload=workload,
        capabilities=frozenset({"open_model_review_case"}),
        purpose="model_review",
        domain="ml-platform",
    )
    decision = platform.guard.simulate(
        capability_id="open_model_review_case",
        arguments={"model_id": "m-1", "summary": "s", "severity": "advisory"},
        delegation=run.delegation,
        purpose="model_review",
        signals={"injection_probability": 0.95},
    )
    assert decision.effect is Effect.DENY
    assert decision.reason_code == "prompt_injection_suspected"


def test_an_unknown_signal_cannot_relax_a_threshold(platform, engineer, workload) -> None:
    """A caller cannot invent a signal name the engine will act on."""
    run = platform.begin(
        principal=engineer,
        workload=workload,
        capabilities=frozenset({"open_model_review_case"}),
        purpose="model_review",
        domain="ml-platform",
    )
    decision = platform.guard.simulate(
        capability_id="open_model_review_case",
        arguments={"model_id": "m-1", "summary": "s", "severity": "advisory"},
        delegation=run.delegation,
        purpose="model_review",
        signals={"injection_probability": 0.95, "override_everything": 1.0},
    )
    assert decision.effect is Effect.DENY
    assert "override_everything" not in decision.signals


def test_r4_carries_dual_control_and_step_up(platform, engineer, workload) -> None:
    """The tier posture attaches, without the capability restating it."""
    approver = replace(
        engineer,
        roles=frozenset({"model-risk-approver"}),
        auth_strength=AuthStrength.STEP_UP,
    )
    run = platform.begin(
        principal=approver,
        workload=workload,
        capabilities=frozenset({"promote_model_to_production"}),
        purpose="model_promotion",
        domain="ml-platform",
    )
    decision = platform.guard.simulate(
        capability_id="promote_model_to_production",
        arguments={"model_id": "m-1", "validation_ref": "VAL-1"},
        delegation=run.delegation,
        purpose="model_promotion",
    )
    assert decision.effect is Effect.REVIEW
    assert Obligation.REQUIRE_DUAL_CONTROL in decision.obligations
    assert Obligation.REQUIRE_STEP_UP_AUTH in decision.obligations
    assert Obligation.REQUIRE_ATTESTED_WORKLOAD in decision.obligations


def test_denied_actions_never_reach_a_handler(run: AgentRun) -> None:
    """A denial raises rather than returning something receipt-shaped."""
    with pytest.raises(PolicyDenied):
        run.invoke_tool(
            "notify_model_owner",
            {"model_id": "m-1", "message": "hi"},
            purpose="model_review",
            destination="not-an-approved-channel",
            idempotency_key="k-1",
        )
