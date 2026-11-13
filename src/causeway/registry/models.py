"""The approved model catalog.

Applications do not call model endpoints directly. The catalog is what lets the
platform answer, for any run in the evidence ledger, which model version ran,
whether it was approved for the data class involved, and what it was allowed to
fall back to.

Fallback is the subtle one: falling back to *any* available model on an error
quietly moves a workload onto an endpoint that may have different data-use
terms, a different residency or no validation. So fallbacks are declared, and
the gateway refuses a fallback that does not meet the same requirements as the
model it replaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..contracts.identity import DataClass
from ..contracts.risk import RiskTier
from ..errors import ConfigurationError, PolicyDenied


class ModelKind(StrEnum):
    """What role a model plays in the platform."""

    SYSTEM_ONE = "system_one"
    """Typed, calibrated judgment: Choice, Score, Noul. Used by policy signals,
    guardrails, falsifiers and supervisors."""

    SYSTEM_TWO = "system_two"
    """Open-ended reasoning and drafting. Proposes; never authorizes."""

    EMBEDDING = "embedding"


class ValidationState(StrEnum):
    """Where a model sits in the model-risk lifecycle."""

    UNVALIDATED = "unvalidated"
    IN_VALIDATION = "in_validation"
    VALIDATED = "validated"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class ModelCard:
    """One approved model version and the terms it may be used under."""

    id: str
    provider: str
    version: str
    kind: ModelKind
    allowed_data_classes: frozenset[DataClass]
    max_risk_tier: RiskTier
    """The highest-tier decision this model's output may inform. It never
    authorizes at any tier -- this bounds what it may be consulted about."""
    validation_state: ValidationState = ValidationState.UNVALIDATED
    residency: str = "unspecified"
    retains_data: bool = False
    trains_on_data: bool = False
    fallbacks: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""

    def permits(self, *, data_class: DataClass, risk_tier: RiskTier) -> bool:
        """Whether this model may be consulted for this data and this tier."""
        return (
            self.validation_state is not ValidationState.RETIRED
            and data_class in self.allowed_data_classes
            and risk_tier <= self.max_risk_tier
        )


class ModelCatalog:
    """The approved catalog, plus the routing and fallback rules over it."""

    def __init__(self, cards: list[ModelCard] | None = None) -> None:
        self._cards: dict[str, ModelCard] = {}
        for card in cards or []:
            self.register(card)

    def register(self, card: ModelCard) -> None:
        """Add an approved model version to the catalog."""
        if card.id in self._cards:
            raise ConfigurationError(f"model {card.id!r} is already in the catalog")
        self._cards[card.id] = card

    def get(self, model_id: str) -> ModelCard:
        """Fetch a model card, refusing unknown endpoints."""
        try:
            return self._cards[model_id]
        except KeyError:
            raise PolicyDenied(
                f"model {model_id!r} is not in the approved catalog",
                reason_code="model_not_approved",
            ) from None

    def resolve(
        self, model_id: str, *, data_class: DataClass, risk_tier: RiskTier
    ) -> ModelCard:
        """Return the model to use, following declared fallbacks if needed.

        Degrades safely: if neither the requested model nor any declared
        fallback meets the data and risk requirements, this raises rather than
        returning a model that does not.
        """
        card = self.get(model_id)
        if card.permits(data_class=data_class, risk_tier=risk_tier):
            return card
        for fallback_id in card.fallbacks:
            fallback = self.get(fallback_id)
            if fallback.permits(data_class=data_class, risk_tier=risk_tier):
                return fallback
        raise PolicyDenied(
            f"no approved model for data class {data_class.value!r} at "
            f"{risk_tier.label}; requested {model_id!r}",
            reason_code="no_approved_model",
        )

    def all(self) -> tuple[ModelCard, ...]:
        """Every card in the catalog."""
        return tuple(self._cards.values())
