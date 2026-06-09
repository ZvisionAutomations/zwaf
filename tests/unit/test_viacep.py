"""Story-040: cliente ViaCEP — sempre mockado, sem rede real.

CEP publico de exemplo (01001-000) e payloads ficticios. Sem PII real.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from zwaf.integrations.viacep import _mask_cep, map_viacep_response, resolve_cep


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "viacep error",
                request=httpx.Request("GET", "https://viacep.com.br/ws/01001000/json/"),
                response=httpx.Response(self.status_code, text=self.text),
            )


class FakeClient:
    def __init__(self, response=None, raise_exc=None):
        self._response = response
        self._raise = raise_exc
        self.calls: list[str] = []

    async def get(self, url):
        self.calls.append(url)
        if self._raise is not None:
            raise self._raise
        return self._response


# --------------------------------------------------------------- mapping ----

def test_map_viacep_response_maps_fields():
    data = {
        "cep": "01001-000",
        "logradouro": "Praca da Se",
        "bairro": "Se",
        "localidade": "Sao Paulo",
        "uf": "sp",
    }
    mapped = map_viacep_response(data)
    assert mapped == {
        "street": "Praca da Se",
        "district": "Se",
        "city": "Sao Paulo",
        "state": "SP",
    }


def test_map_viacep_erro_true_returns_none():
    # AC-8: CEP inexistente {"erro": true} -> None.
    assert map_viacep_response({"erro": True}) is None
    assert map_viacep_response({"erro": "true"}) is None


def test_map_viacep_empty_returns_none():
    assert map_viacep_response({"logradouro": "", "bairro": "", "localidade": "", "uf": ""}) is None


# --------------------------------------------------------------- resolve ----

def test_resolve_cep_success_maps_fields():
    # AC-1/AC-3: 200 valido -> mapeia street/district/city/state.
    client = FakeClient(
        response=FakeResponse(
            {"logradouro": "Praca da Se", "bairro": "Se", "localidade": "Sao Paulo", "uf": "SP"}
        )
    )
    result = asyncio.run(resolve_cep("01001-000", client=client))
    assert result["street"] == "Praca da Se"
    assert result["state"] == "SP"
    assert client.calls[0] == "https://viacep.com.br/ws/01001000/json/"


def test_resolve_cep_erro_true_returns_none():
    # AC-8.
    client = FakeClient(response=FakeResponse({"erro": True}))
    assert asyncio.run(resolve_cep("99999-999", client=client)) is None


def test_resolve_cep_timeout_returns_none_no_exception():
    # NFR-1/AC-4: timeout -> None sem excecao.
    client = FakeClient(raise_exc=httpx.TimeoutException("timed out"))
    assert asyncio.run(resolve_cep("01001-000", client=client)) is None


def test_resolve_cep_5xx_returns_none():
    # FR-6: 5xx -> None.
    client = FakeClient(response=FakeResponse({}, status_code=500))
    assert asyncio.run(resolve_cep("01001-000", client=client)) is None


def test_resolve_cep_invalid_json_returns_none():
    # FR-6: JSON invalido -> None.
    client = FakeClient(response=FakeResponse(ValueError("bad json")))
    assert asyncio.run(resolve_cep("01001-000", client=client)) is None


def test_resolve_cep_invalid_length_returns_none_without_call():
    client = FakeClient(response=FakeResponse({"logradouro": "x"}))
    assert asyncio.run(resolve_cep("123", client=client)) is None
    assert client.calls == []  # nem chega a chamar a API


def test_resolve_cep_network_error_returns_none():
    client = FakeClient(raise_exc=httpx.ConnectError("no route"))
    assert asyncio.run(resolve_cep("01001-000", client=client)) is None


# -------------------------------------------------------------- masking ----

def test_mask_cep_hides_pii():
    # NFR-4: nunca logar CEP completo.
    masked = _mask_cep("01001000")
    assert masked.startswith("010")
    assert "01000" not in masked
    assert masked == "010*****"
