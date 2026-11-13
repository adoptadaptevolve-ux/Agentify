"""Falsification: verify by trying to break the claim, not to confirm it.

Asking a model "is this correct?" is a bad question, and it is bad in a way
that gets worse as the model gets better. A capable model asked to confirm will
find a reading under which the thing is fine, because that is what the question
requested. The failure is not hallucination, it is compliance.

So the platform never asks for confirmation. For each way a claim could be
wrong, it asks a separate, narrow question whose *yes* is bad news:

    claim:      "the holdout set is uncontaminated"
    falsifier:  "is there evidence in the state that holdout rows also appear
                 in training?"

Four properties follow, and all four are why this is the platform's verifier
rather than a domain team's helper function:

1. **A failure mode has to be named to be checked.** Writing a falsifier forces
   the author to say what specifically could be wrong, which is most of the
   value even before anything runs.
2. **Each answer is small enough to be true or false.** A reviewer can read one
   falsifier, one probability, and one piece of state and agree or disagree.
   Nobody can do that with a paragraph of assessment.
3. **Nothing is ever proven.** A suite returns SURVIVED, not CORRECT: these
   falsifiers did not refute this claim. That is genuinely weaker than proof,
   and saying so keeps the platform honest about what it checked.
4. **It is affordable.** Independent questions about the same state batch into
   one judgment call, so twenty falsifiers cost about what one does. A check
   that is cheap runs on every action; a check that is expensive runs on the
   ones that already looked suspicious, which are not the ones that need it.

Thresholds are per-falsifier because failure modes are not equally tolerable:
a 0.3 probability of silent data loss deserves a different response from a 0.3
probability of an unhelpful phrasing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Final

from ..contracts.judgment import Noul, Question, State, SystemOne


class Severity(IntEnum):
    """How much a refutation should cost.

    ``BLOCKING`` does not itself block anything -- the supervisor board and the
    policy engine decide that. It states how seriously the author of the
    falsifier meant it, which is a domain judgment, separate from the
    platform's control decision.
    """

    ADVISORY = 0
    CONCERN = 1
    BLOCKING = 2


SEVERITY_WEIGHT: Final[dict[Severity, float]] = {
    Severity.ADVISORY: 0.4,
    Severity.CONCERN: 0.7,
    Severity.BLOCKING: 1.0,
}
"""How much each severity contributes to a suite's concern score. A blocking
refutation at probability 0.6 outweighs an advisory one at 1.0, which is the
ordering a reviewer would expect and which a flat average would lose."""


class Verdict(StrEnum):
    """What a falsification run concluded.

    Note what is absent: there is no ``CORRECT``. A suite can only report that
    it failed to break the claim with the attacks it had.
    """

    SURVIVED = "survived"
    """No falsifier refuted the claim, and the answers were decisive."""

    REFUTED = "refuted"
    """At least one falsifier crossed its threshold."""

    INCONCLUSIVE = "inconclusive"
    """The judgment plane was too uncertain to say either way. This routes to a
    human; it never quietly becomes SURVIVED."""


@dataclass(frozen=True, slots=True)
class Falsifier:
    """One named way the claim could be wrong."""

    id: str
    claim: str
    """The claim under attack, stated plainly enough for a reviewer to check."""
    question: str
    """The falsifying question. Its *yes* must mean the claim is in trouble."""
    criteria: dict[str, str] | None = None
    """What "true" and "false" look like. Writing the line down is what makes a
    falsifier reviewable rather than a matter of taste."""
    threshold: float = 0.5
    """Probability at or above which this falsifier counts as a refutation."""
    severity: Severity = Severity.CONCERN
    rationale: str = ""
    """Why this failure mode matters. Read by humans, not by the model."""

    def __post_init__(self) -> None:
        if not 0.0 < self.threshold <= 1.0:
            raise ValueError(f"{self.id}: threshold must be within (0, 1]")

    def to_question(self) -> Question:
        """Render as the Noul the judgment plane will answer."""
        return Noul(instructions=self.question, criteria=self.criteria)


@dataclass(frozen=True, slots=True)
class Finding:
    """One falsifier's result."""

    falsifier_id: str
    claim: str
    question: str
    probability: float
    threshold: float
    severity: Severity
    confidence: float
    rationale: str = ""

    @property
    def crossed_threshold(self) -> bool:
        """Whether the raw probability reached this falsifier's threshold."""
        return self.probability >= self.threshold

    @property
    def decisive(self) -> bool:
        """Whether the answer was far enough from 0.5 to mean anything.

        An answer of 0.49 against a threshold of 0.5 is not a pass, it is a
        shrug -- and 0.51 is not a refutation, it is the same shrug. Treating
        either as an answer is how an uncertain verifier becomes a rubber stamp
        in one direction or a nuisance in the other.
        """
        return self.confidence >= 0.2

    @property
    def refuted(self) -> bool:
        """Whether this falsifier actually refuted the claim.

        Both halves are required. A probability over the threshold that the
        judgment plane is not confident about does not refute anything; it
        makes the run inconclusive, which routes to a human.
        """
        return self.crossed_threshold and self.decisive

    @property
    def weighted_concern(self) -> float:
        """This finding's contribution to the suite's concern score."""
        return self.probability * SEVERITY_WEIGHT[self.severity]


@dataclass(frozen=True, slots=True)
class FalsificationReport:
    """The outcome of running one suite against one state."""

    suite_id: str
    verdict: Verdict
    findings: tuple[Finding, ...]
    model: str
    subject: str = ""

    @property
    def refutations(self) -> tuple[Finding, ...]:
        """Findings that decisively refuted their claim, worst first."""
        return tuple(
            sorted(
                (finding for finding in self.findings if finding.refuted),
                key=lambda finding: (-finding.severity, -finding.probability),
            )
        )

    @property
    def concern(self) -> float:
        """The suite's single concern score in [0, 1].

        The maximum rather than the mean: a suite is a set of detectors, and
        averaging a genuine hit against nineteen clean answers is how a real
        finding gets voted away by the questions that found nothing.
        """
        if not self.findings:
            return 0.0
        return max(finding.weighted_concern for finding in self.findings)

    @property
    def undecided(self) -> tuple[Finding, ...]:
        """Findings the judgment plane could not call either way.

        Surfaced separately because they are the ones worth a human's time:
        nobody needs to re-read a falsifier that came back at 0.02.
        """
        return tuple(finding for finding in self.findings if not finding.decisive)

    @property
    def confidence(self) -> float:
        """How decisive the weakest answer was, in [0, 1]."""
        if not self.findings:
            return 0.0
        return min(finding.confidence for finding in self.findings)

    @property
    def blocking_refutations(self) -> tuple[Finding, ...]:
        """Refutations the suite author marked as blocking."""
        return tuple(f for f in self.refutations if f.severity is Severity.BLOCKING)

    def summary(self) -> str:
        """One line for a status checklist or a review packet."""
        refuted = len(self.refutations)
        return (
            f"{self.suite_id}: {self.verdict.value} "
            f"({refuted}/{len(self.findings)} falsifiers refuted, "
            f"concern {self.concern:.2f})"
        )


@dataclass(frozen=True, slots=True)
class FalsificationSuite:
    """A named bank of falsifiers aimed at one kind of claim."""

    id: str
    description: str
    falsifiers: tuple[Falsifier, ...]
    inconclusive_when_uncertain: bool = True
    """When true, an undecisive answer makes the whole run INCONCLUSIVE rather
    than letting a shrug count as a pass."""

    def __post_init__(self) -> None:
        if not self.falsifiers:
            raise ValueError(f"suite {self.id} has no falsifiers; it would pass everything")
        seen = [f.id for f in self.falsifiers]
        if len(set(seen)) != len(seen):
            raise ValueError(f"suite {self.id} has duplicate falsifier ids")

    def questions(self) -> dict[str, Question]:
        """The whole suite as one batch of questions."""
        return {falsifier.id: falsifier.to_question() for falsifier in self.falsifiers}

    def run(self, judge: SystemOne, state: State, *, subject: str = "") -> FalsificationReport:
        """Run every falsifier against ``state`` in a single judgment call."""
        result = judge.ask(state, self.questions())
        findings = tuple(
            Finding(
                falsifier_id=falsifier.id,
                claim=falsifier.claim,
                question=falsifier.question,
                probability=result.noul(falsifier.id).noul,
                threshold=falsifier.threshold,
                severity=falsifier.severity,
                confidence=result.noul(falsifier.id).confidence,
                rationale=falsifier.rationale,
            )
            for falsifier in self.falsifiers
        )
        return FalsificationReport(
            suite_id=self.id,
            verdict=self._verdict(findings),
            findings=findings,
            model=result.model,
            subject=subject,
        )

    def _verdict(self, findings: tuple[Finding, ...]) -> Verdict:
        """Decide the suite verdict.

        A decisive refutation wins outright. Otherwise any undecisive answer
        makes the whole run inconclusive: uncertainty never passes, and it
        never over-claims either.
        """
        if any(finding.refuted for finding in findings):
            return Verdict.REFUTED
        if self.inconclusive_when_uncertain and any(not f.decisive for f in findings):
            return Verdict.INCONCLUSIVE
        return Verdict.SURVIVED

    def extend(self, *falsifiers: Falsifier) -> FalsificationSuite:
        """Return a suite with extra falsifiers, leaving this one unchanged.

        Suites are meant to grow: every incident that a suite missed should
        become a falsifier, so the bank is a record of what has actually gone
        wrong rather than what someone imagined might.
        """
        return FalsificationSuite(
            id=self.id,
            description=self.description,
            falsifiers=self.falsifiers + falsifiers,
            inconclusive_when_uncertain=self.inconclusive_when_uncertain,
        )


@dataclass(frozen=True, slots=True)
class SuiteLibrary:
    """A named collection of suites, so a team can version its verifiers."""

    suites: dict[str, FalsificationSuite] = field(default_factory=dict)

    def add(self, suite: FalsificationSuite) -> SuiteLibrary:
        """Return a library with ``suite`` added."""
        return SuiteLibrary(suites={**self.suites, suite.id: suite})

    def get(self, suite_id: str) -> FalsificationSuite:
        """Fetch a suite by id."""
        return self.suites[suite_id]
