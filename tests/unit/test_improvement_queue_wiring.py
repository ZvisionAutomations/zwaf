"""Unit tests for ImprovementQueue wiring in ZWAFTeam."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from zwaf.conversion.self_improvement import ImprovementKind, ImprovementQueue
from zwaf.core.team import ZWAFTeam


def _make_team() -> ZWAFTeam:
    return ZWAFTeam(
        tenant_config=MagicMock(tenant_id="test-tenant"),
        whatsapp_tool=MagicMock(),
        router=MagicMock(),
    )


class ImprovementQueueWiringTests(unittest.TestCase):
    def test_improvement_queue_created_on_init(self) -> None:
        with patch.dict("os.environ", {"DATABASE_URL": ""}):
            team = _make_team()

        self.assertIsInstance(team._improvement_queue, ImprovementQueue)

    def test_suggest_improvement_does_not_raise(self) -> None:
        team = _make_team()
        team._improvement_queue.suggest = MagicMock(side_effect=RuntimeError("queue failed"))

        team._suggest_improvement(
            ImprovementKind.COPY,
            "Objeção detectada",
            {"buying_intent": "medium"},
        )

    def test_suggest_improvement_calls_queue(self) -> None:
        team = _make_team()
        team._improvement_queue.suggest = MagicMock()

        team._suggest_improvement(
            ImprovementKind.TEMPLATE,
            "Escalação para humano no checkout",
            {"stage": "checkout_escalation"},
        )

        team._improvement_queue.suggest.assert_called_once_with(
            kind=ImprovementKind.TEMPLATE,
            summary="Escalação para humano no checkout",
            evidence={"stage": "checkout_escalation"},
        )


if __name__ == "__main__":
    unittest.main()
