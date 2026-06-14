"""Unit tests for ImprovementQueue public in-memory behavior."""
from __future__ import annotations

import unittest

from zwaf.conversion.self_improvement import (
    ImprovementCandidate,
    ImprovementKind,
    ImprovementQueue,
    ImprovementStatus,
    is_live_change_allowed_without_approval,
)


class ImprovementQueueTests(unittest.TestCase):
    def test_suggest_returns_candidate(self) -> None:
        queue = ImprovementQueue()

        candidate = queue.suggest(
            kind=ImprovementKind.COPY,
            summary="Improve checkout copy",
            evidence={"conversion_rate": 0.12},
        )

        self.assertIsInstance(candidate, ImprovementCandidate)
        self.assertIs(candidate.status, ImprovementStatus.SUGGESTED)
        self.assertEqual(candidate.kind, ImprovementKind.COPY)
        self.assertFalse(is_live_change_allowed_without_approval(candidate.kind))

    def test_pending_filters_suggested(self) -> None:
        queue = ImprovementQueue()
        suggested = queue.suggest(
            kind=ImprovementKind.PROMPT,
            summary="Improve objection handling",
        )
        approved = queue.suggest(
            kind=ImprovementKind.OPERATIONAL,
            summary="Tune follow-up timing",
        )
        queue.review(candidate_id=approved.id, status=ImprovementStatus.APPROVED, actor="axis")

        self.assertEqual(queue.pending(), [suggested])

    def test_review_changes_status(self) -> None:
        queue = ImprovementQueue()
        candidate = queue.suggest(kind=ImprovementKind.TEMPLATE, summary="New template")

        reviewed = queue.review(
            candidate_id=candidate.id,
            status=ImprovementStatus.APPROVED,
            actor="axis",
            note="approved",
        )

        self.assertIs(reviewed.status, ImprovementStatus.APPROVED)
        self.assertEqual(reviewed.reviewed_by, "axis")
        self.assertEqual(reviewed.review_note, "approved")

    def test_review_log_records_transition(self) -> None:
        queue = ImprovementQueue()
        candidate = queue.suggest(kind=ImprovementKind.SEGMENTATION, summary="Segment hot leads")

        queue.review(candidate_id=candidate.id, status=ImprovementStatus.REJECTED, actor="qa")

        log = queue.review_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0].candidate_id, candidate.id)
        self.assertIs(log[0].from_status, ImprovementStatus.SUGGESTED)
        self.assertIs(log[0].to_status, ImprovementStatus.REJECTED)
        self.assertEqual(log[0].actor, "qa")

    def test_promote_requires_approved(self) -> None:
        queue = ImprovementQueue()
        candidate = queue.suggest(kind=ImprovementKind.COPY, summary="Promote copy")

        with self.assertRaises(ValueError):
            queue.review(
                candidate_id=candidate.id,
                status=ImprovementStatus.PROMOTED,
                actor="pixel",
            )

    def test_no_db_url_is_inmemory(self) -> None:
        queue = ImprovementQueue()
        candidate = queue.suggest(kind="operational", summary="No DB needed")

        self.assertEqual(queue.pending(), [candidate])
        self.assertTrue(is_live_change_allowed_without_approval(candidate.kind))

    def test_candidate_tenant_id_default(self) -> None:
        candidate = ImprovementCandidate(
            id="impr-test",
            kind=ImprovementKind.COPY,
            summary="Default tenant",
            evidence={},
        )

        self.assertEqual(candidate.tenant_id, "")


if __name__ == "__main__":
    unittest.main()
