"""The workflow runtime.

Regulated and consequential journeys run as an explicit graph, not as an
open-ended plan. That is the single biggest reliability decision in the
platform, and it is worth being clear about what it costs and what it buys.

It costs flexibility: a workflow can only go where its edges go, so a genuinely
novel situation ends the run rather than improvising through it.

It buys everything else. A graph can be checkpointed, so a run survives a
restart. It can be paused for a human decision and resumed without losing
causal state. It can declare, per step, whether a side effect happens and what
compensates for it. It can be replayed, because the control flow is data rather
than whatever the model decided that afternoon. And it can be reviewed by
someone who is not an ML engineer, because it is a diagram.

Open-ended reasoning still happens -- inside a step. A step may call a model,
weigh options and draft. What it may not do is decide what the *next step* is
outside the edges the workflow declared.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..contracts.evidence import EventDomain
from ..errors import ApprovalRequired, BudgetExhausted, ConfigurationError, ContractViolation
from ..evidence import events as event_types

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from ..sdk import AgentRun


class RunStatus(StrEnum):
    """Where a run is."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    """Waiting on a human decision or an external event. Resumable."""
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """What a step decided to do next.

    ``next_step`` must name an edge the workflow declared. A step returning an
    arbitrary string is a configuration error, not a new edge.
    """

    next_step: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @staticmethod
    def go(next_step: str, **data: Any) -> StepOutcome:
        """Continue to ``next_step``, merging ``data`` into run state."""
        return StepOutcome(next_step=next_step, data=data)

    @staticmethod
    def finish(**data: Any) -> StepOutcome:
        """End the run successfully."""
        return StepOutcome(next_step=None, data=data)


StepHandler = Callable[["AgentRun", dict[str, Any]], StepOutcome]
"""A step's body. Receives the SDK handle and the run's accumulated state."""


@dataclass(frozen=True, slots=True)
class Step:
    """One node in the workflow graph."""

    id: str
    handler: StepHandler
    description: str = ""
    edges: frozenset[str] = field(default_factory=frozenset)
    """Every step this one may hand off to. Declared so the graph can be drawn,
    reviewed and checked before it runs."""
    has_side_effect: bool = False
    """Whether this step can change the world. Drives checkpointing."""
    compensation: str | None = None
    """The capability that undoes this step, if it is reversible."""
    terminal: bool = False


@dataclass(frozen=True, slots=True)
class Workflow:
    """A named, versioned graph of steps."""

    id: str
    version: str
    start: str
    steps: tuple[Step, ...]
    description: str = ""

    def __post_init__(self) -> None:
        by_id = {step.id: step for step in self.steps}
        if len(by_id) != len(self.steps):
            raise ConfigurationError(f"workflow {self.id} has duplicate step ids")
        if self.start not in by_id:
            raise ConfigurationError(f"workflow {self.id} starts at unknown step {self.start!r}")
        for step in self.steps:
            unknown = step.edges - set(by_id)
            if unknown:
                raise ConfigurationError(
                    f"workflow {self.id} step {step.id!r} declares edges to unknown steps "
                    f"{sorted(unknown)}"
                )
            if not step.edges and not step.terminal:
                raise ConfigurationError(
                    f"workflow {self.id} step {step.id!r} has no edges and is not terminal; "
                    "a run would have nowhere to go"
                )

    def step(self, step_id: str) -> Step:
        """Look up a step."""
        for step in self.steps:
            if step.id == step_id:
                return step
        raise ConfigurationError(f"workflow {self.id} has no step {step_id!r}")

    @property
    def irreversible_steps(self) -> tuple[Step, ...]:
        """Side-effecting steps with no declared compensation.

        The commitment boundary: everything before these can be unwound,
        everything after cannot. Worth knowing before a run, not during one.
        """
        return tuple(
            step for step in self.steps if step.has_side_effect and step.compensation is None
        )

    def as_mermaid(self) -> str:
        """Render the graph as Mermaid, for documentation and review."""
        lines = ["flowchart TD"]
        for step in self.steps:
            # A side-effecting step is drawn as a parallelogram, so the
            # commitment boundary is visible in the rendered diagram.
            shape = (
                f'{step.id}[/"{step.id}"/]'
                if step.has_side_effect
                else f'{step.id}["{step.id}"]'
            )
            lines.append(f"    {shape}")
            for edge in sorted(step.edges):
                lines.append(f"    {step.id} --> {edge}")
        return "\n".join(lines)


@dataclass(slots=True)
class Checkpoint:
    """Durable run state, written before and after every side effect."""

    run_id: str
    step_id: str
    status: RunStatus
    data: dict[str, Any]
    budget: dict[str, int | str]
    recorded_at: datetime
    pending_approval_id: str | None = None
    pending_action_digest: str | None = None


@dataclass(slots=True)
class RunState:
    """The live state of one workflow run."""

    run_id: str
    workflow_id: str
    workflow_version: str
    status: RunStatus
    current_step: str
    data: dict[str, Any] = field(default_factory=dict)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    pending_approval_id: str | None = None
    pending_action_digest: str | None = None
    error: str | None = None
    history: list[str] = field(default_factory=list)

    @property
    def is_resumable(self) -> bool:
        """Whether this run can be continued."""
        return self.status in (RunStatus.PAUSED, RunStatus.PENDING, RunStatus.RUNNING)

    def latest_checkpoint(self) -> Checkpoint | None:
        """The most recent checkpoint, or ``None`` if the run never reached one."""
        return self.checkpoints[-1] if self.checkpoints else None


class WorkflowRunner:
    """Executes a workflow, checkpointing around side effects.

    Pausing is not an error path. When a step needs a human decision the
    transaction guard raises ``ApprovalRequired``; the runner catches it,
    checkpoints, and marks the run ``PAUSED`` with the approval it is waiting
    on. ``resume`` re-enters the same step once the decision exists, and the
    step's idempotency key means re-running it cannot duplicate anything it
    already did.
    """

    def __init__(self, workflow: Workflow, *, max_transitions: int = 100) -> None:
        self._workflow = workflow
        self._max_transitions = max_transitions

    @property
    def workflow(self) -> Workflow:
        """The graph this runner executes."""
        return self._workflow

    def start(self, run: AgentRun, *, initial: dict[str, Any] | None = None) -> RunState:
        """Begin a run at the workflow's start step."""
        state = RunState(
            run_id=run.run_id,
            workflow_id=self._workflow.id,
            workflow_version=self._workflow.version,
            status=RunStatus.RUNNING,
            current_step=self._workflow.start,
            data=dict(initial or {}),
        )
        run.ledger.append(
            domain=EventDomain.AGENT,
            event_type=event_types.RUN_STARTED,
            run_id=run.run_id,
            tenant=run.tenant,
            actor=run.workload.service,
            payload={
                "workflow": self._workflow.id,
                "workflow_version": self._workflow.version,
                "principal_id": run.principal.id,
                "started_at": datetime.now(UTC).isoformat(),
                "budget": run.budget.budget.describe(),
            },
        )
        return self._drive(run, state)

    def resume(self, run: AgentRun, state: RunState) -> RunState:
        """Continue a paused run from its current step."""
        if not state.is_resumable:
            raise ContractViolation(
                f"run {state.run_id} is {state.status.value} and cannot be resumed"
            )
        state.status = RunStatus.RUNNING
        state.pending_approval_id = None
        state.pending_action_digest = None
        return self._drive(run, state)

    def cancel(self, run: AgentRun, state: RunState, *, reason: str) -> RunState:
        """Stop a run deliberately, recording why.

        Cancellation is a first-class feature, not an exception: kill switches
        and safe shutdown depend on there being a supported way to stop a run
        that leaves the evidence chain intact.
        """
        state.status = RunStatus.CANCELLED
        state.error = reason
        self._checkpoint(run, state)
        run.ledger.append(
            domain=EventDomain.AGENT,
            event_type=event_types.RUN_FINISHED,
            run_id=run.run_id,
            tenant=run.tenant,
            actor=run.workload.service,
            payload={
                "status": RunStatus.CANCELLED.value,
                "reason": reason,
                "finished_at": datetime.now(UTC).isoformat(),
                "steps": state.history,
            },
        )
        return state

    def _drive(self, run: AgentRun, state: RunState) -> RunState:
        """Run steps until the graph ends, pauses, or a budget stops it."""
        transitions = 0
        while state.status is RunStatus.RUNNING:
            transitions += 1
            if transitions > self._max_transitions:
                # A graph that cycles is a bug in the graph, not a reason to
                # keep spending. Stop, and say so.
                state.status = RunStatus.FAILED
                state.error = f"workflow exceeded {self._max_transitions} transitions"
                break

            step = self._workflow.step(state.current_step)
            state.history.append(step.id)
            try:
                run.budget.charge_step()
                run.budget.check_wall_time()
                if step.has_side_effect:
                    # Checkpoint *before* the side effect, so a crash between
                    # here and the receipt is recoverable rather than ambiguous.
                    self._checkpoint(run, state)
                outcome = step.handler(run, state.data)
            except ApprovalRequired as pause:
                state.status = RunStatus.PAUSED
                state.pending_approval_id = pause.approval_id
                state.pending_action_digest = pause.action_digest
                self._checkpoint(run, state)
                return state
            except BudgetExhausted as exhausted:
                state.status = RunStatus.FAILED
                state.error = str(exhausted)
                break
            except Exception as exc:
                state.status = RunStatus.FAILED
                state.error = f"{type(exc).__name__}: {exc}"
                self._checkpoint(run, state)
                run.ledger.append(
                    domain=EventDomain.AGENT,
                    event_type=event_types.RUN_FAILED,
                    run_id=run.run_id,
                    tenant=run.tenant,
                    actor=run.workload.service,
                    payload={
                        "step": step.id,
                        "error": type(exc).__name__,
                        "detail": str(exc),
                        "finished_at": datetime.now(UTC).isoformat(),
                    },
                )
                return state

            state.data.update(outcome.data)
            if step.has_side_effect:
                self._checkpoint(run, state)

            if outcome.next_step is None:
                state.status = RunStatus.COMPLETED
                break
            if outcome.next_step not in step.edges:
                state.status = RunStatus.FAILED
                state.error = (
                    f"step {step.id!r} tried to go to {outcome.next_step!r}, "
                    "which is not a declared edge"
                )
                break
            state.current_step = outcome.next_step

        self._checkpoint(run, state)
        run.ledger.append(
            domain=EventDomain.AGENT,
            event_type=(
                event_types.RUN_FINISHED
                if state.status is RunStatus.COMPLETED
                else event_types.RUN_FAILED
            ),
            run_id=run.run_id,
            tenant=run.tenant,
            actor=run.workload.service,
            payload={
                "status": state.status.value,
                "error": state.error,
                "steps": state.history,
                "budget": run.budget.snapshot(),
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )
        return state

    @staticmethod
    def _checkpoint(run: AgentRun, state: RunState) -> Checkpoint:
        """Record durable state so the run survives a restart."""
        checkpoint = Checkpoint(
            run_id=state.run_id,
            step_id=state.current_step,
            status=state.status,
            data=dict(state.data),
            budget=run.budget.snapshot(),
            recorded_at=datetime.now(UTC),
            pending_approval_id=state.pending_approval_id,
            pending_action_digest=state.pending_action_digest,
        )
        state.checkpoints.append(checkpoint)
        return checkpoint
