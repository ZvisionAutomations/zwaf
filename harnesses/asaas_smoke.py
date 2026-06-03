"""
Asaas smoke harness.

Default mode validates configuration and prints the selected tenant/product
without calling Asaas. Use --execute to create a sandbox customer/payment.
Production is blocked unless --allow-production is also provided.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from zwaf.tools.payment import make_payment_link_generator


TENANTS_ROOT = Path(__file__).resolve().parents[1] / "tenants"
PLACEHOLDER_MARKERS = ("aqui", "placeholder", "sua-chave", "sk-...")


def _ok(message: str) -> None:
    print(f"  [OK] {message}")


def _fail(message: str) -> None:
    print(f"  [FAIL] {message}")
    sys.exit(1)


def _step(message: str) -> None:
    print(f"\n>> {message}")


def _env_value(name: str) -> str:
    return os.getenv(name, "").strip()


def _looks_placeholder(value: str) -> bool:
    lowered = value.lower()
    return not value or any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _is_production_base_url(base_url: str) -> bool:
    normalized = base_url.rstrip("/")
    return normalized == "https://api.asaas.com/v3"


def validate_env(allow_production: bool) -> None:
    _step("1. Validar env Asaas")
    required = {
        "ASAAS_API_KEY": _env_value("ASAAS_API_KEY"),
        "ASAAS_BASE_URL": _env_value("ASAAS_BASE_URL"),
        "ASAAS_USER_AGENT": _env_value("ASAAS_USER_AGENT"),
    }
    missing = [name for name, value in required.items() if _looks_placeholder(value)]
    if missing:
        _fail("Env Asaas ausente ou placeholder: " + ", ".join(missing))

    base_url = required["ASAAS_BASE_URL"]
    if _is_production_base_url(base_url) and not allow_production:
        _fail("ASAAS_BASE_URL aponta para producao; use --allow-production apos aprovacao manual.")

    _ok(f"Base URL configurada: {base_url}")
    _ok("API key presente (valor mascarado)")


def load_payment_config(tenant_id: str) -> dict:
    _step("2. Carregar tenant")
    config_path = TENANTS_ROOT / tenant_id / "config.json"
    if not config_path.exists():
        _fail(f"Tenant '{tenant_id}' nao encontrado em {TENANTS_ROOT}")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    payment = data.get("payment")
    if not payment:
        _fail(f"Tenant '{tenant_id}' nao possui payment config")

    payment = {
        "billing_type": payment.get("billing_type", "PIX"),
        "due_days": payment.get("due_days", 2),
        "products": payment.get("products", {}),
    }
    _ok(f"Tenant carregado: {tenant_id}")
    return payment


async def run(args: argparse.Namespace) -> None:
    print("\n=== ASAAS SMOKE HARNESS ===")
    validate_env(args.allow_production)
    payment_config = load_payment_config(args.tenant)
    products = payment_config.get("products", {})
    if args.product not in products:
        _fail(f"Produto '{args.product}' nao existe no tenant '{args.tenant}'")

    selected = products[args.product]
    _ok(
        "Produto selecionado: "
        f"{args.product} / product_id={selected.get('product_id')} / "
        f"pix_cents={selected.get('price_cents_pix')}"
    )

    if args.dry_run or not args.execute:
        print("\n=== DRY RUN COMPLETO ===")
        print("  Nenhuma chamada externa foi feita. Use --execute para criar cobranca sandbox.")
        return

    _step("3. Criar cliente/cobranca Asaas")
    generate_payment_link = make_payment_link_generator(args.tenant, payment_config)
    url = await generate_payment_link(
        product_id=args.product,
        customer_phone=args.phone,
        customer_name=args.name,
        customer_document=args.document,
        billing_type=args.billing_type,
    )
    if not url.startswith("http"):
        _fail(url)
    _ok(f"Link criado: {url}")
    print("\n=== SMOKE ASAAS COMPLETO ===")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ZWAF Asaas smoke harness")
    parser.add_argument("--tenant", default="livia-raiz-vital")
    parser.add_argument("--product", default="new-woman-1")
    parser.add_argument("--phone", default="5511999990001")
    parser.add_argument("--name", default="Cliente Smoke ZWAF")
    parser.add_argument("--document", default="")
    parser.add_argument("--billing-type", default="PIX", choices=["PIX", "BOLETO", "CREDIT_CARD"])
    parser.add_argument("--dry-run", action="store_true", help="Valida config sem chamada externa")
    parser.add_argument("--execute", action="store_true", help="Cria cliente/cobranca no Asaas")
    parser.add_argument("--allow-production", action="store_true", help="Permite ASAAS_BASE_URL de producao")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
