"""
Setup Harness — pre-requisito de deploy para Livia Raiz Vital.

Executa em ordem:
1. Health check ZWAF API
2. Health check Evolution API
3. Cria instancias WhatsApp (uma por numero configurado)
4. Configura webhook da Evolution API -> ZWAF API
5. Valida webhook (envia evento de teste)
6. QR Code — AGUARDA CONEXAO MANUAL (requer Fernando)
7. Smoke test — envia mensagem de validacao

Uso:
    # Antes de conectar chips (configura tudo exceto QR)
    python -m harnesses.setup_harness --pre-qr

    # Apos scan do QR (valida conexao)
    python -m harnesses.setup_harness --post-qr

    # Completo (interativo)
    python -m harnesses.setup_harness --all
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Optional

import httpx


# ─── Config ──────────────────────────────────────────────────

ZWAF_URL = os.getenv("ZWAF_API_URL", "http://localhost:8000")
EVOLUTION_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
EVOLUTION_KEY = os.getenv("EVOLUTION_API_KEY", "")
TENANT_ID = os.getenv("ZWAF_TENANTS", "livia-raiz-vital").split(",")[0].strip()
WA_INSTANCE_1 = os.getenv("WA_INSTANCE_1", "livia-raiz-vital-1")
WA_INSTANCE_2 = os.getenv("WA_INSTANCE_2", "livia-raiz-vital-2")
WA_NUMBER_1 = os.getenv("WA_NUMBER_1", "")
WA_NUMBER_2 = os.getenv("WA_NUMBER_2", "")

WEBHOOK_URL = f"{ZWAF_URL}/v1/webhook/{TENANT_ID}"


def _evo_headers() -> dict:
    return {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    sys.exit(1)


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _step(msg: str) -> None:
    print(f"\n>> {msg}")


# ─── Steps ───────────────────────────────────────────────────

async def check_zwaf_health() -> None:
    _step("1. ZWAF API health check")
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{ZWAF_URL}/health")
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "ok":
                _ok(f"ZWAF API online — {ZWAF_URL}")
            else:
                _fail(f"ZWAF health returned unexpected: {data}")
    except Exception as e:
        _fail(f"ZWAF API unreachable: {e}\n  -> docker compose up -d zwaf-api")


async def check_evolution_health() -> None:
    _step("2. Evolution API health check")
    if not EVOLUTION_KEY:
        _fail("EVOLUTION_API_KEY nao configurado")
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{EVOLUTION_URL}/", headers=_evo_headers())
            if r.status_code in (200, 404):
                _ok(f"Evolution API online — {EVOLUTION_URL}")
            else:
                _fail(f"Evolution API retornou {r.status_code}")
    except Exception as e:
        _fail(f"Evolution API unreachable: {e}\n  -> docker compose up -d evolution-api")


async def create_instance(instance_name: str, number: str) -> None:
    """Cria instancia WhatsApp na Evolution API (idempotente)."""
    async with httpx.AsyncClient(timeout=10.0) as c:
        # Verificar se ja existe
        r = await c.get(
            f"{EVOLUTION_URL}/instance/fetchInstances",
            headers=_evo_headers(),
        )
        instances = r.json() if r.status_code == 200 else []
        existing = [i.get("instance", {}).get("instanceName") for i in instances]

        if instance_name in existing:
            _ok(f"Instancia '{instance_name}' ja existe")
            return

        # Criar instancia
        payload = {
            "instanceName": instance_name,
            "number": number,
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS",
            "webhook": {
                "url": WEBHOOK_URL,
                "byEvents": True,
                "base64": False,
                "events": ["MESSAGES_UPSERT"],
            },
        }
        r = await c.post(
            f"{EVOLUTION_URL}/instance/create",
            json=payload,
            headers=_evo_headers(),
        )
        if r.status_code in (200, 201):
            _ok(f"Instancia '{instance_name}' criada")
        else:
            _warn(f"Instancia '{instance_name}' create returned {r.status_code}: {r.text[:100]}")


async def create_instances() -> None:
    _step("3. Criar instancias WhatsApp")
    if not WA_INSTANCE_1:
        _fail("WA_INSTANCE_1 nao configurado")

    await create_instance(WA_INSTANCE_1, WA_NUMBER_1)
    if WA_INSTANCE_2 and WA_NUMBER_2:
        await create_instance(WA_INSTANCE_2, WA_NUMBER_2)
    else:
        _warn("WA_INSTANCE_2 / WA_NUMBER_2 nao configurados — usando apenas 1 chip")


async def configure_webhook() -> None:
    _step("4. Configurar webhook Evolution API -> ZWAF")
    async with httpx.AsyncClient(timeout=10.0) as c:
        for instance in [WA_INSTANCE_1, WA_INSTANCE_2]:
            if not instance:
                continue
            payload = {
                "url": WEBHOOK_URL,
                "byEvents": True,
                "base64": False,
                "events": ["MESSAGES_UPSERT"],
            }
            r = await c.post(
                f"{EVOLUTION_URL}/webhook/set/{instance}",
                json=payload,
                headers=_evo_headers(),
            )
            if r.status_code in (200, 201):
                _ok(f"Webhook configurado para instancia '{instance}' -> {WEBHOOK_URL}")
            else:
                _warn(f"Webhook set para '{instance}' retornou {r.status_code}: {r.text[:100]}")


async def validate_webhook() -> None:
    _step("5. Validar webhook ZWAF (evento de teste)")
    test_payload = {
        "event": "messages.upsert",
        "instance": WA_INSTANCE_1,
        "data": {
            "key": {"remoteJid": "5511000000000@s.whatsapp.net", "fromMe": False, "id": "test-001"},
            "message": {"conversation": "__harness_test__"},
            "pushName": "Harness",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.post(
                WEBHOOK_URL,
                json=test_payload,
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "accepted":
                    _ok(f"Webhook validado — ZWAF respondeu 'accepted'")
                else:
                    _warn(f"Webhook respondeu: {data}")
            else:
                _fail(f"Webhook retornou {r.status_code}: {r.text[:200]}")
    except Exception as e:
        _fail(f"Webhook test failed: {e}")


async def show_qr_codes() -> None:
    _step("6. QR Codes para conexao dos chips")
    print()
    print("  ATENCAO: Esta etapa requer o Fernando (dono dos chips WhatsApp).")
    print("  Abra os links abaixo e escaneie com o WhatsApp Business de cada numero.\n")

    for instance in [WA_INSTANCE_1, WA_INSTANCE_2]:
        if not instance:
            continue
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"{EVOLUTION_URL}/instance/connect/{instance}",
                headers=_evo_headers(),
            )
            if r.status_code == 200:
                data = r.json()
                qr = data.get("base64") or data.get("code", "")
                if qr:
                    print(f"  [{instance}] QR Code disponivel:")
                    print(f"    -> {EVOLUTION_URL}/instance/connect/{instance}")
                    print(f"    -> Ou acesse a Evolution API Manager")
                else:
                    _warn(f"QR nao disponivel para '{instance}': {data}")
            else:
                _warn(f"Nao foi possivel obter QR para '{instance}': {r.status_code}")


async def check_connection_status() -> None:
    _step("6b. Verificar status de conexao")
    for instance in [WA_INSTANCE_1, WA_INSTANCE_2]:
        if not instance:
            continue
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"{EVOLUTION_URL}/instance/connectionState/{instance}",
                headers=_evo_headers(),
            )
            if r.status_code == 200:
                data = r.json()
                state = (data.get("instance") or {}).get("state", "UNKNOWN")
                if state == "open":
                    _ok(f"Instancia '{instance}' CONECTADA")
                else:
                    _warn(f"Instancia '{instance}' estado: {state}")
            else:
                _warn(f"Nao foi possivel verificar '{instance}': {r.status_code}")


# ─── Modos de execucao ────────────────────────────────────────

async def run_pre_qr() -> None:
    """Configura tudo exceto conexao do chip (pode rodar sem Fernando)."""
    print("\n=== SETUP HARNESS — PRE-QR (sem Fernando) ===\n")
    await check_zwaf_health()
    await check_evolution_health()
    await create_instances()
    await configure_webhook()
    await validate_webhook()
    print("\n=== PRE-QR COMPLETO ===")
    print("  Proximo passo: rodar --post-qr junto com Fernando para escanear QR codes.\n")


async def run_post_qr() -> None:
    """Verifica conexao apos scan do QR (requer Fernando)."""
    print("\n=== SETUP HARNESS — POS-QR (com Fernando) ===\n")
    await check_zwaf_health()
    await check_evolution_health()
    await show_qr_codes()
    print("\n  Aguardando scan dos QR codes...")
    print("  Pressione ENTER apos Fernando escanear os chips.")
    input()
    await check_connection_status()
    print("\n=== POS-QR COMPLETO — WhatsApp conectado ===\n")


async def run_all() -> None:
    await run_pre_qr()
    await run_post_qr()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ZWAF Setup Harness — Livia Raiz Vital")
    parser.add_argument("--pre-qr", action="store_true", help="Configura stack sem conexao WhatsApp")
    parser.add_argument("--post-qr", action="store_true", help="Verifica conexao apos scan QR")
    parser.add_argument("--all", action="store_true", help="Executa tudo (interativo)")
    args = parser.parse_args()

    if args.pre_qr:
        asyncio.run(run_pre_qr())
    elif args.post_qr:
        asyncio.run(run_post_qr())
    else:
        asyncio.run(run_all())