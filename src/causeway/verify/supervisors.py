"""The supervisor board: many narrow reviewers, one deterministic aggregation.

A single "judge" model asked whether an agent's proposal is acceptable has to
hold safety, policy, evidence quality, domain correctness and business
consequence in mind at once. It will produce one answer, and that answer will
be dominated by whichever concern the prompt happened to emphasise. Worse, the
answer arrives as a single number with no way to see which concern drove it.

So the platform runs a board instead. Each supervisor has a narrow charter, its
own questions and its own verdict. They do not talk to each other, deliberately:
independent reviewers who cannot see each other's answers disagree in useful
ways, and that disagreement is itself a signal. This is the runtime form of
"effective challenge" from model risk management -- a standing panel whose job
is to find the problem, not to agree.

**The aggregation is code, not a model.** ``SupervisorBoard.deliberate`` is a
pure function of the verdicts. That matters for three reasons: the same
verdicts always produce the same outcome, an auditor can read the rule in a
dozen lines, and no amount of persuasive text from any supervisor can change
the arithmetic. A model that could be talked into a different aggregation would
put the whole board back to being one judge.

The board never authorizes anything. It produces a ``board_concern`` and a
``board_confidence`` signal, which the policy engine may take into account
alongside everything else it knows. A blocking board is a strong input to a
deny; it is not the deny itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from ..contracts.action import ActionIntent
from ..contracts.judgment import SystemOne
from ..contracts.risk import RiskTier
from ..ids import new_case_id
from .falsify import FalsificationReport, Severity


class Position(StrEnum):
    """One supervisor's stance."""

    CLEAR = "clear"
    CONCERN = "concern"
    BLOCK = "block"


class BoardOutcome(StrEnum):
    """What the board concluded, as an input to policy."""

    CLEAR = "clear"
    REVIEW = "review"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class SupervisorFinding:
    """One specific thing a supervisor objects to."""

    code: str
    detail: str
    severity: Severity = Severity.CONCERN
    probability: float | None = None
    evidence_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SupervisorVerdict:
    """One supervisor's answer, with its own confidence and findings."""

    supervisor_id: str
    charter: str
    position: Position
    concern: float
    """How worried this supervisor is, in [0, 1]."""
    confidence: float
    """How sure it is of its own concern score, in [0, 1]."""
    findings: tuple[SupervisorFinding, ...] = field(default_factory=tuple)
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("concern", "confidence"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{self.supervisor_id}: {name} must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class ReviewCase:
    """What the board is asked to look at.

    ``subject`` is the judgment state: the proposal, the retrieved sources, the
    normalised arguments -- whatever the supervisors need to see. It is
    untrusted input, and supervisors treat it as data.
    """

    case_id: str
    subject: dict[str, Any]
    risk_tier: RiskTier
    intent: ActionIntent | None = None
    claims: dict[str, str] = field(default_factory=dict)
    sources: tuple[str, ...] = field(default_factory=tuple)
    destination: str | None = None
    permitted_class: str = "internal"

    @staticmethod
    def build(
        subject: dict[str, Any],
        *,
        risk_tier: RiskTier,
        intent: ActionIntent | None = None,
        claims: dict[str, str] | None = None,
        sources: tuple[str, ...] = (),
        destination: str | None = None,
        permitted_class: str = "internal",
    ) -> ReviewCase:
        """Construct a case with a fresh identifier."""
        return ReviewCase(
            case_id=new_case_id(),
            subject=subject,
            risk_tier=risk_tier,
            intent=intent,
            claims=dict(claims or {}),
            sources=sources,
            destination=destination,
            permitted_class=permitted_class,
        )


@runtime_checkable
class Supervisor(Protocol):
    """One narrow reviewer on the board."""

    @property
    def id(self) -> str:
        """Stable identifier, recorded in evidence."""
        ...

    @property
    def charter(self) -> str:
        """What this supervisor is responsible for noticing, in one sentence."""
        ...

    @property
    def veto_from(self) -> RiskTier | None:
        """The tier at and above which this supervisor's BLOCK is decisive.

        ``None`` means the supervisor advises but never vetoes. Veto authority
        is narrow on purpose: a board where everyone can veto is a board that
        blocks everything, and a board where nobody can is decoration.
        """
        ...

    @property
    def weight(self) -> float:
        """Relative weight in the composite concern score."""
        ...

    def review(self, case: ReviewCase, judge: SystemOne) -> SupervisorVerdict:
        """Examine the case and return a verdict."""
        ...


@dataclass(frozen=True, slots=True)
class BoardDecision:
    """The board's aggregated position on one case."""

    case_id: str
    outcome: BoardOutcome
    concern: float
    confidence: float
    disagreement: float
    verdicts: tuple[SupervisorVerdict, ...]
    rationale: str
    reports: tuple[FalsificationReport, ...] = field(default_factory=tuple)

    @property
    def blocking_findings(self) -> tuple[SupervisorFinding, ...]:
        """Every finding from a supervisor that took a BLOCK position."""
        return tuple(
            finding
            for verdict in self.verdicts
            if verdict.position is Position.BLOCK
            for finding in verdict.findings
        )

    @property
    def all_findings(self) -> tuple[SupervisorFinding, ...]:
        """Every finding, worst first, for a human review packet."""
        return tuple(
            sorted(
                (finding for verdict in self.verdicts for finding in verdict.findings),
                key=lambda f: (-f.severity, -(f.probability or 0.0)),
            )
        )

    def signals(self) -> dict[str, float]:
        """The board's contribution to a policy decision.

        Exactly two numbers. Everything else the board produced is for humans
        and for evidence; the policy engine sees only what it can threshold.
        """
        return {"board_concern": self.concern, "board_confidence": self.confidence}

    def summary(self) -> str:
        """One line for a status checklist."""
        return (
            f"board {self.outcome.value}: concern {self.concern:.2f}, "
            f"confidence {self.confidence:.2f}, {len(self.all_findings)} findings"
        )


class SupervisorBoard:
    """Runs the supervisors and aggregates their verdicts deterministically."""

    def __init__(
        self,
        supervisors: list[Supervisor],
        *,
        review_threshold: float = 0.5,
        min_confidence: float = 0.5,
        disagreement_threshold: float = 0.5,
    ) -> None:
        if not supervisors:
            raise ValueError("a board with no supervisors reviews nothing")
        self._supervisors = tuple(supervisors)
        self._review_threshold = review_threshold
        self._min_confidence = min_confidence
        self._disagreement_threshold = disagreement_threshold

    @property
    def supervisors(self) -> tuple[Supervisor, ...]:
        """The seated supervisors."""
        return self._supervisors

    def deliberate(self, case: ReviewCase, judge: SystemOne) -> BoardDecision:
        """Collect every verdict and aggregate them.

        The aggregation rules, in order, and they are the whole decision:

        1. A supervisor with veto authority at this case's risk tier returning
           BLOCK is decisive. One reviewer who is certain outranks a quiet
           majority, because the majority mostly did not look at what they saw.
        2. Otherwise the composite concern is the weighted mean of the
           supervisors' concern scores, floored by the highest concern held by
           any supervisor that filed a named finding. At or above the review
           threshold, a human decides.
        3. Low aggregate confidence routes to a human. "We are not sure" is a
           reason to ask someone, not a reason to proceed.
        4. Strong disagreement routes to a human, even when the mean is low. A
           board split between 0.1 and 0.9 has found something that averaging
           destroys.
        5. Otherwise, clear.
        """
        verdicts = tuple(supervisor.review(case, judge) for supervisor in self._supervisors)
        weights = {s.id: s.weight for s in self._supervisors}
        veto_from = {s.id: s.veto_from for s in self._supervisors}

        total_weight = sum(weights.get(v.supervisor_id, 1.0) for v in verdicts) or 1.0
        weighted_mean = sum(
            v.concern * weights.get(v.supervisor_id, 1.0) for v in verdicts
        ) / total_weight
        # A supervisor that filed a specific, named finding sets a floor. The
        # weighted mean alone would let five quiet supervisors average away one
        # reviewer's concrete objection, which is precisely how a board becomes
        # a rubber stamp. Supervisors with nothing to say can move the mean;
        # they cannot erase a finding.
        floor = max((v.concern for v in verdicts if v.findings), default=0.0)
        concern = max(weighted_mean, floor)
        confidence = min((v.confidence for v in verdicts), default=0.0)
        concerns = [v.concern for v in verdicts]
        disagreement = (max(concerns) - min(concerns)) if len(concerns) > 1 else 0.0

        vetoes = [
            v
            for v in verdicts
            if v.position is Position.BLOCK
            and veto_from.get(v.supervisor_id) is not None
            and case.risk_tier >= veto_from[v.supervisor_id]  # type: ignore[operator]
        ]
        if vetoes:
            names = ", ".join(v.supervisor_id for v in vetoes)
            return self._decision(
                case, BoardOutcome.BLOCK, concern, confidence, disagreement, verdicts,
                f"vetoed by {names} at {case.risk_tier.label}",
            )
        if concern >= self._review_threshold:
            return self._decision(
                case, BoardOutcome.REVIEW, concern, confidence, disagreement, verdicts,
                f"composite concern {concern:.2f} is at or above the review threshold",
            )
        if confidence < self._min_confidence:
            return self._decision(
                case, BoardOutcome.REVIEW, concern, confidence, disagreement, verdicts,
                f"aggregate confidence {confidence:.2f} is too low to act on",
            )
        if disagreement >= self._disagreement_threshold:
            return self._decision(
                case, BoardOutcome.REVIEW, concern, confidence, disagreement, verdicts,
                f"supervisors disagree by {disagreement:.2f}; a human should look",
            )
        return self._decision(
            case, BoardOutcome.CLEAR, concern, confidence, disagreement, verdicts,
            "no supervisor raised a blocking concern",
        )

    def _decision(
        self,
        case: ReviewCase,
        outcome: BoardOutcome,
        concern: float,
        confidence: float,
        disagreement: float,
        verdicts: tuple[SupervisorVerdict, ...],
        rationale: str,
    ) -> BoardDecision:
        reports = tuple(
            report
            for verdict in verdicts
            for report in [verdict.detail.get("report")]
            if isinstance(report, FalsificationReport)
        )
        return BoardDecision(
            case_id=case.case_id,
            outcome=outcome,
            concern=round(concern, 6),
            confidence=round(confidence, 6),
            disagreement=round(disagreement, 6),
            verdicts=verdicts,
            rationale=rationale,
            reports=reports,
        )
