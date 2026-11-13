"""The typed capability registry.

The tool plane is the primary safety boundary, because it is what controls
real-world effects. A generic tool ("run this SQL", "send this HTTP request",
"execute this shell command") hands an agent an unbounded blast radius and
leaves policy nothing specific to reason about. A narrow business capability
("issue a refund up to an approved bound", "open a model review case") is small
enough to type, to tier, to bound and to verify afterwards.

So capabilities are registered, not discovered. A capability that is not in the
registry cannot execute, whatever the model proposes and whatever a delegation
envelope happens to name. Registration is also where the platform learns the
facts it needs before it has any idea what the capability does: its risk tier,
whether it is reversible, what it may spend, where it may send data, and how to
check afterwards that it actually happened.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from ..canonical import digest
from ..contracts.action import ActionIntent, CredentialGrant
from ..contracts.delegation import Constraints
from ..contracts.identity import AuthStrength, DataClass
from ..contracts.risk import RiskTier
from ..errors import CapabilityNotRegistered, ConfigurationError, ValidationFailed


class ArgumentType(StrEnum):
    """The argument shapes the platform will normalise and digest."""

    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    ENUM = "enum"
    STRING_LIST = "string_list"


@dataclass(frozen=True, slots=True)
class ArgumentSpec:
    """One argument of a typed capability."""

    name: str
    type: ArgumentType
    description: str
    required: bool = True
    choices: tuple[str, ...] = field(default_factory=tuple)
    max_length: int | None = None
    data_class: DataClass = DataClass.INTERNAL

    def normalise(self, value: Any) -> Any:
        """Coerce ``value`` into its canonical form, or refuse it.

        Normalisation happens before the action digest is taken, so that two
        clients spelling the same action differently -- ``"10"`` and ``10.00``,
        or a list in a different order -- produce the same digest and therefore
        the same approval and the same idempotent result.
        """
        match self.type:
            case ArgumentType.STRING:
                if not isinstance(value, str):
                    raise ValidationFailed(f"{self.name!r} must be a string")
                text = value.strip()
                if self.max_length is not None and len(text) > self.max_length:
                    raise ValidationFailed(
                        f"{self.name!r} exceeds its {self.max_length}-character limit"
                    )
                return text
            case ArgumentType.INTEGER:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValidationFailed(f"{self.name!r} must be an integer")
                return value
            case ArgumentType.DECIMAL:
                try:
                    # Route through str so a float argument cannot smuggle in
                    # binary-floating-point error on a monetary amount.
                    amount = Decimal(str(value))
                except (InvalidOperation, ValueError) as exc:
                    raise ValidationFailed(f"{self.name!r} must be a decimal") from exc
                if not amount.is_finite():
                    raise ValidationFailed(f"{self.name!r} must be finite")
                return amount
            case ArgumentType.BOOLEAN:
                if not isinstance(value, bool):
                    raise ValidationFailed(f"{self.name!r} must be a boolean")
                return value
            case ArgumentType.ENUM:
                if value not in self.choices:
                    raise ValidationFailed(
                        f"{self.name!r} must be one of {list(self.choices)}, got {value!r}"
                    )
                return value
            case ArgumentType.STRING_LIST:
                if not isinstance(value, (list, tuple)) or not all(
                    isinstance(item, str) for item in value
                ):
                    raise ValidationFailed(f"{self.name!r} must be a list of strings")
                # Sorted and de-duplicated: membership is what the argument
                # means, so two orderings are the same action.
                return sorted({item.strip() for item in value})
        raise ConfigurationError(f"unhandled argument type {self.type!r}")


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    """What a capability handler returns.

    ``output`` is whatever the caller needs next. ``external_ref`` is the
    downstream system's own identifier, which is what makes post-execution
    verification and reconciliation possible.
    """

    output: dict[str, Any] = field(default_factory=dict)
    external_ref: str | None = None

    @property
    def output_digest(self) -> str:
        """Digest of the output, recorded on the receipt."""
        return digest(self.output)


Handler = Callable[[ActionIntent, CredentialGrant], CapabilityResult]
"""A capability's implementation. It receives a just-in-time credential and
never reads one from the environment."""

Verifier = Callable[[ActionIntent, CapabilityResult], bool]
"""A post-condition: did the side effect actually land? Retrying a mutation
without checking is how duplicates happen."""

SemanticValidator = Callable[[Mapping[str, Any]], None]
"""A domain check that a schema cannot express. Raises ``ValidationFailed``."""


@dataclass(frozen=True, slots=True)
class Capability:
    """A narrow, typed business capability the platform is willing to execute."""

    id: str
    version: str
    owner: str
    summary: str
    risk_tier: RiskTier
    arguments: tuple[ArgumentSpec, ...]
    handler: Handler

    idempotent: bool = True
    irreversible: bool = False
    compensation_capability: str | None = None

    data_classes: frozenset[DataClass] = field(
        default_factory=lambda: frozenset({DataClass.INTERNAL})
    )
    destinations: frozenset[str] | None = None
    """Allowed egress destinations. ``None`` means the capability does not leave
    the platform boundary at all."""
    max_amount: Decimal | None = None
    currencies: frozenset[str] | None = None

    required_auth_strength: AuthStrength = AuthStrength.SINGLE_FACTOR
    requires_attested_workload: bool = False
    approval_queue: str = "platform-review"
    verifier: Verifier | None = None
    semantic_validators: tuple[SemanticValidator, ...] = field(default_factory=tuple)
    rate_limit_per_minute: int | None = None
    kill_switch: bool = False
    """Set true to disable the capability fleet-wide without a deployment."""

    def __post_init__(self) -> None:
        if self.irreversible and self.compensation_capability:
            raise ConfigurationError(
                f"{self.id}: an irreversible capability cannot declare a compensation"
            )
        if self.risk_tier >= RiskTier.R3_CUSTOMER_IMPACT and self.idempotent is False:
            # Not a hard error -- some actions genuinely are not idempotent --
            # but it must be deliberate, so it is declared, not defaulted.
            pass

    @property
    def registry_key(self) -> str:
        """The versioned key under which the capability is registered."""
        return f"{self.id}@{self.version}"

    @property
    def registered_constraints(self) -> Constraints:
        """The capability's own bounds, folded into every delegation.

        A delegation can narrow these further but never past them, so a
        capability registered with a £500 ceiling cannot be talked into £5,000
        by any envelope, prompt or plan.
        """
        return Constraints(
            max_amount=self.max_amount,
            currencies=self.currencies,
            destinations=self.destinations,
            data_classes=frozenset(dc.value for dc in self.data_classes),
            max_risk_tier=self.risk_tier,
        )

    def normalise(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and canonicalise ``arguments`` against the schema.

        Unknown arguments are rejected rather than dropped: silently ignoring a
        field a caller thought was meaningful is how an approver ends up
        reviewing a different action than the one that executes.
        """
        specs = {spec.name: spec for spec in self.arguments}
        unknown = sorted(set(arguments) - set(specs))
        if unknown:
            raise ValidationFailed(f"{self.id}: unknown arguments {unknown}")

        normalised: dict[str, Any] = {}
        for name, spec in specs.items():
            if name not in arguments:
                if spec.required:
                    raise ValidationFailed(f"{self.id}: missing required argument {name!r}")
                continue
            normalised[name] = spec.normalise(arguments[name])

        for validator in self.semantic_validators:
            validator(normalised)
        return normalised


class CapabilityRegistry:
    """The control-plane registry of capabilities the platform will execute."""

    def __init__(self, capabilities: list[Capability] | None = None) -> None:
        self._by_id: dict[str, Capability] = {}
        for capability in capabilities or []:
            self.register(capability)

    def register(self, capability: Capability) -> None:
        """Add a capability, refusing to silently replace an existing one."""
        if capability.id in self._by_id:
            raise ConfigurationError(
                f"capability {capability.id!r} is already registered; "
                "register a new version explicitly rather than shadowing it"
            )
        self._by_id[capability.id] = capability

    def get(self, capability_id: str) -> Capability:
        """Look up a capability, or refuse the action outright."""
        try:
            return self._by_id[capability_id]
        except KeyError:
            raise CapabilityNotRegistered(
                f"capability {capability_id!r} is not registered, so it cannot execute"
            ) from None

    def has(self, capability_id: str) -> bool:
        """Whether a capability is registered."""
        return capability_id in self._by_id

    def all(self) -> tuple[Capability, ...]:
        """Every registered capability, in registration order."""
        return tuple(self._by_id.values())

    def ids(self) -> frozenset[str]:
        """The set of registered capability identifiers."""
        return frozenset(self._by_id)

    def routing_criteria(self) -> dict[str, str | None]:
        """Capability summaries shaped as ``Choice`` criteria.

        This is how intent routing stays closed: the judgment plane can only
        select a capability that is actually registered, because the option set
        *is* the registry.
        """
        return {capability.id: capability.summary for capability in self._by_id.values()}
