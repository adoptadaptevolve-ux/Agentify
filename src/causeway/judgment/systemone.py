"""Adapter for a hosted System One model.

The platform depends only on the ``SystemOne`` protocol, so this adapter is the
single place that knows about a provider. It is deliberately thin: it renders
the platform's question objects onto the wire format, calls the SDK, and maps
answers back. Anything cleverer than that belongs on the platform side of the
boundary, where it can be tested without a network.

Two behaviours here are platform requirements rather than adapter details:

* A judgment plane that cannot answer raises ``JudgmentUnavailable``. It never
  returns a default, a zero or a "probably fine", because every caller in this
  platform treats an unavailable judgment as a reason to fail closed.
* Every call is recorded with its model id, so evidence can answer which model
  version informed a decision months later.
"""

from __future__ import annotations

from typing import Any

from ..contracts.judgment import (
    Answer,
    ChoiceAnswer,
    JudgmentResult,
    NoulAnswer,
    Question,
    ScoreAnswer,
    State,
    Usage,
)
from ..errors import ConfigurationError, JudgmentUnavailable

DEFAULT_MODEL = "jev"


def _get(source: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from an object or a mapping, whichever the SDK returned."""
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _parse_answer(key: str, raw: Any) -> Answer:
    """Map one wire answer onto the platform's typed answer."""
    kind = _get(raw, "type")
    match kind:
        case "noul":
            return NoulAnswer(noul=float(_get(raw, "noul")))
        case "choice":
            return ChoiceAnswer(
                choice=str(_get(raw, "choice")),
                probabilities={
                    str(k): float(v) for k, v in dict(_get(raw, "probabilities", {})).items()
                },
                confidence=float(_get(raw, "confidence", 0.0)),
            )
        case "score":
            return ScoreAnswer(
                score=float(_get(raw, "score")),
                probabilities={
                    str(k): float(v) for k, v in dict(_get(raw, "probabilities", {})).items()
                },
                legend={str(k): str(v) for k, v in dict(_get(raw, "legend", {})).items()},
                confidence=float(_get(raw, "confidence", 0.0)),
            )
    raise JudgmentUnavailable(f"question {key!r} returned an unrecognised answer type {kind!r}")


class HostedSystemOne:
    """Calls a hosted System One endpoint through the TypeSafe Python SDK.

    Install with the ``systemone`` extra and set ``TYPESAFE_API_KEY``. Without
    them the platform falls back to ``OfflineSystemOne``; see
    ``causeway.judgment.default_judge``.
    """

    def __init__(self, *, model: str = DEFAULT_MODEL, client: Any = None) -> None:
        self._model = model
        if client is not None:
            self._client = client
            return
        try:
            from typesafe_sdk import TypeSafeClient  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ConfigurationError(
                "the hosted judgment plane needs the 'systemone' extra: "
                "pip install 'causeway[systemone]'"
            ) from exc
        self._client = TypeSafeClient(model=model)

    @property
    def model_id(self) -> str:
        """The identifier recorded in evidence for every judgment."""
        return self._model

    def ask(self, state: State, questions: dict[str, Question]) -> JudgmentResult:
        """Evaluate every question against ``state`` in a single call.

        Questions are sent as raw wire dictionaries rather than SDK objects, so
        that a field the platform adds ahead of an SDK release still reaches
        the endpoint instead of being dropped on the floor.
        """
        wire = {key: question.to_wire() for key, question in questions.items()}
        try:
            response = self._client.system_one(state=state, questions=wire)
        except Exception as exc:
            raise JudgmentUnavailable(f"system one call failed: {exc}") from exc

        raw_answers = _get(response, "answers")
        if not raw_answers:
            raise JudgmentUnavailable("system one returned no answers")

        answers: dict[str, Answer] = {}
        for key in questions:
            raw = raw_answers[key] if isinstance(raw_answers, dict) else _get(raw_answers, key)
            if raw is None:
                raise JudgmentUnavailable(f"system one did not answer question {key!r}")
            answers[key] = _parse_answer(key, raw)

        usage_raw = _get(response, "usage") or {}
        return JudgmentResult(
            model=str(_get(response, "model", self._model)),
            answers=answers,
            usage=Usage(
                input_tokens=int(_get(usage_raw, "input_tokens", 0) or 0),
                output_tokens=int(_get(usage_raw, "output_tokens", 0) or 0),
            ),
        )
