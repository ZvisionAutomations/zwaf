"""Langfuse observability smoke.

--dry-run : validates env + masking locally, sends nothing, prints no secrets.
--execute : sends one neutral trace to the configured Langfuse project.
"""
from __future__ import annotations

import argparse

from zwaf.observability import langfuse as obs


def _dry_run() -> int:
    enabled = obs.is_enabled()
    base = obs.langfuse_base_url()
    print(f"langfuse_enabled: {enabled}")
    print(f"base_url_set: {bool(base)}")

    sample = "cliente 5511980142484, email teste@raizvital.com, cpf 123.456.789-01, token sk-abcdef1234567890"
    masked = obs.mask_pii(sample)
    leaked = any(tok in masked for tok in ("5511980142484", "teste@raizvital.com", "123.456.789-01", "sk-abcdef"))
    print(f"masking_ok: {not leaked}")
    print(f"masked_sample: {masked}")
    print(f"stable_session: {obs.stable_id('livia-raiz-vital:5511980142484', prefix='sess_')}")
    print(f"phone_tail: {obs.phone_tail('5511980142484')}")
    return 0 if not leaked else 1


def _execute() -> int:
    if not obs.is_enabled():
        print("LANGFUSE keys not configured; nothing sent.")
        return 2
    obs.record_conversation(
        name="langfuse-smoke",
        session_seed="smoke:session",
        user_seed="smoke:user",
        tags=["tenant:smoke", "feature:whatsapp-agent"],
        metadata={
            "tenant_id": "smoke",
            "agent_used": "smoke",
            "feature": "whatsapp-agent",
            "phone_tail": "0000",
            "latency_ms": 0,
            "status": "ok",
        },
    )
    obs.flush()
    print("Neutral trace sent (check the Langfuse project).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    return _dry_run() if args.dry_run else _execute()


if __name__ == "__main__":
    raise SystemExit(main())
