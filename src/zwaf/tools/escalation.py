"""
Escalation Tool — escala conversa para humano responsável.

Política SPEC seção 9.2:
- Lead pede humano → agente tenta resolver 1-2 turnos
- Lead insiste 2ª vez → escala para Fernando (+55 11 98014-2484)
- Situações diretas (reembolso, reação adversa, defeito) → escala imediata
- Após escalar: agente para de responder ativamente
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("zwaf.tools.escalation")

# Número de escalação padrão (Raiz Vital)
# Configurável por tenant no config.json — fallback hardcoded como default
_ESCALATION_NUMBER = os.getenv("ESCALATION_PHONE", "+5511980142484")


async def escalate_to_human(
    lead_phone: str,
    lead_name: str,
    problem_summary: str,
    conversation_history: Optional[str] = None,
    escalation_phone: Optional[str] = None,
    agent_name: str = "Lívia",
) -> str:
    """
    Escala a conversa para o responsável humano via WhatsApp.

    Envia mensagem ao responsável com contexto completo.
    Retorna mensagem de confirmação para o lead.

    Args:
        lead_phone: Número do lead
        lead_name: Nome do lead
        problem_summary: Resumo do problema
        conversation_history: Últimas mensagens (opcional)
        escalation_phone: Número do responsável (default: config/env)
        agent_name: Nome do agente (para mensagem ao lead)
    """
    target_phone = escalation_phone or _ESCALATION_NUMBER

    # Mensagem para o responsável humano
    human_message = (
        f"🚨 *Escalação de Conversa — {agent_name}*\n\n"
        f"*Lead:* {lead_name}\n"
        f"*Telefone:* {lead_phone}\n"
        f"*Problema:* {problem_summary}\n"
    )
    if conversation_history:
        human_message += f"\n*Últimas mensagens:*\n{conversation_history[:800]}"

    # Envia para o responsável via WhatsApp (usa a tool de WA do mesmo tenant)
    try:
        # Import tardio para evitar ciclo — o tool de WA é injetado via contexto
        from zwaf.tools.whatsapp import _tenant_tools
        if _tenant_tools:
            tool = next(iter(_tenant_tools.values()))
            await tool.send_message(
                phone=target_phone,
                text=human_message,
                session_id=f"escalation_{lead_phone}",
            )
            logger.info(
                "Escalation sent to human",
                extra={"lead": lead_phone, "escalation_to": target_phone[-4:]},
            )
        else:
            logger.warning(
                "No WhatsApp tool available for escalation",
                extra={"target": target_phone[-4:]},
            )
    except Exception as e:
        logger.error("Escalation notification failed: %s", e)

    # Mensagem de confirmação para o lead
    return (
        "Estou chamando o Fernando agora para te ajudar pessoalmente. "
        "Ele vai entrar em contato com você em breve! 😊"
    )
