"""The offline judge: a deterministic System One stand-in.

Every golden path in this repository runs end to end with no API key and no
network, because the default judgment plane is this fixture rather than a
hosted model.

That is a deliberate platform decision, not a convenience. It means:

* **CI tests the platform, not the model.** An assertion that a prohibited
  action is denied should fail when the transaction guard regresses, never
  because a model phrased something differently today. Model quality is
  measured by the eval harness, against recorded cases, on its own schedule.
* **The local sandbox is genuinely local.** A developer can run the whole
  paved road on a laptop, on a plane, before they have credentials.
* **Judgment is swappable.** If the platform only ever depends on the
  ``SystemOne`` protocol, changing judgment providers is a constructor
  argument, not a migration.

The fixture is cue-driven: a cue says "when a question that looks like *this*
is asked about a state that looks like *that*, push the answer *this* far in
*that* direction". Probabilities come out of a logistic over the matched
weights, so the fixture produces calibrated-looking distributions and genuine
uncertainty rather than 0.0 and 1.0, and code that thresholds on confidence
gets exercised.

It is a fixture. It is not a model, it does not generalise, and nothing in the
platform should behave differently because it is the judge.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..canonical import canonical_json
from ..contracts.judgment import (
    Answer,
    Choice,
    ChoiceAnswer,
    JudgmentResult,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
    State,
    Usage,
)


@dataclass(frozen=True, slots=True)
class Cue:
    """One piece of evidence the offline judge knows how to recognise."""

    when_question: str
    """Regex matched against the question's instructions, case-insensitively."""
    when_state: str
    """Regex matched against the rendered state, case-insensitively."""
    weight: float = 1.0
    """Log-odds contribution. Positive supports "yes"/this option/this level."""
    option: str | None = None
    """For a ``Choice``: which option this cue supports."""
    level: int | None = None
    """For a ``Score``: which rubric level this cue supports."""

    def applies(self, instructions: str, state_text: str) -> bool:
        """Whether both halves of the cue match."""
        return bool(
            re.search(self.when_question, instructions, re.IGNORECASE)
            and re.search(self.when_state, state_text, re.IGNORECASE)
        )


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _softmax(logits: list[float], temperature: float = 1.0) -> list[float]:
    largest = max(logits)
    exponentials = [math.exp((value - largest) / temperature) for value in logits]
    total = sum(exponentials)
    return [value / total for value in exponentials]


def _confidence(probabilities: list[float]) -> float:
    """Normalised negative entropy of the distribution, in [0, 1].

    This is what "confidence" means throughout the platform: a statistic
    computed from the distribution the answer already gives you. A distribution
    concentrated on one option scores near 1; a uniform one scores 0.
    """
    if len(probabilities) < 2:
        return 1.0
    entropy = -sum(p * math.log(p) for p in probabilities if p > 0.0)
    return max(0.0, min(1.0, 1.0 - entropy / math.log(len(probabilities))))


def render_state(state: State) -> str:
    """Flatten judgment state to searchable text.

    The hosted model reads structure; the fixture only needs to find cues, so
    it matches against the canonical rendering.
    """
    return state if isinstance(state, str) else canonical_json(state)


@dataclass(slots=True)
class OfflineSystemOne:
    """A deterministic ``SystemOne`` implementation driven by cues."""

    cues: tuple[Cue, ...] = ()
    noul_bias: float = -1.4
    """Prior log-odds for a Noul with no matching cue: about 0.2, i.e. "probably
    not, but not confidently". A fixture that answered 0.0 by default would let
    a falsification suite pass without ever being exercised."""
    choice_temperature: float = 1.0
    model_id_value: str = "offline-judge-1"
    extra_usage: Usage = field(default_factory=lambda: Usage(0, 0))

    @property
    def model_id(self) -> str:
        """The identifier recorded in evidence for every judgment."""
        return self.model_id_value

    def with_cues(self, *cues: Cue) -> OfflineSystemOne:
        """Return a copy with additional cues; the original is untouched."""
        return OfflineSystemOne(
            cues=self.cues + cues,
            noul_bias=self.noul_bias,
            choice_temperature=self.choice_temperature,
            model_id_value=self.model_id_value,
        )

    def ask(self, state: State, questions: dict[str, Question]) -> JudgmentResult:
        """Answer every question against ``state``."""
        state_text = render_state(state)
        answers: dict[str, Answer] = {
            key: self._answer(question, state_text) for key, question in questions.items()
        }
        return JudgmentResult(
            model=self.model_id,
            answers=answers,
            usage=Usage(input_tokens=len(state_text) // 4, output_tokens=len(questions) * 4),
        )

    def _matching(self, instructions: str, state_text: str) -> list[Cue]:
        return [cue for cue in self.cues if cue.applies(instructions, state_text)]

    def _answer(self, question: Question, state_text: str) -> Answer:
        match question:
            case Noul():
                matched = self._matching(question.instructions, state_text)
                log_odds = self.noul_bias + sum(cue.weight for cue in matched)
                return NoulAnswer(noul=round(_logistic(log_odds), 6))

            case Choice():
                options = list(question.criteria)
                matched = self._matching(question.instructions, state_text)
                logits = [
                    sum(cue.weight for cue in matched if cue.option == option)
                    for option in options
                ]
                probabilities = _softmax(logits, self.choice_temperature)
                best = max(range(len(options)), key=lambda index: probabilities[index])
                return ChoiceAnswer(
                    choice=options[best],
                    probabilities={
                        option: round(p, 6)
                        for option, p in zip(options, probabilities, strict=True)
                    },
                    confidence=round(_confidence(probabilities), 6),
                )

            case Score():
                levels = list(range(len(question.criteria)))
                matched = self._matching(question.instructions, state_text)
                logits = [
                    sum(cue.weight for cue in matched if cue.level == level) for level in levels
                ]
                probabilities = _softmax(logits, self.choice_temperature)
                return ScoreAnswer(
                    score=round(
                        sum(level * p for level, p in zip(levels, probabilities, strict=True)), 6
                    ),
                    probabilities={
                        str(level): round(p, 6)
                        for level, p in zip(levels, probabilities, strict=True)
                    },
                    legend={
                        str(level): text for level, text in enumerate(question.criteria)
                    },
                    confidence=round(_confidence(probabilities), 6),
                )
