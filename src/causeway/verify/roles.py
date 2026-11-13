"""The supervisors the platform seats by default.

Each of these has one charter and looks at one thing. Domain teams add their
own -- a payments supervisor, a privacy supervisor, a model-risk supervisor --
by implementing the same protocol; see ``docs/guides/write-falsifiers.md``.

Veto authority is deliberately unequal. Safety and policy can stop a
consequential action outright, because their failure modes are the ones that
cannot be fixed afterwards. Groundedness and domain review raise concern and
route to a human, because being wrong about a claim is recoverable and being
too eager to block is its own failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts.judgment import Noul, Question, SystemOne
from ..contracts.policy import Effect, PolicyDecision
from ..contracts.risk import RiskTier
from ..judgment.questions import (
    RISK_TIER_RUBRIC,
    egress_questions,
    groundedness_questions,
    injection_questions,
    risk_tier_question,
)
from .falsify import FalsificationSuite, Severity
from .supervisors import (
    Position,
    ReviewCase,
    SupervisorFinding,
    SupervisorVerdict,
)


@dataclass(frozen=True, slots=True)
class SafetySupervisor:
    """Watches for untrusted content trying to become policy, and for leaks.

    Vetoes from R2 upward: a run that has been hijacked should not get to write
    anything, even something reversible, because the reversal depends on the
    same compromised run noticing.
    """

    id: str = "safety"
    charter: str = "Detect prompt injection, goal hijacking and data exfiltration"
    veto_from: RiskTier | None = RiskTier.R2_REVERSIBLE_WRITE
    weight: float = 1.5
    block_at: float = 0.7

    def review(self, case: ReviewCase, judge: SystemOne) -> SupervisorVerdict:
        """Run the injection and egress sweeps over the case subject."""
        questions: dict[str, Question] = dict(injection_questions())
        if case.destination is not None:
            questions |= egress_questions(
                destination=case.destination, permitted_class=case.permitted_class
            )
        result = judge.ask(case.subject, questions)

        findings: list[SupervisorFinding] = []
        worst = 0.0
        confidence = 1.0
        for key in questions:
            answer = result.noul(key)
            worst = max(worst, answer.noul)
            confidence = min(confidence, answer.confidence)
            if answer.noul >= self.block_at:
                findings.append(
                    SupervisorFinding(
                        code=key,
                        detail=f"{key} fired at {answer.noul:.2f}",
                        severity=Severity.BLOCKING,
                        probability=answer.noul,
                    )
                )
        position = (
            Position.BLOCK
            if worst >= self.block_at
            else Position.CONCERN
            if worst >= 0.4
            else Position.CLEAR
        )
        return SupervisorVerdict(
            supervisor_id=self.id,
            charter=self.charter,
            position=position,
            concern=worst,
            confidence=confidence,
            findings=tuple(findings),
            detail={"model": result.model},
        )


@dataclass(frozen=True, slots=True)
class GroundednessSupervisor:
    """Checks that every claim the run would repeat is supported by a source.

    Advises rather than vetoes. An unsupported claim is a quality problem to put
    in front of a human, not a security event -- and a supervisor that blocked
    on it would block every genuinely novel synthesis.
    """

    id: str = "groundedness"
    charter: str = "Verify that stated claims are supported by the cited sources"
    veto_from: RiskTier | None = None
    weight: float = 1.0
    floor: float = 0.6

    def review(self, case: ReviewCase, judge: SystemOne) -> SupervisorVerdict:
        """Ask one support question per claim."""
        if not case.claims:
            return SupervisorVerdict(
                supervisor_id=self.id,
                charter=self.charter,
                position=Position.CLEAR,
                concern=0.0,
                confidence=1.0,
                detail={"claims": 0, "note": "no claims were put forward"},
            )
        questions = groundedness_questions(case.claims)
        state = {"claims": case.claims, "sources": list(case.sources), **case.subject}
        result = judge.ask(state, questions)

        findings: list[SupervisorFinding] = []
        worst_gap = 0.0
        confidence = 1.0
        for key, claim in case.claims.items():
            answer = result.noul(f"grounded__{key}")
            confidence = min(confidence, answer.confidence)
            gap = max(0.0, self.floor - answer.noul)
            worst_gap = max(worst_gap, gap / self.floor if self.floor else 0.0)
            if answer.noul < self.floor:
                findings.append(
                    SupervisorFinding(
                        code=f"unsupported:{key}",
                        detail=f"support for {claim!r} is {answer.noul:.2f}, below {self.floor}",
                        severity=Severity.CONCERN,
                        probability=answer.noul,
                    )
                )
        return SupervisorVerdict(
            supervisor_id=self.id,
            charter=self.charter,
            position=Position.CONCERN if findings else Position.CLEAR,
            concern=min(1.0, worst_gap),
            confidence=confidence,
            findings=tuple(findings),
            detail={"claims": len(case.claims), "model": result.model},
        )


@dataclass(frozen=True, slots=True)
class RiskSupervisor:
    """Asks whether the action is more consequential than its registered tier.

    Only ever escalates. A capability registered at R2 that is being used to do
    something that looks like R4 should attract R4's controls; the reverse is
    never allowed, because talking the platform down a tier is exactly what an
    attacker would want.
    """

    id: str = "risk"
    charter: str = "Detect actions more consequential than their registered tier"
    veto_from: RiskTier | None = None
    weight: float = 1.0

    def review(self, case: ReviewCase, judge: SystemOne) -> SupervisorVerdict:
        """Score the proposed action against the risk rubric."""
        result = judge.ask(case.subject, risk_tier_question())
        answer = result.score("proposed_risk_tier")
        proposed = min(round(answer.score), RiskTier.R5_PROHIBITED.value)
        gap = max(0, proposed - case.risk_tier.value)
        findings: list[SupervisorFinding] = []
        if gap:
            findings.append(
                SupervisorFinding(
                    code="tier_escalation",
                    detail=(
                        f"registered {case.risk_tier.label} but the proposal reads as "
                        f"{RISK_TIER_RUBRIC[proposed]!r}"
                    ),
                    severity=Severity.BLOCKING if gap >= 2 else Severity.CONCERN,
                    probability=answer.confidence,
                )
            )
        return SupervisorVerdict(
            supervisor_id=self.id,
            charter=self.charter,
            position=Position.CONCERN if gap else Position.CLEAR,
            concern=min(1.0, gap / 2.0),
            confidence=answer.confidence,
            findings=tuple(findings),
            detail={"proposed_tier": proposed, "registered_tier": case.risk_tier.value},
        )


@dataclass(frozen=True, slots=True)
class PolicySupervisor:
    """Seats the deterministic policy decision on the board.

    It calls no model. It is here so that a human reading the board's verdicts
    sees policy alongside the judgment-based supervisors rather than in a
    separate place, and so that a policy denial is visibly decisive: it vetoes
    from R0, because there is no tier at which a denied action becomes a
    question of degree.
    """

    decision: PolicyDecision
    id: str = "policy"
    charter: str = "Carry the deterministic authorization decision onto the board"
    veto_from: RiskTier | None = RiskTier.R0_INFORMATIONAL
    weight: float = 2.0

    def review(self, case: ReviewCase, judge: SystemOne) -> SupervisorVerdict:
        """Translate the policy decision into a verdict. No judgment call."""
        match self.decision.effect:
            case Effect.DENY:
                position, concern = Position.BLOCK, 1.0
            case Effect.REVIEW:
                position, concern = Position.CONCERN, 0.6
            case _:
                position, concern = Position.CLEAR, 0.0
        findings = (
            (
                SupervisorFinding(
                    code=self.decision.reason_code,
                    detail=self.decision.explanation,
                    severity=Severity.BLOCKING,
                ),
            )
            if position is Position.BLOCK
            else ()
        )
        return SupervisorVerdict(
            supervisor_id=self.id,
            charter=self.charter,
            position=position,
            concern=concern,
            # Deterministic: the engine is certain of its own rules.
            confidence=1.0,
            findings=findings,
            detail={
                "effect": self.decision.effect.value,
                "reason_code": self.decision.reason_code,
                "policy_version": self.decision.policy_version,
                "tier_reached": self.decision.tier_reached.value,
            },
        )


@dataclass(frozen=True, slots=True)
class FalsificationSupervisor:
    """Seats a falsification suite on the board.

    This is how a domain team's own verifiers become part of the platform's
    review rather than a separate check the team has to remember to run. The
    suite's ``BLOCKING`` refutations become a block; everything else is concern.
    """

    suite: FalsificationSuite
    id: str = "falsification"
    charter: str = "Attempt to refute the claims the run is relying on"
    veto_from: RiskTier | None = None
    weight: float = 1.5
    subject_key: str = ""

    def review(self, case: ReviewCase, judge: SystemOne) -> SupervisorVerdict:
        """Run the suite and translate its report into a verdict."""
        report = self.suite.run(judge, case.subject, subject=case.case_id)
        findings = tuple(
            SupervisorFinding(
                code=finding.falsifier_id,
                detail=f"{finding.claim} -- refuted at {finding.probability:.2f}",
                severity=finding.severity,
                probability=finding.probability,
            )
            for finding in report.refutations
        )
        position = (
            Position.BLOCK
            if report.blocking_refutations
            else Position.CONCERN
            if report.refutations
            else Position.CLEAR
        )
        return SupervisorVerdict(
            supervisor_id=self.id,
            charter=self.charter,
            position=position,
            concern=report.concern,
            confidence=report.confidence,
            findings=findings,
            detail={"report": report, "verdict": report.verdict.value},
        )


@dataclass(frozen=True, slots=True)
class DomainSupervisor:
    """A domain reviewer built from a handful of Nouls.

    The escape hatch for "we need someone watching for X" where X does not
    warrant a full falsification suite. Each question is one concern, and the
    supervisor's concern is the worst of them.
    """

    id: str
    charter: str
    questions: dict[str, str]
    criteria: dict[str, dict[str, str]] = field(default_factory=dict)
    veto_from: RiskTier | None = None
    weight: float = 1.0
    concern_at: float = 0.5

    def review(self, case: ReviewCase, judge: SystemOne) -> SupervisorVerdict:
        """Ask every question and take the worst answer as the concern."""
        questions: dict[str, Question] = {
            key: Noul(instructions=text, criteria=self.criteria.get(key))
            for key, text in self.questions.items()
        }
        result = judge.ask(case.subject, questions)
        worst = 0.0
        confidence = 1.0
        findings: list[SupervisorFinding] = []
        for key in questions:
            answer = result.noul(key)
            worst = max(worst, answer.noul)
            confidence = min(confidence, answer.confidence)
            if answer.noul >= self.concern_at:
                findings.append(
                    SupervisorFinding(
                        code=key,
                        detail=f"{self.questions[key]} -> {answer.noul:.2f}",
                        severity=Severity.CONCERN,
                        probability=answer.noul,
                    )
                )
        return SupervisorVerdict(
            supervisor_id=self.id,
            charter=self.charter,
            position=Position.CONCERN if findings else Position.CLEAR,
            concern=worst,
            confidence=confidence,
            findings=tuple(findings),
            detail={"model": result.model},
        )
