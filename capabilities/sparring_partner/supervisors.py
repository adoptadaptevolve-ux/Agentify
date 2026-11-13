"""The sparring partner's board.

Five reviewers, each with one job. Three come from the platform unchanged --
safety, groundedness and risk -- and two are the ML team's own: the
falsification supervisor that runs their suite, and a model-risk supervisor
that asks the questions an independent validator would ask.

Veto authority is set where the team can defend it. The model-risk supervisor
can veto from R4 upward, because promoting an unvalidated model to production
is the one action here that cannot be undone. It cannot veto an R2 case being
opened, because a supervisor that can block a *ticket* will end up blocking
everything.
"""

from __future__ import annotations

from causeway.contracts.risk import RiskTier
from causeway.verify.roles import (
    DomainSupervisor,
    FalsificationSupervisor,
    GroundednessSupervisor,
    RiskSupervisor,
    SafetySupervisor,
)
from causeway.verify.supervisors import Supervisor

from .falsifiers import MODEL_CHANGE_SUITE, PROPOSAL_INTEGRITY_SUITE

MODEL_RISK_SUPERVISOR = DomainSupervisor(
    id="model_risk",
    charter="Ask what an independent model validator would ask before production",
    questions={
        "no_validation_record": (
            "Does the proposal lack a reference to an independent validation record?"
        ),
        "no_monitoring_plan": (
            "Does the proposal omit how the model will be monitored in production?"
        ),
        "no_owner": "Is there no named accountable owner for this model?",
        "unvalidated_for_tier": (
            "Is the model being used for a decision more consequential than it was validated for?"
        ),
    },
    criteria={
        "no_validation_record": {
            "true": "No validation id, report or reviewer is named",
            "false": "A validation record is cited by reference",
        },
        "no_monitoring_plan": {
            "true": "No metrics, thresholds or alerting are described for production",
            "false": "Production monitoring is described with thresholds",
        },
    },
    veto_from=RiskTier.R4_HIGH_CONSEQUENCE,
    weight=1.5,
)


def sparring_board(*, include_integrity: bool = True) -> list[Supervisor]:
    """Seat the board for a sparring session.

    The integrity suite is separable because it is about the *proposal* rather
    than the *model*: a human pasting a document wants it checked, and an
    internal caller supplying already-validated structure does not.
    """
    board: list[Supervisor] = [
        SafetySupervisor(),
        FalsificationSupervisor(suite=MODEL_CHANGE_SUITE, id="model_change_falsification"),
        GroundednessSupervisor(),
        RiskSupervisor(),
        MODEL_RISK_SUPERVISOR,
    ]
    if include_integrity:
        board.append(
            FalsificationSupervisor(
                suite=PROPOSAL_INTEGRITY_SUITE,
                id="proposal_integrity",
                charter="Attempt to refute the claim that the proposal is what it appears to be",
                weight=1.0,
            )
        )
    return board
