"""Run labelled cases against a judgment model and report how well it did.

The harness calls the judge exactly the way production does -- one batched
``ask`` per state, with the platform's own question sets and thresholds -- so
the score describes the model the platform will actually consult, not a
re-phrased stand-in.

A model that cannot answer (``JudgmentUnavailable`` or any other error) is
recorded as an error against the case. It is never skipped and never scored
as a pass: in production an unavailable judgment fails closed, and an eval
that hid outages would overstate how usable the model is.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..canonical import canonical_json
from ..contracts.judgment import (
    Answer,
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
    SystemOne,
)
from .cases import EvalCase, dataset_digest
from .gates import GateResult, Gates, Regression, apply_gates, compare
from .metrics import Observation, summarise

WORST_CASES = 10
"""How many wrong answers a report lists in full, worst first."""


@dataclass(frozen=True, slots=True)
class CaseError:
    """A case the model could not answer."""

    case_id: str
    family: str
    error: str


@dataclass(slots=True)
class EvalReport:
    """Everything one evaluation run found."""

    model: str
    dataset_digest: str
    case_count: int
    generated_at: str
    observations: list[Observation] = field(default_factory=list)
    errors: list[CaseError] = field(default_factory=list)
    family_case_counts: dict[str, int] = field(default_factory=dict)
    gate_results: list[GateResult] = field(default_factory=list)
    regressions: list[Regression] = field(default_factory=list)
    baseline_dataset_digest: str | None = None

    @property
    def passed(self) -> bool:
        """Every gate held and nothing regressed."""
        return all(g.passed for g in self.gate_results) and not self.regressions

    def check(
        self,
        *,
        gates: Gates | None = None,
        baseline: dict[str, Any] | None = None,
        tolerance: float = 0.02,
    ) -> EvalReport:
        """Apply gates and a baseline comparison, recording the results."""
        summary = self._metrics()
        if gates is not None:
            self.gate_results = apply_gates(summary, gates)
        if baseline is not None:
            self.regressions = compare(summary, baseline, tolerance=tolerance)
            self.baseline_dataset_digest = baseline.get("dataset_digest")
        return self

    def to_dict(self) -> dict[str, Any]:
        """The JSON report: reproducible, diffable and readable by a person."""
        report = self._metrics()
        report.update(
            {
                "model": self.model,
                "dataset_digest": self.dataset_digest,
                "case_count": self.case_count,
                "generated_at": self.generated_at,
                "errors": [
                    {"case_id": e.case_id, "family": e.family, "error": e.error}
                    for e in self.errors
                ],
                "worst": [_observation_dict(o) for o in self.worst()],
                "gates": [g.as_dict() for g in self.gate_results],
                "regressions": [r.as_dict() for r in self.regressions],
                "baseline_dataset_digest": self.baseline_dataset_digest,
                "passed": self.passed,
            }
        )
        return report

    def to_json(self) -> str:
        """Canonical JSON text of :meth:`to_dict`."""
        return canonical_json(self.to_dict())

    def worst(self) -> list[Observation]:
        """Wrong answers, most confidently wrong first."""
        wrong = [o for o in self.observations if not o.correct]
        return sorted(wrong, key=lambda o: (-_wrongness(o), o.case_id, o.key))[:WORST_CASES]

    def summary_text(self) -> str:
        """A short terminal summary."""
        data = self._metrics()
        lines = [
            f"model        : {self.model}",
            f"dataset      : {self.dataset_digest[:19]}  ({self.case_count} cases, "
            f"{len(self.observations)} scored answers, {len(self.errors)} errors)",
            "",
            f"{'scope':24s} {'n':>4s} {'acc':>6s} {'brier':>6s} {'miss':>6s} "
            f"{'f.alarm':>7s} {'undec':>6s} {'errors':>6s}",
        ]
        rows = [("overall", data["overall"]), *sorted(data["families"].items())]
        for scope, m in rows:
            lines.append(
                f"{scope:24s} {m['count']:>4d} {_fmt(m['accuracy']):>6s} {_fmt(m['brier']):>6s} "
                f"{_fmt(m['miss_rate']):>6s} {_fmt(m['false_alarm_rate']):>7s} "
                f"{_fmt(m['undecided_rate']):>6s} {_fmt(m['error_rate']):>6s}"
            )
        if self.gate_results:
            lines.append("")
            failed = [g for g in self.gate_results if not g.passed]
            lines.append(f"gates        : {len(self.gate_results) - len(failed)}/"
                         f"{len(self.gate_results)} passed")
            for g in failed:
                actual = "n/a" if g.actual is None else f"{g.actual:.3f}"
                note = f" ({g.note})" if g.note else ""
                lines.append(f"  FAIL {g.scope}: {g.limit} {g.threshold} -> {actual}{note}")
        if self.baseline_dataset_digest is not None:
            lines.append("")
            if self.baseline_dataset_digest != self.dataset_digest:
                lines.append("baseline     : WARNING scored on a different dataset")
            lines.append(f"regressions  : {len(self.regressions)}")
            for r in self.regressions:
                lines.append(
                    f"  WORSE {r.scope}: {r.metric} {r.baseline:.3f} -> {r.current:.3f}"
                )
        worst = self.worst()
        if worst:
            lines.append("")
            lines.append("worst answers:")
            for o in worst[:5]:
                got = f"{o.probability:.2f}" if o.probability is not None else str(o.predicted)
                lines.append(
                    f"  {o.case_id} {o.key}: expected {o.expected}, got {got}"
                )
        if self.gate_results or self.baseline_dataset_digest is not None:
            lines.append("")
            lines.append("result       : " + ("PASS" if self.passed else "FAIL"))
        return "\n".join(lines)

    def _metrics(self) -> dict[str, Any]:
        by_family: dict[str, list[Observation]] = defaultdict(list)
        by_question: dict[str, list[Observation]] = defaultdict(list)
        for o in self.observations:
            by_family[o.family].append(o)
            by_question[f"{o.family}/{o.key}"].append(o)
        errors_by_family: dict[str, int] = defaultdict(int)
        for e in self.errors:
            errors_by_family[e.family] += 1

        overall = summarise(self.observations).as_dict()
        overall["error_rate"] = len(self.errors) / self.case_count if self.case_count else None
        families: dict[str, dict[str, Any]] = {}
        for family, cases in sorted(self.family_case_counts.items()):
            metrics = summarise(by_family.get(family, [])).as_dict()
            metrics["error_rate"] = errors_by_family[family] / cases if cases else None
            families[family] = metrics
        questions = {
            key: summarise(observations).as_dict()
            for key, observations in sorted(by_question.items())
        }
        return {"overall": overall, "families": families, "questions": questions}


class EvalHarness:
    """Scores a ``SystemOne`` judge against labelled cases."""

    def __init__(self, judge: SystemOne) -> None:
        self._judge = judge

    def run(self, cases: Iterable[EvalCase]) -> EvalReport:
        """Ask every case's questions in one call per case, and score the answers."""
        cases = tuple(cases)
        report = EvalReport(
            model=str(getattr(self._judge, "model_id", "unknown")),
            dataset_digest=dataset_digest(cases),
            case_count=len(cases),
            generated_at=datetime.now(UTC).isoformat(),
        )
        families: dict[str, int] = defaultdict(int)
        models: set[str] = set()
        for case in cases:
            families[case.family] += 1
            try:
                result = self._judge.ask(case.state, case.questions)
                observations = [
                    _observe(case, key, result.answers[key])
                    for key in sorted(case.questions)
                    if case.expect[key] is not None
                ]
            except Exception as exc:
                report.errors.append(
                    CaseError(
                        case_id=case.id,
                        family=case.family,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            models.add(result.model)
            report.observations.extend(observations)
        report.family_case_counts = dict(families)
        if len(models) == 1:
            report.model = models.pop()
        elif models:
            report.model = ",".join(sorted(models))
        return report


def _observe(case: EvalCase, key: str, answer: Answer) -> Observation:
    """Score one answer against its label, checking the answer has the right type."""
    question = case.questions[key]
    expected = case.expect[key]
    if isinstance(question, Noul):
        if not isinstance(answer, NoulAnswer):
            raise TypeError(f"{key} is a Noul but the answer was {type(answer).__name__}")
        predicted = answer.noul >= case.threshold(key)
        return Observation(
            case_id=case.id,
            family=case.family,
            key=key,
            kind="noul",
            expected=expected,
            predicted=predicted,
            confidence=answer.confidence,
            correct=predicted is expected,
            probability=answer.noul,
        )
    if isinstance(question, Choice):
        if not isinstance(answer, ChoiceAnswer):
            raise TypeError(f"{key} is a Choice but the answer was {type(answer).__name__}")
        return Observation(
            case_id=case.id,
            family=case.family,
            key=key,
            kind="choice",
            expected=expected,
            predicted=answer.choice,
            confidence=answer.confidence,
            correct=answer.choice == expected,
        )
    if isinstance(question, Score):
        if not isinstance(answer, ScoreAnswer):
            raise TypeError(f"{key} is a Score but the answer was {type(answer).__name__}")
        return Observation(
            case_id=case.id,
            family=case.family,
            key=key,
            kind="score",
            expected=expected,
            predicted=answer.level,
            confidence=answer.confidence,
            correct=answer.level == expected,
            score=answer.score,
        )
    raise TypeError(f"unsupported question type {type(question).__name__}")


def _wrongness(o: Observation) -> float:
    if o.probability is not None:
        return abs(o.probability - (1.0 if o.expected is True else 0.0))
    return o.confidence


def _observation_dict(o: Observation) -> dict[str, Any]:
    return {
        "case_id": o.case_id,
        "family": o.family,
        "question": o.key,
        "expected": o.expected,
        "predicted": o.predicted,
        "probability": o.probability,
        "score": o.score,
        "confidence": o.confidence,
    }


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"
