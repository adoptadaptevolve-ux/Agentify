"""Scoped agent memory.

Memory is untrusted input that the platform wrote to itself. That is the whole
difficulty: a summary an agent stored last week reads exactly like a fact, and
nothing about its formatting says "a model made this up under time pressure".

So every entry carries its provenance, its confidence and its scope, and a
model-generated summary is never an authoritative record -- if a decision needs
a fact, it reads the source system, and memory only tells it where to look.

Memory types are kept separate with different lifecycle rules, because sharing
them implicitly is how a user's stated preference ends up treated as an
enterprise policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from ..contracts.identity import DataClass
from ..errors import ContractViolation


class MemoryScope(StrEnum):
    """Which lifecycle an entry belongs to."""

    TASK = "task"
    """Ephemeral working state. Dies with the run."""

    USER_PREFERENCE = "user_preference"
    """What this person asked for. Long-lived, owned by them, deletable."""

    CASE = "case"
    """Facts about one case or ticket. Retained under the case's own rules."""

    ENTERPRISE = "enterprise"
    """Curated organisational knowledge. Written by an ingestion process with
    review, never by an agent mid-run."""


DEFAULT_TTL: dict[MemoryScope, timedelta | None] = {
    MemoryScope.TASK: timedelta(hours=1),
    MemoryScope.USER_PREFERENCE: timedelta(days=365),
    MemoryScope.CASE: timedelta(days=90),
    MemoryScope.ENTERPRISE: None,
}


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """One remembered thing, with everything needed to distrust it properly."""

    key: str
    value: str
    scope: MemoryScope
    owner: str
    tenant: str
    written_at: datetime
    provenance: str
    """Where this came from: a source system, a run id, or the model that
    summarised it. "model_summary" is a provenance, and a weak one."""
    confidence: float = 1.0
    classification: DataClass = DataClass.INTERNAL
    expires_at: datetime | None = None
    authoritative: bool = False
    """True only for entries copied verbatim from a system of record. A model's
    summary is never authoritative, however confident it sounds."""
    tags: frozenset[str] = field(default_factory=frozenset)

    def is_live(self, now: datetime) -> bool:
        """Whether the entry is still within its retention window."""
        return self.expires_at is None or now < self.expires_at


class MemoryStore:
    """Scoped, TTL-bounded memory with provenance. In-memory reference."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, MemoryScope, str], MemoryEntry] = {}

    def write(
        self,
        *,
        key: str,
        value: str,
        scope: MemoryScope,
        owner: str,
        tenant: str,
        provenance: str,
        confidence: float = 1.0,
        classification: DataClass = DataClass.INTERNAL,
        ttl: timedelta | None = None,
        authoritative: bool = False,
        now: datetime | None = None,
    ) -> MemoryEntry:
        """Store an entry under its scope's lifecycle rules."""
        moment = now or datetime.now(UTC)
        if authoritative and provenance.startswith("model"):
            raise ContractViolation(
                "a model-generated value cannot be marked authoritative; "
                "record the source system instead"
            )
        effective_ttl = ttl if ttl is not None else DEFAULT_TTL[scope]
        entry = MemoryEntry(
            key=key,
            value=value,
            scope=scope,
            owner=owner,
            tenant=tenant,
            written_at=moment,
            provenance=provenance,
            confidence=confidence,
            classification=classification,
            expires_at=moment + effective_ttl if effective_ttl else None,
            authoritative=authoritative,
        )
        self._entries[(tenant, scope, key)] = entry
        return entry

    def read(
        self,
        *,
        key: str,
        scope: MemoryScope,
        tenant: str,
        now: datetime | None = None,
    ) -> MemoryEntry | None:
        """Read an entry, treating an expired one as absent."""
        moment = now or datetime.now(UTC)
        entry = self._entries.get((tenant, scope, key))
        if entry is None or not entry.is_live(moment):
            return None
        return entry

    def forget(self, *, key: str, scope: MemoryScope, tenant: str) -> bool:
        """Delete an entry. Returns whether anything was there.

        Right-to-delete has to reach derived state too: a deployment that
        caches memory downstream invalidates those caches here.
        """
        return self._entries.pop((tenant, scope, key), None) is not None

    def forget_scope(self, *, scope: MemoryScope, tenant: str) -> int:
        """Delete every entry in one scope, e.g. at the end of a run."""
        keys = [k for k in self._entries if k[0] == tenant and k[1] is scope]
        for key in keys:
            del self._entries[key]
        return len(keys)

    def entries(
        self, *, scope: MemoryScope | None = None, tenant: str | None = None
    ) -> tuple[MemoryEntry, ...]:
        """List entries, optionally narrowed."""
        return tuple(
            entry
            for (entry_tenant, entry_scope, _), entry in self._entries.items()
            if (scope is None or entry_scope is scope)
            and (tenant is None or entry_tenant == tenant)
        )
