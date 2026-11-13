"""The typed judgment plane.

Wherever the platform needs a judgment -- is this an injection attempt, is this
claim supported by the cited source, which capability does this request mean --
it asks a *System One* model a narrow, typed question and branches on the typed
answer. It does not ask a chat model for prose and parse the prose.

The three question types mirror TypeSafe's System One primitives, because that
is the shape the platform needs and because it keeps the hosted adapter thin:

* ``Noul``   -- "is this true?"      -> a calibrated probability in [0, 1]
* ``Choice`` -- "which of these?"    -> one option, a distribution, a confidence
* ``Score``  -- "which level?"       -> a weighted position on an ordered rubric

Three properties of this shape matter to the platform, and all three are why it
is used here rather than free-form structured output:

1. **The answer space is closed.** A ``Choice`` can only return an option the
   platform supplied, so a judgment can never name a capability, destination or
   approver that does not exist.
2. **Uncertainty is a number, not a hedge.** ``confidence`` and the probability
   distribution are values policy can threshold on, which is how the platform
   routes genuinely uncertain cases to humans instead of guessing.
3. **Questions batch.** Independent questions about the same state evaluate in
   parallel in one call, so decomposing a judgment into twenty narrow questions
   costs about what one broad question costs. That is what makes falsification
   suites affordable -- see ``causeway.verify.falsify``.

None of this is authorization. A judgment is evidence that a deterministic
policy decision may take into account; it is never itself a decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..errors import ContractViolation

# Any JSON-shaped value the platform is willing to send as judgment state.
State = str | dict[str, Any] | list[Any]


@dataclass(frozen=True, slots=True)
class Noul:
    """A yes/no question answered with the probability that the answer is yes.

    ``criteria`` optionally describes what "true" and "false" look like, which
    matters far more than it sounds: most disagreement between a falsifier and
    a reviewer is disagreement about where the line is, and writing the line
    down is how the falsifier becomes reviewable.
    """

    instructions: str
    criteria: dict[str, str] | None = None

    def to_wire(self) -> dict[str, Any]:
        """Render as the transport-level question object."""
        question: dict[str, Any] = {"type": "noul", "instructions": self.instructions}
        if self.criteria:
            question["criteria"] = dict(self.criteria)
        return question


@dataclass(frozen=True, slots=True)
class Choice:
    """A single selection from a fixed, unordered set of options."""

    instructions: str
    criteria: dict[str, str | None]

    def __post_init__(self) -> None:
        if len(self.criteria) < 2:
            raise ContractViolation("a Choice needs at least two options")

    def to_wire(self) -> dict[str, Any]:
        """Render as the transport-level question object."""
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": dict(self.criteria),
        }


@dataclass(frozen=True, slots=True)
class Score:
    """A rating against an ordered rubric, lowest level first."""

    instructions: str
    criteria: list[str]

    def __post_init__(self) -> None:
        if not 2 <= len(self.criteria) <= 10:
            raise ContractViolation("a Score needs between 2 and 10 ordered levels")

    def to_wire(self) -> dict[str, Any]:
        """Render as the transport-level question object."""
        return {
            "type": "score",
            "instructions": self.instructions,
            "criteria": list(self.criteria),
        }


Question = Noul | Choice | Score


@dataclass(frozen=True, slots=True)
class NoulAnswer:
    """The probability that the answer to a ``Noul`` is yes."""

    noul: float

    @property
    def confidence(self) -> float:
        """How far from maximally uncertain the answer is, in [0, 1].

        A Noul has no separate confidence field: a probability of 0.5 *is* the
        uncertain answer. Folding it into the same 0-1 scale as Choice and
        Score lets the supervisor board treat all three uniformly.
        """
        return abs(self.noul - 0.5) * 2.0


@dataclass(frozen=True, slots=True)
class ChoiceAnswer:
    """The selected option, the full distribution, and a confidence."""

    choice: str
    probabilities: dict[str, float]
    confidence: float


@dataclass(frozen=True, slots=True)
class ScoreAnswer:
    """A probability-weighted position on the rubric, plus its legend."""

    score: float
    probabilities: dict[str, float]
    legend: dict[str, str]
    confidence: float

    @property
    def level(self) -> int:
        """The nearest discrete rubric level, for code that needs an index."""
        return round(self.score)


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer


@dataclass(frozen=True, slots=True)
class Usage:
    """Token accounting for one judgment call, carried into cost budgets."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class JudgmentResult:
    """The answers to one batch of questions about one state."""

    model: str
    answers: dict[str, Answer]
    usage: Usage = field(default_factory=Usage)

    def noul(self, key: str) -> NoulAnswer:
        """Fetch a Noul answer, asserting its type."""
        answer = self.answers[key]
        if not isinstance(answer, NoulAnswer):
            raise ContractViolation(f"question {key!r} did not answer with a noul")
        return answer

    def choice(self, key: str) -> ChoiceAnswer:
        """Fetch a Choice answer, asserting its type."""
        answer = self.answers[key]
        if not isinstance(answer, ChoiceAnswer):
            raise ContractViolation(f"question {key!r} did not answer with a choice")
        return answer

    def score(self, key: str) -> ScoreAnswer:
        """Fetch a Score answer, asserting its type."""
        answer = self.answers[key]
        if not isinstance(answer, ScoreAnswer):
            raise ContractViolation(f"question {key!r} did not answer with a score")
        return answer


@runtime_checkable
class SystemOne(Protocol):
    """The judgment plane contract.

    Implementations must be *batching*: one call, many independent questions
    about one state. The platform relies on that to keep decomposed judgments
    affordable, and on it never raising an allow-shaped value on failure -- a
    judgment plane that cannot answer must raise ``JudgmentUnavailable`` so the
    caller fails closed.
    """

    @property
    def model_id(self) -> str:
        """The model identifier recorded in evidence for every judgment."""
        ...

    def ask(self, state: State, questions: dict[str, Question]) -> JudgmentResult:
        """Evaluate every question against ``state`` and return typed answers."""
        ...
