"""Governance and commercial WhatsApp notifications without versioned PII."""
from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any

logger = logging.getLogger("zwaf.tools.notifications")


class NotificationRoute(str, Enum):
    OPERATOR = "operator"
    FERNANDO = "fernando"


ROUTE_ENV = {
    NotificationRoute.OPERATOR: "OPERATOR_PERSONAL_WHATSAPP",
    NotificationRoute.FERNANDO: "FERNANDO_WHATSAPP",
}


async def send_configured_notification(
    *,
    route: NotificationRoute | str,
    text: str,
    whatsapp_tool: Any,
    session_id: str = "notification",
) -> bool:
    """Send to the locally configured recipient; no phone number is stored in code."""
    route_enum = NotificationRoute(route)
    phone = os.getenv(ROUTE_ENV[route_enum], "").strip()
    if not phone:
        logger.warning("notification_recipient_not_configured", extra={"route": route_enum.value})
        return False
    if whatsapp_tool is None or not hasattr(whatsapp_tool, "send_message"):
        logger.warning("notification_whatsapp_tool_not_configured", extra={"route": route_enum.value})
        return False
    await whatsapp_tool.send_message(phone=phone, text=text, session_id=session_id)
    return True


async def alert_operator(*, text: str, whatsapp_tool: Any, session_id: str = "operator_alert") -> bool:
    return await send_configured_notification(
        route=NotificationRoute.OPERATOR,
        text=text,
        whatsapp_tool=whatsapp_tool,
        session_id=session_id,
    )


async def notify_fernando(*, text: str, whatsapp_tool: Any, session_id: str = "fernando_report") -> bool:
    return await send_configured_notification(
        route=NotificationRoute.FERNANDO,
        text=text,
        whatsapp_tool=whatsapp_tool,
        session_id=session_id,
    )
