"""Action risk tiers.

Risk changes the execution path. Read, reversible write, customer-impacting
and privileged actions must not share one control path, so every registered
capability declares a tier and the platform derives its control posture from
it. Tiers are an ``IntEnum`` so policy can express floors and ceilings with
comparisons, and so a judgment plane's *proposed* tier can be clamped against
the registry's declared tier without a mapping table.

The scale is the one in the north-star architecture; institutions extend the
posture attached to a tier in a policy pack rather than renumbering the tiers.
"""

from __future__ import annotations

from enum import IntEnum


class RiskTier(IntEnum):
    """How much a capability can cost if it goes wrong."""

    R0_INFORMATIONAL = 0
    """Public knowledge, non-sensitive drafting. No side effect."""

    R1_INTERNAL_READ = 1
    """Entitled reads: policy search, case summarisation. No side effect."""

    R2_REVERSIBLE_WRITE = 2
    """Create a ticket, draft a communication, update low-risk metadata."""

    R3_CUSTOMER_IMPACT = 3
    """Customer- or money-facing: notices, bounded refunds, account workflow."""

    R4_HIGH_CONSEQUENCE = 4
    """Large monetary action, access change, destructive admin, filings."""

    R5_PROHIBITED = 5
    """Actions an agent may never autonomously perform. Hard deny."""

    @property
    def has_side_effect(self) -> bool:
        """Whether reaching this tier can change the world outside the run."""
        return self >= RiskTier.R2_REVERSIBLE_WRITE

    @property
    def label(self) -> str:
        """Short human label for dashboards, CLI output and evidence."""
        return self.name.split("_", 1)[0]
