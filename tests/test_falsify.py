"""Falsification never proves; it fails to refute."""

from __future__ import annotations

import pytest

from causeway.judgment.offline import Cue, OfflineSystemOne
from causeway.verify.falsify import (
    FalsificationSuite,
    Falsifier,
    Severity,
    Verdict,
)

SUITE = FalsificationSuite(
    id="demo",
    description="two failure modes",
    falsifiers=(
        Falsifier(
            id="leak",
            claim="the holdout is clean",
            question="Is there evidence that holdout rows appear in training?",
            threshold=0.4,
            severity=Severity.BLOCKING,
        ),
        Falsifier(
            id="docs",
            claim="limitations are documented",
            question="Does the proposal omit its limitations?",
            threshold=0.8,
            severity=Severity.ADVISORY,
        ),
    ),
)

DECISIVE = OfflineSystemOne(
    cues=(
        Cue(
            when_question="holdout rows appear in training",
            when_state="random row split",
            weight=4.0,
        ),
        Cue(
            when_question="holdout rows appear in training",
            when_state="split by customer",
            weight=-4.0,
        ),
        Cue(when_question="omit its limitations", when_state="limitations:", weight=-4.0),
        Cue(when_question="omit its limitations", when_state="no limitations", weight=4.0),
    )
)


def test_a_refutation_is_reported_with_its_severity() -> None:
    """A crossed threshold is a refutation, and it keeps its severity."""
    report = SUITE.run(DECISIVE, {"notes": "random row split; limitations: none stated"})
    assert report.verdict is Verdict.REFUTED
    assert [f.falsifier_id for f in report.refutations] == ["leak"]
    assert report.blocking_refutations


def test_surviving_is_not_the_same_as_being_correct() -> None:
    """The vocabulary matters: the verdict is SURVIVED, never CORRECT."""
    report = SUITE.run(DECISIVE, {"notes": "split by customer; limitations: small accounts"})
    assert report.verdict is Verdict.SURVIVED
    assert not report.refutations
    assert Verdict.__members__.keys() == {"SURVIVED", "REFUTED", "INCONCLUSIVE"}


def test_an_undecisive_answer_does_not_count_as_a_pass() -> None:
    """A shrug routes to a human rather than quietly clearing the claim."""
    undecided = OfflineSystemOne(noul_bias=0.0)  # every answer lands on 0.5
    report = SUITE.run(undecided, {"notes": "nothing recognisable"})
    assert report.verdict is Verdict.INCONCLUSIVE


def test_severity_weights_the_concern_score() -> None:
    """A blocking refutation outranks an advisory one at the same probability."""
    blocking = SUITE.run(DECISIVE, {"notes": "random row split; limitations: stated"})
    advisory = SUITE.run(DECISIVE, {"notes": "split by customer; no limitations"})
    assert blocking.concern > advisory.concern


def test_concern_is_the_worst_finding_not_the_average() -> None:
    """One real hit must not be averaged away by the questions that found nothing."""
    report = SUITE.run(DECISIVE, {"notes": "random row split; limitations: stated"})
    worst = max(finding.weighted_concern for finding in report.findings)
    assert report.concern == pytest.approx(worst)


def test_the_whole_suite_is_one_judgment_call() -> None:
    """Batching is what makes decomposition affordable, so it is asserted."""
    calls: list[int] = []

    class Counting(OfflineSystemOne):
        def ask(self, state, questions):  # type: ignore[no-untyped-def]
            calls.append(len(questions))
            return super().ask(state, questions)

    Counting(cues=DECISIVE.cues).ask  # noqa: B018
    suite_judge = Counting(cues=DECISIVE.cues)
    SUITE.run(suite_judge, {"notes": "split by customer; limitations: stated"})
    assert calls == [2]


def test_an_empty_suite_is_refused() -> None:
    """A suite with no falsifiers would pass everything."""
    with pytest.raises(ValueError, match="no falsifiers"):
        FalsificationSuite(id="empty", description="", falsifiers=())


def test_suites_grow_without_mutating() -> None:
    """Every incident a suite missed should become a falsifier."""
    extended = SUITE.extend(
        Falsifier(id="new", claim="c", question="Did something else go wrong?")
    )
    assert len(extended.falsifiers) == len(SUITE.falsifiers) + 1
    assert len(SUITE.falsifiers) == 2
