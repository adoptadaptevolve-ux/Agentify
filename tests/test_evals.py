"""The eval harness scores a judge correctly, refuses bad data, and gates honestly."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from capabilities.sparring_partner.falsifiers import MODEL_CHANGE_SUITE

from causeway.cli import main as cli_main
from causeway.contracts.judgment import (
    Answer,
    ChoiceAnswer,
    JudgmentResult,
    Noul,
    NoulAnswer,
    Question,
    ScoreAnswer,
    State,
    question_from_wire,
)
from causeway.errors import ConfigurationError, ContractViolation, JudgmentUnavailable
from causeway.evals import (
    EvalHarness,
    apply_gates,
    compare,
    load_cases,
    parse_case,
    parse_gates,
)
from causeway.verify.falsify import SuiteLibrary

ROOT = Path(__file__).resolve().parents[1]
YES_NO = {"type": "noul", "instructions": "Is this bad?"}


class StubJudge:
    """Answers from a table keyed by state, so every metric can be checked by hand."""

    model_id = "stub-1"

    def __init__(self, answers: Callable[[State, dict[str, Question]], dict[str, Answer]]):
        self._answers = answers

    def ask(self, state: State, questions: dict[str, Question]) -> JudgmentResult:
        return JudgmentResult(model=self.model_id, answers=self._answers(state, questions))


def _noul_case(case_id: str, label: bool | None, family: str = "detector") -> dict[str, Any]:
    return {
        "id": case_id,
        "family": family,
        "state": case_id,
        "questions": {"bad": YES_NO},
        "expect": {"bad": label},
    }


def _noul_judge(probabilities: dict[str, float]) -> StubJudge:
    return StubJudge(lambda state, _: {"bad": NoulAnswer(noul=probabilities[str(state)])})


# --------------------------------------------------------------------- metrics --


def test_noul_metrics_match_a_hand_calculation() -> None:
    """Two misses and one false alarm, so a formula that mixes them up is caught."""
    cases = [
        parse_case(_noul_case("a", True)),   # 0.90 -> yes, right
        parse_case(_noul_case("b", False)),  # 0.20 -> no, right
        parse_case(_noul_case("c", True)),   # 0.45 -> no: a miss, and undecided
        parse_case(_noul_case("d", False)),  # 0.55 -> yes: a false alarm, and undecided
        parse_case(_noul_case("e", True)),   # 0.30 -> no: a confident miss
    ]
    judge = _noul_judge({"a": 0.9, "b": 0.2, "c": 0.45, "d": 0.55, "e": 0.3})
    overall = EvalHarness(judge).run(cases).to_dict()["overall"]

    assert overall["count"] == 5
    assert overall["accuracy"] == pytest.approx(2 / 5)
    assert overall["brier"] == pytest.approx((0.01 + 0.04 + 0.3025 + 0.3025 + 0.49) / 5)
    assert overall["calibration_error"] == pytest.approx((0.1 + 0.2 + 0.55 + 0.55 + 0.7) / 5)
    assert overall["miss_rate"] == pytest.approx(2 / 3)
    assert overall["false_alarm_rate"] == pytest.approx(1 / 2)
    assert overall["undecided_rate"] == pytest.approx(2 / 5)
    assert (overall["true_positives"], overall["false_negatives"]) == (1, 2)
    assert (overall["false_positives"], overall["true_negatives"]) == (1, 1)


def test_choice_and_score_answers_are_scored() -> None:
    case = parse_case(
        {
            "id": "mixed",
            "family": "routing",
            "state": "s",
            "questions": {
                "route": {
                    "type": "choice",
                    "instructions": "Which?",
                    "criteria": {"a": None, "b": None},
                },
                "tier": {
                    "type": "score",
                    "instructions": "How bad?",
                    "criteria": ["low", "mid", "high"],
                },
            },
            "expect": {"route": "a", "tier": 2},
        }
    )
    judge = StubJudge(
        lambda *_: {
            "route": ChoiceAnswer(choice="b", probabilities={"a": 0.3, "b": 0.7}, confidence=0.4),
            "tier": ScoreAnswer(score=1.6, probabilities={}, legend={}, confidence=0.8),
        }
    )
    report = EvalHarness(judge).run([case]).to_dict()

    assert report["questions"]["routing/route"]["accuracy"] == 0.0
    assert report["questions"]["routing/tier"]["accuracy"] == 1.0  # 1.6 rounds to level 2
    assert report["questions"]["routing/tier"]["mean_absolute_error"] == pytest.approx(0.4)


def test_an_unlabelled_question_is_asked_but_not_scored() -> None:
    report = EvalHarness(_noul_judge({"a": 0.9})).run([parse_case(_noul_case("a", None))])
    assert report.observations == []


def test_falsifier_questions_use_each_falsifiers_own_threshold() -> None:
    """holdout_leakage refutes at 0.35, so 0.40 is a yes even though it is under 0.5."""
    library = SuiteLibrary().add(MODEL_CHANGE_SUITE)
    labels = {f.id: None for f in MODEL_CHANGE_SUITE.falsifiers} | {"holdout_leakage": True}
    case = parse_case(
        {
            "id": "f",
            "family": "model_change",
            "state": "s",
            "questions": {"falsifiers": "model_change"},
            "expect": labels,
        },
        suites=library,
    )
    judge = StubJudge(lambda _, qs: {k: NoulAnswer(noul=0.4) for k in qs})
    (observation,) = EvalHarness(judge).run([case]).observations
    assert observation.predicted is True and observation.correct


# ---------------------------------------------------------------------- errors --


def test_an_unavailable_model_is_an_error_not_a_pass() -> None:
    def refuse(*_: Any) -> dict[str, Answer]:
        raise JudgmentUnavailable("endpoint down")

    report = EvalHarness(StubJudge(refuse)).run([parse_case(_noul_case("a", True))])
    report.check(gates=parse_gates({"overall": {"max_error_rate": 0.0}}))

    assert report.observations == []
    assert report.errors[0].error.startswith("JudgmentUnavailable")
    assert report.to_dict()["overall"]["error_rate"] == 1.0
    assert not report.passed


def test_an_answer_of_the_wrong_type_is_an_error() -> None:
    judge = StubJudge(lambda *_: {"bad": ChoiceAnswer(choice="x", probabilities={}, confidence=1)})
    report = EvalHarness(judge).run([parse_case(_noul_case("a", True))])
    assert "is a Noul" in report.errors[0].error


# ---------------------------------------------------------------------- loading --


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"surprise": 1}, "unknown fields"),
        ({"expect": {}}, "no label for"),
        ({"expect": {"bad": True, "other": False}}, "questions not asked"),
        ({"expect": {"bad": "yes"}}, "true, false or null"),
        ({"questions": "telepathy"}, "unknown question set"),
        ({"questions": {"falsifiers": "nope"}}, "unknown falsification suite"),
        ({"questions": {"bad": {"type": "noul", "instructions": "x", "tone": "y"}}}, "unexpected"),
    ],
)
def test_bad_cases_are_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        parse_case({**_noul_case("a", True), **change})


def test_a_choice_label_must_be_one_of_the_options() -> None:
    raw = {
        "id": "c",
        "family": "f",
        "state": "s",
        "questions": {
            "q": {"type": "choice", "instructions": "?", "criteria": {"a": None, "b": None}}
        },
        "expect": {"q": "c"},
    }
    with pytest.raises(ConfigurationError, match="not one of"):
        parse_case(raw)


def test_a_score_label_must_be_a_level_inside_the_rubric() -> None:
    raw = {
        "id": "s",
        "family": "f",
        "state": "s",
        "questions": {"q": {"type": "score", "instructions": "?", "criteria": ["lo", "hi"]}},
        "expect": {"q": True},
    }
    with pytest.raises(ConfigurationError, match="level index"):
        parse_case(raw)
    with pytest.raises(ConfigurationError, match="outside"):
        parse_case({**raw, "expect": {"q": 5}})


def test_duplicate_case_ids_are_refused(tmp_path: Path) -> None:
    line = json.dumps(_noul_case("same", True))
    (tmp_path / "cases.jsonl").write_text(f"{line}\n{line}\n")
    with pytest.raises(ConfigurationError, match="duplicate case id"):
        load_cases(tmp_path)


def test_wire_questions_round_trip() -> None:
    question = Noul(instructions="Is it?", criteria={"true": "yes", "false": "no"})
    assert question_from_wire(question.to_wire()) == question
    with pytest.raises(ContractViolation, match="unknown question type"):
        question_from_wire({"type": "essay", "instructions": "Discuss."})


def test_the_seed_dataset_loads() -> None:
    from causeway.cli import _sparring_suites

    cases = load_cases(ROOT / "evals" / "cases", suites=_sparring_suites())
    assert {case.family for case in cases} == {"injection", "model_change", "proposal_integrity"}


# ------------------------------------------------------------- gates & baseline --


def _summary(accuracy: float, miss: float | None = 0.0) -> dict[str, Any]:
    metrics = {"accuracy": accuracy, "miss_rate": miss, "error_rate": 0.0}
    return {"overall": metrics, "families": {"injection": dict(metrics)}}


def test_gates_pass_and_fail_on_their_limits() -> None:
    gates = parse_gates(
        {"overall": {"min_accuracy": 0.9}, "families": {"injection": {"max_miss_rate": 0.05}}}
    )
    assert all(g.passed for g in apply_gates(_summary(0.95, 0.0), gates))
    failed = [g for g in apply_gates(_summary(0.85, 0.2), gates) if not g.passed]
    assert {(g.scope, g.limit) for g in failed} == {
        ("overall", "min_accuracy"),
        ("injection", "max_miss_rate"),
    }


def test_a_gate_on_a_family_with_no_cases_fails() -> None:
    gates = parse_gates({"families": {"egress": {"min_accuracy": 0.9}}})
    (result,) = apply_gates(_summary(1.0), gates)
    assert not result.passed and result.note == "no cases in this family"


def test_unknown_gate_limits_are_refused() -> None:
    with pytest.raises(ConfigurationError, match="unknown limits"):
        parse_gates({"overall": {"min_vibes": 0.9}})


def test_a_regression_beyond_the_tolerance_is_flagged() -> None:
    regressions = compare(_summary(0.90, 0.10), _summary(0.95, 0.02), tolerance=0.02)
    assert {(r.scope, r.metric) for r in regressions} == {
        ("overall", "accuracy"),
        ("overall", "miss_rate"),
        ("injection", "accuracy"),
        ("injection", "miss_rate"),
    }
    assert compare(_summary(0.94), _summary(0.95), tolerance=0.02) == []


# ------------------------------------------------------------------------- CLI --


def test_the_cli_matches_the_committed_sandbox_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """What CI runs: the dataset is valid and the offline judge has not got worse."""
    monkeypatch.chdir(ROOT)
    out = tmp_path / "report.json"
    code = cli_main(
        ["eval", "evals/cases", "--judge", "sandbox",
         "--baseline", "evals/baselines/sandbox.json", "--out", str(out)]
    )
    assert code == 0
    report = json.loads(out.read_text())
    assert report["passed"] is True
    assert report["model"] == "offline-judge-1"


def test_the_cli_exits_non_zero_when_a_gate_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture is not a real model, and the production gates say so."""
    monkeypatch.chdir(ROOT)
    code = cli_main(["eval", "evals/cases", "--judge", "sandbox", "--gates", "evals/gates.json"])
    assert code == 1


def test_the_hosted_judge_never_falls_back_to_the_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="TYPESAFE_API_KEY"):
        cli_main(["eval", "evals/cases", "--judge", "hosted"])
