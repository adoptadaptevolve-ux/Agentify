"""Evaluation cases: labelled states the judgment plane is scored against.

A case is one state, the questions the platform would ask about it, and what a
careful human says the right answers are. Cases live in JSONL files, one case
per line, so they diff cleanly and a new case after an incident is a one-line
change::

    {"id": "inj-007", "family": "injection", "state": "...",
     "questions": "injection",
     "expect": {"injection_instruction": true, "injection_authority": false,
                "injection_exfiltration": false},
     "source": "hand-labelled", "notes": "why the labels are what they are"}

``questions`` takes one of three forms:

* a named platform question set, e.g. ``"injection"`` -- the exact questions
  the guardrails ask in production, so the eval scores what actually runs;
* ``{"falsifiers": "<suite id>"}`` -- every falsifier in a suite, scored at
  each falsifier's own threshold;
* an inline object of wire-format questions, for anything else.

``expect`` names a label for every question: ``true``/``false`` for a Noul, an
option name for a Choice, a level index for a Score. ``null`` means the
labeller could not decide from the state alone; the question is still asked
but not scored. A label is required for every question, so leaving one out has
to be a decision rather than an oversight.

Loading is strict. Unknown fields, labels outside a question's answer space and
duplicate case ids are refused, because a dataset that silently drops a case
reports a better score than the model earned.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..canonical import digest
from ..contracts.judgment import (
    Choice,
    Noul,
    Question,
    Score,
    State,
    question_from_wire,
)
from ..errors import ConfigurationError
from ..judgment.questions import injection_questions, risk_tier_question
from ..verify.falsify import SuiteLibrary

Label = bool | str | int | None

NAMED_QUESTION_SETS: dict[str, Callable[[], dict[str, Question]]] = {
    "injection": injection_questions,
    "risk_tier": risk_tier_question,
}
"""Platform question sets a case can name instead of spelling the questions out.
Only sets that take no parameters can be named."""

DEFAULT_THRESHOLD = 0.5
"""Probability at or above which a Noul counts as answering yes, unless the
question comes from a falsifier with its own threshold."""

_CASE_KEYS = frozenset({"id", "family", "state", "questions", "expect", "source", "notes"})


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One labelled state."""

    id: str
    family: str
    state: State
    questions: dict[str, Question]
    expect: dict[str, Label]
    thresholds: dict[str, float] = field(default_factory=dict)
    """Per-question yes-thresholds for Nouls; missing keys use the default."""
    source: str = ""
    notes: str = ""

    def threshold(self, key: str) -> float:
        """The yes-threshold for Noul ``key``."""
        return self.thresholds.get(key, DEFAULT_THRESHOLD)


def load_cases(
    path: str | Path, *, suites: SuiteLibrary | None = None
) -> tuple[EvalCase, ...]:
    """Load every case from a JSONL file, or from every ``*.jsonl`` in a directory."""
    root = Path(path)
    files = sorted(root.glob("*.jsonl")) if root.is_dir() else [root]
    if not files:
        raise ConfigurationError(f"no *.jsonl case files found in {root}")
    cases: list[EvalCase] = []
    for file in files:
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            where = f"{file}:{number}"
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ConfigurationError(f"{where}: not valid JSON: {exc}") from None
            cases.append(parse_case(raw, suites=suites, where=where))
    _reject_duplicates(cases)
    return tuple(cases)


def parse_case(
    raw: Any, *, suites: SuiteLibrary | None = None, where: str = "case"
) -> EvalCase:
    """Validate and build one case."""
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{where}: a case must be a JSON object")
    unknown = set(raw) - _CASE_KEYS
    if unknown:
        raise ConfigurationError(f"{where}: unknown fields {sorted(unknown)}")
    for required in ("id", "family", "state", "questions", "expect"):
        if required not in raw:
            raise ConfigurationError(f"{where}: missing {required!r}")
    case_id, family = raw["id"], raw["family"]
    if not isinstance(case_id, str) or not case_id:
        raise ConfigurationError(f"{where}: 'id' must be a non-empty string")
    if not isinstance(family, str) or not family:
        raise ConfigurationError(f"{where}: 'family' must be a non-empty string")
    state = raw["state"]
    if not isinstance(state, (str, dict, list)):
        raise ConfigurationError(f"{where}: 'state' must be text, an object or a list")

    where = f"{where} ({case_id})"
    questions, thresholds = _resolve_questions(raw["questions"], suites=suites, where=where)
    expect = raw["expect"]
    if not isinstance(expect, dict):
        raise ConfigurationError(f"{where}: 'expect' must be an object")
    missing = set(questions) - set(expect)
    if missing:
        raise ConfigurationError(
            f"{where}: no label for {sorted(missing)}; use null if it cannot be decided"
        )
    extra = set(expect) - set(questions)
    if extra:
        raise ConfigurationError(f"{where}: labels for questions not asked: {sorted(extra)}")
    for key, label in expect.items():
        _check_label(questions[key], label, where=f"{where} {key}")

    return EvalCase(
        id=case_id,
        family=family,
        state=state,
        questions=questions,
        expect=dict(expect),
        thresholds=thresholds,
        source=str(raw.get("source", "")),
        notes=str(raw.get("notes", "")),
    )


def dataset_digest(cases: Iterable[EvalCase]) -> str:
    """A digest of exactly what was scored, so two reports can be compared honestly."""
    return digest(
        [
            {
                "id": case.id,
                "family": case.family,
                "state": case.state,
                "questions": {k: q.to_wire() for k, q in sorted(case.questions.items())},
                "expect": case.expect,
                "thresholds": case.thresholds,
            }
            for case in cases
        ]
    )


def _resolve_questions(
    spec: Any, *, suites: SuiteLibrary | None, where: str
) -> tuple[dict[str, Question], dict[str, float]]:
    if isinstance(spec, str):
        factory = NAMED_QUESTION_SETS.get(spec)
        if factory is None:
            raise ConfigurationError(
                f"{where}: unknown question set {spec!r}; "
                f"known sets are {sorted(NAMED_QUESTION_SETS)}"
            )
        return factory(), {}
    if isinstance(spec, dict) and set(spec) == {"falsifiers"}:
        suite_id = spec["falsifiers"]
        if suites is None or suite_id not in suites.suites:
            raise ConfigurationError(f"{where}: unknown falsification suite {suite_id!r}")
        suite = suites.get(suite_id)
        return suite.questions(), {f.id: f.threshold for f in suite.falsifiers}
    if isinstance(spec, dict) and spec:
        try:
            return {str(k): question_from_wire(v) for k, v in spec.items()}, {}
        except Exception as exc:
            raise ConfigurationError(f"{where}: {exc}") from None
    raise ConfigurationError(f"{where}: 'questions' must name a set, a suite or list questions")


def _check_label(question: Question, label: Label, *, where: str) -> None:
    if label is None:
        return
    if isinstance(question, Noul):
        if not isinstance(label, bool):
            raise ConfigurationError(f"{where}: a Noul label must be true, false or null")
    elif isinstance(question, Choice):
        if not isinstance(label, str) or label not in question.criteria:
            raise ConfigurationError(
                f"{where}: label {label!r} is not one of {sorted(question.criteria)}"
            )
    elif isinstance(question, Score):
        if isinstance(label, bool) or not isinstance(label, int):
            raise ConfigurationError(f"{where}: a Score label must be a level index")
        if not 0 <= label < len(question.criteria):
            raise ConfigurationError(
                f"{where}: level {label} is outside 0..{len(question.criteria) - 1}"
            )


def _reject_duplicates(cases: list[EvalCase]) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ConfigurationError(f"duplicate case id {case.id!r}")
        seen.add(case.id)
