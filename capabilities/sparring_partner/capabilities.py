"""The sparring partner's typed capabilities.

Five narrow business capabilities, spanning four risk tiers plus one that must
never fire. Note what is *not* here: there is no ``run_sql``, no ``send_email``,
no ``http_post``. "Open a model review case" is a capability the platform can
tier, bound, verify and compensate. "Send an email" is not.

The handlers are deliberately trivial -- they stand in for the team's real case
and notification systems. Everything interesting about them is in the
registration metadata, which is what the platform actually enforces.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from causeway.contracts.action import ActionIntent, CredentialGrant
from causeway.contracts.identity import AuthStrength, DataClass
from causeway.contracts.risk import RiskTier
from causeway.errors import ValidationFailed
from causeway.registry.capability import (
    ArgumentSpec,
    ArgumentType,
    Capability,
    CapabilityRegistry,
    CapabilityResult,
)

# A stand-in for the team's case system. A real deployment calls the real one
# through an adapter; the platform contract is identical either way.
_CASES: dict[str, dict[str, Any]] = {}
_NOTIFICATIONS: list[dict[str, Any]] = []

APPROVED_NOTIFICATION_CHANNELS = frozenset(
    {"ml-platform-reviews", "model-risk-queue", "model-owner-direct"}
)
"""The only destinations a notification may reach. An allowlist, not a filter:
the capability cannot send anywhere that is not on this list, whatever the
model proposes."""


def _open_case(intent: ActionIntent, grant: CredentialGrant) -> CapabilityResult:
    """Open a model review case. Reversible."""
    case_id = f"MRC-{intent.action_id[-8:]}"
    _CASES[case_id] = {
        "model": intent.arguments["model_id"],
        "summary": intent.arguments["summary"],
        "severity": intent.arguments["severity"],
        "findings": intent.arguments.get("findings", []),
        "status": "open",
        "opened_by_grant": grant.grant_id,
    }
    return CapabilityResult(output={"case_id": case_id, "status": "open"}, external_ref=case_id)


def _verify_case_exists(intent: ActionIntent, result: CapabilityResult) -> bool:
    """Post-condition: the case really is there and really is open.

    This is the check that turns "the call returned 200" into "the side effect
    happened". Without it, a retry after a timeout has no way to know whether
    it is creating a duplicate.
    """
    case_id = result.external_ref
    if case_id is None:
        return False
    return _CASES.get(case_id, {}).get("status") == "open"


def _close_case(intent: ActionIntent, grant: CredentialGrant) -> CapabilityResult:
    """Close a model review case. The compensation for opening one."""
    case_id = intent.arguments["case_id"]
    if case_id not in _CASES:
        raise ValidationFailed(f"case {case_id!r} does not exist")
    _CASES[case_id]["status"] = "closed"
    _CASES[case_id]["closed_reason"] = intent.arguments["reason"]
    return CapabilityResult(output={"case_id": case_id, "status": "closed"}, external_ref=case_id)


def _verify_case_closed(intent: ActionIntent, result: CapabilityResult) -> bool:
    """Post-condition: the case really did close."""
    case_id = result.external_ref
    if case_id is None:
        return False
    return _CASES.get(case_id, {}).get("status") == "closed"


def _notify_owner(intent: ActionIntent, grant: CredentialGrant) -> CapabilityResult:
    """Notify a model owner through an approved channel."""
    record = {
        "channel": intent.destination,
        "model": intent.arguments["model_id"],
        "message": intent.arguments["message"],
        "case_id": intent.arguments.get("case_id"),
    }
    _NOTIFICATIONS.append(record)
    return CapabilityResult(
        output={"delivered": True, "channel": intent.destination},
        external_ref=f"notif-{len(_NOTIFICATIONS)}",
    )


def _verify_notification(intent: ActionIntent, result: CapabilityResult) -> bool:
    """Post-condition: the notification is in the outbox."""
    return bool(_NOTIFICATIONS) and _NOTIFICATIONS[-1]["model"] == intent.arguments["model_id"]


def _promote_model(intent: ActionIntent, grant: CredentialGrant) -> CapabilityResult:
    """Promote a model version to production. Irreversible, dual control."""
    return CapabilityResult(
        output={"model_id": intent.arguments["model_id"], "stage": "production"},
        external_ref=f"deploy-{intent.arguments['model_id']}",
    )


def _verify_promotion(intent: ActionIntent, result: CapabilityResult) -> bool:
    """Post-condition: the serving path reports the new version.

    A stand-in here, but the shape is the point: an R4 action asserts that the
    world changed, rather than assuming the call that returned cleanly did so.
    """
    return result.output.get("stage") == "production"


def _delete_lineage(intent: ActionIntent, grant: CredentialGrant) -> CapabilityResult:
    """Registered so it can be denied, and never reached.

    An R5 capability exists in the registry precisely so that the platform has
    something concrete to refuse. Leaving it unregistered would make the denial
    a "capability not found" error, which is a different and much weaker
    statement than "this is prohibited".
    """
    raise AssertionError(
        "an R5 capability must never execute; reaching this line means the "
        "hard-deny path in the policy engine has regressed"
    )


def _reject_empty_findings(arguments: Mapping[str, Any]) -> None:
    """Semantic validator: a blocking case must say what is blocking.

    The schema cannot express this, which is exactly what semantic validators
    are for: a case that says "severity: blocking" and lists no findings is
    unreviewable, and the right time to say so is before it exists.
    """
    if arguments.get("severity") == "blocking" and not arguments.get("findings"):
        raise ValidationFailed(
            "a blocking review case must list the findings that block it"
        )


SPARRING_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="open_model_review_case",
        version="1.0.0",
        owner="ml-platform",
        summary="Open a model review case with the sparring partner's findings",
        risk_tier=RiskTier.R2_REVERSIBLE_WRITE,
        arguments=(
            ArgumentSpec(
                "model_id", ArgumentType.STRING, "Model the case is about", max_length=120
            ),
            ArgumentSpec("summary", ArgumentType.STRING, "One-paragraph summary", max_length=2000),
            ArgumentSpec(
                "severity",
                ArgumentType.ENUM,
                "How the sparring partner rated the proposal",
                choices=("advisory", "concern", "blocking"),
            ),
            ArgumentSpec(
                "findings",
                ArgumentType.STRING_LIST,
                "Falsifier ids that refuted a claim",
                required=False,
            ),
        ),
        handler=_open_case,
        verifier=_verify_case_exists,
        semantic_validators=(_reject_empty_findings,),
        compensation_capability="close_model_review_case",
        data_classes=frozenset({DataClass.INTERNAL}),
        approval_queue="ml-platform-reviews",
        rate_limit_per_minute=30,
    ),
    Capability(
        id="close_model_review_case",
        version="1.0.0",
        owner="ml-platform",
        summary="Close a model review case opened in error",
        risk_tier=RiskTier.R2_REVERSIBLE_WRITE,
        arguments=(
            ArgumentSpec("case_id", ArgumentType.STRING, "Case to close", max_length=64),
            ArgumentSpec("reason", ArgumentType.STRING, "Why it is being closed", max_length=500),
        ),
        handler=_close_case,
        verifier=_verify_case_closed,
        data_classes=frozenset({DataClass.INTERNAL}),
        approval_queue="ml-platform-reviews",
    ),
    Capability(
        id="notify_model_owner",
        version="1.0.0",
        owner="ml-platform",
        summary="Tell a model owner that their proposal needs attention",
        risk_tier=RiskTier.R3_CUSTOMER_IMPACT,
        arguments=(
            ArgumentSpec("model_id", ArgumentType.STRING, "Model concerned", max_length=120),
            ArgumentSpec(
                "message", ArgumentType.STRING, "What the owner needs to know", max_length=1500
            ),
            ArgumentSpec(
                "case_id", ArgumentType.STRING, "Related case", required=False, max_length=64
            ),
        ),
        handler=_notify_owner,
        verifier=_verify_notification,
        destinations=APPROVED_NOTIFICATION_CHANNELS,
        data_classes=frozenset({DataClass.INTERNAL, DataClass.CONFIDENTIAL}),
        required_auth_strength=AuthStrength.MULTI_FACTOR,
        approval_queue="model-risk-queue",
        rate_limit_per_minute=10,
    ),
    Capability(
        id="promote_model_to_production",
        version="1.0.0",
        owner="ml-platform",
        summary="Promote a validated model version into the production serving path",
        risk_tier=RiskTier.R4_HIGH_CONSEQUENCE,
        arguments=(
            ArgumentSpec("model_id", ArgumentType.STRING, "Model version", max_length=120),
            ArgumentSpec(
                "validation_ref", ArgumentType.STRING, "Validation record", max_length=120
            ),
        ),
        handler=_promote_model,
        verifier=_verify_promotion,
        irreversible=True,
        idempotent=True,
        requires_attested_workload=True,
        required_auth_strength=AuthStrength.STEP_UP,
        data_classes=frozenset({DataClass.CONFIDENTIAL}),
        approval_queue="model-risk-queue",
        rate_limit_per_minute=2,
    ),
    Capability(
        id="delete_experiment_lineage",
        version="1.0.0",
        owner="ml-platform",
        summary="Destroy experiment lineage records (prohibited for agents)",
        risk_tier=RiskTier.R5_PROHIBITED,
        arguments=(
            ArgumentSpec("experiment_id", ArgumentType.STRING, "Experiment", max_length=120),
        ),
        handler=_delete_lineage,
        verifier=lambda intent, result: False,
        irreversible=True,
        data_classes=frozenset({DataClass.RESTRICTED}),
        approval_queue="model-risk-queue",
    ),
)


def register_sparring_capabilities(registry: CapabilityRegistry) -> CapabilityRegistry:
    """Register every sparring partner capability."""
    for capability in SPARRING_CAPABILITIES:
        registry.register(capability)
    return registry


def cases() -> dict[str, dict[str, Any]]:
    """Read the stand-in case store, for tests and the demo."""
    return dict(_CASES)


def notifications() -> list[dict[str, Any]]:
    """Read the stand-in notification outbox, for tests and the demo."""
    return list(_NOTIFICATIONS)


def reset_stores() -> None:
    """Clear the stand-in stores between tests."""
    _CASES.clear()
    _NOTIFICATIONS.clear()
