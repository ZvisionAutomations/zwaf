"""Supervised self-improvement queue for Livia."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger("zwaf.conversion.self_improvement")


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
    tenant_id: str = ""
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
    """Review queue with optional DB persistence and in-memory fallback."""

    def __init__(self, db_url: str | None = None, tenant_id: str = "") -> None:
        self._db_url = db_url.replace("+asyncpg", "") if db_url else None
        self._tenant_id = tenant_id
        self._candidates: dict[str, ImprovementCandidate] = {}
        self._review_log: list[ReviewLogEntry] = []

    def _run_db(self, coro: Any, operation: str) -> Any:
        try:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                return asyncio.run(coro)
            if loop.is_running():
                raise RuntimeError("cannot run DB coroutine while event loop is already running")
            return loop.run_until_complete(coro)
        except (KeyError, ValueError):
            if hasattr(coro, "close"):
                coro.close()
            raise
        except Exception as exc:  # pragma: no cover - exact DB failures vary by environment
            if hasattr(coro, "close"):
                coro.close()
            logger.warning("ImprovementQueue DB %s failed; using in-memory fallback: %s", operation, exc)
            return None

    @staticmethod
    def _candidate_from_row(row: Any) -> ImprovementCandidate:
        evidence = row["evidence"]
        if isinstance(evidence, str):
            evidence = json.loads(evidence)
        created_at = row["created_at"]
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        return ImprovementCandidate(
            id=row["id"],
            tenant_id=row["tenant_id"],
            kind=ImprovementKind(row["kind"]),
            summary=row["summary"],
            evidence=dict(evidence or {}),
            status=ImprovementStatus(row["status"]),
            created_at=created_at,
            reviewed_by=row["reviewed_by"],
            review_note=row["review_note"],
        )

    @staticmethod
    def _review_log_entry_from_row(row: Any) -> ReviewLogEntry:
        created_at = row["created_at"]
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        return ReviewLogEntry(
            candidate_id=row["candidate_id"],
            from_status=ImprovementStatus(row["from_status"]),
            to_status=ImprovementStatus(row["to_status"]),
            actor=row["actor"],
            note=row["note"],
            created_at=created_at,
        )

    async def _db_suggest(self, candidate: ImprovementCandidate) -> ImprovementCandidate:
        import asyncpg

        conn = await asyncpg.connect(self._db_url)
        try:
            await conn.execute(
                """
                INSERT INTO improvement_candidates (
                    id, tenant_id, kind, summary, evidence, status, reviewed_by, review_note
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
                ON CONFLICT (id) DO NOTHING
                """,
                candidate.id,
                candidate.tenant_id,
                candidate.kind.value,
                candidate.summary,
                json.dumps(candidate.evidence),
                candidate.status.value,
                candidate.reviewed_by,
                candidate.review_note,
            )
            return candidate
        finally:
            await conn.close()

    async def _db_review(
        self,
        candidate_id: str,
        next_status: ImprovementStatus,
        actor: str,
        note: str,
    ) -> ImprovementCandidate:
        import asyncpg

        conn = await asyncpg.connect(self._db_url)
        try:
            candidate_row = await conn.fetchrow(
                """
                SELECT id, tenant_id, kind, summary, evidence, status, reviewed_by, review_note, created_at
                FROM improvement_candidates
                WHERE id = $1 AND tenant_id = $2
                """,
                candidate_id,
                self._tenant_id,
            )
            if candidate_row is None:
                raise KeyError(candidate_id)
            candidate = self._candidate_from_row(candidate_row)
            if next_status is ImprovementStatus.PROMOTED and candidate.status is not ImprovementStatus.APPROVED:
                raise ValueError("candidate must be approved before promotion")

            updated_row = await conn.fetchrow(
                """
                UPDATE improvement_candidates
                SET status = $3,
                    reviewed_by = $4,
                    review_note = $5
                WHERE id = $1 AND tenant_id = $2
                RETURNING id, tenant_id, kind, summary, evidence, status, reviewed_by, review_note, created_at
                """,
                candidate_id,
                self._tenant_id,
                next_status.value,
                actor,
                note,
            )
            await conn.execute(
                """
                INSERT INTO improvement_review_log (
                    candidate_id, from_status, to_status, actor, note
                )
                VALUES ($1, $2, $3, $4, $5)
                """,
                candidate_id,
                candidate.status.value,
                next_status.value,
                actor,
                note,
            )
            return self._candidate_from_row(updated_row)
        finally:
            await conn.close()

    async def _db_pending(self) -> list[ImprovementCandidate]:
        import asyncpg

        conn = await asyncpg.connect(self._db_url)
        try:
            rows = await conn.fetch(
                """
                SELECT id, tenant_id, kind, summary, evidence, status, reviewed_by, review_note, created_at
                FROM improvement_candidates
                WHERE tenant_id = $1 AND status = 'suggested'
                ORDER BY created_at ASC
                """,
                self._tenant_id,
            )
            return [self._candidate_from_row(row) for row in rows]
        finally:
            await conn.close()

    async def _db_review_log(self) -> list[ReviewLogEntry]:
        import asyncpg

        conn = await asyncpg.connect(self._db_url)
        try:
            rows = await conn.fetch(
                """
                SELECT l.candidate_id, l.from_status, l.to_status, l.actor, l.note, l.created_at
                FROM improvement_review_log l
                JOIN improvement_candidates c ON c.id = l.candidate_id
                WHERE c.tenant_id = $1
                ORDER BY l.created_at ASC, l.id ASC
                """,
                self._tenant_id,
            )
            return [self._review_log_entry_from_row(row) for row in rows]
        finally:
            await conn.close()

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
            tenant_id=self._tenant_id,
        )
        self._candidates[candidate.id] = candidate
        if self._db_url:
            self._run_db(self._db_suggest(candidate), "suggest")
        return candidate

    def review(
        self,
        *,
        candidate_id: str,
        status: ImprovementStatus | str,
        actor: str,
        note: str = "",
    ) -> ImprovementCandidate:
        next_status = ImprovementStatus(status)
        if self._db_url:
            db_candidate = self._run_db(
                self._db_review(candidate_id, next_status, actor, note),
                "review",
            )
            if db_candidate is not None:
                self._candidates[candidate_id] = db_candidate
                return db_candidate

        candidate = self._candidates[candidate_id]
        if next_status is ImprovementStatus.PROMOTED and candidate.status is not ImprovementStatus.APPROVED:
            raise ValueError("candidate must be approved before promotion")
        updated = ImprovementCandidate(
            id=candidate.id,
            kind=candidate.kind,
            summary=candidate.summary,
            evidence=candidate.evidence,
            tenant_id=candidate.tenant_id,
            status=next_status,
            created_at=candidate.created_at,
            reviewed_by=actor,
            review_note=note,
        )
        self._candidates[candidate_id] = updated
        self._review_log.append(ReviewLogEntry(candidate.id, candidate.status, next_status, actor, note))
        return updated

    def pending(self) -> list[ImprovementCandidate]:
        if self._db_url:
            db_candidates = self._run_db(self._db_pending(), "pending")
            if db_candidates is not None:
                return db_candidates
        return [
            candidate
            for candidate in self._candidates.values()
            if candidate.status is ImprovementStatus.SUGGESTED
        ]

    def review_log(self) -> list[ReviewLogEntry]:
        if self._db_url:
            db_log = self._run_db(self._db_review_log(), "review_log")
            if db_log is not None:
                return db_log
        return list(self._review_log)


def is_live_change_allowed_without_approval(kind: ImprovementKind | str) -> bool:
    """Only operational tuning is allowed automatically."""
    return ImprovementKind(kind) is ImprovementKind.OPERATIONAL
