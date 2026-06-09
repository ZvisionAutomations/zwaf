"""ViaCEP client — resolve Brazilian postal codes to address fields.

Story-040: o checkout passa a precisar apenas de CEP + numero do cliente; este
modulo completa street/district/city/state a partir do CEP (fonte de verdade),
eliminando o erro de segmentacao bairro vs cidade do LLM.

Resiliencia (NFR-1/NFR-2/FR-6): qualquer falha (timeout, 5xx, rede, JSON
invalido, {"erro": true}) retorna None — NUNCA levanta excecao para o checkout.
O caller aplica o fallback (usa os campos do LLM/cliente).

PII (NFR-4): logs NUNCA registram o CEP completo nem endereco; apenas os 3
primeiros digitos do CEP mascarado.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from zwaf.security.pii import only_digits

logger = logging.getLogger("zwaf.integrations.viacep")

_VIACEP_URL = "https://viacep.com.br/ws/{cep}/json/"

# Mapeamento ViaCEP -> campos internos de endereco.
_FIELD_MAP = {
    "logradouro": "street",
    "bairro": "district",
    "localidade": "city",
    "uf": "state",
}


def _mask_cep(cep: str) -> str:
    """Mascara o CEP para log: '01001000' -> '010*****' (NFR-4)."""
    digits = only_digits(cep)
    if len(digits) < 3:
        return "***"
    return digits[:3] + "*" * (len(digits) - 3)


def map_viacep_response(data: dict[str, Any]) -> Optional[dict[str, str]]:
    """Mapeia um payload do ViaCEP para {street, district, city, state}.

    Retorna None se o payload sinalizar erro ({"erro": true}) ou nao for um dict.
    """
    if not isinstance(data, dict):
        return None
    # ViaCEP sinaliza CEP inexistente com {"erro": true} (FR-4/AC-8).
    if data.get("erro") in (True, "true", "True"):
        return None
    resolved: dict[str, str] = {}
    for viacep_key, internal_key in _FIELD_MAP.items():
        value = str(data.get(viacep_key) or "").strip()
        if internal_key == "state":
            value = value.upper()
        resolved[internal_key] = value
    # Um CEP sem nenhum campo util nao ajuda o checkout — trate como falha.
    if not any(resolved.values()):
        return None
    return resolved


async def resolve_cep(
    cep: str,
    *,
    timeout: float = 3.0,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[dict[str, str]]:
    """Resolve um CEP via ViaCEP -> {street, district, city, state} ou None.

    Args:
        cep: CEP em qualquer formato (com ou sem mascara).
        timeout: timeout total da chamada em segundos (~3s, NFR-1).
        client: httpx.AsyncClient opcional (injecao para testes; quando ausente,
            um cliente proprio e criado com o timeout configurado).

    Returns:
        dict com street/district/city/state, ou None em qualquer falha (fallback).
    """
    digits = only_digits(cep)
    if len(digits) != 8:
        logger.info("viacep: CEP com tamanho invalido (%s)", _mask_cep(cep))
        return None

    url = _VIACEP_URL.format(cep=digits)
    try:
        if client is not None:
            resp = await client.get(url)
        else:
            async with httpx.AsyncClient(timeout=timeout) as own_client:
                resp = await own_client.get(url)
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        logger.warning("viacep: timeout resolving CEP %s", _mask_cep(cep))
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "viacep: HTTP %s resolving CEP %s",
            exc.response.status_code,
            _mask_cep(cep),
        )
        return None
    except Exception as exc:  # rede, JSON invalido, etc. -> fallback
        logger.warning("viacep: failed resolving CEP %s: %s", _mask_cep(cep), type(exc).__name__)
        return None

    resolved = map_viacep_response(data)
    if resolved is None:
        logger.info("viacep: CEP %s nao resolvido (inexistente/vazio)", _mask_cep(cep))
    return resolved
