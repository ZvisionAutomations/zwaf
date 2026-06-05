"""TDD — testes para TenantConfig antes da implementação."""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from zwaf.core.tenant import (
    LLMConfig,
    PhoneNumberEntry,
    RouterConfig,
    TenantConfig,
    TenantLoadError,
    TypingSimulationConfig,
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

MINIMAL_CONFIG = {
    "tenant_id": "test-tenant",
    "agent_name": "TestBot",
    "brand_name": "Test Brand",
    "language": "pt-BR",
    "llm": {"primary": "gpt-4o-mini", "fallback": "gemini-1.5-flash", "temperature": 0.7},
    "whatsapp": {
        "evolution_api_url": "http://localhost:8080",
        "evolution_api_key": "test-key",
        "phone_numbers": [{"number": "5511999990001", "instance": "test-1"}],
        "warm_up_mode": False,
        "messages_per_minute": 10,
        "typing_simulation": True,
    },
    "agents_enabled": ["vendedor"],
    "router": {
        "keywords": {"vendedor": ["quero comprar"]},
        "fallback_llm": True,
    },
    "lgpd": {"consent_required": True, "data_retention_days": 730},
}

WARMUP_CONFIG = {
    **MINIMAL_CONFIG,
    "tenant_id": "warmup-tenant",
    "whatsapp": {
        **MINIMAL_CONFIG["whatsapp"],
        "warm_up_mode": True,
        "warm_up_start_date": "2026-05-01",
        "phone_numbers": [
            {"number": "5511999990001", "instance": "warmup-1"},
            {"number": "5511999990002", "instance": "warmup-2"},
        ],
    },
}


def _write_config(tmpdir: Path, config: dict, tenant_id: str) -> Path:
    tenant_dir = tmpdir / "tenants" / tenant_id
    tenant_dir.mkdir(parents=True)
    config_path = tenant_dir / "config.json"
    config_path.write_text(json.dumps(config))
    return tmpdir


# ─────────────────────────────────────────────────────────────
# Schema + loading básico
# ─────────────────────────────────────────────────────────────

class TestTenantConfigLoad:
    def test_load_minimal_config(self, tmp_path):
        root = _write_config(tmp_path, MINIMAL_CONFIG, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert cfg.tenant_id == "test-tenant"
        assert cfg.agent_name == "TestBot"
        assert cfg.brand_name == "Test Brand"
        assert cfg.language == "pt-BR"

    def test_load_llm_config(self, tmp_path):
        root = _write_config(tmp_path, MINIMAL_CONFIG, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert isinstance(cfg.llm, LLMConfig)
        assert cfg.llm.primary == "gpt-4o-mini"
        assert cfg.llm.fallback == "gemini-1.5-flash"
        assert cfg.llm.temperature == 0.7

    def test_load_phone_numbers_as_objects(self, tmp_path):
        root = _write_config(tmp_path, MINIMAL_CONFIG, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert len(cfg.whatsapp.phone_numbers) == 1
        assert isinstance(cfg.whatsapp.phone_numbers[0], PhoneNumberEntry)
        assert cfg.whatsapp.phone_numbers[0].number == "5511999990001"
        assert cfg.whatsapp.phone_numbers[0].instance == "test-1"

    def test_load_router_keywords(self, tmp_path):
        root = _write_config(tmp_path, MINIMAL_CONFIG, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert isinstance(cfg.router, RouterConfig)
        assert "vendedor" in cfg.router.keywords
        assert "quero comprar" in cfg.router.keywords["vendedor"]

    def test_load_legacy_typing_simulation_boolean(self, tmp_path):
        root = _write_config(tmp_path, MINIMAL_CONFIG, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert isinstance(cfg.whatsapp.typing_simulation, TypingSimulationConfig)
        assert cfg.whatsapp.typing_simulation.enabled is True
        assert cfg.whatsapp.typing_simulation.min_ms == 1000
        assert cfg.whatsapp.typing_simulation.max_ms == 5000

    def test_load_typing_simulation_object(self, tmp_path):
        config = {
            **MINIMAL_CONFIG,
            "whatsapp": {
                **MINIMAL_CONFIG["whatsapp"],
                "typing_simulation": {
                    "enabled": True,
                    "min_ms": 1500,
                    "max_ms": 7000,
                    "chars_per_second": 25,
                    "jitter_ms": 250,
                },
                "send_text_delay_ms": 800,
            },
        }
        root = _write_config(tmp_path, config, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert cfg.whatsapp.typing_simulation == TypingSimulationConfig(
            enabled=True,
            min_ms=1500,
            max_ms=7000,
            chars_per_second=25,
            jitter_ms=250,
        )
        assert cfg.whatsapp.send_text_delay_ms == 800

    @pytest.mark.parametrize(
        "typing_config, error",
        [
            ({"enabled": "yes"}, "enabled"),
            ({"min_ms": -1}, "min_ms"),
            ({"max_ms": 60001}, "max_ms"),
            ({"min_ms": 5000, "max_ms": 1000}, "min_ms"),
            ({"chars_per_second": 0}, "chars_per_second"),
            ({"jitter_ms": -1}, "jitter_ms"),
        ],
    )
    def test_invalid_typing_simulation_raises(self, tmp_path, typing_config, error):
        config = {
            **MINIMAL_CONFIG,
            "whatsapp": {
                **MINIMAL_CONFIG["whatsapp"],
                "typing_simulation": typing_config,
            },
        }
        root = _write_config(tmp_path, config, "test-tenant")
        with pytest.raises(TenantLoadError, match=error):
            TenantConfig.load("test-tenant", tenants_root=root / "tenants")

    def test_invalid_send_text_delay_raises(self, tmp_path):
        config = {
            **MINIMAL_CONFIG,
            "whatsapp": {
                **MINIMAL_CONFIG["whatsapp"],
                "send_text_delay_ms": -1,
            },
        }
        root = _write_config(tmp_path, config, "test-tenant")
        with pytest.raises(TenantLoadError, match="send_text_delay_ms"):
            TenantConfig.load("test-tenant", tenants_root=root / "tenants")

    def test_missing_tenant_raises(self, tmp_path):
        with pytest.raises(TenantLoadError, match="not found"):
            TenantConfig.load("nao-existe", tenants_root=tmp_path / "tenants")

    def test_invalid_json_raises(self, tmp_path):
        tenant_dir = tmp_path / "tenants" / "bad-tenant"
        tenant_dir.mkdir(parents=True)
        (tenant_dir / "config.json").write_text("{ invalid json }")
        with pytest.raises(TenantLoadError, match="JSON"):
            TenantConfig.load("bad-tenant", tenants_root=tmp_path / "tenants")

    def test_missing_required_field_raises(self, tmp_path):
        bad = {k: v for k, v in MINIMAL_CONFIG.items() if k != "agent_name"}
        tenant_dir = tmp_path / "tenants" / "bad-tenant"
        tenant_dir.mkdir(parents=True)
        (tenant_dir / "config.json").write_text(json.dumps(bad))
        with pytest.raises(TenantLoadError, match="agent_name"):
            TenantConfig.load("bad-tenant", tenants_root=tmp_path / "tenants")


# ─────────────────────────────────────────────────────────────
# Substituição de variáveis de ambiente
# ─────────────────────────────────────────────────────────────

class TestEnvVarSubstitution:
    def test_substitutes_env_var_in_string(self, tmp_path):
        config = {
            **MINIMAL_CONFIG,
            "whatsapp": {
                **MINIMAL_CONFIG["whatsapp"],
                "evolution_api_key": "${MY_API_KEY}",
            },
        }
        root = _write_config(tmp_path, config, "test-tenant")
        with patch.dict(os.environ, {"MY_API_KEY": "real-key-123"}):
            cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert cfg.whatsapp.evolution_api_key == "real-key-123"

    def test_substitutes_env_var_in_phone_number(self, tmp_path):
        config = {
            **MINIMAL_CONFIG,
            "whatsapp": {
                **MINIMAL_CONFIG["whatsapp"],
                "phone_numbers": [{"number": "${WA_NUMBER_1}", "instance": "${WA_INSTANCE_1}"}],
            },
        }
        root = _write_config(tmp_path, config, "test-tenant")
        with patch.dict(os.environ, {"WA_NUMBER_1": "5511900000001", "WA_INSTANCE_1": "inst-1"}):
            cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert cfg.whatsapp.phone_numbers[0].number == "5511900000001"
        assert cfg.whatsapp.phone_numbers[0].instance == "inst-1"

    def test_missing_env_var_raises(self, tmp_path):
        config = {
            **MINIMAL_CONFIG,
            "whatsapp": {
                **MINIMAL_CONFIG["whatsapp"],
                "evolution_api_key": "${MISSING_VAR}",
            },
        }
        root = _write_config(tmp_path, config, "test-tenant")
        env = {k: v for k, v in os.environ.items() if k != "MISSING_VAR"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(TenantLoadError, match="MISSING_VAR"):
                TenantConfig.load("test-tenant", tenants_root=root / "tenants")

    def test_no_substitution_needed_passes(self, tmp_path):
        root = _write_config(tmp_path, MINIMAL_CONFIG, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert cfg.whatsapp.evolution_api_key == "test-key"


# ─────────────────────────────────────────────────────────────
# Warm-up day calculation
# ─────────────────────────────────────────────────────────────

class TestWarmUpDay:
    def test_warm_up_disabled_returns_none(self, tmp_path):
        root = _write_config(tmp_path, MINIMAL_CONFIG, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert cfg.whatsapp.warm_up_mode is False
        assert cfg.whatsapp.current_warm_up_day is None

    def test_warm_up_day_1_on_start_date(self, tmp_path):
        today = date.today()
        config = {
            **WARMUP_CONFIG,
            "whatsapp": {**WARMUP_CONFIG["whatsapp"], "warm_up_start_date": today.isoformat()},
        }
        root = _write_config(tmp_path, config, "warmup-tenant")
        cfg = TenantConfig.load("warmup-tenant", tenants_root=root / "tenants")
        assert cfg.whatsapp.current_warm_up_day == 1

    def test_warm_up_day_increments_correctly(self, tmp_path):
        start = date.today() - timedelta(days=4)
        config = {
            **WARMUP_CONFIG,
            "whatsapp": {**WARMUP_CONFIG["whatsapp"], "warm_up_start_date": start.isoformat()},
        }
        root = _write_config(tmp_path, config, "warmup-tenant")
        cfg = TenantConfig.load("warmup-tenant", tenants_root=root / "tenants")
        assert cfg.whatsapp.current_warm_up_day == 5  # day 5 (0-indexed: 4 days elapsed + 1)

    def test_warm_up_missing_start_date_disables_warmup(self, tmp_path):
        config = {
            **WARMUP_CONFIG,
            "whatsapp": {
                k: v for k, v in WARMUP_CONFIG["whatsapp"].items()
                if k != "warm_up_start_date"
            },
        }
        root = _write_config(tmp_path, config, "warmup-tenant")
        cfg = TenantConfig.load("warmup-tenant", tenants_root=root / "tenants")
        assert cfg.whatsapp.warm_up_mode is False

    def test_warm_up_limit_days_1_to_3(self, tmp_path):
        start = date.today() - timedelta(days=1)  # day 2
        config = {
            **WARMUP_CONFIG,
            "whatsapp": {**WARMUP_CONFIG["whatsapp"], "warm_up_start_date": start.isoformat()},
        }
        root = _write_config(tmp_path, config, "warmup-tenant")
        cfg = TenantConfig.load("warmup-tenant", tenants_root=root / "tenants")
        assert cfg.whatsapp.daily_message_limit == 20

    def test_warm_up_limit_days_4_to_7(self, tmp_path):
        start = date.today() - timedelta(days=5)  # day 6
        config = {
            **WARMUP_CONFIG,
            "whatsapp": {**WARMUP_CONFIG["whatsapp"], "warm_up_start_date": start.isoformat()},
        }
        root = _write_config(tmp_path, config, "warmup-tenant")
        cfg = TenantConfig.load("warmup-tenant", tenants_root=root / "tenants")
        assert cfg.whatsapp.daily_message_limit == 50

    def test_warm_up_limit_day_8_plus_normal_operation(self, tmp_path):
        start = date.today() - timedelta(days=9)  # day 10
        config = {
            **WARMUP_CONFIG,
            "whatsapp": {
                **WARMUP_CONFIG["whatsapp"],
                "warm_up_start_date": start.isoformat(),
                "messages_per_minute": 10,
            },
        }
        root = _write_config(tmp_path, config, "warmup-tenant")
        cfg = TenantConfig.load("warmup-tenant", tenants_root=root / "tenants")
        # messages_per_minute * 60 * 8 (8h workday)
        assert cfg.whatsapp.daily_message_limit == 10 * 60 * 8

    def test_warm_up_disabled_no_daily_limit(self, tmp_path):
        root = _write_config(tmp_path, MINIMAL_CONFIG, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        # No limit when warm_up_mode is False
        assert cfg.whatsapp.daily_message_limit is None


# ─────────────────────────────────────────────────────────────
# Agents enabled / router
# ─────────────────────────────────────────────────────────────

class TestAgentsAndRouter:
    def test_agents_enabled_list(self, tmp_path):
        config = {
            **MINIMAL_CONFIG,
            "agents_enabled": ["vendedor", "recompra", "suporte"],
        }
        root = _write_config(tmp_path, config, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert cfg.agents_enabled == ["vendedor", "recompra", "suporte"]

    def test_router_fallback_llm_default_true(self, tmp_path):
        root = _write_config(tmp_path, MINIMAL_CONFIG, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert cfg.router.fallback_llm is True

    def test_fidelizacao_config_optional(self, tmp_path):
        root = _write_config(tmp_path, MINIMAL_CONFIG, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert cfg.fidelizacao is None

    def test_fidelizacao_config_loaded(self, tmp_path):
        config = {
            **MINIMAL_CONFIG,
            "fidelizacao": {"trigger_days_after_purchase": 30, "nps_enabled": True},
        }
        root = _write_config(tmp_path, config, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert cfg.fidelizacao is not None
        assert cfg.fidelizacao["trigger_days_after_purchase"] == 30

    def test_payment_config_optional(self, tmp_path):
        root = _write_config(tmp_path, MINIMAL_CONFIG, "test-tenant")
        cfg = TenantConfig.load("test-tenant", tenants_root=root / "tenants")
        assert cfg.payment is None
