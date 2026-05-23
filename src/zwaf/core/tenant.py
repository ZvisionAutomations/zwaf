"""TenantConfig — carrega e valida configuração por cliente do ZWAF."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

# Diretório padrão de tenants relativo ao pacote
_DEFAULT_TENANTS_ROOT = Path(__file__).parent.parent.parent.parent / "tenants"

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class TenantLoadError(Exception):
    """Falha ao carregar ou validar configuração do tenant."""


# ─────────────────────────────────────────────────────────────
# Dataclasses de configuração
# ─────────────────────────────────────────────────────────────

@dataclass
class LLMConfig:
    primary: str
    fallback: str
    temperature: float = 0.7


@dataclass
class PhoneNumberEntry:
    number: str
    instance: str


@dataclass
class WhatsAppConfig:
    evolution_api_url: str
    evolution_api_key: str
    phone_numbers: list[PhoneNumberEntry]
    messages_per_minute: int = 10
    typing_simulation: bool = True
    warm_up_mode: bool = False
    warm_up_start_date: Optional[str] = None  # ISO date string

    @property
    def current_warm_up_day(self) -> Optional[int]:
        """Dia atual de warm-up (1-indexed), ou None se não ativo."""
        if not self.warm_up_mode or not self.warm_up_start_date:
            return None
        try:
            start = date.fromisoformat(self.warm_up_start_date)
        except ValueError:
            return None
        elapsed = (date.today() - start).days
        return max(1, elapsed + 1)

    @property
    def daily_message_limit(self) -> Optional[int]:
        """Limite diário de mensagens conforme fase de warm-up, ou None se ilimitado."""
        day = self.current_warm_up_day
        if day is None:
            return None
        if day <= 3:
            return 20
        if day <= 7:
            return 50
        # Operação normal: jornada de 8h
        return self.messages_per_minute * 60 * 8


@dataclass
class RouterConfig:
    keywords: dict[str, list[str]]
    fallback_llm: bool = True


@dataclass
class TenantConfig:
    tenant_id: str
    agent_name: str
    brand_name: str
    language: str
    llm: LLMConfig
    whatsapp: WhatsAppConfig
    router: RouterConfig
    agents_enabled: list[str]
    lgpd: dict[str, Any]
    payment: Optional[dict[str, Any]] = None
    fidelizacao: Optional[dict[str, Any]] = None

    # ─────────────────────────────────────────────────────────
    # Factory
    # ─────────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        tenant_id: str,
        tenants_root: Optional[Path] = None,
    ) -> "TenantConfig":
        """
        Carrega TenantConfig de tenants/{tenant_id}/config.json.

        Substitui ${ENV_VAR} por valores reais de os.environ.
        Lança TenantLoadError em caso de falha.
        """
        root = Path(tenants_root) if tenants_root else _DEFAULT_TENANTS_ROOT
        config_path = root / tenant_id / "config.json"

        if not config_path.exists():
            raise TenantLoadError(
                f"Tenant '{tenant_id}' not found: {config_path} does not exist"
            )

        try:
            raw = config_path.read_text(encoding="utf-8")
        except OSError as e:
            raise TenantLoadError(f"Cannot read config for '{tenant_id}': {e}") from e

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise TenantLoadError(
                f"JSON parse error in config for '{tenant_id}': {e}"
            ) from e

        # Substituir ${ENV_VAR} em todo o JSON (recursivo)
        data = _substitute_env_vars(data)

        return cls._from_dict(tenant_id, data)

    @classmethod
    def _from_dict(cls, tenant_id: str, data: dict) -> "TenantConfig":
        _require(data, "agent_name")
        _require(data, "brand_name")
        _require(data, "language")
        _require(data, "llm")
        _require(data, "whatsapp")
        _require(data, "router")
        _require(data, "agents_enabled")
        _require(data, "lgpd")

        llm = LLMConfig(
            primary=data["llm"]["primary"],
            fallback=data["llm"]["fallback"],
            temperature=data["llm"].get("temperature", 0.7),
        )

        wa_raw = data["whatsapp"]
        phone_numbers = [
            PhoneNumberEntry(number=p["number"], instance=p["instance"])
            for p in wa_raw.get("phone_numbers", [])
        ]

        warm_up_mode = wa_raw.get("warm_up_mode", False)
        warm_up_start_date = wa_raw.get("warm_up_start_date")

        # Se warm_up_mode=True mas sem start_date, desabilita automaticamente
        if warm_up_mode and not warm_up_start_date:
            warm_up_mode = False

        whatsapp = WhatsAppConfig(
            evolution_api_url=wa_raw["evolution_api_url"],
            evolution_api_key=wa_raw["evolution_api_key"],
            phone_numbers=phone_numbers,
            messages_per_minute=wa_raw.get("messages_per_minute", 10),
            typing_simulation=wa_raw.get("typing_simulation", True),
            warm_up_mode=warm_up_mode,
            warm_up_start_date=warm_up_start_date,
        )

        router = RouterConfig(
            keywords=data["router"].get("keywords", {}),
            fallback_llm=data["router"].get("fallback_llm", True),
        )

        return cls(
            tenant_id=tenant_id,
            agent_name=data["agent_name"],
            brand_name=data["brand_name"],
            language=data["language"],
            llm=llm,
            whatsapp=whatsapp,
            router=router,
            agents_enabled=data["agents_enabled"],
            lgpd=data["lgpd"],
            payment=data.get("payment"),
            fidelizacao=data.get("fidelizacao"),
        )


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _require(data: dict, key: str) -> None:
    if key not in data:
        raise TenantLoadError(f"Required field '{key}' missing from tenant config")


def _substitute_env_vars(value: Any) -> Any:
    """Substitui ${ENV_VAR} recursivamente em strings, listas e dicts."""
    if isinstance(value, str):
        return _substitute_string(value)
    if isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]
    return value


def _substitute_string(s: str) -> str:
    def replace_match(m: re.Match) -> str:
        var_name = m.group(1)
        value = os.environ.get(var_name)
        if value is None:
            raise TenantLoadError(
                f"Environment variable '{var_name}' referenced in config but not set"
            )
        return value

    return _ENV_VAR_PATTERN.sub(replace_match, s)
