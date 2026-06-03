"""Tenant product isolation tests for Raiz Vital personas."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from zwaf.core.tenant import TenantConfig
from zwaf.tools.catalog import make_catalog_search


TENANTS_ROOT = Path(__file__).resolve().parents[2] / "tenants"

TENANT_ENV = {
    "EVOLUTION_API_URL": "http://localhost:8080",
    "EVOLUTION_API_KEY": "test-evolution-key",
    "WA_NUMBER_1": "5511999990001",
    "WA_INSTANCE_1": "livia-raiz-vital-1",
    "WA_NUMBER_2": "5511999990002",
    "WA_INSTANCE_2": "caio-alpha-pulse-1",
    "WA_WARMUP_START_DATE": "2026-05-23",
    "ASAAS_API_KEY": "test-asaas-key",
    "ASAAS_BASE_URL": "https://api-sandbox.asaas.com/v3",
    "ASAAS_WEBHOOK_AUTH_TOKEN": "test-webhook-token",
    "ASAAS_USER_AGENT": "zwaf-test",
    "ASAAS_RETURN_URL": "https://raiz-vital.test/pagamento",
    "ASAAS_COMPLETION_URL": "https://raiz-vital.test/pagamento/confirmado",
    "ASAAS_DEFAULT_CUSTOMER_CPF_CNPJ": "",
}


def _load(tenant_id: str) -> TenantConfig:
    with patch.dict("os.environ", TENANT_ENV, clear=False):
        return TenantConfig.load(tenant_id, tenants_root=TENANTS_ROOT)


def test_livia_only_exposes_new_woman_payment_products():
    cfg = _load("livia-raiz-vital")

    products = set(cfg.payment["products"])
    assert products == {"new-woman"}
    assert set(cfg.payment["products"]).isdisjoint({"alpha-pulse-1", "alpha-pulse-2", "alpha-pulse-3"})


def test_caio_only_exposes_alpha_pulse_payment_products():
    cfg = _load("caio-alpha-pulse")

    products = set(cfg.payment["products"])
    assert products == {"alpha-pulse"}
    assert set(cfg.payment["products"]).isdisjoint({"new-woman", "new-woman-1"})


def test_knowledge_files_are_isolated_by_tenant():
    livia_files = {p.name for p in (TENANTS_ROOT / "livia-raiz-vital" / "knowledge").glob("*.md")}
    caio_files = {p.name for p in (TENANTS_ROOT / "caio-alpha-pulse" / "knowledge").glob("*.md")}

    assert "new-woman.md" in livia_files
    assert "alpha-pulse.md" not in livia_files
    assert "alpha-pulse.md" in caio_files
    assert "new-woman.md" not in caio_files


@pytest.mark.asyncio
async def test_catalog_search_does_not_cross_expose_products():
    livia_catalog = make_catalog_search("livia-raiz-vital")
    caio_catalog = make_catalog_search("caio-alpha-pulse")

    livia_alpha_result = await livia_catalog("Alpha Pulse")
    caio_new_woman_result = await caio_catalog("New Woman")

    assert "Alpha Pulse" not in livia_alpha_result
    assert "New Woman" not in caio_new_woman_result
