"""Who is asking, and how strongly do we know it.

Identity is a workload-security problem, not a prompt-engineering problem. A
compromised model or agent process must be unable to forge who authorized an
action or what scope was granted, so none of the types here are ever
constructed from model output. They are built by the runtime from the
authenticated session and the deployment's own attested identity, and they
travel inside a signed envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum


class AuthStrength(IntEnum):
    """How strongly the calling human or service was authenticated.

    Ordered so policy can require a floor ("R4 needs at least STEP_UP") with a
    comparison rather than a lookup table. ``UNAUTHENTICATED`` exists so that
    an absent credential is represented explicitly and denies, rather than
    being represented by ``None`` and accidentally passing a truthiness check.
    """

    UNAUTHENTICATED = 0
    SINGLE_FACTOR = 1
    MULTI_FACTOR = 2
    STEP_UP = 3  # re-authenticated for this specific action
    HARDWARE_BOUND = 4  # phishing-resistant, device-bound credential


class PrincipalKind(StrEnum):
    """What kind of thing the authenticated principal is."""

    HUMAN = "human"
    SERVICE = "service"


class DataClass(StrEnum):
    """Data sensitivity classes used by policy, retrieval and egress control.

    Kept deliberately small and generic. Institutions map their own scheme onto
    these in a policy pack rather than forking the platform's enum.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"  # e.g. customer PII, account data
    REGULATED = "regulated"  # e.g. cardholder data, health, material non-public


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated user or service on whose behalf a run happens.

    ``roles`` and ``entitlements`` are what the enterprise IAM asserted, not
    what a prompt claimed. The platform never reads a role out of model output
    or out of retrieved content.
    """

    id: str
    kind: PrincipalKind
    tenant: str
    roles: frozenset[str] = field(default_factory=frozenset)
    entitlements: frozenset[str] = field(default_factory=frozenset)
    auth_strength: AuthStrength = AuthStrength.UNAUTHENTICATED
    session_id: str | None = None
    session_risk: float = 0.0  # 0 = normal, 1 = maximally anomalous session

    def __post_init__(self) -> None:
        if not 0.0 <= self.session_risk <= 1.0:
            raise ValueError("session_risk must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class WorkloadIdentity:
    """The agent process itself: what code is running, where, at what version.

    High-risk capabilities can require an attested workload (a known image
    digest on a known deployment), so that "the agent asked for it" means a
    specific reviewed artifact asked for it.
    """

    service: str
    version: str
    environment: str  # e.g. "dev", "staging", "prod"
    image_digest: str | None = None
    attested: bool = False
