"""Unit tests for admin improvements routes (story-055)."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch


class AdminImprovementsTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_returns_empty_without_db_url(self) -> None:
        with patch.dict("os.environ", {"DATABASE_URL": ""}):
            import importlib
            import zwaf.api.routes.improvements as mod

            importlib.reload(mod)
            result = await mod.list_improvements(tenant_id="t1", status="suggested")

        self.assertEqual(result["total"], 0)
        self.assertEqual(result["candidates"], [])

    async def test_list_returns_rows_from_db(self) -> None:
        from datetime import datetime, timezone

        fake_row = {
            "id": "impr-1",
            "tenant_id": "t1",
            "kind": "copy",
            "summary": "Melhore o CTA",
            "evidence": {"rate": 0.1},
            "status": "suggested",
            "reviewed_by": None,
            "review_note": "",
            "created_at": datetime(2026, 6, 14, tzinfo=timezone.utc),
        }
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[fake_row])
        mock_conn.close = AsyncMock()

        with (
            patch("asyncpg.connect", return_value=mock_conn),
            patch.dict("os.environ", {"DATABASE_URL": "postgresql://x"}),
        ):
            import importlib
            import zwaf.api.routes.improvements as mod

            importlib.reload(mod)
            result = await mod.list_improvements(tenant_id="t1", status="suggested")

        self.assertEqual(result["total"], 1)

    async def test_review_not_found_raises_404(self) -> None:
        from fastapi import HTTPException

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.close = AsyncMock()

        with (
            patch("asyncpg.connect", return_value=mock_conn),
            patch.dict("os.environ", {"DATABASE_URL": "postgresql://x"}),
        ):
            import importlib
            import zwaf.api.routes.improvements as mod

            importlib.reload(mod)
            from zwaf.api.routes.improvements import ReviewRequest

            with self.assertRaises(HTTPException) as ctx:
                await mod.review_improvement(
                    "missing-id",
                    ReviewRequest(status="approved", actor="axis"),
                )

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_review_invalid_transition_raises_422(self) -> None:
        from fastapi import HTTPException

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"id": "impr-1", "status": "suggested"})
        mock_conn.close = AsyncMock()

        with (
            patch("asyncpg.connect", return_value=mock_conn),
            patch.dict("os.environ", {"DATABASE_URL": "postgresql://x"}),
        ):
            import importlib
            import zwaf.api.routes.improvements as mod

            importlib.reload(mod)
            from zwaf.api.routes.improvements import ReviewRequest

            with self.assertRaises(HTTPException) as ctx:
                await mod.review_improvement(
                    "impr-1",
                    ReviewRequest(status="promoted", actor="axis"),
                )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("Cannot promote", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
