"""Entitlement-aware retrieval.

The rule that matters here is one line long and is the difference between a
retrieval layer and a data breach:

    Filter before retrieval, not after, and never by asking the model to
    ignore what it should not have seen.

A model that has been shown an unauthorized document has been shown it. Any
instruction not to use it is a request, and requests to models are not access
control. So the ACL check happens in the query, against the caller's real
entitlements, before any chunk is loaded.

Everything retrieved is untrusted input. A document is data, never policy --
which is why retrieved content goes through the safety supervisor before it can
influence a consequential action, and why nothing in this module can widen a
delegation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..contracts.identity import DataClass, Principal
from ..errors import ContractViolation


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable unit, with the provenance that makes it citable.

    Provenance is not decoration. A claim the platform will repeat has to be
    traceable to a source, a version and a freshness date, or the groundedness
    supervisor has nothing to check it against.
    """

    id: str
    text: str
    source_id: str
    source_version: str
    owner: str
    classification: DataClass
    updated_at: datetime
    entitlements: frozenset[str] = field(default_factory=frozenset)
    """Entitlements a caller must hold to see this chunk. Empty means every
    authenticated caller in the tenant."""
    tenant: str = "*"
    tags: frozenset[str] = field(default_factory=frozenset)

    def citation(self) -> str:
        """A short, stable citation string for use in answers and evidence."""
        return f"{self.source_id}@{self.source_version}#{self.id}"


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """What a query returned, and what it was not allowed to return."""

    query: str
    chunks: tuple[Chunk, ...]
    withheld: int
    """How many matching chunks the caller was not entitled to see. Surfaced
    rather than hidden: "there is material here you cannot see" is useful to a
    user and to an auditor, and hiding it makes retrieval look complete when it
    is not."""
    purpose: str
    retrieved_at: datetime

    @property
    def citations(self) -> tuple[str, ...]:
        """Citations for everything returned."""
        return tuple(chunk.citation() for chunk in self.chunks)

    def as_state(self) -> dict[str, object]:
        """Render for a judgment call, keeping provenance attached.

        Sources travel with their text so that a groundedness question can be
        answered against the same structure the answer was written from.
        """
        return {
            "query": self.query,
            "sources": [
                {
                    "citation": chunk.citation(),
                    "owner": chunk.owner,
                    "classification": chunk.classification.value,
                    "updated_at": chunk.updated_at.isoformat(),
                    "text": chunk.text,
                }
                for chunk in self.chunks
            ],
            "withheld_count": self.withheld,
        }


class RetrievalGateway:
    """An in-memory, entitlement-filtered retrieval reference implementation.

    Matching is deliberately simple -- the point of this module is the access
    boundary, not the ranking function. A production deployment swaps the
    matcher for hybrid search and keeps the filter exactly as it is.
    """

    def __init__(self, chunks: list[Chunk] | None = None) -> None:
        self._chunks: list[Chunk] = list(chunks or [])

    def ingest(self, chunk: Chunk) -> None:
        """Add a chunk from a trusted source registry."""
        self._chunks.append(chunk)

    def query(
        self,
        text: str,
        *,
        principal: Principal,
        purpose: str,
        max_classification: DataClass = DataClass.CONFIDENTIAL,
        limit: int = 5,
        now: datetime | None = None,
    ) -> RetrievalResult:
        """Retrieve chunks this principal is entitled to, for this purpose."""
        if not purpose:
            raise ContractViolation("retrieval requires a purpose-of-use")
        moment = now or datetime.now(UTC)
        terms = {word for word in text.lower().split() if len(word) > 3}

        matched: list[tuple[int, Chunk]] = []
        withheld = 0
        for chunk in self._chunks:
            overlap = sum(1 for term in terms if term in chunk.text.lower())
            if overlap == 0:
                continue
            # The entitlement check happens here, on the candidate set, before
            # anything is returned to a caller or shown to a model.
            if not self._entitled(chunk, principal, max_classification):
                withheld += 1
                continue
            matched.append((overlap, chunk))

        matched.sort(key=lambda pair: (-pair[0], pair[1].id))
        return RetrievalResult(
            query=text,
            chunks=tuple(chunk for _, chunk in matched[:limit]),
            withheld=withheld,
            purpose=purpose,
            retrieved_at=moment,
        )

    @staticmethod
    def _entitled(
        chunk: Chunk, principal: Principal, max_classification: DataClass
    ) -> bool:
        """Whether ``principal`` may see ``chunk`` at this classification ceiling."""
        order = list(DataClass)
        if order.index(chunk.classification) > order.index(max_classification):
            return False
        if chunk.tenant not in ("*", principal.tenant):
            return False
        return not (chunk.entitlements and not (chunk.entitlements & principal.entitlements))
