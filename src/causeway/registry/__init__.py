"""Control-plane registries: what the platform is willing to run."""

from .capability import (
    ArgumentSpec,
    ArgumentType,
    Capability,
    CapabilityRegistry,
    CapabilityResult,
)
from .models import ModelCard, ModelCatalog, ModelKind, ValidationState

__all__ = [
    "ArgumentSpec",
    "ArgumentType",
    "Capability",
    "CapabilityRegistry",
    "CapabilityResult",
    "ModelCard",
    "ModelCatalog",
    "ModelKind",
    "ValidationState",
]
