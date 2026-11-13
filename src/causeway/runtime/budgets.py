"""Run budgets.

An agent without a budget is an agent whose worst case is unbounded: unbounded
steps, unbounded spend, unbounded tool calls, unbounded subagents. Budgets are
not a cost-control feature bolted on afterwards -- they are the blast-radius
limit that makes a runaway run a bounded incident.

Every budget dimension fails closed. Exhaustion raises; it never logs a warning
and continues, because a run that continues past its budget has no budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ..errors import BudgetExhausted


@dataclass(frozen=True, slots=True)
class RunBudget:
    """The ceiling for one run.

    Defaults are deliberately small. A team that needs more raises them
    explicitly in their workflow definition, which makes the ceiling a
    reviewable decision rather than an accident of whatever the default was.
    """

    max_steps: int = 40
    max_tool_calls: int = 20
    max_judgment_calls: int = 60
    max_subagents: int = 4
    max_wall_time: timedelta = timedelta(minutes=10)
    max_spend: Decimal = Decimal("1.00")
    max_retrieved_chunks: int = 100

    def describe(self) -> dict[str, str]:
        """Render the ceiling for evidence and checkpoints."""
        return {
            "max_steps": str(self.max_steps),
            "max_tool_calls": str(self.max_tool_calls),
            "max_judgment_calls": str(self.max_judgment_calls),
            "max_subagents": str(self.max_subagents),
            "max_wall_time": str(self.max_wall_time),
            "max_spend": str(self.max_spend),
            "max_retrieved_chunks": str(self.max_retrieved_chunks),
        }


@dataclass(slots=True)
class BudgetLedger:
    """Live consumption against a ``RunBudget``."""

    budget: RunBudget
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    steps: int = 0
    tool_calls: int = 0
    judgment_calls: int = 0
    subagents: int = 0
    spend: Decimal = Decimal("0")
    retrieved_chunks: int = 0

    def charge_step(self) -> None:
        """Count one workflow step."""
        self.steps += 1
        self._check("max_steps", self.steps, self.budget.max_steps, "workflow steps")

    def charge_tool_call(self) -> None:
        """Count one attempted side effect."""
        self.tool_calls += 1
        self._check("max_tool_calls", self.tool_calls, self.budget.max_tool_calls, "tool calls")

    def charge_judgment(self, *, questions: int = 1, cost: Decimal = Decimal("0")) -> None:
        """Count one judgment call and its cost.

        Questions are counted per call rather than per question: batching
        twenty falsifiers into one call is the behaviour the platform wants, so
        the budget must not punish it.
        """
        self.judgment_calls += 1
        self.spend += cost
        self._check(
            "max_judgment_calls",
            self.judgment_calls,
            self.budget.max_judgment_calls,
            "judgment calls",
        )
        if self.spend > self.budget.max_spend:
            raise BudgetExhausted(
                f"run spend {self.spend} exceeds its budget of {self.budget.max_spend}"
            )

    def charge_subagent(self) -> None:
        """Count one spawned subagent."""
        self.subagents += 1
        self._check("max_subagents", self.subagents, self.budget.max_subagents, "subagents")

    def charge_retrieval(self, chunks: int) -> None:
        """Count retrieved chunks, which bound how much context a run can pull."""
        self.retrieved_chunks += chunks
        self._check(
            "max_retrieved_chunks",
            self.retrieved_chunks,
            self.budget.max_retrieved_chunks,
            "retrieved chunks",
        )

    def check_wall_time(self, *, now: datetime | None = None) -> None:
        """Raise if the run has been going longer than its budget allows."""
        moment = now or datetime.now(UTC)
        if moment - self.started_at > self.budget.max_wall_time:
            raise BudgetExhausted(
                f"run exceeded its wall-time budget of {self.budget.max_wall_time}"
            )

    def snapshot(self) -> dict[str, int | str]:
        """Consumption so far, for checkpoints and evidence."""
        return {
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "judgment_calls": self.judgment_calls,
            "subagents": self.subagents,
            "retrieved_chunks": self.retrieved_chunks,
            "spend": str(self.spend),
        }

    @staticmethod
    def _check(name: str, used: int, limit: int, label: str) -> None:
        if used > limit:
            raise BudgetExhausted(f"run exceeded its budget of {limit} {label} ({name})")
