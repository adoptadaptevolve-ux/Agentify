"""The board aggregates deterministically, and one reviewer can still matter."""

from __future__ import annotations

from dataclasses import dataclass

from causeway.contracts.judgment import SystemOne
from causeway.contracts.risk import RiskTier
from causeway.judgment.offline import OfflineSystemOne
from causeway.verify.falsify import Severity
from causeway.verify.supervisors import (
    BoardOutcome,
    Position,
    ReviewCase,
    SupervisorBoard,
    SupervisorFinding,
    SupervisorVerdict,
)


@dataclass(frozen=True, slots=True)
class Fixed:
    """A supervisor that always returns the verdict it was constructed with."""

    id: str
    charter: str = "fixture"
    veto_from: RiskTier | None = None
    weight: float = 1.0
    position: Position = Position.CLEAR
    concern: float = 0.0
    confidence: float = 1.0
    with_finding: bool = False

    def review(self, case: ReviewCase, judge: SystemOne) -> SupervisorVerdict:
        """Return the fixed verdict."""
        findings = (
            (SupervisorFinding(code=f"{self.id}_finding", detail="x", severity=Severity.CONCERN),)
            if self.with_finding
            else ()
        )
        return SupervisorVerdict(
            supervisor_id=self.id,
            charter=self.charter,
            position=self.position,
            concern=self.concern,
            confidence=self.confidence,
            findings=findings,
        )


JUDGE = OfflineSystemOne()
CASE = ReviewCase.build(subject={"x": 1}, risk_tier=RiskTier.R3_CUSTOMER_IMPACT)


def test_a_veto_holder_blocking_is_decisive() -> None:
    """One reviewer who is certain outranks a quiet majority."""
    board = SupervisorBoard(
        [
            Fixed(
                id="safety",
                veto_from=RiskTier.R2_REVERSIBLE_WRITE,
                position=Position.BLOCK,
                concern=1.0,
            ),
            Fixed(id="a"),
            Fixed(id="b"),
            Fixed(id="c"),
        ]
    )
    decision = board.deliberate(CASE, JUDGE)
    assert decision.outcome is BoardOutcome.BLOCK
    assert "safety" in decision.rationale


def test_a_block_without_veto_authority_does_not_block() -> None:
    """Veto is narrow on purpose: a board where everyone can veto blocks everything."""
    board = SupervisorBoard(
        [
            Fixed(id="advisor", position=Position.BLOCK, concern=0.9, with_finding=True),
            Fixed(id="a"),
        ]
    )
    decision = board.deliberate(CASE, JUDGE)
    assert decision.outcome is BoardOutcome.REVIEW


def test_veto_only_applies_at_or_above_its_tier() -> None:
    """A supervisor that vetoes from R4 cannot veto an R1 read."""
    low_case = ReviewCase.build(subject={}, risk_tier=RiskTier.R1_INTERNAL_READ)
    board = SupervisorBoard(
        [
            Fixed(
                id="model_risk",
                veto_from=RiskTier.R4_HIGH_CONSEQUENCE,
                position=Position.BLOCK,
                concern=0.2,
            )
        ]
    )
    assert board.deliberate(low_case, JUDGE).outcome is not BoardOutcome.BLOCK


def test_a_named_finding_cannot_be_averaged_away() -> None:
    """Five quiet supervisors must not erase one reviewer's concrete objection."""
    board = SupervisorBoard(
        [
            Fixed(id="finder", concern=0.9, position=Position.CONCERN, with_finding=True),
            *[Fixed(id=f"quiet{index}") for index in range(5)],
        ]
    )
    decision = board.deliberate(CASE, JUDGE)
    assert decision.concern == 0.9
    assert decision.outcome is BoardOutcome.REVIEW


def test_a_supervisor_without_findings_only_moves_the_mean() -> None:
    """The floor comes from findings, not from a bare concern score."""
    board = SupervisorBoard(
        [
            Fixed(id="worried", concern=0.9, position=Position.CONCERN, with_finding=False),
            *[Fixed(id=f"quiet{index}") for index in range(5)],
        ]
    )
    decision = board.deliberate(CASE, JUDGE)
    assert decision.concern < 0.9


def test_low_confidence_routes_to_a_human() -> None:
    """"We are not sure" is a reason to ask someone, not a reason to proceed."""
    board = SupervisorBoard([Fixed(id="a", confidence=0.2), Fixed(id="b")])
    assert board.deliberate(CASE, JUDGE).outcome is BoardOutcome.REVIEW


def test_strong_disagreement_routes_to_a_human() -> None:
    """A board split between 0.1 and 0.9 has found something averaging destroys."""
    board = SupervisorBoard(
        [Fixed(id="a", concern=0.9), Fixed(id="b"), Fixed(id="c"), Fixed(id="d"), Fixed(id="e")],
        review_threshold=0.9,
    )
    decision = board.deliberate(CASE, JUDGE)
    assert decision.outcome is BoardOutcome.REVIEW
    assert decision.disagreement >= 0.5


def test_a_clear_board_is_possible() -> None:
    """A board that can never clear is decoration."""
    board = SupervisorBoard([Fixed(id="a"), Fixed(id="b")])
    assert board.deliberate(CASE, JUDGE).outcome is BoardOutcome.CLEAR


def test_aggregation_is_deterministic() -> None:
    """The same verdicts always produce the same outcome."""
    supervisors = [Fixed(id="a", concern=0.4), Fixed(id="b", concern=0.2)]
    first = SupervisorBoard(supervisors).deliberate(CASE, JUDGE)
    second = SupervisorBoard(supervisors).deliberate(CASE, JUDGE)
    assert (first.outcome, first.concern, first.confidence) == (
        second.outcome,
        second.concern,
        second.confidence,
    )


def test_the_board_only_exports_two_signals() -> None:
    """Policy sees what it can threshold; everything else is for humans."""
    decision = SupervisorBoard([Fixed(id="a")]).deliberate(CASE, JUDGE)
    assert set(decision.signals()) == {"board_concern", "board_confidence"}
