"""The typed judgment plane.

Where the platform needs a judgment it asks a narrow, typed question and
branches on the typed answer. It never asks for prose and parses it, and a
judgment is never authorization.
"""

from __future__ import annotations

import os

from ..contracts.judgment import SystemOne
from .offline import Cue, OfflineSystemOne, render_state
from .questions import (
    RISK_TIER_RUBRIC,
    GuardrailReading,
    egress_questions,
    groundedness_questions,
    injection_questions,
    read_guardrails,
    risk_tier_question,
    routing_question,
)
from .recording import RecordingJudge
from .systemone import DEFAULT_MODEL, HostedSystemOne

__all__ = [
    "DEFAULT_MODEL",
    "RISK_TIER_RUBRIC",
    "Cue",
    "GuardrailReading",
    "HostedSystemOne",
    "OfflineSystemOne",
    "RecordingJudge",
    "SystemOne",
    "default_judge",
    "egress_questions",
    "groundedness_questions",
    "injection_questions",
    "read_guardrails",
    "render_state",
    "risk_tier_question",
    "routing_question",
]


def default_judge(*, cues: tuple[Cue, ...] = ()) -> SystemOne:
    """Return the hosted judge when it is configured, else the offline fixture.

    The fallback direction is the safe one: a missing credential degrades to a
    deterministic local fixture that the test suite understands, rather than to
    an unavailable platform or, worse, to skipping the guardrail entirely.
    """
    if os.environ.get("TYPESAFE_API_KEY"):
        try:
            return HostedSystemOne(model=os.environ.get("TYPESAFE_DEFAULT_MODEL", DEFAULT_MODEL))
        except Exception:
            pass
    return OfflineSystemOne(cues=cues)
