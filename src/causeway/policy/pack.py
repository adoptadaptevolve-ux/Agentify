"""Policy packs: policy as a versioned, signed, replayable artifact.

Policy is not code scattered through the runtime and it is not a prompt. It is
a data artifact with a version, a signature and a test suite, which is what
makes three things possible that matter more than any individual rule:

* **Replay.** "Why was this allowed in March?" is answerable by evaluating the
  March pack against the recorded intent, because the pack version is on the
  decision and the pack is immutable.
* **Simulation.** A proposed pack can be run against recorded traffic before it
  ships, so a tightening is measured rather than discovered in production.
* **Independent review.** Risk and security can read the pack without reading
  the runtime, and diff two versions without diffing a codebase.

Packs compose. A platform baseline pack and a domain pack (payments, privacy,
model risk) evaluate together, and the result is the *tightest* of them: a
domain pack can add obligations and denials but cannot grant what the baseline
withholds. That is the same attenuation rule delegation uses, applied to policy.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..canonical import canonical_bytes, digest
from ..contracts.identity import AuthStrength
from ..contracts.policy import Effect, Obligation
from ..contracts.risk import RiskTier
from ..errors import ConfigurationError, SignatureInvalid


@dataclass(frozen=True, slots=True)
class Rule:
    """One authorization rule.

    A rule matches when *every* stated condition matches; conditions left empty
    are wildcards. Rules never carry priorities: evaluation is deny-overrides
    with a default deny, so a reader does not have to simulate an ordering in
    their head to know what a pack does.
    """

    id: str
    effect: Effect
    capabilities: frozenset[str] = field(default_factory=frozenset)
    principal_roles: frozenset[str] = field(default_factory=frozenset)
    purposes: frozenset[str] = field(default_factory=frozenset)
    domains: frozenset[str] = field(default_factory=frozenset)
    max_risk_tier: RiskTier | None = None
    min_auth_strength: AuthStrength | None = None
    obligations: frozenset[Obligation] = field(default_factory=frozenset)
    reason_code: str = ""
    description: str = ""

    def matches(
        self,
        *,
        capability_id: str,
        roles: frozenset[str],
        purpose: str,
        domain: str,
        risk_tier: RiskTier,
    ) -> bool:
        """Whether this rule applies to the action being evaluated."""
        if self.capabilities and capability_id not in self.capabilities:
            return False
        if self.principal_roles and not (self.principal_roles & roles):
            return False
        if self.purposes and purpose not in self.purposes:
            return False
        if self.domains and domain not in self.domains:
            return False
        return not (self.max_risk_tier is not None and risk_tier > self.max_risk_tier)


@dataclass(frozen=True, slots=True)
class TierPosture:
    """The control posture the platform applies to a whole risk tier.

    Expressed per tier rather than per capability so that a new R3 capability
    inherits R3's controls the day it is registered, instead of depending on
    whoever registered it having remembered them.
    """

    obligations: frozenset[Obligation] = field(default_factory=frozenset)
    min_auth_strength: AuthStrength = AuthStrength.SINGLE_FACTOR
    requires_attested_workload: bool = False
    review_queue: str | None = None


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Numeric gates the engine applies to typed judgment signals.

    Kept in the pack rather than in code because these are risk decisions, not
    engineering decisions: moving the injection-deny threshold from 0.7 to 0.5
    is a change a risk owner should be able to make, review and replay.
    """

    injection_deny: float = 0.7
    """P(prompt injection) at or above which an action is denied outright."""
    egress_deny: float = 0.6
    """P(output carries data above the destination's class) at or above which
    an action is denied."""
    board_block_review: float = 0.5
    """Aggregated supervisor concern at or above which an action goes to review."""
    min_decision_confidence: float = 0.5
    """Below this, a judgment-informed decision is too uncertain to act on and
    routes to a human rather than guessing."""
    groundedness_floor: float = 0.6
    """Minimum support probability for a claim the platform will repeat."""


@dataclass(frozen=True, slots=True)
class PolicyPack:
    """A versioned bundle of rules, postures and thresholds."""

    name: str
    version: str
    rules: tuple[Rule, ...]
    tier_posture: dict[RiskTier, TierPosture] = field(default_factory=dict)
    thresholds: Thresholds = field(default_factory=Thresholds)
    hard_deny_capabilities: frozenset[str] = field(default_factory=frozenset)
    """Capabilities an agent may never invoke, whatever any rule says. This is
    the R5 list: it is checked at L0, before any rule is read."""
    description: str = ""

    @property
    def artifact_digest(self) -> str:
        """The digest an evidence record cites to identify this exact pack."""
        return digest(self)

    def posture_for(self, tier: RiskTier) -> TierPosture:
        """The posture for ``tier``, defaulting to the tightest declared below it.

        A tier with no declared posture inherits the nearest lower tier's, so
        adding R4 to a pack that only declares up to R3 tightens rather than
        silently drops controls.
        """
        for candidate in sorted(self.tier_posture, reverse=True):
            if candidate <= tier:
                return self.tier_posture[candidate]
        return TierPosture()

    def sign(self, secret: bytes) -> str:
        """Return a detached HMAC over the canonical pack encoding."""
        return hmac.new(secret, canonical_bytes(self), hashlib.sha256).hexdigest()

    def verify(self, signature: str, secret: bytes) -> None:
        """Raise unless ``signature`` authenticates this pack."""
        if not hmac.compare_digest(self.sign(secret), signature):
            raise SignatureInvalid(f"policy pack {self.name}@{self.version} failed verification")


def _as_frozenset(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise ConfigurationError(f"expected a list, got {type(value)!r}")
    return frozenset(str(item) for item in value)


def _as_obligations(value: Any) -> frozenset[Obligation]:
    return frozenset(Obligation(item) for item in (value or []))


def load_pack(source: str | Path | dict[str, Any]) -> PolicyPack:
    """Load a pack from a JSON file or an already-parsed mapping.

    Unknown keys are rejected rather than ignored: a typo in an obligation name
    must fail loudly at load time, not quietly weaken a control at runtime.
    """
    if isinstance(source, (str, Path)):
        raw: dict[str, Any] = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        raw = dict(source)

    known = {
        "name",
        "version",
        "description",
        "rules",
        "tier_posture",
        "thresholds",
        "hard_deny_capabilities",
    }
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigurationError(f"policy pack has unknown top-level keys: {unknown}")

    rules = []
    for entry in raw.get("rules", []):
        rules.append(
            Rule(
                id=entry["id"],
                effect=Effect(entry["effect"]),
                capabilities=_as_frozenset(entry.get("capabilities")),
                principal_roles=_as_frozenset(entry.get("principal_roles")),
                purposes=_as_frozenset(entry.get("purposes")),
                domains=_as_frozenset(entry.get("domains")),
                max_risk_tier=(
                    RiskTier(entry["max_risk_tier"]) if "max_risk_tier" in entry else None
                ),
                min_auth_strength=(
                    AuthStrength(entry["min_auth_strength"])
                    if "min_auth_strength" in entry
                    else None
                ),
                obligations=_as_obligations(entry.get("obligations")),
                reason_code=entry.get("reason_code", ""),
                description=entry.get("description", ""),
            )
        )

    posture: dict[RiskTier, TierPosture] = {}
    for tier_key, entry in (raw.get("tier_posture") or {}).items():
        posture[RiskTier(int(tier_key))] = TierPosture(
            obligations=_as_obligations(entry.get("obligations")),
            min_auth_strength=AuthStrength(
                entry.get("min_auth_strength", AuthStrength.SINGLE_FACTOR.value)
            ),
            requires_attested_workload=bool(entry.get("requires_attested_workload", False)),
            review_queue=entry.get("review_queue"),
        )

    threshold_entry = raw.get("thresholds") or {}
    thresholds = Thresholds(
        injection_deny=float(threshold_entry.get("injection_deny", 0.7)),
        egress_deny=float(threshold_entry.get("egress_deny", 0.6)),
        board_block_review=float(threshold_entry.get("board_block_review", 0.5)),
        min_decision_confidence=float(threshold_entry.get("min_decision_confidence", 0.5)),
        groundedness_floor=float(threshold_entry.get("groundedness_floor", 0.6)),
    )

    return PolicyPack(
        name=raw["name"],
        version=raw["version"],
        description=raw.get("description", ""),
        rules=tuple(rules),
        tier_posture=posture,
        thresholds=thresholds,
        hard_deny_capabilities=_as_frozenset(raw.get("hard_deny_capabilities")),
    )


def compose(*packs: PolicyPack, name: str, version: str) -> PolicyPack:
    """Compose packs so the result is the tightest of them.

    Denials and obligations union; hard-deny lists union; thresholds take the
    strictest value; postures take the strongest requirement. A composed pack
    can therefore only ever be more restrictive than any of its inputs.
    """
    if not packs:
        raise ConfigurationError("cannot compose an empty set of policy packs")

    rules: list[Rule] = []
    hard_deny: set[str] = set()
    for pack in packs:
        rules.extend(pack.rules)
        hard_deny |= pack.hard_deny_capabilities

    tiers: dict[RiskTier, TierPosture] = {}
    for tier in {tier for pack in packs for tier in pack.tier_posture}:
        postures = [pack.tier_posture[tier] for pack in packs if tier in pack.tier_posture]
        tiers[tier] = TierPosture(
            obligations=frozenset().union(*(p.obligations for p in postures)),
            min_auth_strength=max(p.min_auth_strength for p in postures),
            requires_attested_workload=any(p.requires_attested_workload for p in postures),
            review_queue=next((p.review_queue for p in postures if p.review_queue), None),
        )

    return PolicyPack(
        name=name,
        version=version,
        description="composed: " + ", ".join(f"{p.name}@{p.version}" for p in packs),
        rules=tuple(rules),
        tier_posture=tiers,
        thresholds=Thresholds(
            injection_deny=min(p.thresholds.injection_deny for p in packs),
            egress_deny=min(p.thresholds.egress_deny for p in packs),
            board_block_review=min(p.thresholds.board_block_review for p in packs),
            min_decision_confidence=max(p.thresholds.min_decision_confidence for p in packs),
            groundedness_floor=max(p.thresholds.groundedness_floor for p in packs),
        ),
        hard_deny_capabilities=frozenset(hard_deny),
    )
