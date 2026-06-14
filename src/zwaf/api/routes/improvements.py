"""Admin routes para revisão de sugestões de melhoria (story-055)."""
from __future__ import annotations

import json
import logging
import os
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger("zwaf.api.improvements")

_DB_URL_RAW = os.getenv("DATABASE_URL", "")
_DB_URL = _DB_URL_RAW.replace("+asyncpg", "") if _DB_URL_RAW else ""

_VALID_TRANSITIONS = {
    "suggested": {"approved", "rejected"},
    "approved": {"promoted"},
}


class ReviewRequest(BaseModel):
    status: Literal["approved", "rejected", "promoted"]
    actor: str
    note: str = ""


def _evidence_to_dict(value) -> dict:
    if not value:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


@router.get("/improvements")
async def list_improvements(tenant_id: str = "", status: str = "suggested") -> dict:
    """Lista candidatos de melhoria por tenant + status."""
    if not _DB_URL:
        return {"candidates": [], "total": 0}
    try:
        import asyncpg
        conn = await asyncpg.connect(_DB_URL)
        try:
            rows = await conn.fetch(
                """
                SELECT id, tenant_id, kind, summary, evidence, status,
                       reviewed_by, review_note, created_at
                FROM improvement_candidates
                WHERE tenant_id = $1 AND status = $2
                ORDER BY created_at DESC
                """,
                tenant_id,
                status,
            )
        finally:
            await conn.close()
        candidates = [
            {
                "id": r["id"],
                "tenant_id": r["tenant_id"],
                "kind": r["kind"],
                "summary": r["summary"],
                "evidence": _evidence_to_dict(r["evidence"]),
                "status": r["status"],
                "reviewed_by": r["reviewed_by"],
                "review_note": r["review_note"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
        return {"candidates": candidates, "total": len(candidates)}
    except Exception:
        logger.exception("Failed to list improvements")
        return {"candidates": [], "total": 0}


@router.post("/improvements/{candidate_id}/review")
async def review_improvement(candidate_id: str, body: ReviewRequest) -> dict:
    """Aprova ou rejeita uma sugestão de melhoria."""
    if not _DB_URL:
        raise HTTPException(status_code=503, detail="Database not configured")
    import asyncpg
    conn = await asyncpg.connect(_DB_URL)
    try:
        row = await conn.fetchrow(
            "SELECT id, status FROM improvement_candidates WHERE id = $1",
            candidate_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Candidate not found")

        current_status = row["status"]
        allowed = _VALID_TRANSITIONS.get(current_status, set())
        if body.status not in allowed:
            if body.status == "promoted" and current_status == "suggested":
                raise HTTPException(
                    status_code=422,
                    detail="Cannot promote without approval",
                )
            raise HTTPException(
                status_code=422,
                detail=f"Cannot transition from '{current_status}' to '{body.status}'",
            )

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE improvement_candidates
                SET status = $1, reviewed_by = $2, review_note = $3
                WHERE id = $4
                """,
                body.status,
                body.actor,
                body.note,
                candidate_id,
            )
            await conn.execute(
                """
                INSERT INTO improvement_review_log
                    (candidate_id, from_status, to_status, actor, note)
                VALUES ($1, $2, $3, $4, $5)
                """,
                candidate_id,
                current_status,
                body.status,
                body.actor,
                body.note,
            )
        return {"id": candidate_id, "status": body.status, "reviewed_by": body.actor}
    finally:
        await conn.close()
