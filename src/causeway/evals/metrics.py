"""Scoring judgment answers against labels.

Pure functions over :class:`Observation` records, so every number in a report
can be re-derived by hand from the observations it was computed from.

For Nouls -- the platform's detectors and falsifiers -- accuracy alone hides
what matters, so four more numbers are reported:

* **Brier score**: mean squared distance between the probability and the label.
  Rewards being confidently right and punishes being confidently wrong, which
  matters because policy thresholds on these probabilities.
* **Calibration error** (expected calibration error, 10 bins): whether "0.8"
  really means right about 80% of the time.
* **Miss rate**: of the cases labelled *yes* -- an injection, a refutation --
  how many the model called *no*. For a guardrail this is the expensive error.
* **Undecided rate**: answers too close to 0.5 to count as a finding, using the
  same floor the falsification suites use. Those route to a human in
  production, so a model that shrugs a lot costs reviewer time.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .cases import Label

UNDECIDED_BELOW = 0.2
"""Confidence under which an answer is a shrug. Matches ``Finding.decisive`` in
``causeway.verify.falsify``, so the eval and the runtime agree on what counts."""

CALIBRATION_BINS = 10

Kind = Literal["noul", "choice", "score"]


@dataclass(frozen=True, slots=True)
class Observation:
    """One scored answer: what the label said and what the model said."""

    case_id: str
    family: str
    key: str
    kind: Kind
    expected: Label
    predicted: Label
    confidence: float
    correct: bool
    probability: float | None = None
    """The Noul probability of *yes*, when the question is a Noul."""
    score: float | None = None
    """The Score's weighted position, when the question is a Score."""

    @property
    def undecided(self) -> bool:
        """Whether the answer was too uncertain to act on."""
        return self.confidence < UNDECIDED_BELOW


@dataclass(frozen=True, slots=True)
class Metrics:
    """Summary numbers for a group of observations. ``None`` means not applicable."""

    count: int
    accuracy: float | None
    undecided_rate: float | None
    brier: float | None = None
    calibration_error: float | None = None
    miss_rate: float | None = None
    false_alarm_rate: float | None = None
    mean_absolute_error: float | None = None
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0

    def as_dict(self) -> dict[str, float | int | None]:
        """JSON-ready form."""
        return {
            "count": self.count,
            "accuracy": self.accuracy,
            "undecided_rate": self.undecided_rate,
            "brier": self.brier,
            "calibration_error": self.calibration_error,
            "miss_rate": self.miss_rate,
            "false_alarm_rate": self.false_alarm_rate,
            "mean_absolute_error": self.mean_absolute_error,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "true_negatives": self.true_negatives,
        }


def summarise(observations: list[Observation]) -> Metrics:
    """Compute every applicable metric for ``observations``."""
    if not observations:
        return Metrics(count=0, accuracy=None, undecided_rate=None)

    nouls = [o for o in observations if o.kind == "noul" and o.probability is not None]
    scores = [o for o in observations if o.kind == "score" and o.score is not None]

    tp = sum(1 for o in nouls if o.expected is True and o.predicted is True)
    fp = sum(1 for o in nouls if o.expected is False and o.predicted is True)
    fn = sum(1 for o in nouls if o.expected is True and o.predicted is False)
    tn = sum(1 for o in nouls if o.expected is False and o.predicted is False)

    return Metrics(
        count=len(observations),
        accuracy=_mean(1.0 if o.correct else 0.0 for o in observations),
        undecided_rate=_mean(1.0 if o.undecided else 0.0 for o in observations),
        brier=_brier(nouls),
        calibration_error=_calibration_error(nouls),
        miss_rate=fn / (tp + fn) if tp + fn else None,
        false_alarm_rate=fp / (fp + tn) if fp + tn else None,
        mean_absolute_error=_mean(abs(_score(o) - _level(o)) for o in scores),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
    )


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def _prob(observation: Observation) -> float:
    if observation.probability is None:
        raise ValueError(f"{observation.case_id}/{observation.key} has no probability")
    return observation.probability


def _score(observation: Observation) -> float:
    if observation.score is None:
        raise ValueError(f"{observation.case_id}/{observation.key} has no score")
    return observation.score


def _level(observation: Observation) -> float:
    if isinstance(observation.expected, bool) or not isinstance(observation.expected, int):
        raise ValueError(f"{observation.case_id}/{observation.key} has no level label")
    return float(observation.expected)


def _target(observation: Observation) -> float:
    return 1.0 if observation.expected is True else 0.0


def _brier(nouls: list[Observation]) -> float | None:
    if not nouls:
        return None
    return sum((_prob(o) - _target(o)) ** 2 for o in nouls) / len(nouls)


def _calibration_error(nouls: list[Observation]) -> float | None:
    """Expected calibration error: bin by probability, compare mean prediction to hit rate."""
    if not nouls:
        return None
    bins: list[list[Observation]] = [[] for _ in range(CALIBRATION_BINS)]
    for o in nouls:
        p = _prob(o)
        bins[min(int(p * CALIBRATION_BINS), CALIBRATION_BINS - 1)].append(o)
    total = len(nouls)
    error = 0.0
    for members in bins:
        if not members:
            continue
        mean_p = sum(_prob(o) for o in members) / len(members)
        hit_rate = sum(_target(o) for o in members) / len(members)
        error += len(members) / total * abs(mean_p - hit_rate)
    return error
