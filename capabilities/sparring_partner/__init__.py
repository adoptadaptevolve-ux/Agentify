"""The genAI sparring partner: the platform's first golden path.

An ML team wants an agent that argues with them. Before a model change goes to
review, the sparring partner takes the proposal apart: it retrieves the team's
own standards, tries to falsify every claim the change rests on, convenes a
board of supervisors, and then -- only if it is allowed to -- opens a review
case and notifies the owner.

It is the first capability on the paved road for a reason. Its job is
*falsification*, which is also the platform's verification primitive, so the
domain logic and the platform mechanism illustrate each other. And its action
set spans four risk tiers: reading standards (R1), opening a case (R2),
notifying a human (R3), promoting a model (R4) and destroying lineage (R5,
prohibited). One workflow therefore exercises every control path the platform
has, including the two that must never fire.

What the ML team actually wrote is in this package: rubrics, falsifiers,
supervisors, five typed capabilities and a workflow graph. Roughly four hundred
lines, no security code, no audit code, no approval plumbing. That is the
measure of the paved road.
"""

from .capabilities import SPARRING_CAPABILITIES, register_sparring_capabilities
from .falsifiers import MODEL_CHANGE_SUITE, PROPOSAL_INTEGRITY_SUITE
from .supervisors import sparring_board
from .workflow import SPARRING_WORKFLOW, run_sparring_session

__all__ = [
    "MODEL_CHANGE_SUITE",
    "PROPOSAL_INTEGRITY_SUITE",
    "SPARRING_CAPABILITIES",
    "SPARRING_WORKFLOW",
    "register_sparring_capabilities",
    "run_sparring_session",
    "sparring_board",
]
