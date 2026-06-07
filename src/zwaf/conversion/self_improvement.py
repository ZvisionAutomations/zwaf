"""Supervised self-improvement queue for Livia."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class ImprovementStatus(str, Enum):
    SUGGESTED = "suggested"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROMOTED = "promoted"


class ImprovementKind(str, Enum):
    COPY = "copy"
    PROMPT = "prompt"
    TEMPLATE = "template"
    SEGMENTATION = "segmentation"
    QUANTITY_RECOMMENDATION = "quantity_recommendation"
    OPERATIONAL = "operational"


@dataclass(frozen=True)
class ImprovementCandidate:
    id: str
    kind: ImprovementKind
    summary: str
    evidence: dict[str, Any]
    status: ImprovementStatus = ImprovementStatus.SUGGESTED
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reviewed_by: str | None = None
    review_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class ReviewLogEntry:
    candidate_id: str
    from_status: ImprovementStatus
    to_status: ImprovementStatus
    actor: str
    note: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ImprovementQueue:
    """In-memory review queue; persistence can be added after governance approval."""

    def __init__(self) -> None:
        self._candidates: dict[str, ImprovementCandidate] = {}
        self._review_log: list[ReviewLogEntry] = []

    def suggest(
        self,
        *,
        kind: ImprovementKind | str,
        summary: str,
        evidence: dict[str, Any] | None = None,
    ) -> ImprovementCandidate:
        candidate = ImprovementCandidate(
            id=f"impr-{uuid4().hex[:12]}",
            kind=ImprovementKind(kind),
            summary=summary,
            evidence=evidence or {},
        )
        self._candidates[candidate.id] = candidate
        return candidate

    def review(
        self,
        *,
        candidate_id: str,
        status: ImprovementStatus | str,
        actor: str,
        note: str = "",
    ) -> ImprovementCandidate:
        candidate = self._candidates[candidate_id]
        next_status = ImprovementStatus(status)
        if next_status is ImprovementStatus.PROMOTED and candidate.status is not ImprovementStatus.APPROVED:
            raise ValueError("candidate must be approved before promotion")
        updated = ImprovementCandidate(
            id=candidate.id,
            kind=candidate.kind,
            summary=candidate.summary,
            evidence=candidate.evidence,
            status=next_status,
            created_at=candidate.created_at,
            reviewed_by=actor,
            review_note=note,
        )
        self._candidates[candidate_id] = updated
        self._review_log.append(ReviewLogEntry(candidate.id, candidate.status, next_status, actor, note))
        return updated

    def pending(self) -> list[ImprovementCandidate]:
        return [
            candidate
            for candidate in self._candidates.values()
            if candidate.status is ImprovementStatus.SUGGESTED
        ]

    def review_log(self) -> list[ReviewLogEntry]:
        return list(self._review_log)


def is_live_change_allowed_without_approval(kind: ImprovementKind | str) -> bool:
    """Only operational tuning is allowed automatically."""
    return ImprovementKind(kind) is ImprovementKind.OPERATIONAL
