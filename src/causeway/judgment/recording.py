"""A judgment plane wrapper that writes every call into the evidence chain.

Wrapping rather than instrumenting each call site means a judgment cannot be
made without being recorded: there is no code path that asks a question and
forgets to log it, because the logging *is* the object the platform holds.

What gets recorded is the question set, the model, and the answers -- not the
state. State can contain retrieved customer data, so it is recorded as a digest
plus a protected reference, which is enough to prove months later that the
judgment was made about exactly this input.
"""

from __future__ import annotations

from ..canonical import digest
from ..contracts.evidence import EventDomain
from ..contracts.judgment import (
    ChoiceAnswer,
    JudgmentResult,
    NoulAnswer,
    Question,
    ScoreAnswer,
    State,
    SystemOne,
)
from ..evidence import events as event_types
from ..evidence.ledger import EvidenceLedger


class RecordingJudge:
    """Decorates a ``SystemOne`` so every judgment lands in the ledger."""

    def __init__(
        self,
        inner: SystemOne,
        ledger: EvidenceLedger,
        *,
        run_id: str,
        tenant: str,
        actor: str,
        action_id: str | None = None,
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._run_id = run_id
        self._tenant = tenant
        self._actor = actor
        self._action_id = action_id

    @property
    def model_id(self) -> str:
        """The wrapped judge's model identifier."""
        return self._inner.model_id

    def for_action(self, action_id: str) -> RecordingJudge:
        """Return a judge whose events are attributed to one action."""
        return RecordingJudge(
            self._inner,
            self._ledger,
            run_id=self._run_id,
            tenant=self._tenant,
            actor=self._actor,
            action_id=action_id,
        )

    def ask(self, state: State, questions: dict[str, Question]) -> JudgmentResult:
        """Ask the wrapped judge and append the call and its answers to evidence."""
        result = self._inner.ask(state, questions)
        self._ledger.append(
            domain=EventDomain.JUDGMENT,
            event_type=event_types.JUDGMENT_ANSWERED,
            run_id=self._run_id,
            tenant=self._tenant,
            actor=self._actor,
            action_id=self._action_id,
            payload={
                "model": result.model,
                "question_keys": sorted(questions),
                "state_digest": digest(state),
                "answers": {key: _summarise(answer) for key, answer in result.answers.items()},
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
            },
            protected={"state": state},
        )
        return result


def _summarise(answer: NoulAnswer | ChoiceAnswer | ScoreAnswer) -> dict[str, float | str]:
    """Reduce an answer to the low-cardinality facts the open ledger carries."""
    match answer:
        case NoulAnswer():
            return {"type": "noul", "noul": answer.noul}
        case ChoiceAnswer():
            return {"type": "choice", "choice": answer.choice, "confidence": answer.confidence}
        case ScoreAnswer():
            return {"type": "score", "score": answer.score, "confidence": answer.confidence}
