"""Daily report formatting tests."""
from __future__ import annotations

from zwaf.reporting.daily_report import format_report


def test_format_report_includes_conversion_rate_without_pii():
    message = format_report(
        {
            "conversations_today": 10,
            "conversion_rate": 0.2,
            "sales_today": 2,
            "revenue_today_cents": 33180,
            "total_sales_all_time": 2,
        },
        date="03/06/2026",
        initial_stock=510,
    )

    assert "Taxa de conversao: 20,0%" in message
    assert "Vendas confirmadas: 2" in message
    assert "CPF" not in message
    assert "Endereco" not in message
