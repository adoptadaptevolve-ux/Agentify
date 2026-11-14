"""Offline evaluation of the judgment model.

The platform's tests run against a deterministic fixture on purpose (ADR 0007),
so they say nothing about whether a real model answers the platform's
questions well. This package does: it replays labelled cases against any
``SystemOne`` judge, scores the answers, checks them against gates a risk owner
sets, and compares them with a previous run so a quieter regression is caught
too.

See ``docs/guides/evaluate-models.md``.
"""

from .cases import (
    DEFAULT_THRESHOLD,
    NAMED_QUESTION_SETS,
    EvalCase,
    dataset_digest,
    load_cases,
    parse_case,
)
from .gates import GateResult, Gates, Regression, apply_gates, compare, load_gates, parse_gates
from .harness import CaseError, EvalHarness, EvalReport
from .metrics import UNDECIDED_BELOW, Metrics, Observation, summarise

__all__ = [
    "DEFAULT_THRESHOLD",
    "NAMED_QUESTION_SETS",
    "UNDECIDED_BELOW",
    "CaseError",
    "EvalCase",
    "EvalHarness",
    "EvalReport",
    "GateResult",
    "Gates",
    "Metrics",
    "Observation",
    "Regression",
    "apply_gates",
    "compare",
    "dataset_digest",
    "load_cases",
    "load_gates",
    "parse_case",
    "parse_gates",
    "summarise",
]
