"""Daily report dynamic alert tests."""
from __future__ import annotations

import unittest

from zwaf.reporting.daily_report import _build_alerts, format_report


class DailyReportAlertTests(unittest.TestCase):
    def test_no_alerts_normal_operation(self) -> None:
        alerts = _build_alerts(
            {
                "conversations_today": 20,
                "sales_today": 3,
                "conversion_rate": 0.15,
                "total_sales_all_time": 100,
            },
            initial_stock=600,
        )

        self.assertEqual(alerts, [])

    def test_low_stock_alert(self) -> None:
        alerts = _build_alerts(
            {"total_sales_all_time": 560, "sales_today": 1, "conversations_today": 5},
            initial_stock=600,
        )

        self.assertIn("⚠️  Estoque crítico: menos de 50 potes restantes", alerts)

    def test_zero_sales_alert(self) -> None:
        alerts = _build_alerts(
            {"sales_today": 0, "conversations_today": 10, "conversion_rate": 0.2},
            initial_stock=600,
        )

        self.assertIn("⚠️  Nenhuma venda hoje com 10 conversas ativas", alerts)

    def test_low_conversion_alert(self) -> None:
        alerts = _build_alerts(
            {"sales_today": 1, "conversations_today": 15, "conversion_rate": 0.03},
            initial_stock=600,
        )

        self.assertIn("⚠️  Taxa de conversão abaixo de 5% (3.0%)", alerts)

    def test_no_conversations_alert(self) -> None:
        alerts = _build_alerts({"conversations_today": 0}, initial_stock=600)

        self.assertIn("⚠️  Nenhuma conversa iniciada hoje", alerts)

    def test_none_values_dont_crash(self) -> None:
        alerts = _build_alerts(
            {
                "conversations_today": None,
                "sales_today": None,
                "conversion_rate": None,
                "total_sales_all_time": None,
            },
            initial_stock=600,
        )

        self.assertIsInstance(alerts, list)

    def test_format_report_shows_alerts(self) -> None:
        message = format_report(
            {
                "conversations_today": 8,
                "sales_today": 2,
                "conversion_rate": 0.25,
                "revenue_today_cents": 10000,
                "total_sales_all_time": 560,
            },
            date="14/06/2026",
            initial_stock=600,
        )

        self.assertIn("⚠️ ", message)

    def test_format_report_shows_nenhum(self) -> None:
        message = format_report(
            {
                "conversations_today": 20,
                "sales_today": 3,
                "conversion_rate": 0.15,
                "revenue_today_cents": 20000,
                "total_sales_all_time": 100,
            },
            date="14/06/2026",
            initial_stock=600,
        )

        self.assertIn("Alertas: nenhum", message)


if __name__ == "__main__":
    unittest.main()
